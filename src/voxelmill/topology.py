"""CPU topology detection for worker sizing and thread affinity.

Detection is the genuinely OS-specific part of scheduling, so it lives here in
Python rather than behind a thicket of preprocessor branches in the native core.
The core is handed the result once and owns every scheduling decision after that.

Two facts drive the design. Hybrid CPUs mix performance and efficiency cores
that differ by nearly 40% in clock, so any *static* split of work across them
leaves the fast cores idle at a barrier waiting for the slow ones; work must be
handed out dynamically instead. And SMT siblings share L1/L2, so for the
bandwidth-bound layer analysis that dominates this pipeline a second thread on
the same physical core buys far less than a thread on an idle core. Both mean
"pick N cpus" must mean "N distinct physical cores, fastest first" and never
"the N lowest cpu numbers".

Everything degrades to a single-group fallback when nothing can be detected.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import platform
import subprocess

_SYS = Path('/sys/devices/system/cpu')
_CACHE = None

#: Fallback hybrid split: treat cores clocked below this fraction of the
#: fastest core as efficiency cores. Intel P-cores vary among themselves
#: (5.2 vs 5.4 GHz on a 13900HX, a 4% spread) while E-cores sit far below
#: (3.9 GHz, 28% down), so the gap is wide and the threshold is not delicate.
_EFFICIENCY_RATIO = 0.85


def _parse_cpu_list(text):
    """Parse a Linux cpu list such as '0-15', '0,2,4' or '0-3,8-11'."""
    cpus = set()
    for part in text.strip().split(','):
        if not part:
            continue
        if '-' in part:
            low, high = part.split('-', 1)
            cpus.update(range(int(low), int(high) + 1))
        else:
            cpus.add(int(part))
    return cpus


def _read(path):
    try:
        return path.read_text()
    except OSError:
        return None


@dataclass(frozen=True)
class Topology:
    """A snapshot of what this process is allowed to run on.

    ``allowed`` is the affinity mask, not every cpu on the machine: cgroups,
    taskset and CI runners all restrict it, and scheduling onto a cpu outside
    it silently fails or throws.  ``cores`` groups allowed cpus into physical
    cores (SMT siblings share an entry), first sibling first.
    """
    allowed: tuple
    perf_cpus: tuple
    eff_cpus: tuple
    cores: tuple
    source: str

    @property
    def n_logical(self):
        return len(self.allowed)

    @property
    def n_physical(self):
        return len(self.cores)

    @property
    def n_perf(self):
        return len(self.perf_cpus)

    @property
    def n_eff(self):
        return len(self.eff_cpus)

    @property
    def smt_factor(self):
        # The widest core, not the average: on a hybrid part the P-cores carry
        # two threads while the E-cores carry one, and an average of 1.33
        # describes neither.
        return max((len(core) for core in self.cores), default=1)

    def describe(self):
        """Compact dict for the timing report, so a slow run is diagnosable."""
        return {'source': self.source, 'logical': self.n_logical,
                'physical': self.n_physical, 'performance': self.n_perf,
                'efficiency': self.n_eff, 'smt_factor': self.smt_factor}


def _allowed_cpus():
    if hasattr(os, 'sched_getaffinity'):
        try:
            return set(os.sched_getaffinity(0))
        except OSError:
            pass
    return set(range(os.cpu_count() or 1))


def _linux_cores(allowed):
    """Group allowed cpus into physical cores via thread_siblings_list."""
    seen, cores = set(), []
    for cpu in sorted(allowed):
        if cpu in seen:
            continue
        text = _read(_SYS / f'cpu{cpu}' / 'topology' / 'thread_siblings_list')
        siblings = sorted((_parse_cpu_list(text) & allowed) if text else {cpu})
        if not siblings:
            siblings = [cpu]
        seen.update(siblings)
        cores.append(tuple(siblings))
    return tuple(cores)


def _linux_hybrid(allowed, cores):
    """Split allowed cpus into performance and efficiency sets.

    Three signals, most authoritative first: the hybrid PMU directories Intel
    exposes, the per-core maximum clock, and the observation that Intel E-cores
    have no SMT sibling while P-cores do.
    """
    perf = _read(Path('/sys/devices/cpu_core/cpus'))
    eff = _read(Path('/sys/devices/cpu_atom/cpus'))
    if perf and eff:
        return (_parse_cpu_list(perf) & allowed, _parse_cpu_list(eff) & allowed, 'sysfs-hybrid-pmu')

    freqs = {}
    for cpu in allowed:
        text = _read(_SYS / f'cpu{cpu}' / 'cpufreq' / 'cpuinfo_max_freq')
        if text and text.strip().isdigit():
            freqs[cpu] = int(text)
    if freqs and len(set(freqs.values())) > 1:
        top = max(freqs.values())
        slow = {cpu for cpu, f in freqs.items() if f < top * _EFFICIENCY_RATIO}
        if slow:
            return (allowed - slow, slow, 'sysfs-cpufreq')

    threaded = {c for core in cores if len(core) > 1 for c in core}
    lonely = allowed - threaded
    if threaded and lonely:
        return (threaded, lonely, 'sysfs-smt-asymmetry')
    return (set(allowed), set(), 'sysfs-uniform')


def _detect_linux():
    allowed = _allowed_cpus()
    if not (_SYS / 'cpu0').exists():
        return None
    cores = _linux_cores(allowed)
    perf, eff, source = _linux_hybrid(allowed, cores)
    return Topology(tuple(sorted(allowed)), tuple(sorted(perf)), tuple(sorted(eff)),
                    cores, source)


def _sysctl(name):
    try:
        out = subprocess.run(['sysctl', '-n', name], capture_output=True, text=True,
                             timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return int(value) if out.returncode == 0 and value.isdigit() else None


def _detect_macos():
    """Apple silicon reports core classes as perflevels; level 0 is fastest.

    There is no affinity API to honor, and no SMT, so every logical cpu is its
    own physical core.  Numbering puts perflevel0 first.
    """
    logical = _sysctl('hw.logicalcpu') or os.cpu_count() or 1
    perf_n = _sysctl('hw.perflevel0.logicalcpu')
    eff_n = _sysctl('hw.perflevel1.logicalcpu')
    physical = _sysctl('hw.physicalcpu') or logical
    allowed = tuple(range(logical))
    if perf_n is None:
        perf_n, eff_n = logical, 0
    perf = tuple(range(min(perf_n, logical)))
    eff = tuple(range(len(perf), min(len(perf) + (eff_n or 0), logical)))
    smt = max(1, logical // max(1, physical))
    cores = tuple(tuple(allowed[i:i + smt]) for i in range(0, logical, smt))
    return Topology(allowed, perf, eff, cores, 'sysctl-perflevel')


def _detect_windows():
    """GetLogicalProcessorInformationEx; EfficiencyClass 0 is the slowest class.

    Processor groups above 64 threads are not handled: bail to the fallback
    rather than report a mask that cannot be applied.
    """
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    RelationProcessorCore = 0
    size = wintypes.DWORD(0)
    kernel32.GetLogicalProcessorInformationEx(RelationProcessorCore, None,
                                              ctypes.byref(size))
    buf = (ctypes.c_byte * size.value)()
    if not kernel32.GetLogicalProcessorInformationEx(RelationProcessorCore, buf,
                                                     ctypes.byref(size)):
        raise OSError(ctypes.get_last_error(), 'GetLogicalProcessorInformationEx')
    cores, classes, offset = [], {}, 0
    while offset < size.value:
        base = offset
        length = int.from_bytes(bytes(buf[base + 4:base + 8]), 'little')
        efficiency = buf[base + 9] & 0xFF
        group_count = int.from_bytes(bytes(buf[base + 12:base + 14]), 'little')
        if group_count > 1:
            raise OSError('multiple processor groups')
        mask = int.from_bytes(bytes(buf[base + 16:base + 24]), 'little')
        siblings = tuple(i for i in range(64) if mask >> i & 1)
        if siblings:
            cores.append(siblings)
            for cpu in siblings:
                classes[cpu] = efficiency
        offset += length
    if not cores:
        raise OSError('no processor cores reported')
    allowed = tuple(sorted(classes))
    best = max(classes.values())
    perf = tuple(c for c in allowed if classes[c] == best)
    eff = tuple(c for c in allowed if classes[c] != best)
    return Topology(allowed, perf, eff, tuple(cores), 'win32-efficiency-class')


def _fallback():
    """No topology information: every cpu its own core, all equally fast."""
    allowed = tuple(sorted(_allowed_cpus()))
    return Topology(allowed, allowed, (), tuple((c,) for c in allowed), 'fallback')


def detect(*, refresh=False):
    """Detect once per process; affinity does not change under us mid-run."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    if os.environ.get('VOXELMILL_NO_TOPOLOGY'):
        _CACHE = _fallback()
        return _CACHE
    system = platform.system()
    detector = {'Linux': _detect_linux, 'Darwin': _detect_macos,
                'Windows': _detect_windows}.get(system)
    topology = None
    if detector is not None:
        try:
            topology = detector()
        except Exception:
            topology = None
    _CACHE = topology or _fallback()
    return _CACHE


#: Measured plateau. A `prepare` of fixtures/shapes/overhang_bracket.stl runs
#: 52.1, 30.7, 28.8, 27.8, 28.4 s at 2, 4, 6, 8 and 24 workers, while peak RSS
#: climbs 609 MB -> 600 -> 725 -> 782 -> 1952. Past eight the ordered merge
#: stage is the limit, so more workers buy nothing and cost memory linearly.
#: `_layer_worker_cap` still trims this further to fit the memory budget.
PARALLEL_PLATEAU = 8


def default_workers(topology=None):
    """How many workers to derive when `resources.workers` is 0.

    One per physical core, not per logical cpu: the layer analysis this sizes
    is memory-bandwidth-bound, so an SMT sibling contends for the same cache
    instead of adding throughput. Capped at the measured plateau above, because
    the ordered merge stage stops scaling there and every extra worker holds
    another set of full-panel buffers.
    """
    topology = topology or detect()
    return max(1, min(PARALLEL_PLATEAU, topology.n_physical))


def select_cpus(count, *, policy='performance', topology=None):
    """Choose `count` cpus, distinct physical cores first, fastest class first.

    Taking the lowest `count` cpu numbers instead — as this code used to —
    lands on the SMT siblings of a single physical core on any machine that
    numbers siblings adjacently, which is the worst available choice.

    `policy` is 'performance' (P cores first), 'efficiency' (E cores first, for
    background work that must not stall an interactive session), or 'all'
    (every allowed cpu, no restriction).
    """
    topology = topology or detect()
    if policy == 'all' or count >= topology.n_logical:
        return topology.allowed
    rank = {cpu: 0 for cpu in topology.perf_cpus}
    rank.update({cpu: 1 for cpu in topology.eff_cpus})
    if policy == 'efficiency':
        rank = {cpu: 1 - value for cpu, value in rank.items()}
    # Round-robin over physical cores so the first pass takes one cpu from each
    # core before any core gives up a second sibling.
    ordered = sorted(topology.cores, key=lambda core: (rank.get(core[0], 0), core[0]))
    picked, depth = [], 0
    while len(picked) < count and depth < max((len(c) for c in ordered), default=1):
        for core in ordered:
            if depth < len(core):
                picked.append(core[depth])
                if len(picked) == count:
                    break
        depth += 1
    return tuple(picked) or topology.allowed
