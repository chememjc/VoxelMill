"""SDCP V3 printer adapter.

The adapter is deliberately conservative: construction and :meth:`connect` do
not discover devices, broadcast packets, or start a job.  Discovery is an
explicit operation and all control commands are explicit method calls.  The
transport is injectable so protocol behavior can be tested without a printer
on the network (see :class:`LoopbackTransport`).

The wire format follows CBD's SDCP V3.0.0 document.  SDCP has no authentication
or encryption; callers should only use the network transport on a trusted LAN.
"""
from __future__ import annotations

import hashlib
import json
import queue
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse

from .contracts import VoxelMillError


DISCOVERY_PORT = 3000
DISCOVERY_PAYLOAD = b"M99999"
WEBSOCKET_PORT = 3030
UPLOAD_PATH = "/uploadFile/upload"
CHUNK_SIZE = 1024 * 1024
MAX_WS_MESSAGE = 8 * 1024 * 1024
MAX_PENDING_REQUESTS = 64

MACHINE_IDLE = 0
MACHINE_PRINTING = 1
MACHINE_FILE_TRANSFERRING = 2
PRINT_IDLE = 0
PRINT_HOMING = 1
PRINT_DROPPING = 2
PRINT_EXPOSURING = 3
PRINT_LIFTING = 4
PRINT_PAUSING = 5
PRINT_PAUSED = 6
PRINT_STOPPING = 7
PRINT_STOPPED = 8
PRINT_COMPLETE = 9
PRINT_FILE_CHECKING = 10


class PrinterError(VoxelMillError):
    """Base exception for adapter and protocol failures."""

    def __init__(self, message: str, code: str = "printer_error", details: Mapping[str, Any] | None = None):
        super().__init__(code, message, dict(details or {}))


class PrinterTimeout(PrinterError):
    def __init__(self, message: str = "Timed out waiting for printer", **details: Any):
        super().__init__(message, "timeout", details)


class PrinterBusy(PrinterError):
    def __init__(self, message: str = "Printer is busy", **details: Any):
        super().__init__(message, "busy", details)


class PrinterProtocolError(PrinterError):
    def __init__(self, message: str, **details: Any):
        super().__init__(message, "protocol_error", details)


class PrinterNotConnected(PrinterError):
    def __init__(self):
        super().__init__("Printer is not connected", "not_connected")


class PrinterDisconnected(PrinterError):
    def __init__(self, message: str = "Printer connection closed", **details: Any):
        super().__init__(message, "disconnected", details)


@dataclass(frozen=True)
class Discovery:
    """A validated SDCP UDP discovery result."""

    brand_id: str
    name: str
    machine_name: str
    brand_name: str
    host: str
    mainboard_id: str
    protocol_version: str = ""
    firmware_version: str = ""

    @classmethod
    def from_message(cls, value: Mapping[str, Any], sender: str | None = None) -> "Discovery":
        if not isinstance(value, Mapping):
            raise PrinterProtocolError("Discovery response is not an object")
        data = value.get("Data")
        if not isinstance(data, Mapping):
            raise PrinterProtocolError("Discovery response has no Data object")
        def required(key: str) -> str:
            item = data.get(key)
            if not isinstance(item, str) or not item.strip():
                raise PrinterProtocolError(f"Discovery response field {key!r} is missing")
            return item
        host = required("MainboardIP")
        # The advertised address is authoritative, but a simulator can omit it
        # and provide the UDP sender address instead.
        if host in ("0.0.0.0", "::") and sender:
            host = sender
        return cls(
            brand_id=str(value.get("Id", "")),
            name=required("Name"), machine_name=required("MachineName"),
            brand_name=required("BrandName"), host=host,
            mainboard_id=required("MainboardID"),
            protocol_version=str(data.get("ProtocolVersion", "")),
            firmware_version=str(data.get("FirmwareVersion", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "Id": self.brand_id,
            "Data": {
                "Name": self.name, "MachineName": self.machine_name,
                "BrandName": self.brand_name, "MainboardIP": self.host,
                "MainboardID": self.mainboard_id,
                "ProtocolVersion": self.protocol_version,
                "FirmwareVersion": self.firmware_version,
            },
        }


class WebSocketTransport:
    """Small blocking transport around the optional ``websocket-client`` package."""

    def __init__(self, url: str, timeout: float):
        try:
            import websocket  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise PrinterError("websocket-client is required for SDCP network connections", "dependency") from exc
        self._socket = websocket.create_connection(url, timeout=timeout)
        self._socket.settimeout(timeout)

    def send(self, message: str) -> None:
        self._socket.send(message)

    def recv(self, timeout: float = 0.2) -> str:
        self._socket.settimeout(timeout)
        return self._socket.recv()

    def close(self) -> None:
        self._socket.close()


class LoopbackTransport:
    """In-memory WebSocket-like transport used by tests and examples.

    ``on_send`` receives each decoded outgoing JSON object and may call
    :meth:`inject` with a response.  No socket or LAN operation is performed.
    """

    def __init__(self, on_send: Callable[[str, "LoopbackTransport"], None] | None = None):
        self.on_send = on_send
        self.sent: list[str] = []
        self._messages: queue.Queue[str] = queue.Queue()
        self.closed = False

    def send(self, message: str) -> None:
        if self.closed:
            raise OSError("loopback transport is closed")
        self.sent.append(message)
        if self.on_send:
            self.on_send(message, self)

    def inject(self, message: Mapping[str, Any] | str) -> None:
        if isinstance(message, Mapping):
            message = json.dumps(message, separators=(",", ":"))
        self._messages.put(message)

    def recv(self, timeout: float = 0.2) -> str:
        if self.closed:
            raise OSError("loopback transport is closed")
        try:
            return self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError from exc

    def close(self) -> None:
        self.closed = True


class LoopbackPrinterSimulator:
    """Deterministic in-memory SDCP printer for GUI/CLI demos and tests.

    This simulator never opens a socket. It models the command/status exchange
    needed by the adapter, making it suitable for a ``--demo`` UI mode.
    """

    def __init__(self, *, host: str = "loopback", mainboard_id: str = "demo-mainboard"):
        self.discovery = Discovery("demo-brand", "Loopback Printer", "SDCP Demo", "voxelmill", host, mainboard_id, "V3.0.0", "demo")
        self.transport = LoopbackTransport(self._on_send)
        self.machine_status = MACHINE_IDLE
        self.print_status = PRINT_IDLE
        self.filename = ""
        self.task_id = ""
        self.files: set[str] = set()
        # Capacity the simulated root listing reports, so storage() has a
        # deterministic shape to parse without touching a real printer.
        self.storage_total = 6742212608
        self.storage_used = 4303093760
        self.attributes = {
            "Name": self.discovery.name, "MachineName": self.discovery.machine_name,
            "BrandName": self.discovery.brand_name, "ProtocolVersion": "V3.0.0",
            "FirmwareVersion": "demo", "Capabilities": ["FILE_TRANSFER", "PRINT_CONTROL", "VIDEO_STREAM"],
            "CameraStatus": 1,
        }

    def _status_message(self) -> dict[str, Any]:
        return {"Status": {"CurrentStatus": self.machine_status, "PreviousStatus": 0,
                            "PrintInfo": {"Status": self.print_status, "Filename": self.filename,
                                          "TaskId": self.task_id, "CurrentLayer": 0,
                                          "TotalLayer": 1, "ErrorNumber": 0}},
                "MainboardID": self.discovery.mainboard_id,
                "Topic": f"sdcp/status/{self.discovery.mainboard_id}"}

    def _on_send(self, raw: str, transport: LoopbackTransport) -> None:
        if raw == "ping":
            transport.inject("pong")
            return
        request = json.loads(raw)
        data = request["Data"]
        cmd, payload, request_id = data["Cmd"], data.get("Data", {}), data["RequestID"]
        response_data: dict[str, Any] = {"Ack": 0}
        if cmd == 1:
            transport.inject({"Attributes": self.attributes, "MainboardID": self.discovery.mainboard_id,
                              "Topic": f"sdcp/attributes/{self.discovery.mainboard_id}"})
        elif cmd == 0:
            transport.inject(self._status_message())
        elif cmd == 128:
            self.filename = str(payload.get("Filename", ""))
            self.task_id = uuid.uuid4().hex
            self.machine_status, self.print_status = MACHINE_PRINTING, PRINT_HOMING
            transport.inject(self._status_message())
        elif cmd == 386:
            response_data["VideoUrl"] = "rtsp://loopback/demo" if payload.get("Enable") else ""
        elif cmd == 258:
            # The firmware answers "/" with storage roots carrying capacity and
            # any other URL with directory entries.  type 0 is a directory or
            # root, type 1 a file; only roots carry totalSize/usedSize.
            if str(payload.get("Url", "")) == "/":
                response_data["FileList"] = [{"name": "/local", "storageType": 0, "type": 0,
                                              "totalSize": self.storage_total,
                                              "usedSize": self.storage_used}]
            else:
                response_data["FileList"] = [{"name": name, "type": 1} for name in sorted(self.files)]
        transport.inject({"Data": {"Cmd": cmd, "Data": response_data, "RequestID": request_id,
                                    "MainboardID": self.discovery.mainboard_id},
                          "Topic": f"sdcp/response/{self.discovery.mainboard_id}"})

    def make_adapter(self, **kwargs: Any) -> "SDCPPrinterAdapter":
        adapter = SDCPPrinterAdapter(self.discovery, transport_factory=lambda _url, _timeout: self.transport, **kwargs)
        adapter.connect()
        return adapter


def discover(*, timeout: float = 1.0, broadcast_address: str = "255.255.255.255",
             port: int = DISCOVERY_PORT, socket_factory: Callable[..., socket.socket] = socket.socket) -> list[Discovery]:
    """Explicitly broadcast an SDCP discovery request and collect replies.

    This function is the only discovery entry point and is never called by the
    adapter implicitly.  Tests should inject ``socket_factory``; callers should
    be aware that this sends a LAN broadcast and that SDCP has no authentication.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    sock = socket_factory(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    results: list[Discovery] = []
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(DISCOVERY_PAYLOAD, (broadcast_address, port))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                packet, address = sock.recvfrom(64 * 1024)
            except (TimeoutError, socket.timeout):
                break
            try:
                value = json.loads(packet.decode("utf-8"))
                result = Discovery.from_message(value, address[0] if address else None)
            except (UnicodeDecodeError, json.JSONDecodeError, PrinterError, ValueError):
                continue
            if not any(x.mainboard_id == result.mainboard_id and x.host == result.host for x in results):
                results.append(result)
    finally:
        sock.close()
    return results


def _number(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PrinterProtocolError(f"{name} is not numeric", value=value) from exc


class SDCPPrinterAdapter:
    """Threaded SDCP V3 adapter with explicit, synchronous control methods."""

    def __init__(self, target: Discovery | Mapping[str, Any] | str | None = None, *,
                 transport_factory: Callable[[str, float], Any] | None = None,
                 http_post: Callable[..., Mapping[str, Any]] | None = None,
                 request_timeout: float = 5.0, start_timeout: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        if request_timeout <= 0 or start_timeout <= 0:
            raise ValueError("timeouts must be positive")
        self.target = self._coerce_target(target) if target is not None else None
        self.request_timeout = request_timeout
        self.start_timeout = start_timeout
        self._clock = clock
        self._transport_factory = transport_factory or (lambda url, timeout: WebSocketTransport(url, timeout))
        self._http_post = http_post
        self._transport: Any = None
        self._reader: threading.Thread | None = None
        self._stop_reader = threading.Event()
        self._pending: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._status: dict[str, Any] | None = None
        self._attributes: dict[str, Any] | None = None
        self._last_error: dict[str, Any] | None = None
        self._last_notice: dict[str, Any] | None = None
        self._events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        self._upload_state: dict[str, Any] | None = None
        self._pong = threading.Event()
        self._status_generation = 0
        self._attributes_generation = 0
        self._connection_error: PrinterError | None = None
        self._uncertain_start: dict[str, Any] | None = None

    @staticmethod
    def _coerce_target(target: Discovery | Mapping[str, Any] | str) -> Discovery:
        if isinstance(target, Discovery):
            return target
        if isinstance(target, str):
            return Discovery("", target, target, "", target, "")
        if isinstance(target, Mapping):
            if "Data" in target:
                return Discovery.from_message(target)
            return Discovery(
                str(target.get("Id", target.get("brand_id", ""))),
                str(target.get("Name", target.get("name", "Printer"))),
                str(target.get("MachineName", target.get("machine_name", ""))),
                str(target.get("BrandName", target.get("brand_name", ""))),
                str(target.get("MainboardIP", target.get("host", ""))),
                str(target.get("MainboardID", target.get("mainboard_id", ""))),
                str(target.get("ProtocolVersion", target.get("protocol_version", ""))),
                str(target.get("FirmwareVersion", target.get("firmware_version", ""))),
            )
        raise TypeError("target must be Discovery, mapping, host string, or None")

    @property
    def connected(self) -> bool:
        return self._transport is not None and not self._stop_reader.is_set()

    @property
    def attributes(self) -> dict[str, Any] | None:
        with self._state_lock:
            return json.loads(json.dumps(self._attributes)) if self._attributes is not None else None

    @property
    def last_error(self) -> dict[str, Any] | None:
        with self._state_lock:
            return json.loads(json.dumps(self._last_error)) if self._last_error else None

    @property
    def upload_state(self) -> dict[str, Any] | None:
        """Process-local upload checkpoint, suitable for displaying recovery state."""
        with self._state_lock:
            return json.loads(json.dumps(self._upload_state)) if self._upload_state else None

    def _emit(self, event: dict[str, Any]) -> None:
        """Publish a bounded event stream, dropping the oldest event if full."""
        try:
            self._events.put_nowait(event)
        except queue.Full:
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass
            try:
                self._events.put_nowait(event)
            except queue.Full:
                pass

    @staticmethod
    def _is_transport_timeout(exc: BaseException) -> bool:
        # websocket-client raises WebSocketTimeoutException, which does not
        # inherit built-in TimeoutError. Keep the import optional and match the
        # concrete class name only as a compatibility fallback.
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return True
        return exc.__class__.__name__ == "WebSocketTimeoutException"

    def _fail_connection(self, error: PrinterError) -> None:
        """Transition the adapter to disconnected and wake all waiters."""
        self._connection_error = error
        self._stop_reader.set()
        with self._pending_lock:
            pending, self._pending = self._pending, {}
        for event, result in pending.values():
            result["error"] = error
            event.set()
        self._emit({"kind": "disconnect", "error": str(error), "code": error.code})

    def connect(self, target: Discovery | Mapping[str, Any] | str | None = None) -> dict[str, Any]:
        """Connect to a known host and request initial status/attributes.

        No discovery is performed.  ``target`` must identify a previously known
        host and mainboard ID (a host-only target is useful with simulators).
        """
        if self.connected:
            return self.attributes or {}
        if target is not None:
            self.target = self._coerce_target(target)
        if self.target is None or not self.target.host:
            raise PrinterError("A known printer target is required", "target_required")
        if not self.target.mainboard_id:
            # Host-only targets are allowed for loopback transports but cannot
            # produce valid SDCP topics; make the failure explicit on connect.
            raise PrinterError("Target is missing MainboardID", "target_required")
        url = f"ws://{self.target.host}:{WEBSOCKET_PORT}/websocket"
        self._transport = self._transport_factory(url, self.request_timeout)
        self._connection_error = None
        self._stop_reader.clear()
        self._reader = threading.Thread(target=self._read_loop, name="sdcp-reader", daemon=True)
        self._reader.start()
        # The spec permits no automatic pushes on connect. Explicit refreshes
        # make initial state deterministic and also reconcile stale state.
        try:
            attributes_generation = self._attributes_generation
            status_generation = self._status_generation
            self._command(1, {}, timeout=self.request_timeout)
            self._command(0, {}, timeout=self.request_timeout)
            self._wait_for(lambda: self._attributes_generation > attributes_generation,
                           self.request_timeout, "attribute message")
            self._wait_for(lambda: self._status_generation > status_generation,
                           self.request_timeout, "status message")
        except Exception:
            self.close()
            raise
        return self.attributes or {}

    def close(self) -> None:
        self._fail_connection(PrinterNotConnected())
        transport, self._transport = self._transport, None
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(timeout=min(1.0, self.request_timeout))
        self._reader = None

    def _require_connected(self) -> None:
        if not self.connected:
            raise PrinterNotConnected()

    def _topic(self, kind: str) -> str:
        assert self.target is not None
        return f"sdcp/{kind}/{self.target.mainboard_id}"

    def _read_loop(self) -> None:
        while not self._stop_reader.is_set():
            try:
                raw = self._transport.recv(0.2)
            except Exception as exc:
                if self._is_transport_timeout(exc):
                    continue
                if not self._stop_reader.is_set():
                    self._fail_connection(PrinterDisconnected(str(exc), exception=type(exc).__name__))
                break
            if isinstance(raw, (bytes, str)) and len(raw) > MAX_WS_MESSAGE:
                self._fail_connection(PrinterProtocolError("WebSocket message exceeds safety limit", bytes=len(raw)))
                break
            if raw in ("pong", '"pong"'):
                self._pong.set()
                continue
            if isinstance(raw, bytes):
                try:
                    raw = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            try:
                message = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                self._events.put({"kind": "invalid", "raw": raw})
                continue
            if isinstance(message, Mapping):
                self._handle_message(message)

    def _handle_message(self, message: Mapping[str, Any]) -> None:
        topic = str(message.get("Topic", ""))
        if topic == self._topic("response"):
            data = message.get("Data")
            if not isinstance(data, Mapping):
                return
            request_id = str(data.get("RequestID", ""))
            with self._pending_lock:
                pending = self._pending.pop(request_id, None)
            if pending is not None:
                pending[1]["response"] = dict(message)
                pending[0].set()
            return
        if topic == self._topic("status"):
            with self._state_lock:
                self._status = dict(message)
                self._status_generation += 1
            self._emit({"kind": "status", "message": dict(message), "generation": self._status_generation})
        elif topic == self._topic("attributes"):
            with self._state_lock:
                self._attributes = dict(message)
                self._attributes_generation += 1
            self._emit({"kind": "attributes", "message": dict(message), "generation": self._attributes_generation})
        elif topic == self._topic("error"):
            with self._state_lock:
                self._last_error = dict(message)
            self._emit({"kind": "error", "message": dict(message)})
        elif topic == self._topic("notice"):
            with self._state_lock:
                self._last_notice = dict(message)
            self._emit({"kind": "notice", "message": dict(message)})

    def _command(self, cmd: int, payload: Mapping[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        self._require_connected()
        request_id = uuid.uuid4().hex
        event = threading.Event()
        result: dict[str, Any] = {}
        with self._pending_lock:
            if len(self._pending) >= MAX_PENDING_REQUESTS:
                raise PrinterError("Too many outstanding printer requests", "overloaded")
            self._pending[request_id] = (event, result)
        envelope = {
            "Id": self.target.brand_id if self.target else "",
            "Data": {"Cmd": cmd, "Data": dict(payload), "RequestID": request_id,
                     "MainboardID": self.target.mainboard_id, "TimeStamp": int(time.time()), "From": 0},
            "Topic": self._topic("request"),
        }
        try:
            self._transport.send(json.dumps(envelope, separators=(",", ":")))
        except Exception:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise
        wait = self.request_timeout if timeout is None else timeout
        if not event.wait(wait):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise PrinterTimeout(f"Timed out waiting for command {cmd} response")
        if "error" in result:
            raise result["error"]
        response = result.get("response")
        if not isinstance(response, Mapping):
            raise PrinterProtocolError("Response has no envelope", cmd=cmd)
        data = response.get("Data")
        if not isinstance(data, Mapping):
            raise PrinterProtocolError("Response has no Data object", cmd=cmd)
        # RequestID is the correlation key. Cmd 258 is documented with a
        # wrong echoed Cmd, so deliberately do not reject a Cmd mismatch.
        if str(data.get("RequestID", "")) != request_id:
            raise PrinterProtocolError("Response RequestID mismatch", cmd=cmd)
        ack = data.get("Data", {}).get("Ack") if isinstance(data.get("Data"), Mapping) else None
        return {"envelope": dict(response), "data": dict(data), "ack": _number(ack, "Ack") if ack is not None else None}

    @staticmethod
    def _ack_or_raise(result: Mapping[str, Any], *, cmd: int) -> int:
        ack = result.get("ack")
        if ack is None:
            raise PrinterProtocolError("Response has no Ack", cmd=cmd)
        if ack == 0:
            return ack
        if cmd == 128 and ack == 1:
            raise PrinterBusy(cmd=cmd, ack=ack)
        raise PrinterError(f"Printer rejected command {cmd} (Ack {ack})", "command_rejected", {"cmd": cmd, "ack": ack})

    def _wait_for(self, predicate: Callable[[], bool], timeout: float, what: str) -> None:
        deadline = self._clock() + timeout
        while not predicate():
            if self._clock() >= deadline:
                raise PrinterTimeout(f"Timed out waiting for {what}")
            time.sleep(min(0.02, max(0.001, deadline - self._clock())))

    def status(self, *, refresh: bool = True) -> dict[str, Any]:
        self._require_connected()
        if refresh:
            generation = self._status_generation
            self._command(0, {})
            self._wait_for(lambda: self._status_generation > generation,
                           self.request_timeout, "fresh status message")
        with self._state_lock:
            return json.loads(json.dumps(self._status)) if self._status is not None else {}

    def ping(self, *, timeout: float | None = None) -> None:
        """Send the SDCP text heartbeat and require its text ``pong`` response."""
        self._require_connected()
        self._pong.clear()
        self._transport.send("ping")
        if not self._pong.wait(self.request_timeout if timeout is None else timeout):
            raise PrinterTimeout("Timed out waiting for SDCP pong")

    def request_attributes(self) -> dict[str, Any]:
        self._require_connected()
        generation = self._attributes_generation
        self._command(1, {})
        self._wait_for(lambda: self._attributes_generation > generation,
                       self.request_timeout, "fresh attribute message")
        return self.attributes or {}

    def _require_capability(self, capability: str) -> None:
        attrs = self.attributes
        if attrs is None:
            attrs = self.request_attributes()
        data = attrs.get("Attributes", {}) if isinstance(attrs, Mapping) else {}
        capabilities = data.get("Capabilities") if isinstance(data, Mapping) else None
        if not isinstance(capabilities, list):
            raise PrinterProtocolError("Attributes.Capabilities is missing or not an array")
        if capability not in capabilities:
            raise PrinterError(f"Printer does not advertise {capability}", "unsupported", {"capability": capability})

    def _machine_statuses(self, status: Mapping[str, Any] | None = None) -> set[int]:
        status = status if status is not None else self._status
        body = status.get("Status", {}) if isinstance(status, Mapping) else {}
        raw = body.get("CurrentStatus", []) if isinstance(body, Mapping) else []
        values = raw if isinstance(raw, list) else [raw]
        return {_number(item, "CurrentStatus") for item in values}

    @staticmethod
    def _safe_filename(path: Path) -> str:
        filename = path.name
        if not filename or filename in (".", "..") or filename != str(path).split("/")[-1]:
            raise PrinterError("Upload path must end in a filename", "invalid_filename")
        if any(char in filename for char in ('\r', '\n', '"', "\\")):
            raise PrinterError("Upload filename contains unsafe multipart characters", "invalid_filename")
        return filename

    def upload(self, path: Path, cancel: Any = None, progress: Callable[[str, int, int], None] | None = None,
               *, check: bool = True, chunk_size: int = CHUNK_SIZE) -> str:
        """Upload a file sequentially and return its basename for Cmd 128.

        The server provides no positive MD5-verification message.  This method
        reports completion of all HTTP chunks; callers should use status/error
        plus Cmd 258 before starting if the device is known to be unreliable.
        """
        self._require_connected()
        self._require_capability("FILE_TRANSFER")
        current = self.status(refresh=True)
        if self._machine_statuses(current) & {MACHINE_PRINTING, MACHINE_FILE_TRANSFERRING}:
            raise PrinterBusy("Cannot upload while printer is busy", statuses=sorted(self._machine_statuses(current)))
        file_path = Path(path)
        if not file_path.is_file():
            raise PrinterError(f"File does not exist: {file_path}", "file_not_found")
        filename = self._safe_filename(file_path)
        if chunk_size <= 0 or chunk_size > CHUNK_SIZE:
            raise ValueError("chunk_size must be between 1 and 1 MiB")
        total = file_path.stat().st_size
        initial_stat = file_path.stat()
        digest = hashlib.md5()
        with file_path.open("rb") as stream:
            while True:
                if cancel is not None:
                    cancel.check()
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        if (file_path.stat().st_size, file_path.stat().st_mtime_ns) != (initial_stat.st_size, initial_stat.st_mtime_ns):
            raise PrinterError("File changed while calculating upload checksum", "file_changed")
        md5 = digest.hexdigest()
        upload_uuid = uuid.uuid4().hex
        self._upload_state = {"uuid": upload_uuid, "filename": file_path.name, "offset": 0,
                              "total_size": total, "md5": md5}
        offset = 0
        try:
            with file_path.open("rb") as stream:
                uploaded_digest = hashlib.md5()
                while offset < total or (total == 0 and offset == 0):
                    if cancel is not None:
                        cancel.check()
                    stat_now = file_path.stat()
                    if (stat_now.st_size, stat_now.st_mtime_ns) != (initial_stat.st_size, initial_stat.st_mtime_ns):
                        raise PrinterError("File changed during upload", "file_changed", {"offset": offset})
                    chunk = stream.read(chunk_size)
                    if total and not chunk:
                        raise PrinterProtocolError("File changed during upload", path=str(file_path))
                    uploaded_digest.update(chunk)
                    fields = {"S-File-MD5": md5, "Check": "1" if check else "0",
                              "Offset": str(offset), "Uuid": upload_uuid,
                              "TotalSize": str(total), "filename": filename}
                    response = self._post_chunk(fields, chunk, filename)
                    if response.get("success") is not True or str(response.get("code", "")) != "000000":
                        raise PrinterError("Printer rejected file chunk", "upload_failed", {"response": dict(response), "offset": offset})
                    offset += len(chunk)
                    self._upload_state["offset"] = offset
                    if progress:
                        progress("upload", offset, total)
                    if total == 0:
                        break
                if uploaded_digest.hexdigest() != md5:
                    raise PrinterError("File changed during upload", "file_changed", {"offset": offset})
                stat_final = file_path.stat()
                if (stat_final.st_size, stat_final.st_mtime_ns) != (initial_stat.st_size, initial_stat.st_mtime_ns):
                    raise PrinterError("File changed during upload", "file_changed", {"offset": offset})
                # A caller or filesystem watcher can replace bytes while
                # preserving size and timestamps. Rehash the source once at
                # the end so that metadata is only an early cheap check.
                final_digest = hashlib.md5()
                with file_path.open("rb") as verify_stream:
                    while True:
                        if cancel is not None:
                            cancel.check()
                        verify_chunk = verify_stream.read(chunk_size)
                        if not verify_chunk:
                            break
                        final_digest.update(verify_chunk)
                if final_digest.hexdigest() != md5:
                    raise PrinterError("File changed during upload", "file_changed", {"offset": offset})
        except (PrinterError, OSError):
            raise
        return filename

    def list_files(self, url: str = "/local/") -> list[dict[str, Any]]:
        """Return the printer's filtered printable file list (Cmd 258)."""
        self._require_capability("FILE_TRANSFER")
        if not isinstance(url, str) or not url:
            raise ValueError("url must be a nonempty path")
        result = self._command(258, {"Url": url})
        self._ack_or_raise(result, cmd=258)
        data = result["data"].get("Data", {})
        files = data.get("FileList", []) if isinstance(data, Mapping) else []
        if not isinstance(files, list):
            raise PrinterProtocolError("FileList is not an array")
        return [dict(item) for item in files if isinstance(item, Mapping)]

    def storage(self) -> list[dict[str, Any]]:
        """Return storage roots with capacity, from the Cmd 258 root listing.

        Only the root URL reports ``totalSize``/``usedSize``; a directory
        listing does not.  Sizes the firmware omits or sends unparseably stay
        ``None`` rather than becoming a fabricated zero, because zero free
        bytes and unknown free bytes are not the same claim.
        """
        roots: list[dict[str, Any]] = []
        for entry in self.list_files("/"):
            total, used = entry.get("totalSize"), entry.get("usedSize")
            try:
                total_bytes, used_bytes = int(total), int(used)
            except (TypeError, ValueError):
                total_bytes = used_bytes = None
            free_bytes = percent = None
            if total_bytes is not None and used_bytes is not None and total_bytes > 0:
                free_bytes = max(0, total_bytes - used_bytes)
                percent = used_bytes * 100.0 / total_bytes
            roots.append({
                "name": entry.get("name"), "storage_type": entry.get("storageType"),
                "total_bytes": total_bytes, "used_bytes": used_bytes,
                "free_bytes": free_bytes, "percent_used": percent,
            })
        return roots

    def history_ids(self) -> list[str]:
        """Return the printer's ordered historical task identifiers (Cmd 320)."""
        result = self._command(320, {})
        self._ack_or_raise(result, cmd=320)
        data = result["data"].get("Data", {})
        items = data.get("HistoryData", []) if isinstance(data, Mapping) else []
        if not isinstance(items, list):
            raise PrinterProtocolError("HistoryData is not an array")
        return [str(item) for item in items if isinstance(item, (str, int)) and str(item)]

    def history(self, task_ids: list[str] | tuple[str, ...] | None = None, *, batch_size: int = 10) -> list[dict[str, Any]]:
        """Return print-history details, fetching the current history when omitted."""
        ids = self.history_ids() if task_ids is None else [str(item) for item in task_ids]
        if not ids:
            return []
        if any(not item for item in ids):
            raise ValueError("task IDs must be nonempty")
        if batch_size < 1 or batch_size > 10:
            raise ValueError("batch_size must be between 1 and 10")
        found: list[dict[str, Any]] = []
        for offset in range(0, len(ids), batch_size):
            result = self._command(321, {"Id": ids[offset:offset + batch_size]})
            self._ack_or_raise(result, cmd=321)
            data = result["data"].get("Data", {})
            details = data.get("HistoryDetailList", []) if isinstance(data, Mapping) else []
            if not isinstance(details, list):
                raise PrinterProtocolError("HistoryDetailList is not an array")
            found.extend(dict(item) for item in details if isinstance(item, Mapping))
        # Firmware may return each batch in its own order. Present the order
        # supplied by Cmd 320 whenever task IDs are present, then retain any
        # malformed/id-less records for inspection rather than dropping them.
        order = {task_id: index for index, task_id in enumerate(ids)}
        return sorted(found, key=lambda item: order.get(str(item.get("TaskId", "")), len(order)))

    def information(self, *, refresh: bool = True) -> dict[str, Any]:
        """Return normalized read-only telemetry without inventing health claims."""
        status = self.status(refresh=refresh)
        attributes = self.request_attributes() if refresh else (self.attributes or {})
        body = status.get("Status", {}) if isinstance(status, Mapping) else {}
        attrs = attributes.get("Attributes", {}) if isinstance(attributes, Mapping) else {}
        body = body if isinstance(body, Mapping) else {}
        attrs = attrs if isinstance(attrs, Mapping) else {}
        devices = attrs.get("DevicesStatus", {})
        devices = devices if isinstance(devices, Mapping) else {}
        used, maximum = body.get("ReleaseFilm"), attrs.get("ReleaseFilmMax")
        try:
            remaining = max(0, int(maximum) - int(used)) if int(maximum) > 0 else None
            percent = float(used) * 100.0 / float(maximum) if float(maximum) > 0 else None
        except (TypeError, ValueError, ZeroDivisionError):
            remaining = percent = None
        return {
            "target": self.target.to_dict() if self.target else None,
            "status": dict(body), "attributes": dict(attrs),
            "release_film": {
                "uses": used, "rated_max": maximum, "remaining_uses": remaining,
                "percent_of_rated_max": percent,
                "device_state": devices.get("RelaseFilmState"),
                "assessment": "telemetry_only",
            },
        }

    def _download_url(self, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise PrinterError("Download URL is missing", "download_url")
        assert self.target is not None
        url = urljoin(f"http://{self.target.host}:{WEBSOCKET_PORT}/", value.strip())
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise PrinterError("Printer supplied an unsupported download URL", "download_url",
                               {"url": value, "scheme": parsed.scheme})
        return url

    def download(self, url: str, destination: Path, *, cancel: Any = None,
                 progress: Callable[[str, int, int], None] | None = None) -> Path:
        """Download a printer-provided history asset to an atomically replaced file."""
        self._require_connected()
        resolved = self._download_url(url)
        target = Path(destination)
        if not target.name:
            raise ValueError("destination must name a file")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
        try:
            with urllib_request.urlopen(resolved, timeout=self.request_timeout) as response, temporary.open("xb") as stream:
                raw_total = response.headers.get("Content-Length")
                try:
                    total = max(0, int(raw_total)) if raw_total is not None else 0
                except (TypeError, ValueError):
                    total = 0
                received = 0
                while True:
                    if cancel is not None:
                        cancel.check()
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    received += len(chunk)
                    if progress:
                        progress("download", received, total)
            if total and received != total:
                raise PrinterError("Printer download ended before Content-Length bytes arrived",
                                   "download_incomplete", {"received": received, "expected": total})
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return target

    def download_timelapse(self, task: Mapping[str, Any], destination: Path, **kwargs: Any) -> Path:
        """Download a task's completed time-lapse video."""
        status = task.get("TimeLapseVideoStatus")
        try:
            available = int(status) == 1
        except (TypeError, ValueError):
            available = False
        if not available:
            raise PrinterError("Time-lapse video is not available", "timelapse_unavailable",
                               {"status": status, "task_id": task.get("TaskId")})
        return self.download(str(task.get("TimeLapseVideoUrl", "")), destination, **kwargs)

    def abort_upload(self) -> int:
        """Ask the printer to terminate the current upload, if one is tracked."""
        self._require_capability("FILE_TRANSFER")
        state = self.upload_state
        if not state:
            return 1  # SDCP NOT_TRANSFER; harmless local no-op.
        result = self._command(255, {"Uuid": state["uuid"], "FileName": state["filename"]})
        ack = result.get("ack")
        if ack is None:
            raise PrinterProtocolError("Terminate-transfer response has no Ack")
        if ack in (0, 1, 3):
            with self._state_lock:
                self._upload_state = None
        if ack not in (0, 1, 3):
            raise PrinterError(f"Printer rejected upload termination (Ack {ack})", "upload_abort_failed", {"ack": ack})
        return ack

    def _post_chunk(self, fields: Mapping[str, str], chunk: bytes, filename: str) -> Mapping[str, Any]:
        if self._http_post is not None:
            return self._http_post(fields, chunk, filename)
        assert self.target is not None
        boundary = "----voxelmill-" + uuid.uuid4().hex
        body = bytearray()
        for key, value in fields.items():
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"File\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode())
        body.extend(chunk)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        req = urllib_request.Request(
            f"http://{self.target.host}:{WEBSOCKET_PORT}{UPLOAD_PATH}", data=bytes(body), method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))})
        try:
            with urllib_request.urlopen(req, timeout=self.request_timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise PrinterTimeout("Timed out uploading file chunk") from exc
        except OSError as exc:
            raise PrinterError(f"File upload failed: {exc}", "upload_transport") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PrinterProtocolError("Upload response was not JSON") from exc
        if not isinstance(parsed, Mapping):
            raise PrinterProtocolError("Upload response was not an object")
        return parsed

    def _start_matches(self, remote_id: str, previous_info: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
        body = current.get("Status", {})
        if not isinstance(body, Mapping) or MACHINE_PRINTING not in self._machine_statuses(current):
            return False
        info = body.get("PrintInfo", {})
        if not isinstance(info, Mapping) or _number(info.get("Status", -1), "PrintInfo.Status") not in {PRINT_HOMING, PRINT_DROPPING, PRINT_EXPOSURING, PRINT_LIFTING}:
            return False
        # If firmware supplies Filename, it is the strongest identity check.
        expected_name = Path(remote_id).name
        filename = info.get("Filename")
        if filename is not None and str(filename) and Path(str(filename)).name != expected_name:
            return False
        old_task, new_task = previous_info.get("TaskId"), info.get("TaskId")
        if old_task and new_task:
            return str(old_task) != str(new_task)
        if filename is not None and str(filename):
            return Path(str(filename)).name == expected_name
        # A device that omits both identity fields cannot safely confirm a
        # newly accepted start; keep it ambiguous rather than guessing.
        return bool(new_task)

    def _reconcile_uncertain_start(self) -> bool | None:
        pending = self._uncertain_start
        if pending is None:
            return None
        try:
            current = self.status(refresh=True)
        except (PrinterTimeout, PrinterNotConnected, PrinterDisconnected) as exc:
            raise PrinterError("Previous start outcome is still uncertain; status refresh failed",
                               "uncertain_start", {"remote_id": pending["remote_id"], "cause": str(exc)}) from exc
        previous_info = pending.get("previous_info", {})
        if self._start_matches(pending["remote_id"], previous_info, current):
            self._uncertain_start = None
            return True
        statuses = self._machine_statuses(current)
        if statuses & {MACHINE_PRINTING, MACHINE_FILE_TRANSFERRING}:
            raise PrinterBusy("Previous start outcome is unresolved", remote_id=pending["remote_id"], statuses=sorted(statuses))
        # Idle plus a non-live sticky sub-status is a definitive reconciliation:
        # the command did not leave a job running and retry is safe.
        if statuses == {MACHINE_IDLE} or not statuses:
            self._uncertain_start = None
            return False
        raise PrinterError("Previous start outcome is unresolved", "uncertain_start", {"remote_id": pending["remote_id"], "statuses": sorted(statuses)})

    def start(self, remote_id: str, *, wait: bool = True, start_layer: int = 0) -> None:
        if not isinstance(remote_id, str) or not remote_id:
            raise ValueError("remote_id must be a nonempty filename")
        if start_layer < 0:
            raise ValueError("start_layer must be nonnegative")
        self._require_capability("PRINT_CONTROL")
        reconciled = self._reconcile_uncertain_start()
        if reconciled:
            return
        current = self.status(refresh=True)
        current_statuses = self._machine_statuses(current)
        if current_statuses & {MACHINE_PRINTING, MACHINE_FILE_TRANSFERRING}:
            raise PrinterBusy("Cannot start while printer is busy", statuses=sorted(current_statuses))
        previous = current
        previous_info = previous.get("Status", {}).get("PrintInfo", {}) if isinstance(previous.get("Status"), Mapping) else {}
        self._uncertain_start = {"remote_id": remote_id, "previous_info": dict(previous_info) if isinstance(previous_info, Mapping) else {}, "issued_at": self._clock()}
        try:
            result = self._command(128, {"Filename": remote_id, "StartLayer": start_layer})
        except (PrinterTimeout, PrinterNotConnected, PrinterDisconnected) as exc:
            raise PrinterTimeout("Start outcome is uncertain; refresh status before retrying", remote_id=remote_id, uncertain=True) from exc
        try:
            self._ack_or_raise(result, cmd=128)
        except PrinterError:
            self._uncertain_start = None
            raise
        if not wait:
            return
        generation = self._status_generation
        deadline = self._clock() + self.start_timeout
        while not (self._status_generation > generation and self._status is not None and self._start_matches(remote_id, previous_info, self._status)):
            if self._clock() >= deadline:
                # Force one refresh once before declaring an accepted-but-never-
                # started command ambiguous.
                try:
                    self._command(0, {}, timeout=min(self.request_timeout, max(0.01, deadline - self._clock())))
                except (PrinterTimeout, PrinterNotConnected, PrinterDisconnected):
                    raise PrinterTimeout("Start was acknowledged but could not be reconciled", remote_id=remote_id, uncertain=True)
                if not (self._status is not None and self._start_matches(remote_id, previous_info, self._status)):
                    raise PrinterTimeout("Start was acknowledged but printing did not begin", remote_id=remote_id, uncertain=True)
                self._uncertain_start = None
                return
            time.sleep(0.02)
        self._uncertain_start = None

    def _control(self, cmd: int) -> None:
        self._require_capability("PRINT_CONTROL")
        result = self._command(cmd, {})
        self._ack_or_raise(result, cmd=cmd)

    def pause(self) -> None:
        self._control(129)

    def resume(self) -> None:
        self._control(131)

    def cancel(self) -> None:
        self._control(130)

    def camera(self, enable: bool = True) -> str | None:
        self._require_capability("VIDEO_STREAM")
        if enable:
            result = self._command(386, {"Enable": 1})
            self._ack_or_raise(result, cmd=386)
            data = result["data"].get("Data", {})
            return data.get("VideoUrl") if isinstance(data, Mapping) else None
        result = self._command(386, {"Enable": 0})
        self._ack_or_raise(result, cmd=386)
        return None

    def poll_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def reconcile_start(self) -> bool | None:
        """Reconcile an accepted-but-unconfirmed start without issuing Cmd 128.

        Returns ``True`` when the requested task is live, ``False`` when the
        printer is idle and a retry is safe, and ``None`` when no start is
        currently pending. Raises while the printer's state remains ambiguous.
        """
        return self._reconcile_uncertain_start()


def create_loopback_adapter(**kwargs: Any) -> tuple[SDCPPrinterAdapter, LoopbackPrinterSimulator]:
    """Create and connect a no-network demo adapter and its simulator."""
    simulator = LoopbackPrinterSimulator()
    adapter = simulator.make_adapter(**kwargs)
    return adapter, simulator


# Name used by integrations that prefer the protocol acronym in the class name.
PrinterAdapter = SDCPPrinterAdapter
