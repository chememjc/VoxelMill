"""Faults tab: colored diagnostic markers on the shared scene and a 2D layer.

Markers are points derived from validation/plan diagnostics. There are no
per-fault masks. Drainage bottleneck seeds and peel-region centers are expanded
into the same point list so the 2D preview and the 3D glyphs stay aligned.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .layerview import MAX_PREVIEW_PIXELS, preview_decimation
from ..validation import block_any

#: Distinct, reasonably colorblind-friendly RGB triples for known codes.
FAULT_COLORS = {
    'raster_island': (230, 25, 75),
    'enclosed_voids': (60, 180, 75),
    'drainage_bottleneck': (255, 225, 25),
    'support_unroutable': (0, 130, 200),
    'support_dropped_attached': (245, 130, 48),
    'support_coverage': (145, 30, 180),
    'peel_risk': (70, 240, 240),
    'growth_span': (240, 50, 230),
    'clipped_geometry': (210, 245, 60),
    'contact_parameters_unmatched': (250, 190, 190),
    'open_contours': (0, 128, 128),
    'transient_trap': (220, 190, 255),
}

DEFAULT_FAULT_COLOR = (128, 128, 128)


def fault_color(code):
    """RGB tuple for ``code``, or the default gray for unknown codes."""
    return FAULT_COLORS.get(code, DEFAULT_FAULT_COLOR)


def _as_diagnostic(item):
    if isinstance(item, dict):
        return item
    if hasattr(item, 'to_dict'):
        return item.to_dict()
    return {
        'code': getattr(item, 'code', None),
        'message': getattr(item, 'message', ''),
        'severity': getattr(item, 'severity', 'error'),
        'layer': getattr(item, 'layer', None),
        'position_mm': getattr(item, 'position_mm', None),
        'details': getattr(item, 'details', {}) or {},
    }


def _diagnostics_from(report):
    if report is None:
        return []
    if hasattr(report, 'diagnostics'):
        return list(report.diagnostics)
    if isinstance(report, dict):
        return list(report.get('diagnostics') or [])
    return []


def _seed_zyx_to_mm(seed, details, bounds=None):
    """Convert a drainage voxel seed to millimeters when the grid is known."""
    if seed is None or len(seed) != 3:
        return None
    pitch = details.get('analysis_pitch_mm')
    if pitch is None:
        return None
    pitch = float(pitch)
    origin = details.get('analysis_origin_mm')
    if origin is None and bounds is not None:
        low = np.asarray(bounds, dtype=float).reshape(2, 3)[0]
        origin = (low - 2.0 * pitch).tolist()
    if origin is None:
        return None
    z, y, x = (int(seed[0]), int(seed[1]), int(seed[2]))
    return [float(origin[0]) + (x + 0.5) * pitch,
            float(origin[1]) + (y + 0.5) * pitch,
            float(origin[2]) + (z + 0.5) * pitch]


def _region_center(bounds):
    if not bounds or len(bounds) != 2:
        return None
    low = np.asarray(bounds[0], dtype=float)
    high = np.asarray(bounds[1], dtype=float)
    if low.shape != (3,) or high.shape != (3,):
        return None
    return ((low + high) * 0.5).tolist()


def fault_overlay(report, plan=None, *, bounds=None):
    """Flatten diagnostics into ``{code, position_mm, layer, color}`` markers.

    Drainage ``bottleneck_examples[].seed_zyx`` and peel ``regions[].bounds``
    centers become points when present. ``bounds`` is the assembly AABB used to
    recover the drainage analysis origin (padded by two cells) when the report
    does not store it.
    """
    items = []
    seen = set()

    def add(code, position_mm, layer):
        if not code:
            return
        position = None
        if position_mm is not None:
            position = [float(v) for v in position_mm]
            if len(position) != 3 or not all(math.isfinite(v) for v in position):
                return
        key = (code, tuple(position) if position else None, layer)
        if key in seen:
            return
        seen.add(key)
        items.append({
            'code': code,
            'position_mm': position,
            'layer': None if layer is None else int(layer),
            'color': fault_color(code),
        })

    sources = list(_diagnostics_from(report))
    if plan is not None:
        sources.extend(_diagnostics_from(plan))

    for raw in sources:
        data = _as_diagnostic(raw)
        code = data.get('code')
        details = data.get('details') or {}
        add(code, data.get('position_mm'), data.get('layer'))
        if code == 'drainage_bottleneck':
            for example in details.get('bottleneck_examples') or []:
                seed = example.get('seed_zyx') if isinstance(example, dict) else None
                position = _seed_zyx_to_mm(seed, details, bounds=bounds)
                layer = None
                if position is not None:
                    # Layer index is not stored on the seed; leave None unless
                    # the caller later maps Z through process.layer_height_mm.
                    layer = data.get('layer')
                add(code, position, layer)
        elif code == 'peel_risk':
            for region in details.get('regions') or []:
                if not isinstance(region, dict):
                    continue
                position = _region_center(region.get('bounds'))
                span = region.get('z_layer_span') or []
                layer = int(span[0]) if span else data.get('layer')
                add(code, position, layer)
        elif code == 'enclosed_voids':
            for example in details.get('examples') or []:
                if not isinstance(example, dict):
                    continue
                add(code, example.get('position_mm'), example.get('first_layer', data.get('layer')))
    return items


def fault_mask_to_image(mask, markers=(), grid=None, marker=6, max_pixels=MAX_PREVIEW_PIXELS):
    """Gray occupancy with per-fault-class colored markers.

    Occupancy is normalized with ``mask != 0`` before scaling so GOO frames
    holding 0/255 do not wrap when multiplied into ``uint8``.
    """
    mask = np.asarray(mask)
    if mask.ndim != 2:
        raise ValueError('a layer mask must be two dimensional')
    occupied = mask != 0
    factor = preview_decimation(*occupied.shape, max_pixels=max_pixels)
    if factor > 1:
        occupied = block_any(occupied, factor)
    height, width = occupied.shape
    rgb = np.empty((height, width, 3), dtype=np.uint8)
    gray = np.flipud(occupied).astype(np.uint8) * np.uint8(220)
    rgb[..., 0] = rgb[..., 1] = rgb[..., 2] = gray
    if grid is not None:
        for entry in markers:
            position = entry.get('position_mm') if isinstance(entry, dict) else None
            if not position:
                continue
            color = tuple(entry.get('color') or DEFAULT_FAULT_COLOR)
            column = int(round((position[0] - grid.x0) / grid.dx - 0.5)) // factor
            row = int(round((position[1] - grid.y0) / grid.dy - 0.5)) // factor
            if not (0 <= column < width and 0 <= row < height):
                continue
            flipped = height - 1 - row
            lo_y, hi_y = max(0, flipped - marker), min(height, flipped + marker + 1)
            lo_x, hi_x = max(0, column - marker), min(width, column + marker + 1)
            rgb[lo_y:hi_y, lo_x:hi_x] = color
    return QtGui.QImage(rgb.data, width, height, 3 * width, QtGui.QImage.Format_RGB888).copy()


class FaultView(QtWidgets.QWidget):
    """2D fault preview, color key, and independent Z clip controls.

    The shared VTK scene stays on the left of the main window; this tab only
    drives its clip planes and fault glyphs plus a layer image colored to
    match.
    """
    layer_requested = QtCore.Signal(int)
    clip_changed = QtCore.Signal(object, object)
    filter_changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.label = QtWidgets.QLabel('No fault layer rasterized yet')
        self.label.setObjectName('fault_preview')
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.label.setMinimumSize(240, 240)
        self.label.setStyleSheet('background:#111;color:#bbb;')
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setObjectName('fault_layer_slider')
        self.slider.setEnabled(False)
        self.info = QtWidgets.QLabel('')
        self.info.setWordWrap(True)

        self.key = QtWidgets.QWidget()
        self.key.setObjectName('fault_key')
        self.key_layout = QtWidgets.QVBoxLayout(self.key)
        self.key_layout.setContentsMargins(0, 0, 0, 0)
        key_box = QtWidgets.QGroupBox('Color key')
        key_box.setObjectName('fault_key_box')
        key_box_layout = QtWidgets.QVBoxLayout(key_box)
        key_scroll = QtWidgets.QScrollArea()
        key_scroll.setWidgetResizable(True)
        key_scroll.setWidget(self.key)
        key_scroll.setMaximumHeight(180)
        key_box_layout.addWidget(key_scroll)

        clip_box = QtWidgets.QGroupBox('Z clipping')
        clip_form = QtWidgets.QFormLayout(clip_box)
        self.clip_below = QtWidgets.QDoubleSpinBox()
        self.clip_below.setObjectName('fault_clip_below')
        self.clip_below.setDecimals(2)
        self.clip_below.setRange(0.0, 1000.0)
        self.clip_below.setSuffix(' mm')
        self.clip_above = QtWidgets.QDoubleSpinBox()
        self.clip_above.setObjectName('fault_clip_above')
        self.clip_above.setDecimals(2)
        self.clip_above.setRange(0.0, 1000.0)
        self.clip_above.setSuffix(' mm')
        clip_form.addRow('From below (Zmin)', self.clip_below)
        clip_form.addRow('From above (Zmax)', self.clip_above)

        layout.addWidget(self.label, 1)
        layout.addWidget(self.slider)
        layout.addWidget(self.info)
        layout.addWidget(key_box)
        layout.addWidget(clip_box)

        self._markers = []
        self._checks = {}
        self._image = None
        self._clip_silent = False

        self.slider.valueChanged.connect(self.layer_requested)
        self.clip_below.valueChanged.connect(self._emit_clip)
        self.clip_above.valueChanged.connect(self._emit_clip)
        self.set_markers([])

    def set_range(self, layers):
        self.slider.setEnabled(layers > 0)
        self.slider.setRange(0, max(0, layers - 1))

    def set_z_extent(self, zmin, zmax):
        """Configure absolute clip limits; defaults to the full interval."""
        zmin, zmax = float(zmin), float(zmax)
        if zmax < zmin:
            zmin, zmax = zmax, zmin
        self._clip_silent = True
        for box in (self.clip_below, self.clip_above):
            box.setRange(zmin, zmax)
        self.clip_below.setValue(zmin)
        self.clip_above.setValue(zmax)
        self._clip_silent = False

    def clip_limits(self):
        zmin = float(self.clip_below.value())
        zmax = float(self.clip_above.value())
        if zmin > zmax:
            zmin, zmax = zmax, zmin
        return zmin, zmax

    def _emit_clip(self, *_args):
        if self._clip_silent:
            return
        zmin, zmax = self.clip_limits()
        self.clip_changed.emit(zmin, zmax)

    def set_markers(self, markers):
        """Replace the overlay list and rebuild the color key."""
        self._markers = list(markers or [])
        present = []
        seen = set()
        for marker in self._markers:
            code = marker.get('code')
            if not code or code in seen:
                continue
            seen.add(code)
            present.append(code)
        enabled = {code for code, box in self._checks.items() if box.isChecked()}
        while self.key_layout.count():
            item = self.key_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checks = {}
        # The key stays populated even before diagnostics arrive so the tab
        # never looks empty of legend.
        codes = sorted(present) if present else sorted(FAULT_COLORS)
        for code in codes:
            color = fault_color(code)
            row = QtWidgets.QHBoxLayout()
            swatch = QtWidgets.QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f'background:rgb({color[0]},{color[1]},{color[2]});border:1px solid #444;')
            box = QtWidgets.QCheckBox(code)
            box.setChecked(code in enabled if enabled else True)
            box.stateChanged.connect(lambda _state: self.filter_changed.emit())
            self._checks[code] = box
            row.addWidget(swatch)
            row.addWidget(box, 1)
            holder = QtWidgets.QWidget()
            holder.setLayout(row)
            self.key_layout.addWidget(holder)
        self.key_layout.addStretch(1)
        self.filter_changed.emit()

    def enabled_codes(self):
        return {code for code, box in self._checks.items() if box.isChecked()}

    def filtered_markers(self, layer=None):
        enabled = self.enabled_codes()
        result = []
        for marker in self._markers:
            if marker.get('code') not in enabled:
                continue
            if layer is not None and marker.get('layer') is not None and int(marker['layer']) != int(layer):
                continue
            result.append(marker)
        return result

    def show_layer(self, payload, markers=None):
        markers = self.filtered_markers(payload['index']) if markers is None else markers
        self._image = fault_mask_to_image(payload['mask'], markers, payload['grid'])
        self.label.setPixmap(QtGui.QPixmap.fromImage(self._image).scaled(
            self.label.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.FastTransformation))
        origin = payload.get('source', 'union')
        note = ('decoded GOO pixels' if origin == 'goo'
                else 'assembly sliced at the configured layer height')
        self.info.setText(
            f"layer {payload['index']}  z={payload['z_mm']:.3f} mm  "
            f"fault markers {len(markers)}  [{note}]")
