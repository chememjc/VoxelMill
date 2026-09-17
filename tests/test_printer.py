"""Offline SDCP tests; none of these tests touch a network interface."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from voxelmill.printer import (
    create_loopback_adapter,
    Discovery,
    LoopbackTransport,
    PrinterDisconnected,
    PrinterNotConnected,
    PrinterBusy,
    PrinterError,
    PrinterTimeout,
    SDCPPrinterAdapter,
    discover,
)
from voxelmill.contracts import CancellationToken


DISCOVERY = {
    "Id": "brand-id",
    "Data": {
        "Name": "Offline Mars",
        "MachineName": "Mars 5 Ultra",
        "BrandName": "ELEGOO",
        "MainboardIP": "127.0.0.1",
        "MainboardID": "000000000001d354",
        "ProtocolVersion": "V3.0.0",
        "FirmwareVersion": "V1.0.0",
    },
}


def _status(machine=0, print_status=0, task=""):
    return {
        "Status": {
            "CurrentStatus": machine,
            "PreviousStatus": 0,
            "PrintInfo": {"Status": print_status, "CurrentLayer": 0,
                           "TotalLayer": 10, "TaskId": task, "ErrorNumber": 0},
        },
        "MainboardID": DISCOVERY["Data"]["MainboardID"],
        "Topic": "sdcp/status/" + DISCOVERY["Data"]["MainboardID"],
    }


def _attrs(transport):
    transport.inject({
        "Attributes": {"Name": "Offline Mars", "Capabilities": ["FILE_TRANSFER", "PRINT_CONTROL", "VIDEO_STREAM"], "CameraStatus": 1},
        "MainboardID": DISCOVERY["Data"]["MainboardID"],
        "Topic": "sdcp/attributes/" + DISCOVERY["Data"]["MainboardID"],
    })


def simulator():
    state = {"status": _status(), "task": ""}

    def on_send(raw, transport):
        request = json.loads(raw)
        data = request["Data"]
        cmd = data["Cmd"]
        payload = data["Data"]
        response_data = {"Ack": 0}
        if cmd == 386:
            response_data["VideoUrl"] = "rtsp://127.0.0.1/offline"
        elif cmd == 320:
            response_data["HistoryData"] = ["task-old"]
        elif cmd == 321:
            response_data["HistoryDetailList"] = [{
                "TaskId": "task-old", "TaskName": "part.goo", "TaskStatus": 1,
                "TimeLapseVideoStatus": 1, "TimeLapseVideoUrl": "/video/task-old.mp4",
            }]
        transport.inject({"Data": {"Cmd": cmd, "Data": response_data,
                                    "RequestID": data["RequestID"],
                                    "MainboardID": data["MainboardID"]},
                          "Topic": "sdcp/response/" + data["MainboardID"]})
        if cmd == 1:
            _attrs(transport)
        elif cmd == 0:
            transport.inject(state["status"])
        elif cmd == 128:
            state["task"] = "task-new"
            state["status"] = _status(1, 1, state["task"])
            transport.inject(state["status"])

    transport = LoopbackTransport(on_send)
    return transport, state


def adapter_with_simulator():
    transport, state = simulator()
    adapter = SDCPPrinterAdapter(DISCOVERY, transport_factory=lambda _url, _timeout: transport,
                                 request_timeout=0.5, start_timeout=0.5)
    adapter.connect()
    return adapter, transport, state


def test_discovery_is_validated_and_deduplicated_without_real_socket():
    class FakeSocket:
        def __init__(self, *_):
            self.sent = []
            self.packets = [
                (json.dumps(DISCOVERY).encode(), ("127.0.0.1", 3030)),
                (json.dumps(DISCOVERY).encode(), ("127.0.0.1", 3030)),
                (b"not-json", ("127.0.0.1", 3030)),
            ]
        def setsockopt(self, *_): pass
        def settimeout(self, value): self.timeout = value
        def sendto(self, payload, addr): self.sent.append((payload, addr))
        def recvfrom(self, _):
            if self.packets:
                return self.packets.pop(0)
            raise TimeoutError
        def close(self): pass

    sock = FakeSocket()
    result = discover(timeout=0.1, socket_factory=lambda *_: sock)
    assert len(result) == 1
    assert result[0].mainboard_id == DISCOVERY["Data"]["MainboardID"]
    assert sock.sent == [(b"M99999", ("255.255.255.255", 3000))]


def test_connect_has_no_discovery_and_refreshes_initial_state():
    adapter, transport, _ = adapter_with_simulator()
    assert adapter.connected
    assert adapter.attributes["Attributes"]["Name"] == "Offline Mars"
    assert any(json.loads(x)["Data"]["Cmd"] == 1 for x in transport.sent)
    assert any(json.loads(x)["Data"]["Cmd"] == 0 for x in transport.sent)
    adapter.close()


def test_control_start_reconciles_ack_with_live_status_and_controls():
    adapter, _, state = adapter_with_simulator()
    adapter.start("print.goo")
    assert state["status"]["Status"]["CurrentStatus"] == 1
    adapter.pause()
    adapter.resume()
    adapter.cancel()
    adapter.close()


def test_busy_ack_is_distinct_from_other_command_failures():
    def on_send(raw, transport):
        req = json.loads(raw)
        transport.inject({"Data": {"Cmd": req["Data"]["Cmd"], "Data": {"Ack": 1},
                                    "RequestID": req["Data"]["RequestID"]},
                          "Topic": "sdcp/response/" + DISCOVERY["Data"]["MainboardID"]})
        if req["Data"]["Cmd"] == 0:
            transport.inject(_status())
    transport = LoopbackTransport(on_send)
    adapter = SDCPPrinterAdapter(DISCOVERY, transport_factory=lambda *_: transport, request_timeout=0.2)
    adapter._transport = transport
    adapter._attributes = {"Attributes": {"Capabilities": ["PRINT_CONTROL"]}}
    adapter._stop_reader.clear()
    import threading
    adapter._reader = threading.Thread(target=adapter._read_loop, daemon=True)
    adapter._reader.start()
    with pytest.raises(PrinterBusy):
        adapter.start("print.goo", wait=False)
    adapter.close()


def test_upload_is_sequential_cancelable_and_reports_metadata(tmp_path: Path):
    adapter, _, _ = adapter_with_simulator()
    source = tmp_path / "part.goo"
    source.write_bytes(b"a" * 17)
    calls = []
    def post(fields, chunk, filename):
        calls.append((dict(fields), chunk, filename))
        return {"code": "000000", "success": True}
    adapter._http_post = post
    progress = []
    assert adapter.upload(source, progress=lambda *args: progress.append(args), chunk_size=8) == "part.goo"
    assert [len(c[1]) for c in calls] == [8, 8, 1]
    assert [c[0]["Offset"] for c in calls] == ["0", "8", "16"]
    assert progress[-1][1:] == (17, 17)
    adapter.close()


def test_camera_returns_printer_supplied_rtsp_url():
    adapter, _, _ = adapter_with_simulator()
    assert adapter.camera() == "rtsp://127.0.0.1/offline"
    assert adapter.camera(False) is None
    adapter.close()


def test_history_uses_documented_commands_and_preserves_timelapse_url():
    adapter, transport, _ = adapter_with_simulator()
    history = adapter.history()
    assert history == [{"TaskId": "task-old", "TaskName": "part.goo", "TaskStatus": 1,
                        "TimeLapseVideoStatus": 1,
                        "TimeLapseVideoUrl": "/video/task-old.mp4"}]
    commands = [json.loads(raw)["Data"] for raw in transport.sent if raw != "ping"]
    assert next(item for item in commands if item["Cmd"] == 321)["Data"] == {"Id": ["task-old"]}
    adapter.close()


def test_information_labels_release_film_as_telemetry(monkeypatch):
    adapter, _, state = adapter_with_simulator()
    state["status"]["Status"]["ReleaseFilm"] = 64168
    adapter._attributes["Attributes"].update({
        "ReleaseFilmMax": 60000, "DevicesStatus": {"RelaseFilmState": 0}})
    monkeypatch.setattr(adapter, "request_attributes", lambda: adapter.attributes)
    info = adapter.information()
    film = info["release_film"]
    assert film["uses"] == 64168 and film["rated_max"] == 60000
    assert film["remaining_uses"] == 0 and film["percent_of_rated_max"] > 100
    assert film["device_state"] == 0 and film["assessment"] == "telemetry_only"
    adapter.close()


def test_timelapse_download_resolves_relative_url_and_replaces_atomically(tmp_path, monkeypatch):
    adapter, _, _ = adapter_with_simulator()
    opened = []
    class Response:
        headers = {"Content-Length": "5"}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _size):
            value, self.payload = getattr(self, "payload", b"video"), b""
            return value
    monkeypatch.setattr("voxelmill.printer.urllib_request.urlopen",
                        lambda url, timeout: opened.append((url, timeout)) or Response())
    destination = tmp_path / "movie.mp4"
    result = adapter.download_timelapse({"TaskId": "x", "TimeLapseVideoStatus": 1,
                                         "TimeLapseVideoUrl": "/video/x.mp4"}, destination)
    assert result == destination and destination.read_bytes() == b"video"
    assert opened[0][0] == "http://127.0.0.1:3030/video/x.mp4"
    assert not list(tmp_path.glob("*.part"))
    adapter.close()


def test_timelapse_download_refuses_unavailable_video(tmp_path):
    adapter, _, _ = adapter_with_simulator()
    with pytest.raises(PrinterError) as caught:
        adapter.download_timelapse({"TaskId": "x", "TimeLapseVideoStatus": 3},
                                    tmp_path / "x.mp4")
    assert caught.value.code == "timelapse_unavailable"
    adapter.close()


def test_history_rejects_malformed_protocol_arrays():
    def on_send(raw, transport):
        req = json.loads(raw)
        cmd = req["Data"]["Cmd"]
        payload = {"Ack": 0}
        if cmd == 320:
            payload["HistoryData"] = {"not": "an array"}
        transport.inject({"Data": {"Cmd": cmd, "Data": payload,
                                    "RequestID": req["Data"]["RequestID"]},
                          "Topic": "sdcp/response/" + DISCOVERY["Data"]["MainboardID"]})
    transport = LoopbackTransport(on_send)
    adapter = SDCPPrinterAdapter(DISCOVERY, transport_factory=lambda *_: transport,
                                 request_timeout=.2)
    adapter._transport = transport
    import threading
    adapter._stop_reader.clear()
    adapter._reader = threading.Thread(target=adapter._read_loop, daemon=True)
    adapter._reader.start()
    with pytest.raises(Exception, match="HistoryData is not an array"):
        adapter.history_ids()
    adapter.close()


def test_history_batches_firmware_requests_at_ten_and_restores_id_order():
    ids = [f"task-{index:02}" for index in range(23)]
    requested = []
    def on_send(raw, transport):
        req = json.loads(raw)
        cmd = req["Data"]["Cmd"]
        data = {"Ack": 0}
        if cmd == 320:
            data["HistoryData"] = ids
        elif cmd == 321:
            batch = req["Data"]["Data"]["Id"]
            requested.append(batch)
            data["HistoryDetailList"] = [{"TaskId": item} for item in reversed(batch)]
        transport.inject({"Data": {"Cmd": cmd, "Data": data,
                                    "RequestID": req["Data"]["RequestID"]},
                          "Topic": "sdcp/response/" + DISCOVERY["Data"]["MainboardID"]})
    transport = LoopbackTransport(on_send)
    adapter = SDCPPrinterAdapter(DISCOVERY, transport_factory=lambda *_: transport,
                                 request_timeout=.2)
    adapter._transport = transport
    import threading
    adapter._stop_reader.clear()
    adapter._reader = threading.Thread(target=adapter._read_loop, daemon=True)
    adapter._reader.start()
    assert [task["TaskId"] for task in adapter.history()] == ids
    assert [len(batch) for batch in requested] == [10, 10, 3]
    adapter.close()


def test_download_does_not_replace_destination_when_content_length_is_truncated(tmp_path, monkeypatch):
    adapter, _, _ = adapter_with_simulator()
    class Response:
        headers = {"Content-Length": "100"}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _size):
            value, self.payload = getattr(self, "payload", b"short"), b""
            return value
    monkeypatch.setattr("voxelmill.printer.urllib_request.urlopen", lambda *_args, **_kwargs: Response())
    destination = tmp_path / "existing.mp4"
    destination.write_bytes(b"keep")
    with pytest.raises(PrinterError) as caught:
        adapter.download("/video.mp4", destination)
    assert caught.value.code == "download_incomplete"
    assert destination.read_bytes() == b"keep"
    assert not list(tmp_path.glob(".*.part"))
    adapter.close()


def test_start_timeout_reconciles_once_then_raises():
    transport = LoopbackTransport()
    adapter = SDCPPrinterAdapter(DISCOVERY, transport_factory=lambda *_: transport,
                                 request_timeout=0.05, start_timeout=0.06)
    # Seed a connected reader and respond only with command acks; no live status.
    def responder(raw, loop):
        req = json.loads(raw)
        loop.inject({"Data": {"Cmd": req["Data"]["Cmd"], "Data": {"Ack": 0}, "RequestID": req["Data"]["RequestID"]},
                     "Topic": "sdcp/response/" + DISCOVERY["Data"]["MainboardID"]})
    transport.on_send = responder
    adapter._transport = transport
    import threading
    adapter._stop_reader.clear()
    adapter._reader = threading.Thread(target=adapter._read_loop, daemon=True)
    adapter._reader.start()
    with pytest.raises(PrinterTimeout):
        adapter.start("print.goo")
    adapter.close()


def test_loopback_demo_is_public_and_starts_without_network():
    adapter, simulator = create_loopback_adapter(request_timeout=0.2, start_timeout=0.2)
    assert adapter.connected and simulator.discovery.host == "loopback"
    adapter.start("demo.goo")
    assert simulator.filename == "demo.goo"
    adapter.close()


def test_websocket_timeout_exception_is_idle_but_disconnect_unblocks_waiters():
    class WebSocketTimeoutException(Exception):
        pass
    class Transport:
        def __init__(self):
            self.calls = 0
        def send(self, value):
            pass
        def recv(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise WebSocketTimeoutException("idle")
            raise OSError("closed")
        def close(self):
            pass
    transport = Transport()
    adapter = SDCPPrinterAdapter(DISCOVERY, transport_factory=lambda *_: transport, request_timeout=0.2)
    adapter._transport = transport
    adapter._stop_reader.clear()
    import threading
    adapter._reader = threading.Thread(target=adapter._read_loop, daemon=True)
    adapter._reader.start()
    adapter._reader.join(1)
    assert not adapter.connected
    assert adapter.poll_event(0.1)["kind"] == "disconnect"
    adapter.close()


def test_status_refresh_requires_a_new_push():
    adapter, _, state = adapter_with_simulator()
    before = adapter.status(refresh=False)["Status"]["CurrentStatus"]
    state["status"] = _status(1, 1, "fresh")
    after = adapter.status(refresh=True)["Status"]["CurrentStatus"]
    assert before == 0 and after == 1
    adapter.close()


def test_upload_checks_capability_busy_state_strict_success_and_cancellation(tmp_path: Path):
    adapter, _, state = adapter_with_simulator()
    source = tmp_path / "part.goo"
    source.write_bytes(b"data")
    state["status"] = _status(1, 1, "busy")
    adapter._http_post = lambda *_: {"code": "000000", "success": True}
    with pytest.raises(PrinterBusy):
        adapter.upload(source)
    state["status"] = _status()
    adapter._http_post = lambda *_: {"code": "000000", "success": "false"}
    with pytest.raises(PrinterError, match="rejected"):
        adapter.upload(source)
    token = CancellationToken()
    token.cancel()
    with pytest.raises(Exception) as exc:
        adapter.upload(source, cancel=token)
    assert getattr(exc.value, "code", None) == "canceled"
    adapter.close()


def test_upload_rejects_unsafe_multipart_filename(tmp_path: Path):
    adapter, _, _ = adapter_with_simulator()
    source = tmp_path / 'bad"name.goo'
    source.write_bytes(b"x")
    with pytest.raises(PrinterError, match="unsafe"):
        adapter.upload(source)
    adapter.close()


def test_upload_detects_same_size_same_mtime_source_replacement(tmp_path: Path):
    adapter, _, _ = adapter_with_simulator()
    source = tmp_path / "part.goo"
    source.write_bytes(b"original")
    original_mtime = source.stat().st_mtime_ns
    def post(fields, chunk, filename):
        source.write_bytes(b"replaced")
        os.utime(source, ns=(original_mtime, original_mtime))
        return {"code": "000000", "success": True}
    adapter._http_post = post
    with pytest.raises(PrinterError, match="changed"):
        adapter.upload(source)
    adapter.close()


def test_storage_reports_capacity_from_the_root_listing():
    """Cmd 258 on "/" returns storage roots; only those carry capacity."""
    adapter, simulator = create_loopback_adapter()
    simulator.files.update({'/local//a.goo', '/local//b.ctb'})
    roots = adapter.storage()
    assert len(roots) == 1
    root = roots[0]
    assert root['name'] == '/local'
    assert root['total_bytes'] == simulator.storage_total
    assert root['used_bytes'] == simulator.storage_used
    assert root['free_bytes'] == simulator.storage_total - simulator.storage_used
    assert root['percent_used'] == pytest.approx(63.8231, abs=1e-3)
    # A directory listing is a different shape and reports no capacity.
    listing = adapter.list_files('/local/')
    assert [item['name'] for item in listing] == ['/local//a.goo', '/local//b.ctb']
    assert all('totalSize' not in item for item in listing)


def test_storage_keeps_unknown_capacity_out_of_the_numbers():
    """Missing or unparseable sizes stay None; zero free is a different claim."""
    adapter, simulator = create_loopback_adapter()
    simulator.storage_total, simulator.storage_used = 'not-a-number', None
    root = adapter.storage()[0]
    assert root['total_bytes'] is None and root['used_bytes'] is None
    assert root['free_bytes'] is None and root['percent_used'] is None


def test_storage_does_not_divide_by_a_zero_total():
    adapter, simulator = create_loopback_adapter()
    simulator.storage_total, simulator.storage_used = 0, 0
    root = adapter.storage()[0]
    assert root['total_bytes'] == 0 and root['used_bytes'] == 0
    assert root['free_bytes'] is None and root['percent_used'] is None
