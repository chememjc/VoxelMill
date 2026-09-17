"""Bounded execution context for synchronous CLI workers.

Linux affinity limits CPU execution even for Manifold wheels bundling TBB.
The address-space soft ceiling bounds both resident allocations and mappings.
GUI jobs must use isolated processes before reusing this context.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import resource
from .contracts import VoxelMillError

@contextmanager
def execution_limits(budget, *, hard_memory=True):
    from threadpoolctl import threadpool_limits
    affinity={}
    old_limit=resource.getrlimit(resource.RLIMIT_AS)
    native_limit=None
    try:
        if hasattr(os,'sched_getaffinity'):
            cpus=sorted(os.sched_getaffinity(0))[:budget.workers]
            for entry in Path('/proc/self/task').iterdir():
                try:
                    tid=int(entry.name)
                    affinity[tid]=os.sched_getaffinity(tid)
                    os.sched_setaffinity(tid,cpus)
                except ProcessLookupError:
                    continue
        if hard_memory:
            limit=int(budget.memory_gib*1024**3)
            if old_limit[1]!=resource.RLIM_INFINITY:
                limit=min(limit,old_limit[1])
            resource.setrlimit(resource.RLIMIT_AS,(limit,old_limit[1]))
        from . import _native
        if hasattr(_native,'WorkerLimit'):
            native_limit=_native.WorkerLimit(budget.workers)
        with threadpool_limits(limits=budget.workers):
            yield
    except MemoryError as exc:
        raise VoxelMillError('memory_budget','Operation exhausted the configured address-space memory ceiling') from exc
    finally:
        native_limit=None
        resource.setrlimit(resource.RLIMIT_AS,old_limit)
        for tid,cpus in affinity.items():
            try:os.sched_setaffinity(tid,cpus)
            except ProcessLookupError:pass
