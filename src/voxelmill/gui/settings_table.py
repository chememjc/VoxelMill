"""Declarative settings descriptors and a generated typed settings form.

One descriptor per leaf in ``config.DEFAULTS``, presented as declared in
``settings_schema.FIELDS``. The Setup page renders from this table so CLI
``--set`` paths, tooltips, choices and visibility tiers stay aligned with the
validator.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore, QtWidgets

from .helptext import help_for
from ..config import DEFAULTS
from ..settings_schema import FIELDS

TIERS = ('simple', 'advanced', 'expert')
TIER_RANK = {name: index for index, name in enumerate(TIERS)}
RISKS = ('normal', 'caution', 'uncalibrated')

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


def _field_range(field, value_type: str, value) -> tuple[float, float] | None:
    """Slider bounds the validator accepts, when no explicit range is declared.

    The generic fallback offered negative diameters and zero angles that
    validation then refused; bounds now come from the field's own rule.
    """
    fallback = _default_range(value_type, value)
    if field is None or fallback is None or field.kind not in ('number', 'int') or field.count:
        return fallback
    step = 1 if value_type == 'int' else 0.001
    low = field.minimum + step if field.positive else field.minimum
    high = fallback[1]
    if field.maximum is not None:
        high = min(high, field.maximum)
    if field.below is not None:
        high = min(high, field.below - step)
    return (type(fallback[0])(low), type(fallback[1])(high))


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
        field = FIELDS.get(path)
        if path.startswith('printer.motion.'):
            override = {
                'tier': 'expert',
                'risk': 'uncalibrated',
                'label': _humanise(path.split('.')[-1]),
            }
        elif field is not None:
            override = {name: getattr(field, name)
                        for name in ('tier', 'risk', 'unit', 'label', 'value_type', 'range')
                        if getattr(field, name) is not None}
        elif path == 'schema_version':
            # Not a setting a user edits; shown only in expert mode.
            override = {'tier': 'expert', 'risk': 'caution'}
        else:
            override = {}
        value_type = override.get('value_type') or _infer_type(value)
        # Text choices become dropdowns, from the same list the validator uses.
        # Integer choices (antialias levels) stay spin boxes.
        choices = (field.choices if field is not None and field.kind == 'choice'
                   and all(isinstance(c, str) for c in field.choices) else None)
        if choices is not None:
            value_type = 'enum'
        tier = override.get('tier', 'advanced')
        risk = override.get('risk', 'normal')
        unit = override.get('unit')
        span = override.get('range', _field_range(field, value_type, value))
        label = override.get('label') or _humanise(path.split('.')[-1])
        flag = field.flag if field is not None else None
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
