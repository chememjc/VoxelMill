"""Bounded execution context for synchronous CLI workers.

Linux affinity limits CPU execution even for Manifold wheels bundling TBB.
The address-space soft ceiling bounds both resident allocations and mappings.
GUI jobs must use isolated processes before reusing this context.

Which cpus the mask names matters as much as how many. Taking the lowest
`workers` cpu numbers lands on the SMT siblings of one physical core wherever
siblings are numbered adjacently, so the default two workers shared a single
core and left the rest of the machine idle. `topology.select_cpus` spreads the
mask across distinct physical cores, fastest class first, instead.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import sys
try:
    import resource
except ImportError:
    resource=None
from .contracts import VoxelMillError
from .topology import select_cpus

@contextmanager
def execution_limits(budget, *, hard_memory=True):
    from threadpoolctl import threadpool_limits
    affinity={}
    rlimit_as=getattr(resource,'RLIMIT_AS',None) if resource is not None else None
    old_limit=None
    native_limit=None
    try:
        if hasattr(os,'sched_setaffinity'):
            tasks=Path('/proc/self/task')
            if tasks.is_dir():
                cpus=list(select_cpus(budget.workers,policy=budget.worker_policy))
                for entry in tasks.iterdir():
                    try:
                        tid=int(entry.name)
                        affinity[tid]=os.sched_getaffinity(tid)
                        os.sched_setaffinity(tid,cpus)
                    except ProcessLookupError:
                        continue
        # Darwin exposes RLIMIT_AS but setrlimit raises
        # "current limit exceeds maximum limit". Address-space caps stay Linux.
        if hard_memory and rlimit_as is not None and sys.platform.startswith('linux'):
            old_limit=resource.getrlimit(rlimit_as)
            limit=int(budget.memory_gib*1024**3)
            if old_limit[1]!=resource.RLIM_INFINITY:
                limit=min(limit,old_limit[1])
            resource.setrlimit(rlimit_as,(limit,old_limit[1]))
        from . import _native
        if hasattr(_native,'WorkerLimit'):
            native_limit=_native.WorkerLimit(budget.workers)
        with threadpool_limits(limits=budget.workers):
            yield
    except MemoryError as exc:
        raise VoxelMillError('memory_budget','Operation exhausted the configured address-space memory ceiling') from exc
    finally:
        native_limit=None
        if old_limit is not None:
            resource.setrlimit(rlimit_as,old_limit)
        for tid,cpus in affinity.items():
            try:os.sched_setaffinity(tid,cpus)
            except ProcessLookupError:pass
