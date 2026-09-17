"""Version 1 core contracts. Distances are mm; matrices map column vectors."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol
import threading
import numpy as np

SCHEMA_VERSION = 1

class VoxelMillError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code, self.details = code, details or {}
    def to_dict(self):
        return {"code": self.code, "message": str(self), "details": self.details}

class Canceled(VoxelMillError):
    def __init__(self):
        super().__init__("canceled", "Operation canceled")

class CancellationToken:
    def __init__(self):
        self._event = threading.Event()
    def cancel(self):
        self._event.set()
    def check(self):
        if self._event.is_set():
            raise Canceled()

Progress = Callable[[str, int, int], None]

def no_progress(stage: str, completed: int, total: int) -> None:
    pass

@dataclass
class ResourceBudget:
    memory_gib: float = 32.0
    workers: int = 2
    scratch_dir: str | None = None
    acceleration: str = 'auto'
    cuda_device: int = 0
    # Carried on the resources table so settings can splat into this dataclass.
    # It is not a memory limit; hooks.py reads settings, not the budget.
    post_slice_hook: str | None = None
    def __post_init__(self):
        if not np.isfinite(self.memory_gib) or self.memory_gib < 0.25 or not 1 <= self.workers <= 32:
            raise VoxelMillError("resource_budget", "Memory must be >= 0.25 GiB and workers between 1 and 32")
        if self.acceleration not in ('auto', 'cpu', 'cuda') or self.cuda_device < 0:
            raise VoxelMillError("resource_budget", "Acceleration must be auto, cpu, or cuda and CUDA device nonnegative")
    def require(self, estimated_bytes: int, operation: str):
        if estimated_bytes > self.memory_gib * 1024**3 * 0.8:
            raise VoxelMillError("memory_budget", f"{operation} exceeds working memory budget", {"estimated_bytes": estimated_bytes})

@dataclass
class MeshAsset:
    path: Path
    sha256: str
    triangle_count: int
    bounds: list[list[float]]
    units: str = "mm"
    source_format: str = "binary_stl"

@dataclass
class Placement:
    matrix: list[list[float]]
    rotation_deg: list[float]
    center_offset_mm: list[float]
    model_lift_mm: float
    bounds: list[list[float]]
    search: dict[str, Any] = field(default_factory=dict)
    # Per-axis scale factors and mirrored axes. Defaults keep every existing
    # project and every caller that predates them resolving unchanged.
    scale: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    mirror: list[bool] = field(default_factory=lambda: [False, False, False])

@dataclass
class SupportNode:
    id: str
    position_mm: list[float]
    kind: str  # foot, junction, contact

@dataclass
class SupportEdge:
    start: str
    end: str
    radius_mm: float
    kind: str = "pillar"

@dataclass
class SupportGraph:
    nodes: list[SupportNode] = field(default_factory=list)
    edges: list[SupportEdge] = field(default_factory=list)
    overrides: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

@dataclass
class Diagnostic:
    code: str
    message: str
    severity: str = "error"
    layer: int | None = None
    position_mm: list[float] | None = None
    details: dict[str, Any] = field(default_factory=dict)

@dataclass
class ValidationReport:
    diagnostics: list[Diagnostic] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)  # pass, fail, not_run
    metrics: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    @property
    def passed(self) -> bool:
        """Pass needs every check run and none failed.

        ``warn`` is an acceptable outcome; ``not_run`` is not, because a check
        that did not run cannot be reported as satisfied.
        """
        return (bool(self.checks) and all(v in ("pass", "warn") for v in self.checks.values())
                and not any(d.severity == "error" for d in self.diagnostics))
    def to_dict(self):
        return {**asdict(self), "passed": self.passed}
    def fail(self, code: str, message: str, **kwargs):
        self.diagnostics.append(Diagnostic(code, message, **kwargs))
        self.checks[code] = "fail"

@dataclass
class Layer:
    index: int
    z_mm: float
    mask: np.ndarray  # uint8 occupancy [y,x]; origin lower-left, x right, y up

class LayerStream(Protocol):
    def __iter__(self) -> Iterator[Layer]: ...

class PrinterAdapter(Protocol):
    def discover(self) -> list[dict[str, Any]]: ...
    def connect(self, address: str) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...
    def upload(self, path: Path, cancel: CancellationToken, progress: Progress) -> str: ...
    def start(self, remote_id: str) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def cancel(self) -> None: ...
    def camera(self) -> str | None: ...
