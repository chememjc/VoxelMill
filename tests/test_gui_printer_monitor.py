"""Offline checks for the standalone printer monitor."""
import time
import threading

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets
from voxelmill.contracts import CancellationToken
from voxelmill.gui.printer_monitor import PrinterMonitorDialog


def wait_until(application, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        application.processEvents()
        time.sleep(.01)
    assert predicate()


def test_monitor_demo_is_connected_without_a_project_or_network(application):
    dialog = PrinterMonitorDialog(demo=True)
    try:
        assert dialog.adapter.connected
        assert dialog.host.text() == "loopback"
        wait_until(application, lambda: "telemetry" in dialog.summary.text().lower())
        dialog.load_history()
        wait_until(application, lambda: not dialog._workers)
        assert dialog.history.topLevelItemCount() == 0
    finally:
        dialog.close()


def test_worker_keeps_event_loop_live_and_periodic_refresh_skips_overlap(application):
    dialog = PrinterMonitorDialog()
    completed = []
    try:
        assert dialog._run(lambda: (time.sleep(.15), "done")[1], completed.append)
        assert not dialog._run(lambda: "overlap", completed.append)
        ticks = []
        QtWidgets.QApplication.instance().aboutToQuit.connect(lambda: None)
        from PySide6 import QtCore
        QtCore.QTimer.singleShot(20, lambda: ticks.append(True))
        wait_until(application, lambda: completed)
        assert ticks and completed == ["done"]
    finally:
        dialog.close()


def test_worker_result_callback_runs_on_gui_thread(application):
    dialog = PrinterMonitorDialog()
    callback_threads = []
    try:
        gui_thread = threading.get_ident()
        dialog._run(threading.get_ident, lambda _worker_thread: callback_threads.append(threading.get_ident()))
        wait_until(application, lambda: callback_threads)
        assert callback_threads == [gui_thread]
    finally:
        dialog.close()


def test_closed_dialog_cleans_up_resource_returned_by_inflight_connect(application):
    dialog = PrinterMonitorDialog()
    released = []
    class Resource:
        def close(self): released.append(True)
    dialog._run(lambda: (time.sleep(.1), Resource())[1], stale=lambda value: value.close())
    dialog.close()
    wait_until(application, lambda: released)


def test_close_cancels_active_tokens_and_status_only_close_does_not_disable_camera(application):
    dialog = PrinterMonitorDialog(demo=True)
    wait_until(application, lambda: not dialog._workers)
    adapter = dialog.adapter
    camera_calls = []
    adapter.camera = lambda enable=True: camera_calls.append(enable)
    token = CancellationToken()
    dialog._tokens.add(token)
    dialog.close()
    with pytest.raises(Exception) as caught:
        token.check()
    assert getattr(caught.value, "code", None) == "canceled"
    assert camera_calls == []
