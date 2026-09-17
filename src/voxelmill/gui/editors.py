"""Dedicated printer, resin/process, and support parameter editors."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile

from PySide6 import QtCore, QtWidgets

from ..config import (BASE_TYPES, MODEL_ANCHOR_SHAPES, SMALL_PILLAR_MODES,
                      SMALL_PILLAR_SHAPES, TIP_SHAPES, resolve_settings, validate_settings)
from ..contracts import VoxelMillError
from .. import profiles
from ..presets import apply_preset, save_preset
from ..support_example import support_example
from .jobs import JobRunner


SECTIONS = {'printer': ('printer',), 'resin': ('resin', 'process'), 'support': ('support',)}
FILTERS = {'printer': 'Printer profile (*.ptr)', 'resin': 'Resin profile (*.res)',
           'support': 'Support preset (*.json)'}
ENUMS = {('support', 'base_type'): BASE_TYPES,
         ('support', 'model_anchor_shape'): MODEL_ANCHOR_SHAPES,
         ('support', 'small_pillar_mode'): SMALL_PILLAR_MODES,
         ('support', 'small_pillar_shape'): SMALL_PILLAR_SHAPES,
         ('support', 'tip_shape'): TIP_SHAPES}
HELP = {
    'allow_part_to_part': 'Allow supports to start on model material. Disable to require a route to the plate.',
    'drop_attached_unroutable': 'After routing, drop contacts that will not fit if they already have material one printer layer below. Island and manual contacts are never dropped.',
    'tree_supports': 'Cluster nearby vertical plate supports onto one trunk with branches. Off keeps independent pillars.',
    'contour_supports': 'Also sample the outer perimeter of downward-face clusters, not just face centroids and interior lattices.',
    'boundary_supports': 'Also sample open mesh boundary edges (crop cuts). Closed solids add none.',
    'tree_cluster_mm': 'Tree cluster radius. 0 uses twice the support spacing.',
    'part_to_part_avoidance': '0: compare routes equally by length. 1: prefer any available plate route. '
                              'At 0.5 a model route must be less than half the plate route length.',
    'brace_diameter_mm': 'Brace diameter. 0 derives half the thinner adjoining pillar diameter.',
    'brace_max_distance_mm': 'Maximum neighbour distance. 0 derives 1.5 times support spacing.',
    'brace_spacing_mm': 'Vertical interval between braces. 0 derives slenderness limit times pillar diameter.',
    'brace_start_height_mm': 'First brace height above plate. 0 derives the same height as the default interval.',
    'tip_base_diameter_mm': 'Tip cone lower diameter. 0 uses the nominal pillar diameter.',
    'tip_shape': 'Top contact shape. Cone tapers from the tip-base diameter to the contact diameter; cylinder keeps the contact diameter.',
    'break_point_diameter_mm': 'Optional ball at the top contact for a controlled snap-off. 0 disables it. When set it must be at least the contact diameter and must fit in the tip length plus penetration.',
    'model_anchor_shape': 'Bottom connector from the lower model surface to the middle pillar. Cone tapers to the middle radius; cylinder keeps one diameter.',
    'model_anchor_length_mm': 'Bottom connector height above the lower model surface. 0 keeps the direct anchor route.',
    'model_anchor_diameter_mm': 'Bottom connector diameter at its buried endpoint. 0 derives it from the selected middle pillar.',
    'model_anchor_penetration_mm': 'Depth below the sampled model surface for a model anchor. Independent of the top contact penetration.',
    'small_pillar_mode': 'Where the small-pillar geometry applies: middle segments or model-anchor connectors.',
    'small_pillar_shape': 'Buried end shape: cone tapers to a point; cylinder keeps the shaft diameter. Both use independent upper/lower depths.',
    'small_pillar_upper_depth_mm': 'Penetration into the upper model surface. Requires model mode; 0 ends at the surface.',
    'small_pillar_lower_depth_mm': 'Penetration into the lower model surface. Requires model mode; 0 ends at the surface.',
    'base_skate_length_mm': 'Skate capsule total length. 0 derives the length from the touch diameter.',
    'base_rotation_deg': 'Skate orientation around each foot, or grid orientation, in degrees.',
    'base_strut_width_mm': 'Skeleton/grid strut width. 0 derives the width from the nominal pillar diameter.',
    'base_cell_size_mm': 'Lattice cell spacing, center to center, in mm. Grid and hex share it.',
    'base_edge_slope_deg': 'Base wall angle from the plate, widest where it touches. '
                           '0 keeps a vertical wall; the taper is quantised to printed layers.',
    'raft_slope_deg': 'Plate outer-perimeter wall from the plate, for a putty knife. 0 is a near-vertical rim.',
    'base_type': 'grid is the default porous lattice. plate is a solid hull with a 30 degree outer bevel. none is feet only.',
    'base_touch_diameter_mm': 'Pad and per-foot footprint diameter. 0 derives it from the raft expansion.',
    'base_thickness_mm': 'Pad and per-foot footprint thickness. 0 derives it from raft thickness.',
    'density_g_cm3': 'Resin density. 0 means unknown; no weight is estimated.',
    'cost_per_liter': 'Resin price per liter in the chosen currency. 0 means unknown.',
    'build_mm': 'Build width, depth, height in mm, as a JSON array. Width/depth must match pixels times pitch.',
    'pixels': 'LCD width and height in pixels, as a JSON array of integers.',
    'pixel_pitch_mm': 'Pixel width and height in mm, as a JSON array.',
    'layer_height_range_mm': 'Hard minimum and maximum layer height in mm, as a JSON array.',
    'motion': 'Reference motion fields as a JSON object; these are not a calibrated timing or strength model.',
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
                       'The example uses four fixed contacts; project contact selection is separate.'}[kind]
        intro = QtWidgets.QLabel(description)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        splitter = QtWidgets.QSplitter()
        layout.addWidget(splitter, 1)
        tabs = QtWidgets.QTabWidget()
        splitter.addWidget(tabs)
        for section in SECTIONS[kind]:
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            holder = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(holder)
            for key, value in self.draft[section].items():
                field = self._field(section, key, value)
                self.fields[(section, key)] = field
                label = key.replace('_', ' ').capitalize()
                form.addRow(label, field)
            scroll.setWidget(holder)
            tabs.addTab(scroll, section.title())
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
            field.addItems(ENUMS[(section, key)])
            field.setCurrentText(value)
            field.currentTextChanged.connect(self._changed)
        else:
            field = QtWidgets.QLineEdit(value if isinstance(value, str) else json.dumps(value))
            field.textChanged.connect(self._changed)
        field.setObjectName(f'{section}.{key}')
        field.setToolTip(f'{section}.{key} — CLI: --set {section}.{key}=VALUE\n' + HELP.get(key, ''))
        return field

    def settings(self):
        settings = deepcopy(self.draft)
        for (section, key), field in self.fields.items():
            old = self.draft[section][key]
            if isinstance(field, QtWidgets.QCheckBox):
                value = field.isChecked()
            elif isinstance(field, QtWidgets.QComboBox):
                value = field.currentText()
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
            self.jobs.submit('example', lambda token, progress: support_example(
                settings, height, cancel=token))

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
        self.status.setText(f"Example: {metrics['contacts_routed']}/4 routed, "
                            f"{metrics['routing']['model_anchor']} model anchors, "
                            f"{metrics['braces']} braces, "
                            f"{metrics['braces_collision_rejected']} braces blocked by model. "
                            'Geometry illustration; no print validation or strength proof.')

    def _populate(self, settings):
        self._loading = True
        self.draft = deepcopy(settings)
        for (section, key), field in self.fields.items():
            value = settings[section][key]
            if isinstance(field, QtWidgets.QCheckBox):
                field.setChecked(value)
            elif isinstance(field, QtWidgets.QComboBox):
                field.setCurrentText(value)
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
