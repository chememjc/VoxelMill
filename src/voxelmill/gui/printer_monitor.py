"""Read-only SDCP printer monitor, usable without an open print project."""
from __future__ import annotations

import json
from pathlib import Path
import threading

from PySide6 import QtCore, QtGui, QtWidgets

from ..printer import Discovery, SDCPPrinterAdapter, create_loopback_adapter, discover
from ..contracts import CancellationToken


class _WorkerSignals(QtCore.QObject):
    result = QtCore.Signal(object)
    error = QtCore.Signal(object)
    finished = QtCore.Signal()


class _Worker(QtCore.QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = _WorkerSignals()

    @QtCore.Slot()
    def run(self):
        try:
            self.signals.result.emit(self.function())
        except Exception as error:
            self.signals.error.emit(error)
        finally:
            self.signals.finished.emit()


class PrinterMonitorDialog(QtWidgets.QDialog):
    """Status, camera and historical-task viewer. It never starts a print."""
    download_progress = QtCore.Signal(int, int)

    def __init__(self, parent=None, *, host="", mainboard_id="", demo=False):
        super().__init__(parent)
        self.setWindowTitle("Printer monitor")
        self.resize(820, 650)
        self.adapter = None
        self._simulator = None
        self._closing = False
        self._camera_enabled = False
        self._workers = set()
        self._tokens = set()
        # The application pool outlives the dialog, so closing during a socket
        # read never makes QWidget destruction wait for the read timeout.
        self._pool = QtCore.QThreadPool.globalInstance()

        self.host = QtWidgets.QLineEdit(host)
        self.host.setPlaceholderText("Printer IP address")
        self.board = QtWidgets.QLineEdit(mainboard_id)
        self.board.setPlaceholderText("Mainboard ID")
        self.discover_button = QtWidgets.QPushButton("Discover")
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.camera_button = QtWidgets.QPushButton("View live camera")
        self.udp_camera_button = QtWidgets.QPushButton("External UDP fallback")
        self.history_button = QtWidgets.QPushButton("Load print history")
        row = QtWidgets.QHBoxLayout()
        for widget in (self.host, self.board, self.discover_button, self.connect_button,
                       self.refresh_button, self.camera_button, self.udp_camera_button,
                       self.history_button):
            row.addWidget(widget)

        self.summary = QtWidgets.QLabel("Not connected")
        self.summary.setWordWrap(True)
        self.telemetry = QtWidgets.QPlainTextEdit()
        self.telemetry.setReadOnly(True)
        self.history = QtWidgets.QTreeWidget()
        self.history.setHeaderLabels(("Task", "Started", "Result", "Layers", "Time-lapse"))
        self.history.setRootIsDecorated(False)
        self.download_button = QtWidgets.QPushButton("Download selected time-lapse…")
        self.download_button.setEnabled(False)

        self.video = None
        self.player = None
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
            self.video = QVideoWidget()
            self.video.setMinimumHeight(240)
            self.player = QMediaPlayer(self)
            self.player.setVideoOutput(self.video)
            self.player.errorOccurred.connect(
                lambda _code, message: self.summary.setText(
                    f"Embedded camera error: {message}. Try External UDP fallback."))
        except ImportError:
            self.video = QtWidgets.QLabel("Qt multimedia playback is unavailable; the RTSP URL will still be shown.")

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.summary)
        layout.addWidget(self.telemetry, 2)
        layout.addWidget(self.video, 2)
        layout.addWidget(self.history, 2)
        layout.addWidget(self.download_button)

        self.discover_button.clicked.connect(self.find_printer)
        self.connect_button.clicked.connect(self.connect_printer)
        self.refresh_button.clicked.connect(self.refresh)
        self.camera_button.clicked.connect(self.open_camera)
        self.udp_camera_button.clicked.connect(self.open_camera_udp)
        self.history_button.clicked.connect(self.load_history)
        self.history.itemSelectionChanged.connect(self._history_selection)
        self.download_button.clicked.connect(self.download_selected)
        self.download_progress.connect(self._show_download_progress)
        for button in (self.refresh_button, self.camera_button, self.udp_camera_button, self.history_button):
            button.setEnabled(False)
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh)
        if demo:
            self.adapter, self._simulator = create_loopback_adapter(request_timeout=.5)
            self._connected()

    def _set_busy(self, busy):
        for button in (self.discover_button, self.connect_button, self.refresh_button,
                       self.camera_button, self.udp_camera_button, self.history_button,
                       self.download_button):
            button.setEnabled(not busy and (button in (self.discover_button, self.connect_button)
                                            or self.adapter is not None))
        if not busy:
            self._history_selection()

    def _run(self, function, done=None, *, modal_errors=True, stale=None, quiet_busy=False):
        """Run one printer operation off the GUI thread; skip overlaps."""
        if self._closing:
            return False
        if self._workers:
            if not quiet_busy:
                self.summary.setText("A printer operation is already running.")
            return False
        worker = _Worker(function)
        self._workers.add(worker)
        self._set_busy(True)
        def result(value):
            self._workers.discard(worker)
            if self._closing:
                if stale:
                    stale(value)
            elif done:
                done(value)
            if not self._closing and not self._workers:
                self._set_busy(False)
        def error(reason):
            self._workers.discard(worker)
            if self._closing:
                return
            if not self._workers:
                self._set_busy(False)
            if modal_errors:
                self._error(reason)
            else:
                self.summary.setText(f"Refresh failed: {reason}")
        def finished():
            self._workers.discard(worker)
        worker.signals.result.connect(result)
        worker.signals.error.connect(error)
        worker.signals.finished.connect(finished)
        self._pool.start(worker)
        return True

    def _error(self, error):
        QtWidgets.QMessageBox.warning(self, "Printer monitor", str(error))

    def find_printer(self):
        self.summary.setText("Discovering printers…")
        self._run(lambda: discover(timeout=1.0), self._found_printers)

    def _found_printers(self, found):
        if not found:
            return self._error("No SDCP printer answered discovery.")
        selected = found[0]
        if len(found) > 1:
            labels = [f"{item.name} — {item.host}" for item in found]
            label, accepted = QtWidgets.QInputDialog.getItem(self, "Choose printer", "Printer", labels, 0, False)
            if not accepted:
                return
            selected = found[labels.index(label)]
        self.host.setText(selected.host)
        self.board.setText(selected.mainboard_id)

    def connect_printer(self):
        target = Discovery("", "Printer", "", "", self.host.text().strip(), self.board.text().strip())
        old = self.adapter
        self.summary.setText(f"Connecting to {target.host}…")
        def connect():
            adapter = SDCPPrinterAdapter(target)
            adapter.connect()
            return adapter
        def connected(adapter):
            if old:
                old.close()
            self.adapter = adapter
            self._connected()
        self._run(connect, connected, stale=lambda adapter: adapter.close())

    def _connected(self):
        for button in (self.refresh_button, self.camera_button, self.udp_camera_button, self.history_button):
            button.setEnabled(True)
        if self.adapter and self.adapter.target:
            self.host.setText(self.adapter.target.host)
            self.board.setText(self.adapter.target.mainboard_id)
        self.summary.setText(f"Connected to {self.host.text()}; refreshing telemetry…")
        self.refresh()
        self.refresh_timer.start()

    def refresh(self):
        if not self.adapter:
            return
        self._run(self.adapter.information, self._show_information,
                  modal_errors=False, quiet_busy=True)

    def _show_information(self, info):
        film = info["release_film"]
        used, maximum = film["uses"], film["rated_max"]
        self.summary.setText(
            f"Connected to {self.host.text()}. Release-film telemetry: {used} uses / rated {maximum}; "
            f"device state {film['device_state']}. These counters do not establish physical FEP condition.")
        self.telemetry.setPlainText(json.dumps(info, indent=2, sort_keys=True))

    def open_camera(self):
        if not self.adapter:
            return
        self.summary.setText("Opening live camera…")
        self._run(self.adapter.camera, self._play_camera)

    def _play_camera(self, url):
        try:
            if not url:
                raise RuntimeError("Printer did not return a camera URL")
            self._camera_enabled = True
            self.summary.setText(f"Live camera: {url}")
            if self.player:
                self.player.setSource(QtCore.QUrl(url))
                self.player.play()
        except Exception as error:
            self._error(error)

    def open_camera_udp(self):
        """Use ffplay's explicit UDP transport for firmware that rejects RTSP/TCP."""
        if not self.adapter:
            return
        self._run(self.adapter.camera, self._play_camera_udp)

    def _play_camera_udp(self, url):
        try:
            if not url:
                raise RuntimeError("Printer did not return a camera URL")
            self._camera_enabled = True
            if not QtCore.QProcess.startDetached(
                    "ffplay", ["-rtsp_transport", "udp", "-window_title", "voxelmill printer camera", url])[0]:
                raise RuntimeError("Could not start ffplay. Install FFmpeg or use the displayed RTSP URL in a UDP-capable player.")
            self.summary.setText(f"Live camera opened over RTSP/UDP: {url}")
        except Exception as error:
            self._error(error)

    def load_history(self):
        if not self.adapter:
            return
        self.summary.setText("Loading print history…")
        self._run(self.adapter.history, self._show_history)

    def _show_history(self, tasks):
        self.history.clear()
        result_names = {0: "Other", 1: "Completed", 2: "Exceptional", 3: "Stopped"}
        video_names = {0: "Not shot", 1: "Available", 2: "Deleted", 3: "Generating", 4: "Failed"}
        for task in tasks:
            begin = task.get("BeginTime")
            try:
                begun = QtCore.QDateTime.fromSecsSinceEpoch(int(begin)).toString(QtCore.Qt.ISODate)
            except (TypeError, ValueError):
                begun = str(begin or "")
            item = QtWidgets.QTreeWidgetItem((
                str(task.get("TaskName") or task.get("TaskId") or ""), begun,
                result_names.get(task.get("TaskStatus"), str(task.get("TaskStatus", ""))),
                str(task.get("AlreadyPrintLayer", "")),
                video_names.get(task.get("TimeLapseVideoStatus"), str(task.get("TimeLapseVideoStatus", ""))),
            ))
            item.setData(0, QtCore.Qt.UserRole, task)
            self.history.addTopLevelItem(item)
        self.history.resizeColumnToContents(0)
        self.summary.setText(f"Loaded {len(tasks)} print-history record(s).")

    def _selected_task(self):
        selected = self.history.selectedItems()
        return selected[0].data(0, QtCore.Qt.UserRole) if selected else None

    def _history_selection(self):
        task = self._selected_task()
        self.download_button.setEnabled(bool(task and task.get("TimeLapseVideoStatus") == 1))

    def download_selected(self):
        task = self._selected_task()
        if not task or not self.adapter:
            return
        suggested = Path(str(task.get("TaskName") or task.get("TaskId") or "timelapse")).stem + ".mp4"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save time-lapse", suggested, "Video (*.mp4);;All files (*)")
        if not path:
            return
        token = CancellationToken()
        self._tokens.add(token)
        self.summary.setText(f"Downloading time-lapse to {path}…")
        def download():
            return self.adapter.download_timelapse(
                task, Path(path), cancel=token,
                progress=lambda _stage, current, total: self.download_progress.emit(current, total))
        def saved(value):
            self._tokens.discard(token)
            self.summary.setText(f"Saved time-lapse to {value}")
        self._run(download, saved)

    def _show_download_progress(self, current, total):
        if self._closing:
            return
        if total:
            self.summary.setText(f"Downloading time-lapse… {current * 100 // total}%")
        else:
            self.summary.setText(f"Downloading time-lapse… {current} bytes")

    def closeEvent(self, event):
        self._closing = True
        self.refresh_timer.stop()
        for token in tuple(self._tokens):
            token.cancel()
        if self.player:
            self.player.stop()
        adapter, self.adapter = self.adapter, None
        if adapter and self._camera_enabled:
            def stop_camera():
                try:
                    adapter.camera(False)
                except Exception:
                    pass
                adapter.close()
            threading.Thread(target=stop_camera, name="sdcp-camera-close", daemon=True).start()
        elif adapter:
            adapter.close()
        super().closeEvent(event)


def run_monitor(*, host="", mainboard_id="", demo=False):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from .window import application_icon
    app.setWindowIcon(application_icon())
    dialog = PrinterMonitorDialog(host=host, mainboard_id=mainboard_id, demo=demo)
    dialog.setWindowIcon(application_icon())
    dialog.show()
    return app.exec()
