"""Dedicated printer, resin/process, and support parameter editors."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile

from PySide6 import QtCore, QtWidgets

from ..config import (BRACE_DESTINATIONS, BRACE_PATTERNS, BASE_TYPES, MODEL_ANCHOR_SHAPES, SMALL_PILLAR_MODES,
                      SMALL_PILLAR_SHAPES, TIP_SHAPES, resolve_settings, validate_settings)
from ..contracts import VoxelMillError
from .. import profiles
from ..presets import apply_preset, save_preset
from ..support_example import support_example
from .helptext import help_for
from .jobs import JobRunner


SECTIONS = {'printer': ('printer',), 'resin': ('resin', 'process'), 'support': ('support',)}
FILTERS = {'printer': 'Printer profile (*.ptr)', 'resin': 'Resin profile (*.res)',
           'support': 'Support preset (*.json)'}
ENUMS = {('support', 'brace_destination'): BRACE_DESTINATIONS,
         ('support', 'brace_pattern'): BRACE_PATTERNS,
         ('support', 'base_type'): BASE_TYPES,
         ('support', 'model_anchor_shape'): MODEL_ANCHOR_SHAPES,
         ('support', 'small_pillar_mode'): SMALL_PILLAR_MODES,
         ('support', 'small_pillar_shape'): SMALL_PILLAR_SHAPES,
         ('support', 'tip_shape'): TIP_SHAPES}


#: Plain-language labels for the anchor and thin-pillar keys. The generated
#: "Model anchor length mm" reads like the config key it came from and says
#: nothing about what the number measures; these say where it is measured.
ANCHOR_LABELS = {
    'allow_part_to_part': 'Allow anchoring on the model',
    'part_to_part_avoidance': 'Prefer plate routes (0 = never, 1 = always)',
    'model_anchor_shape': 'Bottom connector shape',
    'model_anchor_length_mm': 'Bottom connector height above surface (mm)',
    'model_anchor_diameter_mm': 'Bottom connector buried diameter (mm)',
    'model_anchor_penetration_mm': 'Bottom connector depth into surface (mm)',
    'small_pillar_mode': 'Applies to',
    'small_pillar_diameter_mm': 'Thin pillar diameter (mm)',
    'small_pillar_max_length_mm': 'Use thin pillar up to this length (mm)',
    'small_pillar_shape': 'Buried end shape',
    'small_pillar_upper_depth_mm': 'Depth into upper surface (mm)',
    'small_pillar_lower_depth_mm': 'Depth into lower surface (mm)',
}

#: Shown above a tab's fields. The thin-pillar note exists because these keys
#: used to sit under "Part-to-part" while, in middle mode, they apply to every
#: pillar -- which is a large part of why the group read as unclear.
GROUP_NOTES = {
    'Part-to-part anchors': (
        'How a support attaches when it lands on the model instead of the plate. '
        'Bottom to top a part-to-part support is: a bottom connector buried in the '
        'lower body, then the middle pillar, then the tip at the contact. The fields '
        'here describe only that bottom connector. The lower surface height comes '
        'from the analysis raster, so it is as exact as that pitch and no more.'),
    'Thin pillars': (
        'A second, thinner pillar class for short runs. Diameter and maximum length '
        'must both be set, or the class is off. In middle mode it applies to the '
        'straight middle segment of any pillar short enough, part-to-part or not; '
        'in model mode it applies only to a connector bridging a short '
        'model-to-model gap, and the two depth fields take effect.'),
}


BRACE_LABELS = {
    'auto_bracing': 'Enable bracing',
    'brace_spacing_mm': 'Vertical brace spacing (mm)',
    'brace_max_distance_mm': 'Support-to-support reach (mm; 0 = auto)',
    'brace_max_length_mm': 'Maximum branch length (mm)',
    'brace_diameter_mm': 'Branch diameter (mm; 0 = auto)',
    'brace_destination': 'Brace destinations',
    'brace_branches_per_node': 'Connections per node (1–8)',
    'brace_angle_deg': 'Branch angle from horizontal (°)',
    'brace_pattern': 'Bracing pattern',
    'brace_min_height_mm': 'Minimum origin height (mm)',
    'brace_azimuth_deg': 'Fan / alternating rotation (°)',
    'brace_model_pillars': 'Allow braces to join pillars that stand on the model',
}
BRACE_CHOICE_LABELS = {
    'brace_destination': {'supports': 'Supports only', 'base': 'Base only', 'both': 'Supports or base'},
    'brace_pattern': {'single': 'Single diagonals', 'alternating': 'Alternating diagonals', 'x': 'X bracing'},
}


def load_component(kind, path, settings):
    """Load the same portable files the CLI accepts, preserving unrelated state."""
    if kind == 'support':
        return apply_preset(settings, path)
    if kind == 'printer':
        overrides = {key: deepcopy(value) for key, value in settings.items()
                     if key not in ('schema_version', 'printer')}
        return resolve_settings(profiles.resolve_profile_path(path, 'printer'), None, overrides)
    # Resolve resin blocks against the actual machine, including its id and
    # layer limits. A bare resolve_settings(None, resin) would use the defaults.
    with tempfile.TemporaryDirectory(prefix='voxelmill-editor-') as directory:
        printer = Path(directory) / 'current.ptr'
        printer.write_text(profiles.dumps_printer_profile(settings), encoding='utf-8')
        return resolve_settings(printer, profiles.resolve_profile_path(path, 'resin'))


class ConfigurationEditor(QtWidgets.QDialog):
    """Validated draft fields, portable file IO, and undoable Apply.

    Closing discards unapplied edits. The support example runs in a cancellable
    worker; edits invalidate its result before a replacement is submitted.
    """
    settings_applied = QtCore.Signal(object)

    def __init__(self, document, kind, parent=None, *, headless=False):
        super().__init__(parent)
        self.document, self.kind = document, kind
        self.draft = deepcopy(document.settings)
        self.applied = None
        self.example = None
        self.fields = {}
        self.headless = headless
        self._started = False
        self._loading = False
        self.setWindowTitle(f'{kind.title()} editor')
        self.resize(1100 if kind == 'support' else 820, 760)
        layout = QtWidgets.QVBoxLayout(self)
        description = {'printer': 'Mars 5 Ultra hardware, build volume and limits. '
                       'Printer saves contain hardware only.',
                       'resin': 'Resin properties and exposure process for the current printer. '
                       'A resin save also includes the current support settings.',
                       'support': 'Support dimensions, routing, bases and bracing. '
                       'Choose an attachment array, a part-to-part gap, or the showcase that shows '
                       'every route kind at once. Project contact selection is separate.'}[kind]
        intro = QtWidgets.QLabel(description)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter, 1)
        tabs = QtWidgets.QTabWidget()
        splitter.addWidget(tabs)
        self.tabs = tabs
        if kind == 'support':
            brace_keys = list(BRACE_LABELS)
            # Two groups, not one. "small_pillar_*" in middle mode applies to
            # every pillar, so filing it under part-to-part misdescribed it.
            thin_keys = [key for key in self.draft['support']
                         if key.startswith('small_pillar_')]
            anchor_keys = [key for key in self.draft['support']
                           if key.startswith(('model_anchor_', 'part_to_part_'))
                           or key == 'allow_part_to_part']
            grouped = brace_keys + anchor_keys + thin_keys
            groups = [('Bracing', 'support', brace_keys),
                      ('Pillars, tips and bases', 'support', [key for key in self.draft['support']
                       if key not in grouped]),
                      ('Part-to-part anchors', 'support', anchor_keys),
                      ('Thin pillars', 'support', thin_keys)]
        else:
            groups = [(section.title(), section, list(self.draft[section])) for section in SECTIONS[kind]]
        for title, section, keys in groups:
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            holder = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(holder)
            note = GROUP_NOTES.get(title)
            if note:
                caption = QtWidgets.QLabel(note)
                caption.setWordWrap(True)
                form.addRow(caption)
            for key in keys:
                field = self._field(section, key, self.draft[section][key])
                self.fields[(section, key)] = field
                label = (BRACE_LABELS.get(key) or ANCHOR_LABELS.get(key)
                         or key.replace('_', ' ').capitalize())
                form.addRow(label, field)
            scroll.setWidget(holder)
            tabs.addTab(scroll, title)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.viewport = None
        self.scene = None
        self.jobs = JobRunner(self, max_threads=1)
        self.jobs.completed.connect(self._preview_finished)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh_preview)
        if kind in ('printer', 'support'):
            from .viewport import Scene, Viewport
            right = QtWidgets.QWidget()
            right_layout = QtWidgets.QVBoxLayout(right)
            if kind == 'support':
                self.example_layout = QtWidgets.QComboBox()
                self.example_layout.addItem('Attachment array (pillars / trees / bracing)', 'array')
                self.example_layout.addItem('Part-to-part gap (lower and upper model)', 'part-to-part')
                self.example_layout.addItem('Showcase (every support kind at once)', 'showcase')
                self.example_layout.setToolTip(
                    'Array and part-to-part obey this draft exactly, so a dimension edit is '
                    'comparable before and after; part-to-part needs part-to-part routing '
                    'enabled before it shows anything. Showcase forces the settings each route '
                    'kind needs and lists them under the picture.')
                self.example_layout.currentIndexChanged.connect(self._changed)
                right_layout.addWidget(self.example_layout)
                self.model_anchor_demo = QtWidgets.QPushButton('Show part-to-part supports')
                self.model_anchor_demo.setToolTip('Select the model gap, enable part-to-part routing and set plate avoidance to 0 in this draft.')
                self.model_anchor_demo.clicked.connect(self._show_model_anchors)
                right_layout.addWidget(self.model_anchor_demo)
                controls = QtWidgets.QHBoxLayout()
                controls.addWidget(QtWidgets.QLabel('Example contact height (mm)'))
                self.height = QtWidgets.QDoubleSpinBox()
                self.height.setRange(3, 160)
                self.height.setValue(20)
                self.height.valueChanged.connect(self._changed)
                controls.addWidget(self.height)
                right_layout.addLayout(controls)
            if headless:
                self.scene = Scene()
            else:
                self.viewport = Viewport()
                self.scene = self.viewport.scene
                right_layout.addWidget(self.viewport, 1)
            splitter.addWidget(right)
            splitter.setSizes([480, 620])
        layout.addWidget(self.status)
        buttons = QtWidgets.QHBoxLayout()
        for label, slot in [('Load...', self.load_file), ('Save as...', self.save_file),
                            ('Apply to project', self.apply), ('Close', self.reject)]:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        if kind == 'support':
            export = QtWidgets.QPushButton('Export example STL...')
            export.clicked.connect(self.export_example)
            buttons.addWidget(export)
        layout.addLayout(buttons)
        self._changed()

    def _field(self, section, key, value):
        if type(value) is bool:
            field = QtWidgets.QCheckBox()
            field.setChecked(value)
            field.toggled.connect(self._changed)
        elif (section, key) in ENUMS:
            field = QtWidgets.QComboBox()
            for choice in ENUMS[(section, key)]:
                field.addItem(BRACE_CHOICE_LABELS.get(key, {}).get(choice, choice), choice)
            field.setCurrentIndex(field.findData(value))
            field.currentTextChanged.connect(self._changed)
        else:
            field = QtWidgets.QLineEdit(value if isinstance(value, str) else json.dumps(value))
            field.textChanged.connect(self._changed)
        field.setObjectName(f'{section}.{key}')
        field.setToolTip(f'{section}.{key} — CLI: --set {section}.{key}=VALUE\n'
                         + (help_for(f'{section}.{key}') or ''))
        return field

    def settings(self):
        settings = deepcopy(self.draft)
        for (section, key), field in self.fields.items():
            old = self.draft[section][key]
            if isinstance(field, QtWidgets.QCheckBox):
                value = field.isChecked()
            elif isinstance(field, QtWidgets.QComboBox):
                value = field.currentData()
            elif isinstance(old, str):
                value = field.text()
            else:
                try:
                    value = json.loads(field.text())
                except ValueError as exc:
                    raise VoxelMillError('invalid_setting', f'{section}.{key}: enter valid JSON') from exc
            settings[section][key] = value
        return validate_settings(settings)

    def _changed(self, *_):
        if self._loading:
            return
        self.jobs.invalidate()
        self.example = None
        if self.scene is not None:
            self.scene.clear()
            if self.viewport is not None and self._started:
                self.viewport.render()
        try:
            self.settings()
        except VoxelMillError as error:
            self.timer.stop()
            self.status.setText(str(error))
            return
        self.status.setText('Valid settings. Preview updating...' if self.scene else 'Valid settings.')
        if self.scene is not None:
            self.timer.start()

    def refresh_preview(self):
        self.timer.stop()
        try:
            settings = self.settings()
        except VoxelMillError as error:
            self.status.setText(str(error))
            return
        if self.kind == 'printer':
            self.scene.show_build_volume(settings)
            if self.viewport is not None and self._started:
                self.viewport.reset_camera()
            self.status.setText('Build volume shown to scale; front bottom edge is green.')
        elif self.kind == 'support':
            height = self.height.value()
            example_layout = self.example_layout.currentData()
            self.jobs.submit('example', lambda token, progress: support_example(
                settings, height, layout=example_layout, cancel=token))

    def _preview_finished(self, result):
        if not result.ok:
            if result.error:
                self.status.setText(result.error['message'])
            return
        self.example = result.value
        for role, triangles in self.example['triangles'].items():
            self.scene.set_mesh(role, triangles, opacity=.4 if role == 'model' else 1)
        if self.viewport is not None and self._started:
            self.viewport.reset_camera()
        metrics = self.example['metrics']
        categories = self.example.get('categories') or {}
        kinds = ', '.join(f'{count} {kind.replace("_", " ")}'
                          for kind, count in categories.items() if count)
        overrides = self.example.get('overrides') or {}
        forced = (' Forced for this preview: '
                  + ', '.join(f'{key}={value}' for key, value in overrides.items())
                  + '.') if overrides else ''
        self.status.setText(f"Example: {metrics['contacts_routed']}/{len(self.example['contacts'])} routed"
                            + (f' -- {kinds}' if kinds else '')
                            + f", {metrics['braces_collision_rejected']} braces blocked by model. "
                            + self.example.get('hint', '') + forced
                            + ' Geometry illustration; no print validation or strength proof.')

    def _show_model_anchors(self):
        self._loading = True
        self.example_layout.setCurrentIndex(self.example_layout.findData('part-to-part'))
        self.fields['support', 'allow_part_to_part'].setChecked(True)
        self.fields['support', 'part_to_part_avoidance'].setText('0')
        self.tabs.setCurrentIndex(2)
        self._loading = False
        self._changed()

    def _populate(self, settings):
        self._loading = True
        self.draft = deepcopy(settings)
        for (section, key), field in self.fields.items():
            value = settings[section][key]
            if isinstance(field, QtWidgets.QCheckBox):
                field.setChecked(value)
            elif isinstance(field, QtWidgets.QComboBox):
                field.setCurrentIndex(field.findData(value))
            else:
                field.setText(value if isinstance(value, str) else json.dumps(value))
        self._loading = False
        self._changed()

    def load_file(self, path=None):
        if not path:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Load configuration', '', FILTERS[self.kind])
        if not path:
            return
        try:
            # Loading can recover an invalid draft; preserve the last valid
            # underlying settings rather than forcing users to repair each field.
            self._populate(load_component(self.kind, path, self.draft))
        except (VoxelMillError, OSError) as error:
            self.status.setText(str(error))

    def save_file(self, path=None):
        if not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save configuration', '', FILTERS[self.kind])
        if not path:
            return
        try:
            settings = self.settings()
            if self.kind == 'printer':
                payload = profiles.save_printer_profile(path, settings, hardware_only=True)
            elif self.kind == 'resin':
                payload = profiles.save_resin_profile(path, settings)
            else:
                payload = save_preset(path, Path(path).stem, settings['support'])
        except VoxelMillError as error:
            self.status.setText(str(error))
            return
        self.status.setText(f'Saved {path}')
        return payload

    def apply(self):
        try:
            settings = self.settings()
        except VoxelMillError as error:
            self.status.setText(str(error))
            return
        self.document.set_settings(settings)
        self.applied = deepcopy(settings)
        self.draft = deepcopy(settings)
        self.settings_applied.emit(self.applied)
        self.status.setText('Applied to project. Undo restores the previous settings.')
        return settings

    def export_example(self, path=None):
        if self.example is None:
            self.status.setText('Wait for a valid current example before exporting.')
            return
        if not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export illustrative example', '', 'STL (*.stl)')
        if not path:
            return
        from ..support_example import save_example
        try:
            save_example(path, self.example)
        except VoxelMillError as error:
            self.status.setText(str(error))
            return
        self.status.setText(f'Saved illustration {path}; it has not passed print validation.')
        return str(path)

    def showEvent(self, event):
        super().showEvent(event)
        if self.viewport is not None and not self._started:
            self._started = True
            self.viewport.start()

    def done(self, result):
        self.timer.stop()
        self.jobs.invalidate()
        self.jobs.wait(10000)
        super().done(result)


class PrinterEditorDialog(ConfigurationEditor):
    def __init__(self, document, parent=None, *, headless=False):
        super().__init__(document, 'printer', parent, headless=headless)


class ResinEditorDialog(ConfigurationEditor):
    def __init__(self, document, parent=None, *, headless=False):
        super().__init__(document, 'resin', parent, headless=headless)


class SupportEditorDialog(ConfigurationEditor):
    def __init__(self, document, parent=None, *, headless=False):
        super().__init__(document, 'support', parent, headless=headless)
