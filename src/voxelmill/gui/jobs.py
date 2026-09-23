"""Background work for the editor.

Every heavy operation runs on a thread pool, never on the UI thread. Each job
carries a generation number: when the document changes, the generation is bumped
and any result that arrives from an older generation is dropped rather than
painted, so a slow placement can never overwrite a newer one. Cancellation is
cooperative through the same :class:`CancellationToken` the CLI uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import traceback
from typing import Any, Callable

from PySide6 import QtCore

from ..contracts import Canceled, CancellationToken, VoxelMillError


@dataclass
class JobResult:
    name: str
    generation: int
    value: Any = None
    error: dict | None = None
    canceled: bool = False
    traceback: str = ''
    request_id: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None and not self.canceled


class _Signals(QtCore.QObject):
    progress = QtCore.Signal(str, str, int, int, int, int)   # name, label, done, total, generation, request
    finished = QtCore.Signal(object)               # JobResult


class _Runnable(QtCore.QRunnable):
    def __init__(self, name, generation, request_id, function, token, signals):
        super().__init__()
        self.name, self.generation, self.request_id = name, generation, request_id
        self.function, self.token, self.signals = function, token, signals

    def run(self):
        def progress(stage, done, total):
            self.token.check()
            self.signals.progress.emit(self.name, f'{self.name}: {stage}', int(done), int(total),
                                       self.generation, self.request_id)
        try:
            value = self.function(self.token, progress)
        except Canceled:
            self.signals.finished.emit(JobResult(self.name, self.generation, canceled=True,
                                                 request_id=self.request_id))
        except VoxelMillError as error:
            self.signals.finished.emit(JobResult(self.name, self.generation, error=error.to_dict(),
                                                 traceback=traceback.format_exc(),
                                                 request_id=self.request_id))
        except Exception as error:  # a crash in a worker must not kill the window
            self.signals.finished.emit(JobResult(
                self.name, self.generation,
                error={'code': 'internal_error', 'message': str(error), 'details': {}},
                traceback=traceback.format_exc(), request_id=self.request_id))
        else:
            self.signals.finished.emit(JobResult(self.name, self.generation, value=value,
                                                 request_id=self.request_id))


#: Jobs of different names overlap (a layer scrub during a routing run), but
#: each job already runs its own worker pool, so a few are enough.
MAX_EDITOR_JOBS = 4


def editor_job_threads(workers):
    """Concurrent editor jobs for a ``resources.workers`` setting.

    ``0`` means derive, exactly as the CLI does; reading it literally would
    serialize every background job behind the one before it.
    """
    workers = int(workers)
    if workers <= 0:
        from ..topology import default_workers
        workers = max(2, default_workers())
    return max(1, min(MAX_EDITOR_JOBS, workers))


class JobRunner(QtCore.QObject):
    """Submits jobs and forwards only results from the current generation."""

    progress = QtCore.Signal(str, int, int)
    completed = QtCore.Signal(object)
    stale = QtCore.Signal(object)

    def __init__(self, parent=None, max_threads=2):
        super().__init__(parent)
        self.pool = QtCore.QThreadPool(self)
        self.pool.setMaxThreadCount(max(1, int(max_threads)))
        self.generation = 0
        self.tokens: dict[str, CancellationToken] = {}
        self._requests: dict[str, int] = {}
        self._next_request_id = 0
        self._signals = _Signals(self)
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)

    def invalidate(self):
        """Bump the generation and cancel everything in flight."""
        self.generation += 1
        self.cancel_all()
        return self.generation

    def cancel(self, name):
        token = self.tokens.pop(name, None)
        if token is not None:
            token.cancel()

    def cancel_all(self):
        for token in list(self.tokens.values()):
            token.cancel()
        self.tokens.clear()

    def submit(self, name: str, function: Callable):
        self.cancel(name)
        token = CancellationToken()
        self._next_request_id += 1
        request_id = self._next_request_id
        self._requests[name] = request_id
        self.tokens[name] = token
        self.pool.start(_Runnable(name, self.generation, request_id, function, token, self._signals))
        return token

    def wait(self, milliseconds=120000):
        return self.pool.waitForDone(milliseconds)

    @property
    def busy(self):
        return self.pool.activeThreadCount() > 0

    def _on_progress(self, name, label, done, total, generation, request_id):
        if (generation == self.generation and
                self._requests.get(name) == request_id):
            self.progress.emit(label, done, total)

    def _on_finished(self, result: JobResult):
        # A replacement with the same name may finish before the canceled
        # request.  Only the current request owns the token and result slot.
        if self._requests.get(result.name) != result.request_id:
            return
        self._requests.pop(result.name, None)
        self.tokens.pop(result.name, None)
        if result.generation != self.generation:
            self.stale.emit(result)
            return
        self.completed.emit(result)
