"""Bounded STL I/O and full-resolution native mesh inspection.

``with open_stl(path) as mesh`` exposes a read-only float32 ``triangles``
array of shape (n,3,3), and ``asset`` source metadata. Binary inputs remain
memory-mapped; strict ASCII inputs are streamed into temporary binary scratch.
Non-exact binary lengths recover with warning diagnostics on the mesh rather
than rejecting the file. Arrays must not outlive their owner. Topology and
``inspect_mesh`` use exact coordinate equality; ``weld_mesh`` may take an
optional bounded tolerance for tessellated CAD repair.
"""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
import hashlib
import os
import struct
import tempfile
import time
import numpy as np
from .contracts import (
    MeshAsset, ResourceBudget, CancellationToken, Progress, no_progress, VoxelMillError, Diagnostic,
)

STL_DTYPE = np.dtype([("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])


def _binary_triangle_recovery(size, declared):
    """Recover a usable triangle count from a non-exact binary STL.

    Returns ``(count, Diagnostic|None)``. ``count`` may be zero (caller rejects
    empty meshes). Returns ``None`` when the file cannot be treated as binary.
    """
    if size < 84 or declared < 0:
        return None
    payload = size - 84
    if payload % 50 == 0:
        used = payload // 50
        if used == declared:
            return used, None
        return used, Diagnostic(
            "stl_count_mismatch",
            "Binary STL triangle count does not match file size; using size-implied count",
            severity="warning",
            details={"bytes": size, "declared_triangles": declared, "used_triangles": used},
        )
    declared_bytes = 84 + 50 * declared
    if size > declared_bytes:
        return declared, Diagnostic(
            "stl_trailing_garbage",
            "Binary STL has trailing bytes after the declared triangles; ignoring the tail",
            severity="warning",
            details={
                "bytes": size,
                "declared_triangles": declared,
                "trailing_bytes": size - declared_bytes,
            },
        )
    used = payload // 50
    return used, Diagnostic(
        "stl_truncated",
        "Binary STL is shorter than its declared triangle count; reading complete triangles only",
        severity="warning",
        details={"bytes": size, "declared_triangles": declared, "used_triangles": used},
    )

def _hash(path, cancel, progress):
    h = hashlib.sha256()
    size = path.stat().st_size
    done = 0
    with path.open("rb") as source:
        while chunk := source.read(4 * 1024**2):
            cancel.check()
            h.update(chunk)
            done += len(chunk)
            progress("hash", done, size)
    return h.hexdigest()


def _ascii_to_binary(path, scratch_dir, cancel):
    """Strict facet grammar, no whole-file tokenization and bounded line reads."""
    scratch = tempfile.NamedTemporaryFile(prefix="voxelmill-stl-", suffix=".stl", dir=scratch_dir, delete=False)
    scratch_path = Path(scratch.name)
    count = 0
    def line(source):
        cancel.check()
        raw = source.readline(4097)
        if len(raw) > 4096:
            raise VoxelMillError("invalid_stl", "ASCII STL line exceeds 4096 bytes")
        try:
            return raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise VoxelMillError("invalid_stl", "STL is neither valid binary nor ASCII") from exc
    try:
        with path.open("rb") as source, scratch:
            scratch.write(b"voxelmill streamed ASCII".ljust(80, b"\0") + struct.pack("<I", 0))
            first = line(source)
            if not (first == "solid" or first.startswith("solid ")):
                raise VoxelMillError("invalid_stl", "ASCII STL must begin with solid")
            while True:
                row = line(source)
                if row == "endsolid" or row.startswith("endsolid "):
                    while rest := source.readline(4097):
                        cancel.check()
                        if len(rest) > 4096 or rest.strip():
                            raise VoxelMillError("invalid_stl", "Unexpected data after endsolid")
                    break
                fields = row.split()
                if len(fields) != 5 or fields[:2] != ["facet", "normal"]:
                    raise VoxelMillError("invalid_stl", "Expected facet normal or endsolid")
                normal = [float(v) for v in fields[2:]]
                if line(source) != "outer loop":
                    raise VoxelMillError("invalid_stl", "Expected outer loop")
                points = []
                for _ in range(3):
                    fields = line(source).split()
                    if len(fields) != 4 or fields[0] != "vertex":
                        raise VoxelMillError("invalid_stl", "Expected three vertex records")
                    points.extend(float(v) for v in fields[1:])
                if line(source) != "endloop" or line(source) != "endfacet":
                    raise VoxelMillError("invalid_stl", "Expected endloop/endfacet")
                scratch.write(struct.pack("<12fH", *normal, *points, 0))
                count += 1
                if count > 0xffffffff:
                    raise VoxelMillError("invalid_stl", "STL exceeds 32-bit triangle capacity")
            scratch.seek(80)
            scratch.write(struct.pack("<I", count))
        return scratch_path, count
    except Exception as exc:
        scratch.close()
        scratch_path.unlink(missing_ok=True)
        if isinstance(exc, (ValueError, OverflowError, struct.error)):
            raise VoxelMillError("invalid_stl", f"Invalid ASCII numeric field: {exc}") from exc
        raise


class STLMesh:
    def __init__(self, path, budget=None, cancel=None, progress=no_progress):
        self.path = Path(path)
        self.budget = budget or ResourceBudget()
        self.cancel = cancel or CancellationToken()
        self._scratch = None
        self._records = None
        self.diagnostics = []
        self.cancel.check()
        try:
            size = self.path.stat().st_size
            with self.path.open("rb") as source:
                header = source.read(84)
            declared = struct.unpack("<I", header[80:84])[0] if len(header) == 84 else -1
            recovered = _binary_triangle_recovery(size, declared) if declared >= 0 else None
            # Clean 84+50n payloads are binary even when the header says "solid".
            # Messy solid-headed files stay on the ASCII path.
            if recovered is not None and ((size - 84) % 50 == 0 or not header.startswith(b"solid")):
                count, diagnostic = recovered
                mapped_path, source_format = self.path, "binary_stl"
                if diagnostic is not None:
                    self.diagnostics.append(diagnostic)
            elif header.startswith(b"solid"):
                self._scratch, count = _ascii_to_binary(self.path, self.budget.scratch_dir, self.cancel)
                mapped_path, source_format = self._scratch, "ascii_stl"
            else:
                raise VoxelMillError(
                    "invalid_stl",
                    "Binary STL length does not match triangle count",
                    {"bytes": size, "declared_triangles": declared},
                )
            if count == 0:
                raise VoxelMillError("empty_stl", "STL contains no triangles")
            self.budget.require(min(count, 65536) * 100 + 4 * 1024**2, "STL ingestion")
            self._records = np.memmap(mapped_path, dtype=STL_DTYPE, mode="r", offset=84, shape=(count,))
            self.triangles = self._records["vertices"]
            lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
            for start in range(0, count, 65536):
                self.cancel.check()
                block = self.triangles[start:start+65536].reshape(-1, 3)
                finite = np.isfinite(block)
                lo = np.minimum(lo, np.min(np.where(finite, block, np.inf), axis=0))
                hi = np.maximum(hi, np.max(np.where(finite, block, -np.inf), axis=0))
                progress("bounds", min(start+65536, count), count)
            bounds = [[float(v) if np.isfinite(v) else None for v in side] for side in (lo, hi)]
            self.asset = MeshAsset(self.path, _hash(self.path, self.cancel, progress), count, bounds, source_format=source_format)
        except OSError as exc:
            self.close()
            raise VoxelMillError("mesh_io", str(exc), {"path": str(self.path)}) from exc
        except Exception:
            self.close()
            raise
    def close(self):
        if self._records is not None:
            self.triangles = None
            self._records._mmap.close()
            self._records = None
        if self._scratch is not None:
            self._scratch.unlink(missing_ok=True)
            self._scratch = None
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        self.close()


def open_stl(path, budget=None, cancel=None, progress=no_progress):
    return STLMesh(path, budget, cancel, progress)

read_stl = open_stl


def inspect_mesh(mesh, budget=None, cancel=None, progress=no_progress, self_intersections=True):
    """Inspect all triangles. Invalid triangles are counted, never silently repaired."""
    from . import _native
    if not isinstance(mesh, STLMesh):
        with open_stl(mesh, budget, cancel, progress) as opened:
            return inspect_mesh(opened, budget, cancel, progress, self_intersections)
    budget = budget or mesh.budget
    cancel = cancel or mesh.cancel
    # Conservative allowance covers vertices, face indices, edges, DSU and sorting;
    # intersections run subsequently, so their box storage does not overlap these.
    budget.require(mesh.asset.triangle_count * 240 + 64 * 1024**2, "full mesh inspection")
    def callback(stage, done, total):
        cancel.check()
        progress(stage, done, total)
    start = time.monotonic()
    callback("inspection", 0, mesh.asset.triangle_count)
    result = _native.inspect_mesh(mesh.triangles, callback)
    if self_intersections:
        result.update(_native.inspect_intersections(mesh.triangles, callback))
    asset = asdict(mesh.asset)
    asset["path"] = str(mesh.asset.path)
    result.update(asset)
    result["diagnostics"] = [asdict(item) for item in mesh.diagnostics]
    result["inspection_seconds"] = time.monotonic() - start
    result["estimated_working_bytes"] = mesh.asset.triangle_count * 240 + 64 * 1024**2
    return result


def weld_mesh(triangles, budget=None, cancel=None, progress=no_progress, weld_tolerance_mm=0.0):
    """Weld duplicate vertices. Tolerance 0 (default) is exact-coordinate.

    Positive ``weld_tolerance_mm`` merges vertices within that Euclidean
    distance via a spatial hash (cell size = tolerance). Cap is 0.05 mm.
    """
    from . import _native
    budget = budget or ResourceBudget()
    cancel = cancel or CancellationToken()
    try:
        tol = float(weld_tolerance_mm)
    except (TypeError, ValueError) as exc:
        raise VoxelMillError("invalid_mesh", "weld_tolerance_mm must be a finite number") from exc
    if not np.isfinite(tol) or tol < 0:
        raise VoxelMillError("invalid_mesh", "weld_tolerance_mm must be a finite non-negative number")
    if tol > 0.05:
        raise VoxelMillError("invalid_mesh", "weld_tolerance_mm must be <= 0.05 mm")
    label = "exact vertex welding" if tol == 0 else "tolerant vertex welding"
    budget.require(len(triangles) * 140 + 32 * 1024**2, label)
    def callback(stage, done, total):
        cancel.check()
        progress(stage, done, total)
    cancel.check()
    try:
        return _native.weld_mesh(triangles, callback, tol)
    except ValueError as exc:
        raise VoxelMillError("invalid_mesh", str(exc)) from exc


def write_stl(path, triangles, cancel=None, progress=no_progress):
    """Atomic, bounded binary export; corrupt coordinates never serialize."""
    path = Path(path)
    cancel = cancel or CancellationToken()
    if isinstance(triangles, np.ndarray):
        groups = (triangles,)
    else:
        try:
            groups = tuple(triangles)
        except TypeError as exc:
            raise VoxelMillError("invalid_mesh", "Expected nonempty triangles with shape (n,3,3)") from exc
    total = 0
    for group in groups:
        if getattr(group, "ndim", 0) != 3 or group.shape[1:] != (3, 3):
            raise VoxelMillError("invalid_mesh", "Expected nonempty triangles with shape (n,3,3)")
        total += len(group)
    if not 0 < total <= 0xffffffff:
        raise VoxelMillError("invalid_mesh", "Expected nonempty triangles with shape (n,3,3)")
    scratch = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", dir=path.parent, delete=False) as target:
            scratch = Path(target.name)
            target.write(b"voxelmill binary STL; millimeters".ljust(80,b"\0") + struct.pack("<I", total))
            completed = 0
            for group in groups:
                for start in range(0, len(group), 65536):
                    cancel.check()
                    with np.errstate(over="ignore", invalid="ignore"):
                        block = np.asarray(group[start:start+65536], dtype=np.float32)
                    if not np.isfinite(block).all():
                        raise VoxelMillError("invalid_mesh", "Nonfinite or float32-unencodable coordinates cannot serialize")
                    records = np.zeros(len(block), dtype=STL_DTYPE)
                    records["vertices"] = block
                    normals = np.cross(block[:,1].astype(np.float64)-block[:,0], block[:,2].astype(np.float64)-block[:,0])
                    length = np.linalg.norm(normals, axis=1)
                    normals[length > 0] /= length[length > 0, None]
                    records["normal"] = normals
                    target.write(records.tobytes())
                    completed += len(block)
                    progress("write_stl", completed, total)
            target.flush()
            os.fsync(target.fileno())
        cancel.check()
        os.replace(scratch,path)
    except OSError as exc:
        raise VoxelMillError("mesh_io", str(exc), {"path": str(path)}) from exc
    finally:
        if scratch is not None:
            scratch.unlink(missing_ok=True)
