"""Focused headless tests for the GUI job request lifecycle."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')

from PySide6 import QtCore, QtWidgets  # noqa: E402

from voxelmill.gui.jobs import JobRunner  # noqa: E402


@pytest.fixture(scope='session')
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def process_until(application, runner, predicate, timeout=10000, join=False):
    deadline = QtCore.QElapsedTimer()
    deadline.start()
    while deadline.elapsed() < timeout:
        application.processEvents()
        if predicate():
            break
        runner.wait(20)
    else:
        application.processEvents()
        assert predicate()
    if join:
        assert runner.wait(timeout)
        application.processEvents()
        assert predicate()


def test_same_name_replacement_forwards_only_latest_result_and_progress(application):
    runner = JobRunner(max_threads=2)
    started = QtCore.QSemaphore(0)
    new_started = QtCore.QSemaphore(0)
    release_old = QtCore.QSemaphore(0)
    release_new = QtCore.QSemaphore(0)
    completed, progress = [], []
    worker_results = []
    runner.completed.connect(completed.append)
    runner.progress.connect(lambda label, done, total: progress.append(label))
    runner._signals.finished.connect(worker_results.append)

    def old_job(token, report):
        started.release()
        report('old', 1, 1)
        release_old.acquire()
        return 'old'

    def new_job(token, report):
        new_started.release()
        report('new', 1, 1)
        release_new.acquire()
        return 'new'

    runner.submit('same', old_job)
    assert started.tryAcquire(1, 10000)

    new_token = runner.submit('same', new_job)
    assert new_started.tryAcquire(1, 10000)
    release_old.release()
    process_until(application, runner,
                  lambda: any(result.value == 'old' for result in worker_results))
    assert runner.tokens.get('same') is new_token
    assert completed == []

    release_new.release()
    process_until(application, runner, lambda: len(completed) == 1, join=True)

    assert [result.value for result in completed] == ['new']
    assert progress == ['same: new']


def test_canceling_latest_job_still_forwards_canceled_result(application):
    runner = JobRunner(max_threads=1)
    started = QtCore.QSemaphore(0)
    release = QtCore.QSemaphore(0)
    completed = []
    runner.completed.connect(completed.append)

    def cancellable(token, report):
        started.release()
        release.acquire()
        token.check()

    runner.submit('cancel', cancellable)
    assert started.tryAcquire(1, 10000)
    runner.cancel('cancel')
    release.release()
    process_until(application, runner, lambda: len(completed) == 1, join=True)

    assert len(completed) == 1
    assert completed[0].name == 'cancel'
    assert completed[0].canceled
