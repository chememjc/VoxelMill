"""Offline exposure and tolerance calibration GOO generators.

Emits a printer file whose layers carry a spatial matrix of patches, each
mapped to a different exposure time or morphological offset, plus a JSON
report so the printed result can be read back. Never contacts a printer.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .contracts import VoxelMillError, no_progress
from .goo import GooWriter


def parse_range(text: str) -> tuple[float, float]:
    """Parse ``lo:hi`` into a finite ``(lo, hi)`` pair."""
    if not isinstance(text, str) or text.count(':') != 1:
        raise VoxelMillError('invalid_option', '--range expects lo:hi')
    left, right = text.split(':', 1)
    try:
        lo, hi = float(left), float(right)
    except ValueError as exc:
        raise VoxelMillError('invalid_option', '--range expects finite numbers') from exc
    if not math.isfinite(lo) or not math.isfinite(hi):
        raise VoxelMillError('invalid_option', '--range bounds must be finite')
    return lo, hi


def step_values(lo: float, hi: float, steps: int) -> list[float]:
    if type(steps) is not int or steps < 1:
        raise VoxelMillError('invalid_option', '--steps must be a positive integer')
    if steps == 1:
        return [float(lo)]
    span = hi - lo
    return [float(lo + span * index / (steps - 1)) for index in range(steps)]


def _grid_shape(steps: int) -> tuple[int, int]:
    cols = max(1, int(math.ceil(math.sqrt(steps))))
    rows = int(math.ceil(steps / cols))
    return rows, cols


def _cell_layout(settings, steps: int):
    """Return row/col counts and per-cell pixel boxes on the full LCD."""
    printer = settings['printer']
    width, height = int(printer['pixels'][0]), int(printer['pixels'][1])
    rows, cols = _grid_shape(steps)
    # Leave a one-pixel gutter between cells and at the panel edge.
    cell_w = max(1, (width - (cols + 1)) // cols)
    cell_h = max(1, (height - (rows + 1)) // rows)
    if cell_w < 1 or cell_h < 1:
        raise VoxelMillError('calibration_panel',
                        'Printer panel is too small for the requested calibration matrix',
                        {'pixels': [width, height], 'steps': steps, 'rows': rows, 'cols': cols})
    pitch_x, pitch_y = (float(printer['pixel_pitch_mm'][0]), float(printer['pixel_pitch_mm'][1]))
    x0 = -float(printer['build_mm'][0]) / 2.0
    y0 = -float(printer['build_mm'][1]) / 2.0
    cells = []
    for index in range(steps):
        row, col = divmod(index, cols)
        x0_px = 1 + col * (cell_w + 1)
        y0_px = 1 + row * (cell_h + 1)
        x1_px = min(width, x0_px + cell_w)
        y1_px = min(height, y0_px + cell_h)
        cx = (x0_px + x1_px - 1) / 2.0
        cy = (y0_px + y1_px - 1) / 2.0
        cells.append({
            'index': index, 'row': row, 'col': col,
            'pixel_box': [x0_px, y0_px, x1_px, y1_px],
            'center_mm': [x0 + (cx + 0.5) * pitch_x, y0 + (cy + 0.5) * pitch_y],
        })
    return rows, cols, cells


def _exposure_gray(value: float, lo: float, hi: float) -> int:
    """Map an exposure parameter onto 1..255 for an RERF-style relative dose."""
    if hi == lo:
        return 255
    t = (value - lo) / (hi - lo)
    return max(1, min(255, int(round(t * 255.0))))


def _draw_exposure_frame(settings, cells, values, lo, hi):
    height = int(settings['printer']['pixels'][1])
    width = int(settings['printer']['pixels'][0])
    frame = np.zeros((height, width), dtype=np.uint8)
    for cell, value in zip(cells, values):
        x0, y0, x1, y1 = cell['pixel_box']
        frame[y0:y1, x0:x1] = _exposure_gray(value, lo, hi)
    return frame


def _draw_tolerance_frame(settings, cells, values):
    """Encode each offset as patch occupancy size inside its cell box."""
    height = int(settings['printer']['pixels'][1])
    width = int(settings['printer']['pixels'][0])
    pitch = float(settings['printer']['pixel_pitch_mm'][0])
    frame = np.zeros((height, width), dtype=np.uint8)
    for cell, offset in zip(cells, values):
        x0, y0, x1, y1 = cell['pixel_box']
        box_w = x1 - x0
        box_h = y1 - y0
        # Nominal half-size is just under half the cell; offset grows/shrinks it.
        base = max(1, min(box_w, box_h) // 2 - 1)
        delta = int(round(float(offset) / pitch))
        half = max(1, base + delta)
        cx = (x0 + x1) // 2
        cy = (y0 + y1) // 2
        xa = max(x0, cx - half)
        xb = min(x1, cx + half + 1)
        ya = max(y0, cy - half)
        yb = min(y1, cy + half + 1)
        if xa < xb and ya < yb:
            frame[ya:yb, xa:xb] = 255
    return frame


def write_calibration(kind: str, settings, output, *, range_mm=None, steps: int,
                      layers: int = 2, report_path=None, progress=no_progress):
    """Write a calibration GOO and return the JSON report payload.

    ``kind`` is ``exposure`` (RERF-style gray matrix; each layer uses an
    explicit ``exposure_s``) or ``tolerance`` (patch size encodes offset).
    """
    if kind not in ('exposure', 'tolerance'):
        raise VoxelMillError('invalid_option', f'Unknown calibration kind {kind!r}')
    if type(layers) is not int or layers < 1:
        raise VoxelMillError('invalid_option', '--layers must be a positive integer')
    if range_mm is None:
        raise VoxelMillError('invalid_option', '--range is required')
    lo, hi = range_mm
    if kind == 'exposure' and (lo <= 0 or hi <= 0):
        raise VoxelMillError('invalid_option', 'Exposure range bounds must be positive')
    values = step_values(lo, hi, steps)
    rows, cols, cells = _cell_layout(settings, steps)
    height_mm = float(settings['process']['layer_height_mm'])
    machine_z = float(settings['printer']['build_mm'][2])
    if layers * height_mm > machine_z:
        raise VoxelMillError('calibration_panel',
                        'Requested layers exceed machine Z travel at this layer height')

    if kind == 'exposure':
        frame = _draw_exposure_frame(settings, cells, values, lo, hi)
        # Brightest cell approximates ``hi`` seconds under a constant layer exposure.
        layer_exposure_s = float(hi if hi > 0 else max(values))
    else:
        frame = _draw_tolerance_frame(settings, cells, values)
        layer_exposure_s = float(settings['process']['normal_exposure_s'])

    destination = Path(output)
    report_cells = []
    for cell, value in zip(cells, values):
        entry = {
            'row': cell['row'], 'col': cell['col'],
            'value': value,
            'layer_index': 0,
            'center_mm': cell['center_mm'],
        }
        if kind == 'exposure':
            entry['exposure_s'] = value
        else:
            entry['tolerance_offset_mm'] = value
        report_cells.append(entry)

    with GooWriter(destination, settings, layers,
                   previews={'small': np.zeros((116, 116, 3), np.uint8),
                             'big': np.zeros((290, 290, 3), np.uint8)},
                   progress=progress) as writer:
        for index in range(layers):
            z_mm = (index + 1) * height_mm
            writer.add_layer(frame, z_mm, exposure_s=layer_exposure_s)

    payload = {
        'schema_version': 1,
        'command': f'calibrate {kind}',
        'kind': kind,
        'output': str(destination),
        'range': [lo, hi],
        'steps': steps,
        'layers': layers,
        'rows': rows,
        'cols': cols,
        'layer_exposure_s': layer_exposure_s,
        'cells': report_cells,
        'notes': [
            'Offline calibration artifact; no printer was contacted.',
            'Values are uncalibrated starting points until a printed result is measured.',
        ],
    }
    if report_path is not None:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
        payload['report'] = str(path)
    return payload
