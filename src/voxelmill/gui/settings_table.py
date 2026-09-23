"""Declarative settings descriptors and a generated typed settings form.

One descriptor per leaf in ``config.DEFAULTS``. The Setup page renders from
this table so CLI ``--set`` paths, tooltips and visibility tiers stay aligned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6 import QtCore, QtWidgets

from .helptext import help_for
from ..config import (
    BRACE_DESTINATIONS, BRACE_PATTERNS, BASE_TYPES, DEFAULTS, MODEL_ANCHOR_SHAPES, SMALL_PILLAR_MODES,
    SMALL_PILLAR_SHAPES, SUPPORT_VOID_POLICIES, TIP_SHAPES,
)

TIERS = ('simple', 'advanced', 'expert')
TIER_RANK = {name: index for index, name in enumerate(TIERS)}
RISKS = ('normal', 'caution', 'uncalibrated')

# Compact Setup controls that belong on the Simple surface.
SIMPLE_PATHS = {
    'process.layer_height_mm',
    'process.bottom_exposure_s',
    'process.normal_exposure_s',
    'process.bottom_layers',
    'process.transition_layers',
    'support.spacing_mm',
    'support.overhang_angle_deg',
    'support.base_type',
    'support.automatic',
    'repair.aggressiveness',
    'repair.seal_voids',
    'repair.min_orifice_area_mm2',
    'assembly.clip_to_build_volume',
}

# Dedicated CLI flags mirrored from MainWindow.SETTING_KEYS.
CLI_FLAGS = {
    'support.brace_azimuth_deg': '--brace-azimuth-deg',
    'support.brace_min_height_mm': '--brace-min-height-mm',
    'support.brace_angle_deg': '--brace-angle-deg',
    'support.brace_branches_per_node': '--brace-branches-per-node',
    'support.brace_pattern': '--brace-pattern',
    'support.brace_destination': '--brace-destination',
    'process.layer_height_mm': '--layer-height-mm',
    'support.spacing_mm': '--support-spacing-mm',
    'support.brace_spacing_mm': '--brace-spacing-mm',
    'support.brace_diameter_mm': '--brace-diameter-mm',
    'support.brace_max_distance_mm': '--brace-max-distance-mm',
    'support.brace_max_length_mm': '--brace-max-length-mm',
    'support.overhang_angle_deg': '--overhang-angle-deg',
    'support.base_type': '--base-type',
    'support.automatic': '--auto-supports',
    'support.tree_supports': '--tree-supports',
    'support.contour_supports': '--contour-supports',
    'support.boundary_supports': '--boundary-supports',
    'repair.aggressiveness': '--repair',
    'repair.seal_voids': '--seal-voids',
    'repair.min_orifice_area_mm2': '--min-orifice-area-mm2',
    'assembly.clip_to_build_volume': '--clip-to-build-volume',
}

ENUM_CHOICES = {
    'support.brace_destination': BRACE_DESTINATIONS,
    'support.brace_pattern': BRACE_PATTERNS,
    'support.base_type': BASE_TYPES,
    'support.tip_shape': TIP_SHAPES,
    'support.model_anchor_shape': MODEL_ANCHOR_SHAPES,
    'support.small_pillar_mode': SMALL_PILLAR_MODES,
    'support.small_pillar_shape': SMALL_PILLAR_SHAPES,
    'repair.aggressiveness': ('none', 'conservative', 'aggressive'),
    'repair.support_void_policy': SUPPORT_VOID_POLICIES,
    'assembly.union': ('auto', 'exact', 'raster'),
    'resources.acceleration': ('auto', 'cpu', 'cuda'),
    'resources.worker_policy': ('performance', 'efficiency', 'all'),
    'hollow.mode': ('inner', 'outer'),
    'hollow.infill': ('none', 'gyroid', 'grid'),
}

# Explicit tier / risk / unit / range overrides. Unlisted leaves default to
# advanced/normal with a range inferred from the default value.
OVERRIDES: dict[str, dict[str, Any]] = {
    **{path: {'tier': 'simple'} for path in SIMPLE_PATHS},
    'printer.id': {'tier': 'expert', 'risk': 'caution'},
    'printer.name': {'tier': 'expert'},
    'printer.build_mm': {'tier': 'expert', 'risk': 'caution', 'unit': 'mm'},
    'printer.pixels': {'tier': 'expert', 'risk': 'caution'},
    'printer.pixel_pitch_mm': {'tier': 'expert', 'risk': 'caution', 'unit': 'mm'},
    'printer.edge_clearance_mm': {'tier': 'advanced', 'unit': 'mm', 'range': (0.0, 50.0)},
    'printer.image_mirror_x': {'tier': 'expert', 'risk': 'caution'},
    'printer.image_mirror_y': {'tier': 'expert', 'risk': 'caution'},
    'printer.image_mirror_verified': {'tier': 'expert', 'risk': 'caution'},
    'printer.layer_height_range_mm': {'tier': 'expert', 'unit': 'mm'},
    'printer.output_formats': {'tier': 'expert'},
    'resin.id': {'tier': 'advanced'},
    'resin.name': {'tier': 'advanced'},
    'resin.density_g_cm3': {'tier': 'advanced', 'unit': 'g/cm³', 'range': (0.0, 5.0)},
    'resin.cost_per_liter': {'tier': 'advanced', 'range': (0.0, 1e6)},
    'resin.currency': {'tier': 'advanced'},
    'process.layer_height_mm': {'tier': 'simple', 'unit': 'mm', 'range': (0.001, 1.0)},
    'process.bottom_exposure_s': {'tier': 'simple', 'unit': 's', 'range': (0.01, 300.0)},
    'process.normal_exposure_s': {'tier': 'simple', 'unit': 's', 'range': (0.01, 300.0)},
    'process.bottom_layers': {'tier': 'simple', 'range': (0, 1000)},
    'process.transition_layers': {'tier': 'simple', 'range': (0, 1000)},
    'process.elephant_foot_compensation_mm': {'tier': 'advanced', 'unit': 'mm', 'risk': 'caution'},
    'process.shrink_percent_xy': {'tier': 'expert', 'unit': '%', 'risk': 'uncalibrated'},
    'process.shrink_percent_z': {'tier': 'expert', 'unit': '%', 'risk': 'uncalibrated'},
    'process.tolerance_offset_mm': {'tier': 'expert', 'unit': 'mm', 'risk': 'uncalibrated'},
    'process.bottom_tolerance_offset_mm': {'tier': 'expert', 'unit': 'mm', 'risk': 'uncalibrated'},
    'support.spacing_mm': {'tier': 'simple', 'unit': 'mm', 'range': (0.2, 50.0)},
    'support.brace_spacing_mm': {'tier': 'simple', 'unit': 'mm', 'range': (0.1, 200.0),
                                 'risk': 'caution'},
    'support.brace_diameter_mm': {'tier': 'advanced', 'unit': 'mm', 'range': (0.0, 20.0),
                                  'risk': 'caution'},
    'support.brace_max_distance_mm': {'tier': 'simple', 'unit': 'mm', 'range': (0.0, 200.0),
                                      'risk': 'caution'},
    'support.brace_max_length_mm': {'tier': 'simple', 'unit': 'mm', 'range': (0.1, 200.0),
                                    'risk': 'caution'},
    'support.auto_bracing': {'tier': 'simple', 'label': 'Enable bracing'},
    'support.brace_destination': {'tier': 'simple', 'label': 'Brace destinations (supports / base / both)'},
    'support.brace_pattern': {'tier': 'simple', 'label': 'Bracing pattern'},
    'support.brace_branches_per_node': {'tier': 'simple', 'range': (1, 8), 'label': 'Brace connections per node'},
    'support.brace_angle_deg': {'tier': 'simple', 'unit': 'deg', 'range': (0.1, 89.9)},
    'support.brace_min_height_mm': {'tier': 'advanced', 'unit': 'mm', 'range': (0.0, 200.0)},
    'support.brace_azimuth_deg': {'tier': 'advanced', 'unit': 'deg', 'range': (-360.0, 360.0)},
    'support.allow_part_to_part': {'tier': 'advanced', 'risk': 'caution'},
    'support.overhang_angle_deg': {'tier': 'simple', 'unit': 'deg', 'range': (1.0, 89.0)},
    'support.part_to_part_avoidance': {'tier': 'advanced', 'risk': 'caution', 'range': (0.0, 1.0)},
    'support.tree_supports': {'tier': 'advanced', 'risk': 'caution'},
    'support.contour_supports': {'tier': 'advanced', 'risk': 'caution'},
    'support.boundary_supports': {'tier': 'advanced', 'risk': 'caution'},
    'peel.enabled': {'tier': 'advanced', 'risk': 'uncalibrated'},
    'peel.max_angle_deg': {'tier': 'advanced', 'risk': 'uncalibrated', 'unit': 'deg'},
    'peel.area_threshold_mm2': {'tier': 'advanced', 'risk': 'uncalibrated', 'unit': 'mm²'},
    'peel.reference_lift_speed': {'tier': 'expert', 'risk': 'uncalibrated'},
    'repair.aggressiveness': {'tier': 'simple'},
    'repair.seal_voids': {'tier': 'simple'},
    'repair.min_orifice_area_mm2': {'tier': 'simple', 'unit': 'mm²', 'range': (0.0, 100.0)},
    'repair.voxel_size_mm': {'tier': 'expert', 'unit': 'mm', 'risk': 'caution', 'range': (0.0, 5.0)},
    'repair.smooth_iterations': {'tier': 'expert', 'risk': 'caution', 'range': (0, 50)},
    'repair.min_void_volume_mm3': {'tier': 'expert', 'unit': 'mm³', 'risk': 'caution'},
    'repair.remove_tiny_features': {'tier': 'expert', 'risk': 'caution'},
    'repair.weld_tolerance_mm': {'tier': 'expert', 'unit': 'mm', 'risk': 'caution', 'range': (0.0, 0.05)},
    'assembly.clip_to_build_volume': {'tier': 'simple', 'risk': 'caution'},
    'assembly.require_raster_parity': {'tier': 'expert', 'risk': 'caution'},
    'resources.memory_gib': {'tier': 'advanced', 'unit': 'GiB', 'range': (0.25, 1024.0)},
    'resources.workers': {'tier': 'advanced', 'range': (0, 32)},
    'resources.acceleration': {'tier': 'advanced'},
    'resources.worker_policy': {'tier': 'advanced'},
    'resources.cuda_device': {'tier': 'advanced', 'range': (0, 16)},
    'resources.post_slice_hook': {'tier': 'expert', 'risk': 'caution'},
    'resources.scratch_dir': {'tier': 'expert'},
    'hollow.enabled': {'tier': 'advanced', 'risk': 'caution'},
    'hollow.voxel_size_mm': {'tier': 'expert', 'unit': 'mm', 'risk': 'caution'},
    'schema_version': {'tier': 'expert', 'risk': 'caution'},
}

# Orientation ranking weights live in geometry.py, not DEFAULTS. Expert mode
# surfaces them as read-only guidance so the uncalibrated claim is visible.
ORIENTATION_WEIGHT_DESCRIPTORS = (
    ('SUPPORT_WEIGHT', 'downward area', 'uncalibrated orientation ranking weight'),
    ('SUPPORT_VOLUME_WEIGHT', 'support volume', 'uncalibrated orientation ranking weight'),
    ('HEIGHT_WEIGHT', 'height', 'uncalibrated orientation ranking weight'),
    ('PEEL_WEIGHT', 'peel / footprint', 'uncalibrated orientation ranking weight'),
    ('TRAPPED_WEIGHT', 'trapped resin', 'uncalibrated orientation ranking weight'),
    ('CAVITY_WEIGHT', 'enclosed cavity', 'uncalibrated orientation ranking weight'),
    ('ACCESS_WEIGHT', 'access', 'uncalibrated orientation ranking weight'),
    ('STABILITY_WEIGHT', 'stability', 'uncalibrated orientation ranking weight'),
)


@dataclass(frozen=True)
class SettingDescriptor:
    path: str
    value_type: str
    unit: str | None
    range: tuple[float, float] | None
    tier: str
    risk: str
    label: str
    tooltip: str
    choices: tuple[str, ...] | None = None

    @property
    def section(self) -> str:
        return self.path.split('.', 1)[0]

    @property
    def key(self) -> str:
        return self.path.split('.', 1)[-1]


def _humanise(key: str) -> str:
    return key.replace('_', ' ').replace('.', ' / ')


def _infer_type(value) -> str:
    if isinstance(value, bool):
        return 'bool'
    if isinstance(value, int) and not isinstance(value, bool):
        return 'int'
    if isinstance(value, float):
        return 'float'
    if isinstance(value, list):
        return 'list'
    if value is None:
        return 'optional_str'
    return 'str'


def _default_range(value_type: str, value) -> tuple[float, float] | None:
    if value_type == 'int':
        return (0, 10_000)
    if value_type == 'float':
        return (-1_000.0, 1_000.0)
    return None


def _tooltip(path: str, label: str, risk: str, flag: str | None) -> str:
    """Hover text for one generated row.

    The explanation comes from the shared :data:`~voxelmill.gui.helptext.HELP`
    table, which the dedicated editors read too. Without it a row's tooltip
    was its own label plus the config key, which told a reader nothing they
    could not already see.
    """
    explanation = help_for(path)
    parts = [label, f'Config key: {path}.']
    if explanation:
        parts.insert(1, explanation)
    if flag:
        parts.append(f'Set it from the command line with {flag}, or with --set {path}=VALUE.')
    else:
        parts.append(f'Set it from the command line with --set {path}=VALUE.')
    if risk == 'uncalibrated':
        parts.append('Uncalibrated: values are guidance only and are not fitted to measured prints.')
    elif risk == 'caution':
        parts.append('Caution: changing this can spoil a print or invalidate prior validation.')
    return ' '.join(parts)


def _walk(obj, prefix=''):
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f'{prefix}.{key}' if prefix else str(key)
            yield from _walk(value, path)
    else:
        yield prefix, obj


def build_descriptors(defaults=None) -> tuple[SettingDescriptor, ...]:
    """One descriptor per DEFAULTS leaf, including nested ``printer.motion``."""
    source = defaults if defaults is not None else DEFAULTS
    rows = []
    for path, value in _walk(source):
        if path.startswith('printer.motion.'):
            override = {
                'tier': 'expert',
                'risk': 'uncalibrated',
                'label': _humanise(path.split('.')[-1]),
            }
        else:
            override = dict(OVERRIDES.get(path, {}))
        value_type = override.get('value_type') or _infer_type(value)
        if path in ENUM_CHOICES:
            value_type = 'enum'
        tier = override.get('tier', 'advanced')
        risk = override.get('risk', 'normal')
        unit = override.get('unit')
        span = override.get('range', _default_range(value_type, value))
        label = override.get('label') or _humanise(path.split('.')[-1])
        choices = ENUM_CHOICES.get(path)
        flag = CLI_FLAGS.get(path)
        tip = _tooltip(path, label, risk, flag)
        rows.append(SettingDescriptor(
            path=path,
            value_type=value_type,
            unit=unit,
            range=tuple(span) if span is not None else None,
            tier=tier,
            risk=risk,
            label=label,
            tooltip=tip,
            choices=tuple(choices) if choices is not None else None,
        ))
    return tuple(rows)


SETTINGS_DESCRIPTORS = build_descriptors()


def descriptors_for_tier(tier: str, sections=None) -> tuple[SettingDescriptor, ...]:
    rank = TIER_RANK[tier]
    rows = []
    for descriptor in SETTINGS_DESCRIPTORS:
        if TIER_RANK[descriptor.tier] > rank:
            continue
        if sections is not None and descriptor.section not in sections:
            continue
        rows.append(descriptor)
    return tuple(rows)


def get_path(settings: dict, path: str):
    node = settings
    for part in path.split('.'):
        node = node[part]
    return node


def set_path(settings: dict, path: str, value):
    parts = path.split('.')
    node = settings
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


RISK_COLORS = {
    'normal': '',
    'caution': '#b8860b',
    'uncalibrated': '#b00020',
}


class SettingsTableWidget(QtWidgets.QWidget):
    """Generated typed controls for a slice of the descriptor table."""

    value_changed = QtCore.Signal()

    def __init__(self, parent=None, *, sections=('process', 'support'), tier='advanced'):
        super().__init__(parent)
        self.setObjectName('settings_table')
        self._sections = tuple(sections)
        self._tier = tier
        self._widgets: dict[str, QtWidgets.QWidget] = {}
        self._labels: dict[str, QtWidgets.QLabel] = {}
        self._rows: dict[str, QtWidgets.QWidget] = {}
        self._form = QtWidgets.QFormLayout(self)
        self._form.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        self._rebuild()

    def set_tier(self, tier: str):
        if tier not in TIER_RANK:
            raise ValueError(tier)
        self._tier = tier
        self._apply_visibility()

    def set_sections(self, sections):
        self._sections = tuple(sections)
        self._rebuild()

    def _include(self, descriptor: SettingDescriptor) -> bool:
        if descriptor.path.startswith('printer.motion.'):
            return 'motion' in self._sections or 'printer.motion' in self._sections
        return descriptor.section in self._sections

    def _rebuild(self):
        while self._form.rowCount():
            self._form.removeRow(0)
        self._widgets.clear()
        self._labels.clear()
        self._rows.clear()
        for descriptor in SETTINGS_DESCRIPTORS:
            if not self._include(descriptor):
                continue
            widget = self._make_widget(descriptor)
            label = QtWidgets.QLabel(self._row_label(descriptor))
            color = RISK_COLORS.get(descriptor.risk, '')
            if color:
                label.setStyleSheet(f'color: {color};')
            label.setToolTip(descriptor.tooltip)
            widget.setToolTip(descriptor.tooltip)
            widget.setObjectName(f'setting_{descriptor.path.replace(".", "_")}')
            self._widgets[descriptor.path] = widget
            self._labels[descriptor.path] = label
            self._form.addRow(label, widget)
            self._rows[descriptor.path] = widget
        self._apply_visibility()

    @staticmethod
    def _row_label(descriptor: SettingDescriptor) -> str:
        text = descriptor.label
        if descriptor.unit:
            text = f'{text} ({descriptor.unit})'
        return text

    def _make_widget(self, descriptor: SettingDescriptor) -> QtWidgets.QWidget:
        if descriptor.value_type == 'bool':
            box = QtWidgets.QCheckBox()
            box.toggled.connect(lambda _=False: self.value_changed.emit())
            return box
        if descriptor.value_type == 'enum' and descriptor.choices:
            box = QtWidgets.QComboBox()
            box.addItems(list(descriptor.choices))
            box.currentTextChanged.connect(lambda _=None: self.value_changed.emit())
            return box
        if descriptor.value_type == 'int':
            box = QtWidgets.QSpinBox()
            low, high = descriptor.range or (0, 10_000)
            box.setRange(int(low), int(high))
            box.valueChanged.connect(lambda _=0: self.value_changed.emit())
            return box
        if descriptor.value_type == 'float':
            box = QtWidgets.QDoubleSpinBox()
            low, high = descriptor.range or (-1_000.0, 1_000.0)
            box.setRange(float(low), float(high))
            box.setDecimals(4)
            box.valueChanged.connect(lambda _=0.0: self.value_changed.emit())
            return box
        # lists, optional strings, free strings: JSON-ish line edit
        box = QtWidgets.QLineEdit()
        box.editingFinished.connect(self.value_changed.emit)
        return box

    def _apply_visibility(self):
        rank = TIER_RANK[self._tier]
        for path, widget in self._widgets.items():
            descriptor = next(d for d in SETTINGS_DESCRIPTORS if d.path == path)
            visible = self._include(descriptor) and TIER_RANK[descriptor.tier] <= rank
            self._labels[path].setVisible(visible)
            widget.setVisible(visible)

    def load_settings(self, settings: dict):
        for path, widget in self._widgets.items():
            try:
                value = get_path(settings, path)
            except (KeyError, TypeError):
                continue
            self._set_widget(widget, value)

    def apply_to_settings(self, settings: dict) -> dict:
        """Write visible typed fields into a deep copy of ``settings``."""
        from copy import deepcopy
        target = deepcopy(settings)
        rank = TIER_RANK[self._tier]
        for descriptor in SETTINGS_DESCRIPTORS:
            widget = self._widgets.get(descriptor.path)
            if widget is None or not widget.isVisible():
                continue
            if TIER_RANK[descriptor.tier] > rank:
                continue
            set_path(target, descriptor.path, self._get_widget(widget, descriptor))
        return target

    @staticmethod
    def _set_widget(widget, value):
        if isinstance(widget, QtWidgets.QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QtWidgets.QComboBox):
            text = str(value)
            index = widget.findText(text)
            if index >= 0:
                widget.setCurrentIndex(index)
        elif isinstance(widget, QtWidgets.QSpinBox):
            widget.setValue(int(value))
        elif isinstance(widget, QtWidgets.QDoubleSpinBox):
            widget.setValue(float(value))
        elif isinstance(widget, QtWidgets.QLineEdit):
            if isinstance(value, (list, dict)):
                import json
                widget.setText(json.dumps(value))
            elif value is None:
                widget.setText('')
            else:
                widget.setText(str(value))

    @staticmethod
    def _get_widget(widget, descriptor: SettingDescriptor):
        if isinstance(widget, QtWidgets.QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QtWidgets.QComboBox):
            return widget.currentText()
        if isinstance(widget, QtWidgets.QSpinBox):
            return int(widget.value())
        if isinstance(widget, QtWidgets.QDoubleSpinBox):
            return float(widget.value())
        text = widget.text().strip()
        if descriptor.value_type == 'optional_str':
            return text or None
        if descriptor.value_type == 'list':
            import json
            return json.loads(text) if text else []
        return text


class OrientationWeightsWidget(QtWidgets.QWidget):
    """Read-only expert surface for uncalibrated orientation ranking weights."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('orientation_weights')
        form = QtWidgets.QFormLayout(self)
        from .. import geometry
        for name, label, tip in ORIENTATION_WEIGHT_DESCRIPTORS:
            value = getattr(geometry, name)
            field = QtWidgets.QLabel(f'{value:g}')
            field.setObjectName(f'orientation_weight_{name}')
            field.setToolTip(
                f'{tip}. Constant {name} in voxelmill.geometry. '
                'Uncalibrated: not fitted to measured prints.')
            form.addRow(label, field)
