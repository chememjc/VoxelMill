"""Single-part preparation editor.

The window owns a :class:`Document` (decisions) and a :class:`JobRunner`
(everything expensive). Nothing heavy runs on the UI thread, every stage can be
canceled, and a result from a superseded generation is discarded rather than
drawn. Export refuses a failed validation unless the warned override is armed,
exactly as the CLI does, and a warned export keeps its diagnostics.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import vtkmodules.all as vtk
from PySide6 import QtCore, QtGui, QtWidgets

from ..contracts import VoxelMillError
from ..config import BASE_TYPES, resolve_settings
from . import services
from .document import Document
from .jobs import JobRunner
from .camera import CameraController, SHORTCUTS, VIEWS
from .faults import FaultView, fault_overlay
from .layerview import ISSUE_COLORS, LayerView
from .objects import TOOLS, AttachmentSettings, ObjectPanel, ToolSelector
from .appprefs import (DEFAULT_MOTION_MODE, install_hover_delay, load_preferences,
                       save_preferences)
from .notifications import NotificationBanner, NotificationCenter
from .viewport import Viewport
from .widgets import ReportParameterView, ZClipSlider, install_focused_wheel_filter

STAGES = ('place', 'model', 'supports', 'union', 'validate')
THEMES = ('system', 'light', 'dark')

#: The pose every part starts at on import: no rotation, centered, and the
#: default lift both ``Document`` and ``normalize_extra_model`` use. Motion in
#: "absolute" mode is measured from here rather than from the part's current
#: pose.
IMPORT_POSE = {'rotate': [0.0, 0.0, 0.0], 'center_offset': [0.0, 0.0], 'lift_mm': 5.0}

#: Check name -> menu label, in menu order. The name is what
#: ``services.run_print_checks`` expects in its ``checks`` argument.
PRINT_CHECK_LABELS = (
    ('islands', 'Islands'),
    ('enclosed_voids', 'Enclosed voids'),
    ('overhangs', 'Overhangs'),
    ('suction_cups', 'Suction cups'),
    ('drainage', 'Drainage'),
)

#: Diagnostic code -> readable label for the View menu's per-issue-type
#: visibility toggles. A code not listed here (a check added to the palette
#: after this menu was written) still gets a usable, title-cased label.
ISSUE_LABELS = {
    'raster_island': 'Islands',
    'enclosed_voids': 'Enclosed voids',
    'transient_trap': 'Transient traps',
    'growth_span': 'Growth span',
    'unsupported_overhang': 'Unsupported overhangs',
    'peel_risk': 'Suction cups',
    'drainage_bottleneck': 'Drainage bottlenecks',
}


def _issue_label(code):
    return ISSUE_LABELS.get(code, code.replace('_', ' ').title())


def application_icon() -> QtGui.QIcon:
    """Return the bundled dark full-color application icon.

    The SVG is kept in package data so this works from a source checkout,
    wheel, and the relocatable AppImage alike.  The PNG fallback is useful on
    Qt builds without the SVG image plugin.
    """
    icon_dir = Path(__file__).resolve().parents[1] / 'data' / 'icons'
    svg = icon_dir / 'voxelmill.svg'
    png = icon_dir / 'voxelmill.png'
    return QtGui.QIcon(str(svg if svg.is_file() else png))


def autosave_path() -> Path:
    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'voxelmill'
    return cache / 'autosave.voxmil'


def with_suffix_if_missing(path, suffix):
    """Append ``suffix`` only when the typed name has none of its own.

    A save dialog hands back exactly what the user typed; a bare name like
    ``part`` should get the format's extension, but a name that already ends
    in one -- even a different one, chosen on purpose -- must not be rewritten.
    """
    path = Path(path)
    return str(path) if path.suffix else str(path.with_suffix(suffix))


class _DockTabs(QtCore.QObject):
    """Tab-like facade over right-hand docks so existing callers keep working."""

    currentChanged = QtCore.Signal(int)

    def __init__(self, titles):
        super().__init__()
        self._titles = list(titles)
        self._index = 0
        self._docks: list[QtWidgets.QDockWidget] = []

    def bind(self, docks):
        self._docks = list(docks)
        for index, dock in enumerate(self._docks):
            dock.visibilityChanged.connect(
                lambda visible, i=index: self._on_visibility(i, visible))

    def count(self):
        return len(self._titles)

    def tabText(self, index):
        return self._titles[index]

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        index = int(index)
        if index < 0 or index >= len(self._docks):
            return
        self._index = index
        dock = self._docks[index]
        dock.show()
        dock.raise_()
        self.currentChanged.emit(index)

    def _on_visibility(self, index, visible):
        if visible:
            self._index = index
            self.currentChanged.emit(index)


class MainWindow(QtWidgets.QMainWindow):
    stage_changed = QtCore.Signal(str)

    def __init__(self, settings=None, source=None, headless=False):
        super().__init__()
        self.setWindowIcon(application_icon())
        self.setWindowTitle('VoxelMill')
        self.editor_preferences = load_preferences()
        self.document = Document(settings or resolve_settings(), source)
        # Nothing is attached on import: parts are positioned first, then
        # attachments are computed deliberately.  The CLI default is unchanged;
        # this is an editor decision, so it is applied to the document here
        # rather than to the resolved defaults every caller shares.
        self.document.settings['support']['automatic'] = False
        self.document.baseline_settings['support']['automatic'] = False
        self._attachment_state = 'none'
        self.jobs = JobRunner(self, max_threads=max(1, self.document.settings['resources']['workers']))
        self.jobs.progress.connect(self._on_progress)
        self.jobs.completed.connect(self._on_completed)
        self.jobs.stale.connect(lambda result: self.statusBar().showMessage(
            f'discarded stale {result.name} result', 3000))
        self.headless = headless
        self.allow_unresolved = False
        self.last_error = None
        self.placed = None
        self.scratch = None
        self.viewport = None
        self.scene = None
        self.goo_source = None
        self._requested_layer = None
        self.placement_fits = True
        self.placement_overflow_mm = [0.0, 0.0, 0.0]
        self._pending_export = None
        self._validation_path = None
        self._orientation_candidates = []
        self._orientation_candidates_context = None
        self.project_path = None
        self.visibility_tier = 'simple'
        self.theme_name = 'system'
        self.notifications = NotificationCenter(self)
        # D5: the island scan is cheap next to a full validation, so it runs
        # after every rebuild by default; the toggle exists for parts where
        # even a connectivity pass over every layer is slow.
        self.auto_island_check = True
        self.island_summary = None
        self.island_stale = False
        self._autosave_timer = QtCore.QTimer(self)
        self._autosave_timer.setInterval(5 * 60 * 1000)
        self._autosave_timer.timeout.connect(self._autosave)
        if not headless:
            self._autosave_timer.start()
        self._startup_source = source
        self._startup_completed = False
        self._island_check_show_report = True
        self._build_ui()
        install_focused_wheel_filter(QtWidgets.QApplication.instance())
        self.setAcceptDrops(True)
        # Modal dialogs and VTK Initialize() must wait until the window is
        # shown. On macOS a FreeCAD/wizard QMessageBox during construction,
        # or QVTKRenderWindowInteractor.Initialize() on a hidden widget,
        # leaves the main window never appearing after the prompt.
        if source:
            self.reload()

    def showEvent(self, event):
        """Restore a remembered layout, or give the docks a usable default width.

        The Setup page has always been wider than the width the splitter picks
        for it, so several rows opened behind a horizontal scroll bar. Docks
        cannot be resized meaningfully before the window has a size, which is
        why this happens here rather than during construction. That forced
        width is a sensible default, though, not a memory: a remembered
        geometry and dock layout wins when one was saved, and the default
        sizing only runs when there is nothing to restore.
        """
        super().showEvent(event)
        if getattr(self, '_docks_sized', False) or not self.panel_docks:
            return
        self._docks_sized = True
        if self._restore_window_layout():
            return
        self._apply_default_dock_sizes()

    def _apply_default_dock_sizes(self):
        """The built-in dock width, capped at a share of the window so the 3D
        view never disappears. This is what a first run gets, and what
        ``Reset layout`` puts back."""
        docks = list(self.panel_docks.values())
        # The dock's widget is a scroll area, whose own minimum is tiny. The
        # width that matters is the page inside it, plus room for the vertical
        # scroll bar that page will need.
        page = self.setup_dock.widget()
        inner = page.widget() if isinstance(page, QtWidgets.QScrollArea) else page
        bar = self.style().pixelMetric(QtWidgets.QStyle.PM_ScrollBarExtent) + 8
        wanted = max(560, inner.minimumSizeHint().width() + bar)
        width = max(360, min(wanted, int(self.width() * 0.55)))
        self.resizeDocks(docks, [width] * len(docks), QtCore.Qt.Horizontal)

    def _restore_window_layout(self):
        """Apply a remembered geometry and dock/toolbar layout, if one was saved.

        A corrupt or truncated value must not crash the editor: ``appprefs``
        already validated it decodes as base64, and ``restoreGeometry``/
        ``restoreState`` fail closed (return ``False``) on bytes that decode
        but are not a geometry/state blob, rather than raising.
        """
        restored = False
        geometry = self.editor_preferences.get('window_geometry')
        if geometry:
            try:
                data = QtCore.QByteArray.fromBase64(geometry.encode('ascii'))
                restored = bool(self.restoreGeometry(data)) or restored
            except Exception:
                pass
        state = self.editor_preferences.get('window_state')
        if state:
            try:
                data = QtCore.QByteArray.fromBase64(state.encode('ascii'))
                restored = bool(self.restoreState(data)) or restored
            except Exception:
                pass
        return restored

    def closeEvent(self, event):
        """Prompt on dirty documents, then remember geometry -- never headless."""
        if not self._confirm_discard_or_save():
            event.ignore()
            return
        if not self.headless:
            self._save_window_layout()
        super().closeEvent(event)

    def _confirm_discard_or_save(self):
        """Ask Save / Discard / Cancel when the document is dirty.

        Headless tests construct ``MainWindow(..., headless=True)`` and must
        not block on a modal, so headless is treated as Discard.
        """
        if not self.document.dirty:
            return True
        if self.headless:
            return True
        result = QtWidgets.QMessageBox.question(
            self, 'Unsaved changes',
            'The project has unsaved changes.',
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Save)
        if result == QtWidgets.QMessageBox.Cancel:
            return False
        if result == QtWidgets.QMessageBox.Save:
            return bool(self.save_project())
        return True

    def _save_window_layout(self):
        self.editor_preferences['window_geometry'] = bytes(self.saveGeometry().toBase64()).decode('ascii')
        self.editor_preferences['window_state'] = bytes(self.saveState().toBase64()).decode('ascii')
        save_preferences(self.editor_preferences)

    def reset_layout(self):
        """Discard the remembered layout and put the docks back the way they opened.

        For a user who has wedged a dock somewhere unusable, this is the only
        way out that does not involve hand-editing ``editor.json``.
        """
        self.editor_preferences['window_geometry'] = None
        self.editor_preferences['window_state'] = None
        if not self.headless:
            save_preferences(self.editor_preferences)
        docks = list(self.panel_docks.values())
        for dock in docks:
            dock.setFloating(False)
            self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        for other in docks[1:]:
            self.tabifyDockWidget(docks[0], other)
        docks[0].raise_()
        self.resize(1400, 900)
        self._apply_default_dock_sizes()
        self.statusBar().showMessage('layout reset to the default', 5000)

    # ---- drag and drop -------------------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and ObjectPanel._dropped_models(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and ObjectPanel._dropped_models(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        """Dropping STLs adds them; the first one opens if nothing is loaded."""
        paths = ObjectPanel._dropped_models(event.mimeData())
        if not paths:
            return super().dropEvent(event)
        event.acceptProposedAction()
        if self.document.source is None:
            self.open_stl(paths[0])
            paths = paths[1:]
        if paths:
            self.add_models(paths)

    # ---- construction --------------------------------------------------
    def _build_ui(self):
        if self.headless:
            from .viewport import Scene
            self.scene = Scene()
            left = QtWidgets.QWidget()
        else:
            self.viewport = Viewport()
            self.scene = self.viewport.scene
            self.viewport.picked.connect(self._on_pick)
            self.viewport.painted.connect(self._on_painted)
            self.viewport.paint_finished.connect(self._on_paint_finished)
            self.viewport.object_transformed.connect(self._on_object_transformed)
            self.viewport.object_selected.connect(self._on_object_selected)
            self.viewport.object_preview_transformed.connect(self._on_object_preview_transformed)
            self.viewport.object_deselected.connect(self._on_object_deselected)
            # object_transform_cancelled (Escape / right-click abandons a
            # gizmo drag) is being added alongside this fix in gizmo.py /
            # viewport.py; guard rather than crash if this file loads before
            # that signal exists.
            if hasattr(self.viewport, 'object_transform_cancelled'):
                self.viewport.object_transform_cancelled.connect(
                    lambda _index: self._clear_preview_transforms())
            left = self.viewport
        # The tool strip sits over the 3D view so the model-editing modes are
        # visible rather than only reachable as modifier-clicks.
        self.tools = ToolSelector(radius_mm=self.document.settings['support']['spacing_mm'])
        self.tools.tool_changed.connect(self._on_tool_changed)
        self.tools.radius_changed.connect(self._on_brush_radius_changed)
        self.z_clip_slider = ZClipSlider()
        build_z = float(self.document.settings['printer']['build_mm'][2])
        self.z_clip_slider.set_extent(0.0, build_z)
        self.z_clip_slider.clip_changed.connect(self._on_view_z_clip)
        self.z_clip_slider.show_all_requested.connect(self._on_show_all_clip)
        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.tools)
        view_row = QtWidgets.QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(0)
        view_row.addWidget(left, 1)
        view_row.addWidget(self.z_clip_slider)
        center_layout.addLayout(view_row, 1)
        left = center
        self.scene.show_build_volume(self.document.settings)
        # Both modes get a controller. Headless has no widget to animate, so it
        # uses the pure one directly; that is also what the tests drive.
        self.camera = self.viewport.camera if self.viewport else CameraController(self.scene.renderer)
        self.setCentralWidget(left)

        self.layers = LayerView()
        self.layers.layer_requested.connect(self.request_layer)
        self.layers.source_changed.connect(self._on_layer_source_changed)
        self.layers.issue_layer_requested.connect(self.request_layer)
        self.faults = FaultView()
        self.faults.layer_requested.connect(self.request_layer)
        self.faults.clip_changed.connect(self._on_fault_clip)
        self.faults.filter_changed.connect(self._refresh_fault_glyphs)
        self.faults.show_all_requested.connect(self._on_show_all_clip)
        report_page = QtWidgets.QWidget()
        report_layout = QtWidgets.QVBoxLayout(report_page)
        self.diagnostic_list = QtWidgets.QTreeWidget()
        self.diagnostic_list.setObjectName('diagnostic_list')
        self.diagnostic_list.setHeaderLabels(['Check / diagnostic', 'Layer', 'Severity'])
        self.diagnostic_list.setRootIsDecorated(False)
        self.diagnostic_list.setToolTip(
            'Double-click a diagnostic to open the Layers tab at that layer.')
        self.diagnostic_list.itemActivated.connect(self._select_diagnostic)
        self.diagnostic_list.itemDoubleClicked.connect(self._select_diagnostic)
        # Named parameters, not a JSON dump: the payload is identical and
        # still copyable as JSON, but a reader can find one number without
        # counting braces. ``toPlainText`` keeps returning that JSON.
        self.diagnostics = ReportParameterView()
        self.diagnostics.setObjectName('report_parameters')
        self.diagnostics.setToolTip(
            'Every field of the last report. Right-click to copy the whole report as JSON.')
        report_layout.addWidget(self.diagnostic_list, 1)
        report_layout.addWidget(self.diagnostics, 2)

        setup_page = self._settings_tab()
        titles = ['Setup', 'Layers', 'Faults', 'Report']
        pages = [setup_page, self.layers, self.faults, report_page]
        self.tabs = _DockTabs(titles)
        self.panel_docks = {}
        docks = []
        for title, page in zip(titles, pages):
            dock = QtWidgets.QDockWidget(title, self)
            dock.setObjectName(f'dock_{title.lower()}')
            dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
            dock.setWidget(page)
            self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
            self.panel_docks[title.lower()] = dock
            docks.append(dock)
        for other in docks[1:]:
            self.tabifyDockWidget(docks[0], other)
        docks[0].raise_()
        self.tabs.bind(docks)
        self.setup_dock = docks[0]
        self.layers_dock = docks[1]
        self.faults_dock = docks[2]
        self.report_dock = docks[3]
        self.layers_tab_index = 1
        self.faults_tab_index = 2
        self.report_tab_index = 3
        # The slider does not emit at value 0, so without this the tab looks
        # dead until it is dragged.  Asking for layer 0 on first sight is the
        # difference between "empty" and "broken" for anyone reading it.
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._previous_tab_index = self.tabs.currentIndex()

        self.progress = QtWidgets.QProgressBar()
        self.progress.setMaximumWidth(220)
        # A persistent island badge. Transient status messages scroll away, and
        # the one finding that must not scroll away is unsupported material.
        self.island_badge = QtWidgets.QLabel()
        self.island_badge.setObjectName('island_badge')
        self.notification_banner = NotificationBanner(self.notifications)
        self.statusBar().addWidget(self.notification_banner, 1)
        self.statusBar().addPermanentWidget(self.island_badge)
        self.statusBar().addPermanentWidget(self.progress)
        self._set_island_badge(None)
        self._build_actions()
        self.resize(1400, 900)

    #: Compact Setup control -> (section, key, CLI flag or None).  This is the
    #: bridge between the two interfaces: a GUI user reading a tooltip learns
    #: the exact config key and the exact flag that set the same value from the
    #: command line, so neither interface is a dead end.
    SETTING_KEYS = {
        'layer_height': ('process', 'layer_height_mm', '--layer-height-mm'),
        'bottom_exposure': ('process', 'bottom_exposure_s', None),
        'normal_exposure': ('process', 'normal_exposure_s', None),
        'bottom_layers': ('process', 'bottom_layers', None),
        'transition_layers': ('process', 'transition_layers', None),
        'spacing': ('support', 'spacing_mm', '--support-spacing-mm'),
        'overhang': ('support', 'overhang_angle_deg', '--overhang-angle-deg'),
        'base_type': ('support', 'base_type', '--base-type'),
        'support_auto': ('support', 'automatic', '--auto-supports'),
        'repair': ('repair', 'aggressiveness', '--repair'),
        'seal': ('repair', 'seal_voids', '--seal-voids'),
        'orifice': ('repair', 'min_orifice_area_mm2', '--min-orifice-area-mm2'),
        'clip_to_build': ('assembly', 'clip_to_build_volume', '--clip-to-build-volume'),
    }

    def _settings_tab(self):
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        self.tier_combo = QtWidgets.QComboBox()
        self.tier_combo.setObjectName('visibility_tier')
        self.tier_combo.addItems(['Simple', 'Advanced', 'Expert'])
        self.tier_combo.setCurrentText('Simple')
        self.tier_combo.setToolTip(
            'Simple: compact Setup controls. Advanced: typed process/support table. '
            'Expert: motion, repair voxels, orientation weights and the JSON escape hatch.')
        self.tier_combo.currentTextChanged.connect(self._on_visibility_tier)
        form.addRow('settings mode', self.tier_combo)
        self.rotate_auto = QtWidgets.QCheckBox('automatic orientation search')
        self.rotate_auto.setToolTip('Clear this to use the three rotation fields exactly as entered.')
        self.rotation = [QtWidgets.QDoubleSpinBox() for _ in range(3)]
        row = QtWidgets.QHBoxLayout()
        for box in self.rotation:
            box.setRange(-360, 360)
            box.setDecimals(2)
            row.addWidget(box)
        self.offset = [QtWidgets.QDoubleSpinBox() for _ in range(2)]
        offsets = QtWidgets.QHBoxLayout()
        for box in self.offset:
            box.setRange(-500, 500)
            box.setDecimals(2)
            offsets.addWidget(box)
        self.lift = QtWidgets.QDoubleSpinBox()
        self.lift.setRange(0, 200)
        self.lift.setValue(5.0)
        self.lift.setKeyboardTracking(False)
        self.lift.setToolTip(
            'Height of the lowest part bottom. Changing this lifts every part by the same amount.')
        self.contact_list = QtWidgets.QListWidget()
        self.contact_list.setObjectName('contact_list')
        self.contact_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.contact_list.currentItemChanged.connect(self._load_contact_parameters)
        self.contact_parameter_controls = {}
        self._contact_parameter_touched = set()
        contact_form = QtWidgets.QFormLayout()
        for key, label in (('contact_diameter_mm', 'contact diameter'),
                           ('pillar_diameter_mm', 'pillar diameter'),
                           ('tip_base_diameter_mm', 'tip diameter'),
                           ('tip_length_mm', 'tip length'),
                           ('min_tip_length_mm', 'min tip length'),
                           ('penetration_mm', 'penetration'),
                           ('break_point_diameter_mm', 'break point diameter'),
                           ('pillar_angle_deg', 'pillar angle'),
                           ('support_clearance_mm', 'clearance'),
                           ('max_slenderness', 'max slenderness'),
                           ('model_anchor_length_mm', 'anchor length'),
                           ('model_anchor_diameter_mm', 'anchor diameter'),
                           ('model_anchor_penetration_mm', 'anchor penetration'),
                           ('small_pillar_diameter_mm', 'small pillar diameter'),
                           ('small_pillar_max_length_mm', 'small pillar max length'),
                           ('small_pillar_upper_depth_mm', 'small pillar upper depth'),
                           ('small_pillar_lower_depth_mm', 'small pillar lower depth')):
            box = QtWidgets.QDoubleSpinBox(); box.setRange(0, 100); box.setDecimals(3)
            if key in self.document.settings['support']:
                box.setValue(float(self.document.settings['support'][key]))
            self.contact_parameter_controls[key] = box
            box.valueChanged.connect(lambda _value, name=key: self._contact_parameter_touched.add(name))
            contact_form.addRow(label, box)
        self.contact_parameter_controls['tip_shape'] = QtWidgets.QComboBox()
        self.contact_parameter_controls['tip_shape'].addItems(['cone', 'cylinder'])
        self.contact_parameter_controls['tip_shape'].currentTextChanged.connect(
            lambda _value: self._contact_parameter_touched.add('tip_shape'))
        contact_form.addRow('tip shape', self.contact_parameter_controls['tip_shape'])
        for key, label, values in (('model_anchor_shape', 'anchor shape', ['cone', 'cylinder']),
                                   ('small_pillar_mode', 'small pillar mode', ['middle', 'model']),
                                   ('small_pillar_shape', 'small pillar shape', ['cone', 'cylinder'])):
            combo = QtWidgets.QComboBox(); combo.addItems(values)
            combo.setCurrentText(str(self.document.settings['support'].get(key, values[0])))
            combo.currentTextChanged.connect(lambda _value, name=key: self._contact_parameter_touched.add(name))
            self.contact_parameter_controls[key] = combo
            contact_form.addRow(label, combo)
        contact_buttons = QtWidgets.QHBoxLayout()
        for text, slot, name in (('Apply selected', self._apply_contact_parameters, 'apply_contact_parameters'),
                                 ('Copy first', self._copy_first_contact_parameters, 'copy_contact_parameters'),
                                 ('Paste selected', self._paste_contact_parameters, 'paste_contact_parameters'),
                                 ('Reset', self._reset_contact_parameters, 'reset_contact_parameters')):
            button = QtWidgets.QPushButton(text); button.setObjectName(name); button.clicked.connect(slot)
            contact_buttons.addWidget(button)
        contact_holder = QtWidgets.QVBoxLayout()
        contact_holder.addWidget(self.contact_list)
        contact_holder.addLayout(contact_form)
        contact_holder.addLayout(contact_buttons)
        self.orientation_candidates = QtWidgets.QListWidget()
        self.orientation_candidates.setObjectName('orientation_candidates')
        self.orientation_candidates.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.orientation_candidates.setMaximumHeight(180)
        self.orientation_candidates.currentItemChanged.connect(self._show_orientation_candidate)
        self.orientation_candidate_details = QtWidgets.QPlainTextEdit()
        self.orientation_candidate_details.setObjectName('orientation_candidate_details')
        self.orientation_candidate_details.setReadOnly(True)
        self.orientation_candidate_details.setMaximumHeight(150)
        self.apply_orientation_candidate = QtWidgets.QPushButton('Apply selected orientation')
        self.apply_orientation_candidate.setObjectName('apply_orientation_candidate')
        self.apply_orientation_candidate.setEnabled(False)
        self.apply_orientation_candidate.clicked.connect(self._apply_orientation_candidate)
        candidate_box = QtWidgets.QVBoxLayout()
        candidate_box.addWidget(self.orientation_candidates)
        candidate_box.addWidget(self.orientation_candidate_details)
        candidate_box.addWidget(self.apply_orientation_candidate)
        candidate_holder = self._wrap(candidate_box)
        candidate_holder.setToolTip(
            'Ranked automatic orientation candidates. Scores are display guidance; '
            'weights are uncalibrated and some support terms may be sampled.')
        # Per-axis scale and mirror. Both are model decisions rather than
        # profile settings, so they sit with rotation and offset, not with the
        # machine values below.
        from ..geometry import MAX_SCALE, MIN_SCALE
        self.scale = [QtWidgets.QDoubleSpinBox() for _ in range(3)]
        scales = QtWidgets.QHBoxLayout()
        for box in self.scale:
            box.setRange(MIN_SCALE, MAX_SCALE)
            box.setDecimals(4)
            box.setSingleStep(0.01)
            box.setValue(1.0)
            box.setToolTip('Nothing is scaled unless you ask. A scaled part is recorded as a '
                           'warning diagnostic in every report.')
            scales.addWidget(box)
        self.mirror = [QtWidgets.QCheckBox(axis) for axis in 'XYZ']
        mirrors = QtWidgets.QHBoxLayout()
        for box in self.mirror:
            box.setToolTip('Mirroring reverses triangle winding so the part stays solid. '
                           'A mirrored threaded or keyed part will not assemble.')
            mirrors.addWidget(box)
        self.measurement = QtWidgets.QLabel('no model loaded')
        self.measurement.setObjectName('measurement')
        self.measurement.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.layer_height = QtWidgets.QDoubleSpinBox()
        self.layer_height.setRange(0.001, 1.0)
        self.layer_height.setDecimals(3)
        self.layer_height.setValue(self.document.settings['process']['layer_height_mm'])
        self.bottom_exposure = QtWidgets.QDoubleSpinBox()
        self.bottom_exposure.setRange(0.01, 300)
        self.bottom_exposure.setDecimals(2)
        self.bottom_exposure.setValue(self.document.settings['process']['bottom_exposure_s'])
        self.normal_exposure = QtWidgets.QDoubleSpinBox()
        self.normal_exposure.setRange(0.01, 300)
        self.normal_exposure.setDecimals(2)
        self.normal_exposure.setValue(self.document.settings['process']['normal_exposure_s'])
        self.bottom_layers = QtWidgets.QSpinBox()
        self.bottom_layers.setRange(0, 1000)
        self.bottom_layers.setValue(self.document.settings['process']['bottom_layers'])
        self.transition_layers = QtWidgets.QSpinBox()
        self.transition_layers.setRange(0, 1000)
        self.transition_layers.setValue(self.document.settings['process']['transition_layers'])
        self.spacing = QtWidgets.QDoubleSpinBox()
        self.spacing.setRange(0.2, 50)
        self.spacing.setValue(self.document.settings['support']['spacing_mm'])
        self.overhang = QtWidgets.QDoubleSpinBox()
        self.overhang.setRange(1, 89)
        self.overhang.setValue(self.document.settings['support']['overhang_angle_deg'])
        self.base_type = QtWidgets.QComboBox()
        self.base_type.addItems(BASE_TYPES)
        self.base_type.setCurrentText(self.document.settings['support']['base_type'])
        self.base_type.setToolTip(
            'grid is the default: a porous lattice with less resin and suction than a slab. '
            'plate is one convex hull over every foot with a 30 degree outer putty-knife bevel. '
            'none is feet only. pad gives each foot its own disc. skate is a tapered capsule; '
            'skeleton links pads with an MST; hex is a honeycomb on the same pitch as grid. '
            'Each reports its own resin volume and plate contact area.')
        self.paint_mode = QtWidgets.QComboBox()
        self.paint_mode.addItems(['off', 'block', 'enforce'])
        self.paint_mode.setObjectName('paint_mode')
        self.paint_mode.setToolTip(
            'Manual paint only: block never places an automatic contact on the painted faces; '
            'enforce always does. Island births are never blocked. Nothing is painted automatically.')
        self.paint_mode.currentTextChanged.connect(self._on_paint_mode)
        self.paint_radius = QtWidgets.QDoubleSpinBox()
        self.paint_radius.setRange(0.2, 50)
        self.paint_radius.setDecimals(2)
        self.paint_radius.setValue(self.document.settings['support']['spacing_mm'])
        self.paint_radius.setToolTip('Brush radius in millimeters. Click and drag on the model.')
        self._paint_buffer = []
        self.support_auto = QtWidgets.QCheckBox('automatic support contacts')
        self.support_auto.setChecked(self.document.settings['support'].get('automatic', True))
        self.support_auto.setToolTip('Clear this to route only contacts placed manually with Shift-click.')
        self.repair = QtWidgets.QComboBox()
        self.repair.addItems(['none', 'conservative', 'aggressive'])
        self.repair.setCurrentText(self.document.settings['repair']['aggressiveness'])
        self.seal = QtWidgets.QCheckBox('seal enclosed cavities')
        self.seal.setChecked(self.document.settings['repair']['seal_voids'])
        self.orifice = QtWidgets.QDoubleSpinBox()
        self.orifice.setRange(0, 100)
        self.orifice.setDecimals(3)
        self.orifice.setValue(self.document.settings['repair']['min_orifice_area_mm2'])
        self.clip_to_build = QtWidgets.QCheckBox('clip geometry to the build volume on export')
        self.clip_to_build.setChecked(self.document.settings['assembly']['clip_to_build_volume'])
        self.clip_to_build.setToolTip(
            'Off: an oversized part is refused rather than silently cut. On: geometry the printer '
            'cannot reach is discarded, the amount is recorded, and the export is still withheld '
            'unless Allow warned export is armed. Nothing is ever scaled.')
        form.addRow(self.rotate_auto)
        form.addRow('rotate RX RY RZ', self._wrap(row))
        form.addRow('center offset X Y', self._wrap(offsets))
        form.addRow('model lift mm', self.lift)
        self.object_panel = ObjectPanel(snap_angle_deg=self.editor_preferences['snap_angle_deg'])
        self.object_panel.set_translate_step(self.editor_preferences['translate_step_mm'])
        self.object_panel.set_limits(self.document.settings['printer']['build_mm'])
        self.object_panel.selection_changed.connect(self._on_object_list_changed)
        self.object_panel.selection_indices_changed.connect(self._on_object_selection_indices_changed)
        # A drag is now previewed live on the actor and committed exactly once
        # by the panel itself, so the window no longer debounces a slider drag
        # the way it used to; it only ever writes a committed pose.
        self.object_panel.pose_preview.connect(self._on_pose_preview)
        self.object_panel.pose_committed.connect(self._on_pose_committed)
        self.object_panel.scale_edited.connect(self._on_scale_edited)
        self.object_panel.visibility_changed.connect(self._on_object_visibility)
        self.object_panel.duplicate_requested.connect(self.duplicate_object)
        self.object_panel.remove_requested.connect(self.remove_selected_object)
        self.object_panel.add_requested.connect(self.add_extra_model_dialog)
        self.object_panel.arrange_requested.connect(self.arrange_objects)
        self.object_panel.supports_requested.connect(lambda _index: self.compute_attachments())
        self.object_panel.drop_received.connect(self.add_models)
        self.object_panel.drop_to_plate_requested.connect(self._on_drop_to_plate_requested)
        self.object_panel.zoom_to_selected_requested.connect(self._on_zoom_to_selected_requested)
        self.object_panel.auto_orient_requested.connect(self.auto_orient_selected)
        self.attachment_settings = AttachmentSettings()
        self.attachment_settings.plate_edited.connect(self._on_plate_attachments_edited)
        self.attachment_settings.overlay_edited.connect(self._on_overlay_attachments_edited)
        self.object_overrides = QtWidgets.QLineEdit()
        self.object_overrides.setObjectName('object_overrides')
        self.object_overrides.setPlaceholderText('support overlay JSON, e.g. {"pillar_diameter_mm": 1.6}')
        self.object_overrides.setToolTip(
            'Every other support.* key for this part. The controls above write the common '
            'ones; this field reaches the rest, exactly as the resolved-settings box does '
            'for the plate.')
        apply_overrides = QtWidgets.QPushButton('Apply object support overlay')
        apply_overrides.setObjectName('apply_object_overrides')
        apply_overrides.clicked.connect(self.apply_object_overrides)
        object_editor = QtWidgets.QVBoxLayout()
        object_editor.addWidget(self.object_panel)
        object_editor.addWidget(self._heading_label('Attachments for the selected part'))
        object_editor.addWidget(self.attachment_settings)
        object_editor.addWidget(self.object_overrides)
        object_editor.addWidget(apply_overrides)
        object_holder = self._wrap(object_editor)
        object_holder.setToolTip(
            'Select a part, then move and rotate it with the sliders, the absolute fields, '
            'or by dragging it in the 3D view; all three edit the same numbers. A support '
            'overlay JSON applies only to that part; the plate still shares one collision '
            'field. Equivalent CLI fields are on --add-model-spec.')
        # A form row puts its widget in the narrow field column, which is far
        # too tight for six axis rows plus their nudge buttons. Spanning both
        # columns is what stops the whole panel arriving behind a scroll bar.
        form.addRow(self._heading_label('Plate objects'))
        form.addRow(object_holder)
        form.addRow('orientation candidates', candidate_holder)
        form.addRow('per-contact support parameters', self._wrap(contact_holder))
        form.addRow('paint mode', self.paint_mode)
        form.addRow('paint radius mm', self.paint_radius)
        paint_buttons = QtWidgets.QHBoxLayout()
        for text, slot, name in (('Clear blocked on this part', lambda: self._clear_paint('blocked'), 'clear_blocked_paint'),
                                 ('Clear enforced on this part', lambda: self._clear_paint('enforced'), 'clear_enforced_paint')):
            button = QtWidgets.QPushButton(text); button.setObjectName(name); button.clicked.connect(slot)
            button.setToolTip('Paint belongs to the part it was drawn on, so this clears the '
                              'selected part only.')
            paint_buttons.addWidget(button)
        form.addRow(self._wrap(paint_buttons))
        form.addRow('scale X Y Z', self._wrap(scales))
        form.addRow('mirror axes', self._wrap(mirrors))
        form.addRow('measured size', self.measurement)
        self.markers, self.reverts = {}, {}
        for label, name in (('layer height mm', 'layer_height'),
                            ('bottom exposure s', 'bottom_exposure'),
                            ('normal exposure s', 'normal_exposure'),
                            ('bottom layers', 'bottom_layers'),
                            ('transition layers', 'transition_layers'),
                            ('support spacing mm', 'spacing'),
                            ('overhang angle deg', 'overhang'),
                            ('base type', 'base_type'),
                            (None, 'support_auto'),
                            ('repair', 'repair'),
                            (None, 'seal'),
                            ('min orifice area mm2', 'orifice'),
                            (None, 'clip_to_build')):
            form.addRow(*self._setting_row(label, name))
        self.exposure_schedule = QtWidgets.QLabel()
        self.exposure_schedule.setObjectName('exposure_schedule')
        self.exposure_schedule.setWordWrap(True)
        self.exposure_schedule.setToolTip(
            'The same schedule voxelmill profile prints: bottom layers, then the transition ramp, '
            'then normal exposure. Transition values are intermediate and exclude both endpoints.')
        form.addRow('exposure schedule s', self.exposure_schedule)
        self.visibility = {}
        for role in ('model', 'supports', 'raft', 'contacts'):
            box = QtWidgets.QCheckBox(f'show {role}')
            box.setChecked(True)
            box.toggled.connect(lambda state, name=role: self._set_visible(name, state))
            self.visibility[role] = box
            form.addRow(box)
        apply = QtWidgets.QPushButton('Apply and rebuild')
        apply.clicked.connect(self.apply_settings)
        form.addRow(apply)

        from .settings_table import OrientationWeightsWidget, SettingsTableWidget
        self.settings_table = SettingsTableWidget(
            sections=('process', 'support'), tier='advanced')
        self.settings_table.setObjectName('generated_settings_table')
        form.addRow(self.settings_table)
        self.motion_table = SettingsTableWidget(
            sections=('motion',), tier='expert')
        self.motion_table.setObjectName('motion_settings_table')
        form.addRow('printer motion (uncalibrated)', self.motion_table)
        self.repair_expert_table = SettingsTableWidget(
            sections=('repair',), tier='expert')
        self.repair_expert_table.setObjectName('repair_expert_table')
        form.addRow('repair voxels / expert', self.repair_expert_table)
        self.orientation_weights = OrientationWeightsWidget()
        form.addRow('orientation weights', self.orientation_weights)

        self.settings_json = QtWidgets.QPlainTextEdit()
        self.settings_json.setObjectName('resolved_settings_json')
        self.settings_json.setMinimumHeight(230)
        self.settings_json.setToolTip(
            'Expert escape hatch for every resolved setting. Prefer the typed '
            'table when a control exists; validation happens before a rebuild.')
        self.settings_search = QtWidgets.QLineEdit()
        self.settings_search.setObjectName('settings_search')
        self.settings_search.setPlaceholderText('search settings keys…')
        self.settings_search.setToolTip('Filters the resolved JSON box to matching keys. Case-insensitive substring.')
        self.settings_search.textChanged.connect(self._filter_settings_json)
        self._json_search_label = QtWidgets.QLabel('settings search')
        self._json_label = QtWidgets.QLabel('All resolved settings (JSON, expert)')
        form.addRow(self._json_search_label, self.settings_search)
        form.addRow(self._json_label, self.settings_json)
        self.apply_json_button = QtWidgets.QPushButton('Apply complete settings JSON and rebuild')
        self.apply_json_button.clicked.connect(self.apply_settings)
        form.addRow(self.apply_json_button)
        self.rotate_auto.toggled.connect(self._sync_orientation_controls)
        self._sync_widgets_from_document()
        # After the initial sync so construction does not emit an edit.
        self.lift.valueChanged.connect(self._on_plate_floor_changed)
        self._apply_visibility_tier('simple')
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _exposure_text(settings):
        """The schedule ``voxelmill profile`` prints, computed by the same function."""
        from ..config import layer_exposure
        process = settings['process']
        count = process['bottom_layers'] + process['transition_layers'] + 2
        values = [layer_exposure(settings, index) for index in range(count)]
        return '  '.join(f'{value:g}' for value in values) + '  ...'

    def _setting_row(self, label, name):
        """One Setup row: the control, a modified dot, and a revert button.

        The dot and the button are the whole point of a baseline. Without them
        a settings page shows values but never says which of them this session
        changed, which is exactly the question before an export.
        """
        section, key, flag = self.SETTING_KEYS[name]
        widget = getattr(self, name)
        existing = widget.toolTip()
        flag_text = (f'Set it from the command line with {flag}, or with '
                     f'--set {section}.{key}=VALUE.' if flag else
                     f'Set it from the command line with --set {section}.{key}=VALUE.')
        widget.setToolTip('\n'.join(part for part in (
            existing, f'Config key: {section}.{key}. {flag_text}') if part))
        marker = QtWidgets.QLabel()
        marker.setObjectName(f'modified_{name}')
        marker.setFixedWidth(14)
        revert = QtWidgets.QToolButton()
        revert.setObjectName(f'revert_{name}')
        revert.setText('\u21ba')
        revert.setAutoRaise(True)
        revert.setToolTip(f'Revert {section}.{key} to the value the selected profile resolves to.')
        revert.clicked.connect(lambda _checked=False, control=name: self.revert_setting(control))
        self.markers[name] = marker
        self.reverts[name] = revert
        row = QtWidgets.QHBoxLayout()
        row.addWidget(widget, 1)
        row.addWidget(marker)
        row.addWidget(revert)
        holder = self._wrap(row)
        return (label, holder) if label else (holder,)

    def revert_setting(self, name):
        """Put one control back to the baseline profile value, undoably."""
        section, key, _flag = self.SETTING_KEYS[name]
        try:
            self.document.revert_setting(section, key)
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'revert')
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.statusBar().showMessage(f'reverted {section}.{key} to the profile value', 6000)
        if self.document.source:
            self.reload()
        return True

    def _refresh_modified_markers(self):
        changes = self.document.modified_settings()
        for name, (section, key, _flag) in self.SETTING_KEYS.items():
            entry = (changes.get(section) or {}).get(key)
            marker, revert = self.markers.get(name), self.reverts.get(name)
            if marker is None:
                continue
            marker.setText('\u25cf' if entry else '')
            marker.setStyleSheet('color: #b8860b;' if entry else '')
            marker.setToolTip('' if not entry else
                              f'{section}.{key} was changed from the profile value '
                              f'{entry["left"]!r} to {entry["right"]!r}.')
            revert.setEnabled(bool(entry))

    @staticmethod
    def _wrap(layout):
        holder = QtWidgets.QWidget()
        holder.setLayout(layout)
        return holder

    def _build_actions(self):
        bar = self.menuBar()
        file_menu = bar.addMenu('&File')
        self.file_menu = file_menu
        edit_menu = bar.addMenu('&Edit')
        parts_menu = bar.addMenu('&Parts')
        verification_menu = bar.addMenu('&Verification')
        configuration_menu = bar.addMenu('&Configuration')
        tasks_menu = bar.addMenu('&Tasks')
        self.actions_map = {}

        def add(menu, name, label, slot, shortcut=None):
            action = QtGui.QAction(label, self)
            if shortcut:
                action.setShortcut(shortcut)
            action.triggered.connect(slot)
            menu.addAction(action)
            self.actions_map[name] = action
            return action

        add(file_menu, 'new_project', 'New project...', self.new_project, QtGui.QKeySequence.New)
        add(file_menu, 'open', 'Open STL...', self.open_stl_dialog, QtGui.QKeySequence.Open)
        add(file_menu, 'import_step', 'Import STEP...', self.import_step_dialog)
        add(file_menu, 'open_project', 'Open project...', self.open_project_dialog)
        add(file_menu, 'save_project', 'Save project...', self.save_project, QtGui.QKeySequence.Save)
        add(file_menu, 'save_project_as', 'Save project as...', self.save_project_as, 'Ctrl+Shift+S')
        add(file_menu, 'open_goo', 'Open GOO or CTB for inspection...', self.open_goo_dialog)
        add(file_menu, 'close_goo', 'Close opened slice file', self.close_goo)
        add(file_menu, 'export', 'Export supported STL...', self.export_dialog)
        add(file_menu, 'export_goo', 'Export Elegoo GOO...', self.export_goo_dialog)
        add(file_menu, 'export_ctb', 'Export CTB v3...', self.export_ctb_dialog)
        file_menu.addSeparator()
        add(file_menu, 'quit', 'Quit', self.close, QtGui.QKeySequence.Quit)
        self._refresh_step_import_enabled()

        self.undo_action = add(edit_menu, 'undo', 'Undo', self.undo, QtGui.QKeySequence.Undo)
        self.redo_action = add(edit_menu, 'redo', 'Redo', self.redo, QtGui.QKeySequence.Redo)
        self.history_menu = edit_menu.addMenu('History')
        self.history_menu.setObjectName('history_menu')
        self.history_menu.aboutToShow.connect(self._rebuild_history_menu)

        add(parts_menu, 'add_model', 'Add model...', self.add_extra_model_dialog)
        add(parts_menu, 'compute_attachments', 'Compute attachments', self.compute_attachments, 'Ctrl+R')
        add(parts_menu, 'arrange_objects', 'Arrange on plate', self.arrange_objects, 'Ctrl+L')
        add(parts_menu, 'measure_stl', 'Measure STL (sizes and fit)...', self.measure_stl_dialog)
        part_to_part = QtGui.QAction('Allow part-to-part supports', self, checkable=True)
        part_to_part.setChecked(bool(self.document.settings['support'].get('allow_part_to_part', False)))
        part_to_part.toggled.connect(self._set_allow_part_to_part)
        parts_menu.addAction(part_to_part)
        self.actions_map['allow_part_to_part'] = part_to_part
        add(parts_menu, 'reset_all_lifts', 'Reset all parts to this lift', self.reset_all_lifts)
        presets_menu = parts_menu.addMenu('Support presets')
        from ..presets import list_presets
        for name in list_presets():
            add(presets_menu, f'preset_{name}', f'Apply {name}',
                lambda _checked=False, preset=name: self.apply_support_preset(preset))
        add(presets_menu, 'load_preset', 'Load preset JSON...', self.load_support_preset_dialog)
        add(presets_menu, 'save_preset', 'Save current support settings...', self.save_support_preset_dialog)

        add(verification_menu, 'check_islands', 'Check islands now', self.check_islands, 'Ctrl+I')
        auto_islands = QtGui.QAction('Re-check islands after every edit', self, checkable=True)
        auto_islands.setChecked(self.auto_island_check)
        auto_islands.toggled.connect(self._set_auto_island_check)
        verification_menu.addAction(auto_islands)
        self.actions_map['auto_island_check'] = auto_islands
        for key, label in PRINT_CHECK_LABELS:
            add(verification_menu, f'check_print_{key}', label,
                lambda _checked=False, k=key, l=label: self.run_print_checks_now({k}, l))
        verification_menu.addSeparator()
        add(verification_menu, 'check_print_all', 'Check all',
            lambda _checked=False: self.run_print_checks_now(
                set(services.PRINT_CHECK_NAMES), 'all checks'))
        add(verification_menu, 'check_print_some', 'Check some...', self.check_print_some_dialog)
        add(verification_menu, 'verify_goo', 'Verify GOO or CTB (deep check)...', self.verify_goo_dialog)
        add(verification_menu, 'inspect_stl', 'Inspect STL (mesh inventory)...', self.inspect_stl_dialog)
        add(verification_menu, 'validate_stl', 'Validate STL (reslice and check)...', self.validate_stl_dialog)

        add(configuration_menu, 'profile_library', 'Profile library...', self.profile_library_dialog)
        add(configuration_menu, 'printer_editor', 'Printer editor...', self.printer_editor_dialog)
        add(configuration_menu, 'resin_editor', 'Resin editor...', self.resin_editor_dialog)
        add(configuration_menu, 'support_editor', 'Support editor...', self.support_editor_dialog)
        add(configuration_menu, 'preferences', 'Preferences...', self.preferences_dialog)
        theme_menu = configuration_menu.addMenu('Theme')
        theme_menu.setObjectName('theme_menu')
        self._theme_actions = {}
        for name in THEMES:
            action = QtGui.QAction(name.capitalize(), self, checkable=True)
            action.triggered.connect(lambda _checked=False, theme=name: self.set_theme(theme))
            theme_menu.addAction(action)
            self._theme_actions[name] = action
            self.actions_map[f'theme_{name}'] = action
        self._theme_actions['system'].setChecked(True)
        motion_menu = configuration_menu.addMenu('Motion')
        motion_menu.setObjectName('motion_menu')
        motion_group = QtGui.QActionGroup(self)
        motion_group.setExclusive(True)
        for mode, label in (('relative', 'Relative to current pose'),
                            ('absolute', 'Absolute from import pose')):
            action = QtGui.QAction(label, self, checkable=True)
            action.setChecked(self.motion_mode == mode)
            action.triggered.connect(lambda _checked=False, m=mode: self._set_motion_mode(m))
            motion_group.addAction(action)
            motion_menu.addAction(action)
            self.actions_map[f'motion_{mode}'] = action
        add(configuration_menu, 'shortcuts', 'Shortcuts...', self.shortcuts_dialog)
        warned = QtGui.QAction('Allow warned export', self, checkable=True)
        warned.toggled.connect(self._set_allow_unresolved)
        configuration_menu.addAction(warned)
        self.actions_map['warned'] = warned

        add(tasks_menu, 'printer_monitor', 'Printer monitor...', self.printer_monitor_dialog)
        add(tasks_menu, 'cancel', 'Cancel running job', self.jobs.cancel_all, 'Esc')
        add(tasks_menu, 'run_operation', 'Run operation (all options)...', self.operation_dialog)

        view_menu = bar.addMenu('&View')
        for name in ('front', 'back', 'left', 'right', 'top', 'bottom', 'iso'):
            label = 'Home (isometric)' if name == 'iso' else name.capitalize()
            add(view_menu, f'view_{name}', label,
                (lambda _checked=False, view=name: self.set_view(view)), SHORTCUTS[name])
        view_menu.addSeparator()
        add(view_menu, 'fit', 'Fit to scene', self.fit_view, 'Ctrl+F')
        add(view_menu, 'reset_layout', 'Reset layout', self.reset_layout)
        view_menu.addSeparator()
        for title, dock in (('Setup', self.setup_dock), ('Layers', self.layers_dock),
                            ('Faults', self.faults_dock), ('Report', self.report_dock)):
            action = dock.toggleViewAction()
            action.setText(title)
            view_menu.addAction(action)
            self.actions_map[f'panel_{title.lower()}'] = action
        view_menu.addSeparator()
        show_issues_menu = view_menu.addMenu('Show issues')
        show_issues_menu.setObjectName('show_issues_menu')
        for code in ISSUE_COLORS:
            action = QtGui.QAction(_issue_label(code), self, checkable=True)
            action.setChecked(True)
            action.toggled.connect(lambda checked, c=code: self.layers.set_issue_visibility(c, checked))
            show_issues_menu.addAction(action)
            self.actions_map[f'show_issue_{code}'] = action
        self.layers.issue_visibility_changed.connect(self._sync_issue_visibility_action)
        add(view_menu, 'next_issue_layer', 'Next issue layer',
            lambda _checked=False: self._jump_issue_layer(1), 'Ctrl+Shift+]')
        add(view_menu, 'previous_issue_layer', 'Previous issue layer',
            lambda _checked=False: self._jump_issue_layer(-1), 'Ctrl+Shift+[')
        self._refresh_undo()

    # ---- camera --------------------------------------------------------
    def operation_dialog(self):
        from .operations import OperationDialog
        dialog = OperationDialog(self.document, self)
        dialog.exec()

    def preferences_dialog(self):
        from .preferences import PreferencesDialog
        dialog = PreferencesDialog(self.document, self, headless=self.headless)
        dialog.settings_applied.connect(self._preferences_applied)
        dialog.editor_preferences_applied.connect(self._editor_preferences_applied)
        dialog.exec()
        return dialog

    def _editor_preferences_applied(self, preferences):
        self.editor_preferences.update(preferences)
        self.object_panel.set_snap_angle(self.editor_preferences['snap_angle_deg'])
        self.object_panel.set_translate_step(self.editor_preferences['translate_step_mm'])
        self._refresh_step_import_enabled()

    def _freecad_preferred(self):
        path = self.editor_preferences.get('freecad_path') or ''
        return path or None

    def _refresh_step_import_enabled(self):
        from ..importers import find_freecad
        action = self.actions_map.get('import_step')
        if action is None:
            return
        action.setEnabled(find_freecad(preferred=self._freecad_preferred()) is not None)

    # ---- motion mode -----------------------------------------------------
    #
    # Whether a gizmo drag or a nudge lands on top of the part's current pose
    # (relative, today's behavior) or is measured from its import pose
    # (absolute). The choice is a person's habit, not a decision about the
    # print, so it lives in editor preferences next to the snap increment
    # rather than in the document or the printer profile.

    @property
    def motion_mode(self):
        return self.editor_preferences.get('motion_mode', DEFAULT_MOTION_MODE)

    def _set_motion_mode(self, mode):
        if mode not in ('relative', 'absolute'):
            return
        self.editor_preferences['motion_mode'] = mode
        save_preferences(self.editor_preferences)
        index = self.object_panel.list.currentRow()
        if index >= 0:
            self._sync_object_pose(index)
        self.statusBar().showMessage(
            f'motion: {mode} '
            + ('(edits add to the current pose)' if mode == 'relative'
               else '(edits are measured from the import pose)'), 8000)

    def _preferences_applied(self, _settings):
        self.jobs.invalidate()
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.scene.show_build_volume(self.document.settings)
        if self.document.source:
            self.reload()

    # ---- island badge --------------------------------------------------
    #
    # The badge answers one question — is there unsupported material — and says
    # how old the answer is. It never claims more than the scan behind it: an
    # island scan examines connectivity and growth, not voids, drainage,
    # support routing or plate fit, so a green badge is never an export gate.

    def _set_island_badge(self, summary, *, stale=False):
        self.island_summary = summary
        self.island_stale = bool(stale)
        if summary is None:
            text, tip = 'islands: not checked', 'Run Check islands, or a full validation.'
        else:
            count = summary['island_count']
            body = 'no islands' if not count else f'{count} island{"s" if count != 1 else ""}'
            text = f'islands: {body}'
            tip = (f"{body} over {summary.get('layers')} layers at "
                   f"min_overlap_pixels={summary.get('min_overlap_pixels')}. "
                   f"Not examined: {', '.join(summary.get('not_examined', ()))}.")
        if stale:
            text += ' (stale)'
            tip = 'The model changed since this scan. ' + tip
        self.island_badge.setText(text)
        self.island_badge.setToolTip(tip)
        color = ('#888888' if summary is None or stale
                  else ('#b00020' if summary['island_count'] else '#1b7f3b'))
        self.island_badge.setStyleSheet(f'color: {color}; padding: 0 8px;')

    def mark_islands_stale(self):
        """Any edit invalidates the last scan; say so rather than showing it as current."""
        if self.island_summary is not None and not self.island_stale:
            self._set_island_badge(self.island_summary, stale=True)

    def check_islands(self, checked=False, *, show_report=True):
        """Re-scan the current assembly for islands, in the background.

        ``show_report`` is True for the menu action and False for the
        automatic scan after a rebuild, so a move/rotate does not yank the
        editor over to the Report tab. ``checked`` is the unused ``triggered``
        payload from the menu action.
        """
        self._island_check_show_report = bool(show_report)
        self._clear_preview_transforms()
        union = self.document.derived.union
        if union is None:
            self.statusBar().showMessage(
                'no assembly yet: open a model and let supports build first', 8000)
            return None
        document = self.document
        self.statusBar().showMessage('scanning for islands...')
        return self.jobs.submit('islands', lambda token, progress: services.scan_islands(
            document, union, token, progress))

    def _finish_islands(self, value):
        self._set_island_badge(value)
        count = value['island_count']
        self.statusBar().showMessage(
            f"{count} island(s) over {value['layers']} layers; this scan does not examine "
            f"{', '.join(value['not_examined'])}", 12000)
        # An island scan is not a validation, so it gets its own report shape
        # rather than borrowing ValidationReport's -- the note says so, and the
        # metrics keep everything else scan_islands measured.
        metrics = {key: item for key, item in value.items() if key != 'diagnostics'}
        self._set_report({
            'passed': not count,
            'checks': {'raster_connectivity': 'fail' if count else 'pass',
                       'closed_surface': value['closed_surface']},
            'diagnostics': value['diagnostics'],
            'metrics': metrics,
        }, note='island scan only; this is not a full validation')
        # An automatic re-scan after move/rotate must not steal the tab the
        # user is working in; Check islands now still jumps to Report.
        if getattr(self, '_island_check_show_report', True):
            self.tabs.setCurrentIndex(self.report_tab_index)
        self.layers.set_diagnostic_codes({d['code'] for d in value['diagnostics']})

    # ---- Check print menu -------------------------------------------------
    #
    # Each of these is one or more of the same checks the full export
    # validation runs, over the in-memory assembly rather than a written STL.
    # Like the island scan, none of this is an export decision by itself.

    def run_print_checks_now(self, checks, label):
        """Run the given print checks in the background and report the result."""
        self._clear_preview_transforms()
        union = self.document.derived.union
        if union is None:
            self.statusBar().showMessage(
                'no assembly yet: open a model and let supports build first', 8000)
            return None
        document = self.document
        self._print_check_label = label
        self.statusBar().showMessage(f'checking {label}...')
        return self.jobs.submit('print_checks', lambda token, progress: services.run_print_checks(
            document, union, token, progress, checks=checks))

    def check_print_some_dialog(self):
        """Modal picker: one checkbox per print check, all checked by default."""
        dialog = QtWidgets.QDialog(self)
        dialog.setObjectName('check_print_some_dialog')
        dialog.setWindowTitle('Check some')
        layout = QtWidgets.QVBoxLayout(dialog)
        boxes = {}
        for key, label in PRINT_CHECK_LABELS:
            box = QtWidgets.QCheckBox(label)
            box.setObjectName(f'check_print_box_{key}')
            box.setChecked(True)
            layout.addWidget(box)
            boxes[key] = box
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.selected_checks = lambda: {key for key, box in boxes.items() if box.isChecked()}
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            selected = dialog.selected_checks()
            if selected:
                self.run_print_checks_now(selected, 'selected checks')
        return dialog

    def _finish_print_checks(self, value):
        label = getattr(self, '_print_check_label', 'print checks')
        self._set_report(value, note=f'print checks: {label}; this is not the full export validation')
        self.report_dock.raise_()
        self.layers.set_diagnostic_codes({d['code'] for d in value['diagnostics']})
        state = 'passed' if value['passed'] else 'issues found'
        self.statusBar().showMessage(f'print checks ({label}): {state}', 10000)

    def configuration_editor_dialog(self, kind):
        from .editors import ConfigurationEditor
        dialog = ConfigurationEditor(self.document, kind, self, headless=self.headless)
        dialog.settings_applied.connect(self._configuration_applied)
        dialog.exec()
        return dialog

    def _configuration_applied(self, settings):
        # Invalidate in-flight work at Apply, while the modal editor is still
        # open. Waiting until Close lets an older worker publish stale geometry.
        self.jobs.invalidate()
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.scene.show_build_volume(settings)
        self.rebuild()

    def printer_editor_dialog(self):
        return self.configuration_editor_dialog('printer')

    def printer_monitor_dialog(self):
        """Open printer telemetry/history independently of any prepared model."""
        from .printer_monitor import PrinterMonitorDialog
        dialog = PrinterMonitorDialog(self)
        dialog.exec()
        return dialog

    def resin_editor_dialog(self):
        return self.configuration_editor_dialog('resin')

    def support_editor_dialog(self):
        return self.configuration_editor_dialog('support')

    def profile_library_dialog(self):
        """Everything ``voxelmill profile`` and ``voxelmill resin`` do, in the editor."""
        from .profiles import ProfileLibraryDialog
        dialog = ProfileLibraryDialog(self.document, self)
        dialog.exec()
        if dialog.applied is not None:
            # After choosing a profile, "modified" means changed from that
            # profile, not from whatever the editor happened to open with.
            self.document.adopt_baseline(dialog.applied)
            self._sync_widgets_from_document()
            self._refresh_undo()
            self.statusBar().showMessage(
                f"profile applied: {dialog.applied['printer']['name']} / "
                f"{dialog.applied['resin']['name']}", 8000)
        return dialog

    def apply_support_preset(self, source):
        from ..presets import apply_preset
        try:
            self.document.set_settings(apply_preset(self.document.settings, source))
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'preset')
        self._sync_widgets_from_document()
        self._refresh_undo()
        if self.document.source:
            self.rebuild()

    def load_support_preset_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Load support preset', '', 'JSON (*.json)')
        if path:
            self.apply_support_preset(path)

    def save_support_preset_dialog(self):
        from ..presets import save_preset
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save support preset', '', 'JSON (*.json)')
        if path:
            try:
                save_preset(path, Path(path).stem, self.document.settings['support'])
                self.statusBar().showMessage(f'Saved support preset {path}', 6000)
            except VoxelMillError as error:
                self._report_error(error.to_dict(), 'preset')

    def set_view(self, name):
        """Snap the 3D view to a named printer-relative orientation.

        Front is -Y, the same face the green build-volume edge marks, so the
        cube and the plate never disagree about which way the machine faces.
        """
        if name not in VIEWS:
            raise VoxelMillError('invalid_view', f'Unknown view {name!r}',
                            {'views': sorted(VIEWS)})
        if self.viewport is not None:
            self.viewport.set_view(name)
        else:
            self.camera.set_view(name)
        self.statusBar().showMessage(f'view: {name}', 3000)
        return name

    def fit_view(self):
        if self.viewport is not None:
            return self.viewport.reset_camera()
        return self.camera.fit()

    # ---- state ---------------------------------------------------------
    def _set_auto_island_check(self, state):
        self.auto_island_check = bool(state)

    def _set_allow_unresolved(self, state):
        self.allow_unresolved = bool(state)

    def _set_allow_part_to_part(self, state):
        settings = deepcopy(self.document.settings)
        wanted = bool(state)
        if bool(settings['support'].get('allow_part_to_part')) == wanted:
            return
        settings['support']['allow_part_to_part'] = wanted
        try:
            self.document.set_settings(settings, stage='supports')
        except VoxelMillError as error:
            self._report_error(error.to_dict(), 'part-to-part supports')
            self._sync_widgets_from_document()
            return
        self._sync_widgets_from_document()
        self._refresh_undo()
        if self.document.source:
            self.rebuild()

    def _set_visible(self, role, state):
        self.scene.set_visible(role, state)
        if self.viewport:
            self.viewport.render()

    def _sync_orientation_controls(self, automatic=None):
        automatic = self.rotate_auto.isChecked() if automatic is None else bool(automatic)
        for box in self.rotation:
            box.setEnabled(not automatic)

    def _selected_contact_points(self):
        return [item.data(QtCore.Qt.UserRole) for item in self.contact_list.selectedItems()]

    def _set_contact_list(self, contacts):
        selected = {tuple(item.data(QtCore.Qt.UserRole)) for item in self.contact_list.selectedItems()}
        self.contact_list.blockSignals(True); self.contact_list.clear()
        for point in (() if contacts is None else contacts):
            point = [float(v) for v in point]
            item = QtWidgets.QListWidgetItem('(%0.3f, %0.3f, %0.3f)' % tuple(point))
            item.setData(QtCore.Qt.UserRole, point)
            self.contact_list.addItem(item)
            if tuple(point) in selected:
                item.setSelected(True)
        self.contact_list.blockSignals(False)
        self._load_contact_parameters(self.contact_list.currentItem())

    def _load_contact_parameters(self, item, _previous=None):
        if item is None:
            return
        from ..contact_parameters import parameters_for_contact
        self._contact_parameter_touched.clear()
        for control in self.contact_parameter_controls.values():
            control.blockSignals(True)
        values = dict(self.document.settings['support'])
        from ..contact_parameters import PERSONAL_FIELDS
        values = {key: values[key] for key in PERSONAL_FIELDS if key in values}
        values.update(parameters_for_contact(self.document.contact_parameters,
                                             item.data(QtCore.Qt.UserRole)))
        for key, control in self.contact_parameter_controls.items():
            if key not in values:
                continue
            if isinstance(control, QtWidgets.QComboBox):
                control.setCurrentText(str(values[key]))
            else:
                control.setValue(float(values[key]))
        for control in self.contact_parameter_controls.values():
            control.blockSignals(False)

    def _contact_parameter_values(self):
        return {key: (control.currentText() if isinstance(control, QtWidgets.QComboBox)
                      else control.value())
                for key, control in self.contact_parameter_controls.items()
                if key in self._contact_parameter_touched}

    def _apply_contact_parameters(self):
        points = self._selected_contact_points()
        if not points:
            return False
        values = self._contact_parameter_values()
        if not values:
            return False
        try:
            self.document.set_contact_parameters(points, values)
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'contact parameters')
        self._refresh_undo(); self.rebuild(); return True

    def _copy_first_contact_parameters(self):
        points = self._selected_contact_points()
        if not points:
            return False
        from ..contact_parameters import parameters_for_contact
        self._contact_clipboard = dict(self.document.settings['support'])
        from ..contact_parameters import PERSONAL_FIELDS
        self._contact_clipboard = {key: self._contact_clipboard[key] for key in PERSONAL_FIELDS
                                   if key in self._contact_clipboard}
        self._contact_clipboard.update(parameters_for_contact(self.document.contact_parameters, points[0]))
        return True

    def _paste_contact_parameters(self):
        points = self._selected_contact_points()
        if not points or not getattr(self, '_contact_clipboard', None):
            return False
        try:
            self.document.set_contact_parameters(points, self._contact_clipboard)
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'contact parameters')
        self._refresh_undo(); self.rebuild(); return True

    def _reset_contact_parameters(self):
        points = self._selected_contact_points()
        if not points:
            return False
        self.document.clear_contact_parameters(points)
        self._refresh_undo(); self.rebuild(); return True

    def _orientation_context_snapshot(self):
        """Inputs which define an automatic orientation search.

        Rotation is intentionally absent: choosing a result authors a manual
        rotation while retaining the search so another result can be previewed.
        """
        source_stat = None
        if self.document.source:
            try:
                stat = self.document.source.stat()
                source_stat = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            except OSError:
                source_stat = None
        return {
            'source': str(self.document.source) if self.document.source else None,
            'source_fingerprint': source_stat,
            'source_sha256': self.document.source_sha256,
            'settings': json.dumps(self.document.settings, sort_keys=True, default=str),
            'offset': tuple(float(v) for v in self.document.center_offset_mm),
            'lift': float(self.document.model_lift_mm),
            'scale': tuple(float(v) for v in self.document.scale_factors),
            'mirror': tuple(bool(v) for v in self.document.mirror_axes),
        }

    def _clear_orientation_candidates(self):
        self._orientation_candidates = []
        self._orientation_candidates_context = None
        if hasattr(self, 'orientation_candidates'):
            self.orientation_candidates.clear()
            self.orientation_candidate_details.clear()
            self.apply_orientation_candidate.setEnabled(False)

    @staticmethod
    def _candidate_text(candidate):
        rank = candidate.get('rank', '?')
        rotation = candidate.get('rotation_deg', ())
        angles = ', '.join(f'{float(v):.2f}' for v in rotation)
        total = candidate.get('total')
        score = 'unscored' if total is None else f'total {float(total):.4g}'
        basis = candidate.get('ranking_basis', 'cheap_fallback')
        return f'#{rank}  RX/RY/RZ {angles}°  {score} ({basis})'

    def _show_orientation_candidate(self, item, _previous=None):
        if item is None:
            self.orientation_candidate_details.clear()
            self.apply_orientation_candidate.setEnabled(False)
            return
        candidate = item.data(QtCore.Qt.UserRole) or {}
        terms = candidate.get('score_terms') or {}
        lines = [self._candidate_text(candidate)]
        for name, term in terms.items():
            if not isinstance(term, dict):
                lines.append(f'{name}: {term}')
                continue
            value = term.get('value', '?')
            contribution = term.get('contribution', '?')
            weight = term.get('weight', '?')
            count = 'counted' if term.get('counted', True) else 'not counted'
            reason = f"; {term['reason']}" if term.get('reason') else ''
            lines.append(f'{name}: value={value}, weight={weight}, contribution={contribution} '
                         f'({count}{reason})')
        assessment = candidate.get('assessment') or {}
        status = candidate.get('assessment_status', assessment.get('status', 'not_run'))
        lines.append(f'assessment: {status}')
        lines.append('Weights are uncalibrated guidance; full-resolution bounds determine fit.')
        lines.append('Sampled normals and coarse occupancy guide ranking; actual support volume is not measured.')
        if status != 'complete':
            lines.append('Support terms may use sampled geometry and were not fully assessed.')
        self.orientation_candidate_details.setPlainText('\n'.join(lines))
        self.apply_orientation_candidate.setEnabled(bool(candidate.get('rotation_deg')))

    def _set_orientation_candidates(self, candidates, selected_rank=1):
        self._orientation_candidates = [dict(candidate) for candidate in (candidates or [])]
        self._orientation_candidates_context = self._orientation_context_snapshot()
        self.orientation_candidates.blockSignals(True)
        self.orientation_candidates.clear()
        for candidate in self._orientation_candidates:
            item = QtWidgets.QListWidgetItem(self._candidate_text(candidate))
            item.setData(QtCore.Qt.UserRole, candidate)
            self.orientation_candidates.addItem(item)
        self.orientation_candidates.blockSignals(False)
        if self._orientation_candidates:
            row = max(0, min(len(self._orientation_candidates) - 1, int(selected_rank) - 1))
            self.orientation_candidates.setCurrentRow(row)
        else:
            self._show_orientation_candidate(None)

    def _apply_orientation_candidate(self):
        item = self.orientation_candidates.currentItem()
        candidate = item.data(QtCore.Qt.UserRole) if item else None
        if not candidate or self._orientation_candidates_context != self._orientation_context_snapshot():
            self._clear_orientation_candidates()
            self.statusBar().showMessage('orientation candidates are stale; run automatic search again', 6000)
            return False
        angles = candidate.get('rotation_deg')
        if not isinstance(angles, (list, tuple)) or len(angles) != 3:
            return False
        try:
            self.document.set_orientation(angles, self.document.center_offset_mm,
                                          self.document.model_lift_mm)
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'orientation candidate')
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.statusBar().showMessage(f"applied orientation candidate #{candidate.get('rank', '?')}", 5000)
        self.rebuild()
        return True

    def _sync_widgets_from_document(self):
        """Reflect document state without making an edit command.

        The compact controls cover daily process work; the JSON field exposes
        every profile value, including machine motion, so no automatic setting
        is hidden from an operator who needs a controlled manual print.
        """
        settings = self.document.settings
        automatic = self.document.rotation_deg == 'auto'
        self.rotate_auto.blockSignals(True)
        self.rotate_auto.setChecked(automatic)
        self.rotate_auto.blockSignals(False)
        if not automatic:
            for box, value in zip(self.rotation, self.document.rotation_deg):
                box.setValue(value)
        for box, value in zip(self.offset, self.document.center_offset_mm):
            box.setValue(value)
        self.lift.blockSignals(True)
        self.lift.setValue(self.document.plate_floor())
        self.lift.blockSignals(False)
        action = getattr(self, 'actions_map', {}).get('allow_part_to_part')
        if action is not None:
            action.blockSignals(True)
            action.setChecked(bool(self.document.settings['support'].get('allow_part_to_part', False)))
            action.blockSignals(False)
        for box, value in zip(self.scale, self.document.scale_factors):
            box.setValue(value)
        for box, value in zip(self.mirror, self.document.mirror_axes):
            box.setChecked(bool(value))
        self._sync_measurement()
        self.layer_height.setValue(settings['process']['layer_height_mm'])
        self.bottom_exposure.setValue(settings['process']['bottom_exposure_s'])
        self.normal_exposure.setValue(settings['process']['normal_exposure_s'])
        self.bottom_layers.setValue(settings['process']['bottom_layers'])
        self.transition_layers.setValue(settings['process']['transition_layers'])
        self.spacing.setValue(settings['support']['spacing_mm'])
        self.overhang.setValue(settings['support']['overhang_angle_deg'])
        self.base_type.setCurrentText(settings['support']['base_type'])
        self.support_auto.setChecked(settings['support'].get('automatic', True))
        self.repair.setCurrentText(settings['repair']['aggressiveness'])
        self.seal.setChecked(settings['repair']['seal_voids'])
        self.orifice.setValue(settings['repair']['min_orifice_area_mm2'])
        self.clip_to_build.setChecked(settings['assembly']['clip_to_build_volume'])
        self.exposure_schedule.setText(self._exposure_text(settings))
        self.settings_json.setPlainText(json.dumps(settings, indent=2, sort_keys=True))
        if getattr(self, 'settings_table', None) is not None:
            self.settings_table.load_settings(settings)
        if getattr(self, 'motion_table', None) is not None:
            self.motion_table.load_settings(settings)
        if getattr(self, 'repair_expert_table', None) is not None:
            self.repair_expert_table.load_settings(settings)
        self._refresh_modified_markers()
        self._sync_orientation_controls(automatic)
        self.object_panel.set_limits(settings['printer']['build_mm'])
        selected = self.object_panel.list.currentRow()
        self.object_panel.set_objects(self.object_specs(), selected=selected)
        self.set_attachment_state(self.attachment_state)
        self._sync_object_pose(self.object_panel.current_index())

    def _on_object_list_changed(self, index):
        self._sync_object_pose(index, attach_widget=True)

    def _on_object_selection_indices_changed(self, indices):
        """The gizmo stays on the current row; only say when an edit reaches more.

        The panel's own status label already names the selection count; this
        is the status *bar*, which is where an edit's actual reach belongs.
        """
        if len(indices) > 1:
            self.statusBar().showMessage(
                f'{len(indices)} parts selected: a Move, Rotate or Scale edit applies to all of them.',
                6000)

    def _on_object_selected(self, index):
        """A click in the 3D view selects the same row the list would."""
        if not 0 <= index < self.object_panel.list.count():
            return
        self.object_panel.list.setCurrentRow(index)
        self._sync_object_pose(index, attach_widget=True)

    def object_specs(self):
        """One record per plate object, primary first, for the object panel."""
        primary_name = self.document.source_name or 'no model'
        names = [primary_name] + [spec.get('name') or Path(spec['path']).name
                                  for spec in self.document.extra_models]
        return [{'name': name, 'visible': self._object_visible(index),
                 'supports': self.attachment_state} for index, name in enumerate(names)]

    def _object_visible(self, index):
        """Whether one part's actor is shown.

        A part whose actor has not been drawn yet is visible, not hidden: the
        list is built before the first geometry job finishes, and reading an
        absent actor as hidden left every row unchecked on startup.
        """
        key = 'model' if index == 0 else f'model:{index}'
        if self.scene is None or key not in self.scene.actors:
            return True
        return bool(self.scene.actors[key].GetVisibility())

    def _object_pose(self, index):
        if index == 0:
            rotate = ((0.0, 0.0, 0.0) if self.document.rotation_deg == 'auto'
                      else self.document.rotation_deg)
            return {'rotate': list(rotate), 'center_offset': list(self.document.center_offset_mm),
                    'lift_mm': float(self.document.model_lift_mm),
                    'scale': list(self.document.scale_factors),
                    'mirror': list(self.document.mirror_axes), 'overrides': {}}
        return self.document.extra_models[index - 1]

    def _object_display_pose(self, index):
        """The pose to show in the object panel: stored, or its offset from import.

        The stored pose (``_object_pose``) is always absolute-from-import; in
        relative mode that is exactly what should be displayed and edited,
        unchanged from before motion mode existed. In absolute mode the panel
        instead shows how far the part has moved from where it was imported,
        so a field reading 0 means "still at the import pose" and typing a
        number into it means exactly that number measured from import.
        """
        pose = self._object_pose(index)
        if self.motion_mode != 'absolute':
            return pose
        display = dict(pose)
        display['rotate'] = [a - b for a, b in zip(pose['rotate'], IMPORT_POSE['rotate'])]
        display['center_offset'] = [a - b for a, b in
                                    zip(pose['center_offset'], IMPORT_POSE['center_offset'])]
        display['lift_mm'] = pose['lift_mm'] - IMPORT_POSE['lift_mm']
        return display

    def _object_pose_from_edit(self, index, edited_pose):
        """Convert what the panel's fields mean into the absolute pose to store.

        Relative mode: the field already reads as the absolute pose (today's
        behavior, unchanged). Absolute mode: the field reads as an offset from
        the import pose (see ``_object_display_pose``), so the import baseline
        is added back in before the document sees it.
        """
        if self.motion_mode != 'absolute':
            return dict(edited_pose)
        result = {
            'rotate': [a + b for a, b in zip(IMPORT_POSE['rotate'], edited_pose['rotate'])],
            'center_offset': [a + b for a, b in
                              zip(IMPORT_POSE['center_offset'], edited_pose['center_offset'])],
            'lift_mm': max(0.0, IMPORT_POSE['lift_mm'] + edited_pose['lift_mm']),
        }
        # A multi-select edit's target list is not itself a pose field; carry
        # it through untouched or a commit in absolute mode would silently
        # narrow back down to the current row alone.
        if 'applies_to' in edited_pose:
            result['applies_to'] = edited_pose['applies_to']
        return result

    def _sync_object_pose(self, index, *, attach_widget=False):
        valid = 0 <= index <= len(self.document.extra_models)
        self.object_panel.setEnabled(True)
        if getattr(self, 'object_overrides', None) is not None:
            self.object_overrides.setEnabled(valid and index > 0)
        if not valid:
            return
        spec = self._object_pose(index)
        self.object_panel.set_pose(self._object_display_pose(index))
        overlay = (spec.get('overrides') or {}).get('support') or {}
        self.attachment_settings.load(index, self.document.settings['support'], overlay)
        # The JSON field carries only what the controls above do not.
        handled = set(self.attachment_settings.rows)
        rest = {key: value for key, value in overlay.items() if key not in handled}
        self.object_overrides.setText(json.dumps(rest) if rest else '')
        # Opening an STL must not drop a box widget on the model; that overlay
        # made a successful load look broken. Attach only on an explicit
        # object-list or pick selection.
        if attach_widget and self.viewport:
            self.viewport.select_transform_object(index)

    def _clear_preview_transforms(self):
        """Drop every outstanding live-drag preview, restoring actors to the document's pose.

        A gizmo drag (``_on_object_preview_transformed``) and the object
        panel's own slider drag (``pose_preview``, which is funneled through
        the same method -- see its docstring) both set a ``vtkTransform`` on
        the actor before the document has agreed to it. If the drag is
        abandoned rather than committed -- Escape, a right-click, or the
        sticky-drag bug that made this common -- the actor is left showing an
        orientation the document, the router and every export never
        received, so a support that was routed for the real pose looks like
        it ignored the rotation. No long-running operation may start while a
        preview transform is outstanding, because the preview is a lie the
        document has not agreed to yet; this is the single place that resets
        it before one runs.
        """
        if self.scene is None:
            return
        for actor in self.scene.actors.values():
            # None, not an identity transform: "no preview" is the absence of
            # one, and callers that ask whether a preview is outstanding read
            # GetUserTransform() rather than comparing it against identity.
            actor.SetUserTransform(None)
        if self.viewport:
            self.viewport.render()

    def _on_object_preview_transformed(self, index, translation, rotation):
        """Move the actor live during a gizmo drag, without touching the document.

        This only ever redraws; the document and the geometry it drives are
        untouched until the drag commits (``_on_object_transformed``), so
        nothing here can race a rebuild or need to be undone.
        """
        if self.scene is None:
            return
        actor = self.scene.actors.get('model' if index == 0 else f'model:{index}')
        if actor is None:
            return
        bounds = actor.GetBounds()
        center = [(bounds[0] + bounds[1]) / 2.0, (bounds[2] + bounds[3]) / 2.0,
                  (bounds[4] + bounds[5]) / 2.0]
        transform = vtk.vtkTransform()
        transform.Translate(*translation)
        transform.Translate(*center)
        transform.RotateZ(rotation[2])
        transform.RotateY(rotation[1])
        transform.RotateX(rotation[0])
        transform.Translate(*(-c for c in center))
        actor.SetUserTransform(transform)
        if self.viewport:
            self.viewport.render()

    def _on_object_deselected(self):
        """An empty-space click: drop the list selection and any handles.

        ``setCurrentRow(-1)`` does not emit ``selection_changed`` (the panel
        already ignores a negative row), so this cannot loop back into
        ``_on_object_list_changed``.
        """
        self.object_panel.list.clearSelection()
        self.object_panel.list.setCurrentRow(-1)
        if self.viewport:
            self.viewport.deselect_transform_object()

    def _on_object_transformed(self, index, translation, rotation):
        """A drag in the 3D view writes the same numbers the sliders do.

        The gizmo reports a delta from where the drag started, and its
        rotation is snapped to the editor increment so a dragged part lands
        somewhere repeatable. In relative mode the delta lands on top of the
        part's current pose (the base below); in absolute mode it is measured
        from the import pose instead, so the same drag always ends up at the
        same place regardless of where the part started.
        """
        if not 0 <= index <= len(self.document.extra_models):
            return
        if self.scene is not None:
            actor = self.scene.actors.get('model' if index == 0 else f'model:{index}')
            if actor is not None:
                # The rebuild _write_pose triggers redraws the actor at the
                # committed pose; an identity transform here stops the live
                # preview transform from being applied on top of it too.
                actor.SetUserTransform(None)
        base = self._object_pose(index) if self.motion_mode != 'absolute' else IMPORT_POSE
        rotate = [self.object_panel.snap(a + b) for a, b in zip(base['rotate'], rotation)]
        offset = [a + b for a, b in zip(base['center_offset'], translation[:2])]
        lift = max(0.0, base['lift_mm'] + translation[2])
        self._write_pose(index, {'rotate': rotate, 'center_offset': offset, 'lift_mm': lift})

    def _on_pose_preview(self, index, pose):
        """Move the previewed part(s) live, reusing the gizmo's own preview path.

        The panel reports the absolute numbers on screen; the shared preview
        helper wants a delta from the pose already committed to the document,
        so this converts one into the other rather than adding a second way to
        move an actor. Every selected part gets the same delta, so a
        multi-select drag keeps whatever arrangement they had relative to
        each other, exactly as the eventual commit will.
        """
        target = self._object_pose_from_edit(index, pose)
        base = self._object_pose(index)
        translation = [a - b for a, b in zip(
            list(target['center_offset']) + [target['lift_mm']],
            list(base['center_offset']) + [base['lift_mm']])]
        rotation = [a - b for a, b in zip(target['rotate'], base['rotate'])]
        for part in pose.get('applies_to') or [index]:
            self._on_object_preview_transformed(part, translation, rotation)

    def _on_pose_committed(self, index, pose):
        """Clear the live preview transform(s), then write the committed pose.

        A drag was only ever previewed on the actor; this is the single point
        where it becomes an undoable document edit -- once, not once per drag
        step, and for every selected part when the edit is a multi-select one.
        """
        absolute = self._object_pose_from_edit(index, pose)
        for part in pose.get('applies_to') or [index]:
            if self.scene is not None:
                actor = self.scene.actors.get('model' if part == 0 else f'model:{part}')
                if actor is not None:
                    actor.SetUserTransform(None)
        self._write_pose(index, absolute)

    def apply_object_pose(self):
        """Write the panel's current pose immediately, for a caller that wants
        a synchronous commit rather than waiting on a signal."""
        index = self.object_panel.current_index()
        return self._write_pose(index, self._object_pose_from_edit(index, self.object_panel.pose()))

    def _write_pose(self, index, pose):
        """Apply a committed pose to the document, undoably.

        ``pose`` is always absolute for ``index``. When it carries
        ``applies_to`` (a multi-select edit), every other selected part moves
        by the same delta from its own current pose, rather than jumping to
        ``index``'s own numbers -- that is what keeps a multi-select drag from
        collapsing every selected part onto the same spot.
        """
        targets = pose.get('applies_to') or [index]
        anchor = self._object_pose(index)
        delta_offset = [a - b for a, b in zip(pose['center_offset'], anchor['center_offset'])]
        delta_lift = pose['lift_mm'] - anchor['lift_mm']
        delta_rotate = [a - b for a, b in zip(pose['rotate'], anchor['rotate'])]
        ok = True
        for target in targets:
            if not 0 <= target <= len(self.document.extra_models):
                continue
            before = self._object_pose(target)
            after = {
                'rotate': [a + b for a, b in zip(before['rotate'], delta_rotate)],
                'center_offset': [a + b for a, b in zip(before['center_offset'], delta_offset)],
                'lift_mm': max(0.0, before['lift_mm'] + delta_lift),
            }
            try:
                if target == 0:
                    self.document.set_orientation(after['rotate'], after['center_offset'], after['lift_mm'])
                else:
                    self.document.set_extra_model_pose(
                        target - 1, rotate=after['rotate'], center_offset=after['center_offset'],
                        lift_mm=after['lift_mm'])
            except VoxelMillError as error:
                ok = False
                self._report_error(error.to_dict(), 'move model')
                continue
            self._note_pose_change(before, after)
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.reload()
        return ok

    #: Moving a part across the plate does not change which of its faces point
    #: down or how far its supports must reach, so routed attachments survive.
    #: Raising or lowering it changes every support length, and rotating it
    #: changes which faces need supporting at all; neither can be salvaged by
    #: translating what was already routed.
    def _note_pose_change(self, before, after):
        if self.attachment_state == 'none':
            return
        rotated = [round(a, 6) for a in before['rotate']] != [round(a, 6) for a in after['rotate']]
        lifted = round(before['lift_mm'], 6) != round(after['lift_mm'], 6)
        if rotated or lifted:
            self.set_attachment_state('none')
            self.notify('Attachments were dropped: ' +
                        ('rotating' if rotated else 'changing height') +
                        ' changes which faces need supporting. Compute attachments again.',
                        category='attachments', level='warning')
        elif [round(a, 6) for a in before['center_offset']] != [round(a, 6) for a in after['center_offset']]:
            self.set_attachment_state('stale')

    def _on_scale_edited(self, index, scale, mirror):
        """Write a scale/mirror edit undoably, to every selected part.

        Unlike a pose edit, every selected part lands on the same absolute
        scale and mirror rather than the same delta -- there is no sensible
        "delta" for a multiplier the way there is for a position. ``index``
        is always the current row (see the panel's own docstring); the
        target set comes from ``selected_indices`` directly since
        ``scale_edited`` has no ``applies_to`` slot of its own.
        """
        targets = self.object_panel.selected_indices() or [int(index)]
        changed = False
        for target in targets:
            if not 0 <= target <= len(self.document.extra_models):
                continue
            try:
                if target == 0:
                    self.document.set_transform(scale, mirror)
                else:
                    self.document.set_extra_model_pose(target - 1, scale=scale, mirror=mirror)
            except VoxelMillError as error:
                self._report_error(error.to_dict(), 'scale model')
                continue
            changed = True
        if not changed:
            return False
        # A resized or mirrored part is a different part; whatever was routed
        # for its old shape does not describe the new one.
        if self.attachment_state != 'none':
            self.set_attachment_state('none')
            self.notify('Attachments were dropped: scaling or mirroring changes the shape '
                        'being supported. Compute attachments again.',
                        category='attachments', level='warning')
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.reload()
        return True

    @property
    def attachment_state(self):
        return getattr(self, '_attachment_state', 'none')

    def set_attachment_state(self, state):
        """``none``, ``stale`` or ``routed``; shown on every object row."""
        self._attachment_state = state
        if getattr(self, 'object_panel', None) is not None:
            self.object_panel.set_objects(self.object_specs(),
                                          selected=self.object_panel.list.currentRow())
            self.object_panel.set_status({
                'none': 'No attachments. Position the parts, then Compute attachments.',
                'stale': 'Attachments were routed before the last move and no longer match '
                         'the plate. Compute attachments again before exporting.',
                'routed': 'Attachments routed for the plate as it stands.',
            }[state])

    def compute_attachments(self):
        """Route supports for the plate as it stands, adding attachments under
        any island until none remain (or the pass budget runs out).

        This used to just flip ``support.automatic`` on and reload, which
        restarted the whole place/model/supports/union pipeline. The repaired
        solid and model triangles do not depend on that flag, so this instead
        keeps them and submits the island guard directly against them.
        """
        # This no longer reloads (see above), so a stale preview transform
        # would otherwise survive untouched while the router works from the
        # document's real pose underneath it -- exactly the "supports ignore
        # the rotation" report.
        self._clear_preview_transforms()
        if self.document.source is None:
            self.notify('Open a model before computing attachments.',
                        category='attachments', level='warning')
            return False
        derived = self.document.derived
        if derived.solid is None or derived.model_triangles is None:
            self.notify('Wait for the preview to finish before computing attachments.',
                        category='attachments', level='warning')
            return False
        settings = deepcopy(self.document.settings)
        settings['support']['automatic'] = True
        # stage='supports' keeps the repaired solid, model triangles and
        # column field the guard reuses; only routing, raft and union (which
        # depend on the flag) need to be redone.
        self.document.set_settings(settings, stage='supports')
        self.support_auto.blockSignals(True)
        self.support_auto.setChecked(True)
        self.support_auto.blockSignals(False)
        self._refresh_undo()
        self.jobs.invalidate()
        document = self.document
        triangles, bounds, field = derived.model_triangles, derived.model_bounds, derived.column_field
        self.statusBar().showMessage('routing attachments...')
        self.jobs.submit('attachments', lambda token, progress: services.route_attachments(
            document, triangles, bounds, field, token, progress))
        return True

    def _finish_attachments(self, value):
        """Store the guard's union directly rather than reassembling it.

        ``route_attachments`` already routed, assembled and scanned
        everything it needed to; redoing any of that here would just be
        recomputing what the job already handed back.
        """
        derived = self.document.derived
        derived.column_field = value['field']
        derived.plan, derived.raft = value['plan'], value['raft']
        derived.union = value['union']
        derived.layer_slicer = None
        derived.layer_cache.drop_source('union')
        contacts = value['contacts']
        import manifold3d as m
        supports = (m.Manifold.batch_boolean(value['plan'].solids, m.OpType.Add)
                    if value['plan'].solids else None)
        from ..geometry import manifold_triangles
        self.scene.set_mesh('supports', manifold_triangles(supports) if supports else None)
        self.scene.set_mesh('raft', manifold_triangles(value['raft']) if value['raft'] else None)
        self.scene.set_contacts(contacts)
        self._set_contact_list(contacts)
        for role, box in self.visibility.items():
            self.scene.set_visible(role, box.isChecked())
        if self.viewport:
            self.viewport.render()
        height = self.document.settings['process']['layer_height_mm']
        top = float(np.asarray(value['union'].bounding_box()).reshape(2, 3)[1, 2])
        layer_count = max(1, int(np.ceil(top / height)))
        if self.layers.selected_source == 'union':
            self.layers.set_range(layer_count)
            self.faults.set_range(layer_count)
        self.set_attachment_state('routed')
        self._report_island_guard(value['guard'])

    def _report_island_guard(self, guard):
        """Say plainly what the island guard did -- never claim success it did not earn."""
        added = sum(item.get('contacts_added', 0) for item in guard['passes'])
        passes = len(guard['passes'])
        last = guard['passes'][-1] if guard['passes'] else None
        summary = {
            'island_count': guard['islands_remaining'],
            'layers': last['layers_scanned'] if last else None,
            'min_overlap_pixels': self.document.settings['support'].get('min_overlap_pixels'),
            'not_examined': ['drainage_bottlenecks', 'support_routes', 'plate_fit',
                             'union_raster_parity'],
        }
        self._set_island_badge(summary)
        if guard['resolved']:
            message = (f'attachments routed: {added} contact(s) added over {passes} pass(es); '
                       'no islands remain')
            level = 'info'
        else:
            message = (f'attachments routed: {added} contact(s) added over {passes} pass(es); '
                       f"{guard['islands_remaining']} island(s) remain after {guard['max_passes']} "
                       'pass(es) -- compute attachments again or add support manually')
            level = 'warning'
        self.statusBar().showMessage(message, 12000)
        self.notify(message, category='attachments', level=level)
        diagnostics = [{'code': 'raster_island', 'message': 'unsupported island',
                        'position_mm': list(position), 'severity': 'error'}
                       for position in guard['island_positions']]
        self._set_report({
            'passed': guard['resolved'],
            'checks': {'raster_connectivity': 'pass' if guard['resolved'] else 'fail'},
            'diagnostics': diagnostics,
            'metrics': {'passes': guard['passes'], 'islands_remaining': guard['islands_remaining'],
                       'max_passes': guard['max_passes'], 'contacts_added': added},
        }, note='attachment routing via the island guard; this is not a full validation')
        self.report_dock.raise_()
        self.layers.set_diagnostic_codes({d['code'] for d in diagnostics})

    def _on_object_visibility(self, index, visible):
        if self.scene is None:
            return
        self.scene.set_visible('model' if index == 0 else f'model:{index}', bool(visible))
        if self.viewport:
            self.viewport.render()

    def duplicate_object(self, index, count=1):
        """Copy a placed part, reusing the mesh path rather than re-importing."""
        index = int(index)
        if not 0 <= index <= len(self.document.extra_models):
            return False
        if index == 0:
            if self.document.source is None:
                return False
            spec = {'path': str(self.document.source), 'rotate': list(self._object_pose(0)['rotate']),
                    'center_offset': list(self.document.center_offset_mm),
                    'lift_mm': float(self.document.model_lift_mm),
                    'scale': list(self.document.scale_factors),
                    'mirror': list(self.document.mirror_axes)}
        else:
            spec = deepcopy(self.document.extra_models[index - 1])
        try:
            for _ in range(max(1, int(count))):
                self.document.add_extra_model(deepcopy(spec))
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'duplicate model')
        # Copies land exactly on their original, which is an illegal plate.
        # Arranging is the only honest next step, so it is not optional here.
        self.arrange_objects(announce=False)
        return True

    def arrange_objects(self, announce=True):
        """Lay every part out without overlap, or refuse and change nothing."""
        self._clear_preview_transforms()
        from ..arrange import arrange_footprints
        footprints, poses = self._object_footprints()
        if footprints is None:
            self.notify('Arrange needs the placed geometry; wait for the preview to finish.',
                        category='arrange', level='warning')
            return False
        build = self.document.settings['printer']['build_mm']
        clearance = float(self.document.settings['printer'].get('edge_clearance_mm', 2.0))
        envelope = (build[0] - 2 * clearance, build[1] - 2 * clearance)
        try:
            positions = arrange_footprints(footprints, envelope,
                                           clearance_mm=self.document.settings['support']['spacing_mm'])
        except VoxelMillError as error:
            self._report_error(error.to_dict(), 'arrange')
            return False
        for index, (x, y) in enumerate(positions):
            pose = dict(poses[index])
            pose['center_offset'] = [float(x), float(y)]
            if index == 0:
                self.document.set_orientation(pose['rotate'], pose['center_offset'], pose['lift_mm'])
            else:
                self.document.set_extra_model_pose(
                    index - 1, rotate=pose['rotate'], center_offset=pose['center_offset'],
                    lift_mm=pose['lift_mm'])
        if self.attachment_state == 'routed':
            self.set_attachment_state('stale')
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.reload()
        if announce:
            self.statusBar().showMessage(f'arranged {len(positions)} parts', 5000)
        return True

    def _object_footprints(self):
        """Per-object XY footprint sizes from the placed display geometry."""
        displayed = self.placed if self.placed is not None else self.document.derived.model_triangles
        counts = list(getattr(self, '_display_part_counts', ()) or ())
        poses = [self._object_pose(index) for index in range(len(self.document.extra_models) + 1)]
        if displayed is None:
            return None, poses
        displayed = np.asarray(displayed).reshape(-1, 3, 3)
        if not counts or int(sum(counts)) != len(displayed):
            counts = [len(displayed)]
        footprints, start = [], 0
        for count in counts:
            part = displayed[start:start + count]
            start += count
            if not len(part):
                footprints.append((1.0, 1.0))
                continue
            low = part.reshape(-1, 3).min(axis=0)
            high = part.reshape(-1, 3).max(axis=0)
            footprints.append((max(1e-3, float(high[0] - low[0])), max(1e-3, float(high[1] - low[1]))))
        # A pose can exist before the preview has drawn that part -- a part
        # added or duplicated a moment ago. Reserving a nominal 1 mm square for
        # it packs the plate as if it were tiny and the parts then overlap for
        # real. Reserve the largest footprint already known instead: too much
        # room only spreads the plate out, too little produces a layout the
        # collision check rejects.
        if footprints:
            spare = (max(width for width, _ in footprints),
                     max(depth for _, depth in footprints))
            while len(footprints) < len(poses):
                footprints.append(spare)
        return footprints[:len(poses)], poses

    def add_models(self, paths):
        """Add one or more dropped or chosen STLs and lay the plate out again."""
        added = 0
        for path in paths:
            try:
                self.document.add_extra_model({
                    'path': str(path), 'rotate': (0.0, 0.0, 0.0), 'center_offset': (0.0, 0.0),
                    'lift_mm': float(self.document.model_lift_mm), 'scale': (1.0, 1.0, 1.0),
                    'mirror': (False, False, False)})
                added += 1
            except VoxelMillError as error:
                self._report_error(error.to_dict(), 'add model')
        if not added:
            return False
        if self.attachment_state == 'routed':
            self.set_attachment_state('stale')
        self.arrange_objects(announce=False)
        self.statusBar().showMessage(f'added {added} part(s)', 5000)
        return True

    @staticmethod
    def _heading_label(text):
        label = QtWidgets.QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    def _on_plate_attachments_edited(self, values):
        """Row 0 has no overlay: its attachment values are the plate's."""
        settings = deepcopy(self.document.settings)
        settings['support'].update(values)
        try:
            self.document.set_settings(settings)
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'attachments')
        if self.attachment_state == 'routed':
            self.set_attachment_state('stale')
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.reload()
        return True

    def _on_overlay_attachments_edited(self, index, values):
        """An added part's overlay, merged with whatever the JSON field holds."""
        if not 1 <= index <= len(self.document.extra_models):
            return False
        existing = (self.document.extra_models[index - 1].get('overrides') or {}).get('support') or {}
        handled = set(self.attachment_settings.rows)
        overlay = {key: value for key, value in existing.items() if key not in handled}
        overlay.update(values)
        try:
            self.document.set_extra_model_overrides(index - 1, {'support': overlay} if overlay else {})
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'attachments')
        if self.attachment_state == 'routed':
            self.set_attachment_state('stale')
        self._refresh_undo()
        self.reload()
        return True

    def apply_object_overrides(self):
        index = self.object_panel.current_index()
        if index <= 0:
            return False
        text = self.object_overrides.text().strip()
        try:
            overlay = json.loads(text) if text else {}
            if text and not isinstance(overlay, dict):
                raise ValueError('support overlay must be a JSON object')
            # The controls own their keys; this field owns everything else.
            kept = {key: value for key, value in self.attachment_settings.values().items()
                    if self.attachment_settings.override.isChecked()
                    and round(value, 6) != round(float(self.document.settings['support'][key]), 6)}
            merged = dict(kept)
            merged.update(overlay)
            overrides = {'support': merged} if merged else {}
            self.document.set_extra_model_overrides(index - 1, overrides)
        except (VoxelMillError, ValueError, json.JSONDecodeError) as error:
            payload = error.to_dict() if isinstance(error, VoxelMillError) else {
                'code': 'invalid_model', 'message': str(error)}
            return self._report_error(payload, 'object support overlay')
        self._refresh_undo()
        self.reload()
        return True

    def remove_selected_object(self, index=None):
        index = self.object_panel.current_index() if index is None else int(index)
        if index <= 0:
            return False
        self.document.remove_extra_model(index - 1)
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.reload()
        return True

    def _filter_settings_json(self, text):
        needle = (text or '').strip()
        if not needle:
            return
        content = self.settings_json.toPlainText()
        index = content.lower().find(needle.lower())
        if index < 0:
            return
        cursor = self.settings_json.textCursor()
        cursor.setPosition(index)
        cursor.setPosition(index + len(needle), QtGui.QTextCursor.KeepAnchor)
        self.settings_json.setTextCursor(cursor)

    def _sync_measurement(self):
        """Show the size the current pose produces, and the source size it came from.

        The placed size is measured from the real placement matrix, so it
        already includes rotation, scale and mirror. Before a model is placed
        there is nothing to measure and it says so, rather than showing zeros.
        """
        placement = self.document.placement
        if placement is None:
            self.measurement.setText('no placement yet')
            self.measurement.setToolTip('Open a model; the size appears once it is placed.')
            return
        bounds = np.asarray(placement.bounds, dtype=float)
        size = bounds[1] - bounds[0]
        text = ' x '.join(f'{v:.3f}' for v in size) + ' mm'
        from ..geometry import scale_note
        note = scale_note(placement.scale, placement.mirror)
        if note:
            text += f'  ({note.split(";")[0]})'
        self.measurement.setText(text)
        self.measurement.setToolTip(
            f'Placed bounds {bounds.tolist()}; diagonal {float(np.linalg.norm(size)):.3f} mm.'
            + (f' {note}.' if note else ''))

    def _refresh_undo(self):
        self.undo_action.setEnabled(self.document.undo_label is not None)
        self.redo_action.setEnabled(self.document.redo_label is not None)
        self.undo_action.setText(f'Undo {self.document.undo_label or ""}'.strip())
        self.redo_action.setText(f'Redo {self.document.redo_label or ""}'.strip())
        self._refresh_window_title()

    def _refresh_window_title(self):
        if self.project_path:
            self.setWindowTitle(f'{Path(self.project_path).name} — VoxelMill[*]')
        else:
            self.setWindowTitle('VoxelMill[*]')
        self.setWindowModified(bool(self.document.dirty))
        self._refresh_save_action()

    def _refresh_save_action(self):
        action = getattr(self, 'actions_map', {}).get('save_project')
        if action is None:
            return
        action.setText('Save project' if self.project_path else 'Save project...')

    def _rebuild_history_menu(self):
        self.history_menu.clear()
        stack = list(self.document._undo)
        if not stack:
            empty = self.history_menu.addAction('(no history)')
            empty.setEnabled(False)
            return
        # Newest first; choosing an entry undoes that many steps.
        for steps_back, command in enumerate(reversed(stack), start=1):
            action = self.history_menu.addAction(command.label)
            action.triggered.connect(
                lambda _checked=False, count=steps_back: self.jump_history(count))

    def jump_history(self, steps: int):
        """Undo ``steps`` times to reach a named history entry."""
        steps = max(0, int(steps))
        for _ in range(steps):
            if not self.document.undo():
                break
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.rebuild()

    def undo(self):
        if self.document.undo():
            # The controls have to follow the document, not only the pipeline.
            # Without this an undone settings edit left every Setup field, and
            # every modified marker, showing the value that was just reverted.
            self._sync_widgets_from_document()
            self._refresh_undo()
            self.rebuild()

    def redo(self):
        if self.document.redo():
            self._sync_widgets_from_document()
            self._refresh_undo()
            self.rebuild()

    def _on_visibility_tier(self, label):
        self._apply_visibility_tier(str(label).strip().lower())

    def _apply_visibility_tier(self, tier: str):
        tier = tier if tier in ('simple', 'advanced', 'expert') else 'simple'
        self.visibility_tier = tier
        if getattr(self, 'tier_combo', None) is not None:
            wanted = tier.capitalize()
            if self.tier_combo.currentText() != wanted:
                self.tier_combo.blockSignals(True)
                self.tier_combo.setCurrentText(wanted)
                self.tier_combo.blockSignals(False)
        show_advanced = tier in ('advanced', 'expert')
        show_expert = tier == 'expert'
        if getattr(self, 'settings_table', None) is not None:
            self.settings_table.set_tier('advanced' if show_advanced else 'simple')
            self.settings_table.setVisible(show_advanced)
        if getattr(self, 'motion_table', None) is not None:
            self.motion_table.set_tier('expert')
            self.motion_table.setVisible(show_expert)
        if getattr(self, 'repair_expert_table', None) is not None:
            self.repair_expert_table.set_tier('expert')
            self.repair_expert_table.setVisible(show_expert)
        if getattr(self, 'orientation_weights', None) is not None:
            self.orientation_weights.setVisible(show_expert)
        for widget in (getattr(self, 'settings_json', None),
                       getattr(self, 'settings_search', None),
                       getattr(self, 'apply_json_button', None),
                       getattr(self, '_json_label', None),
                       getattr(self, '_json_search_label', None)):
            if widget is not None:
                widget.setVisible(show_expert)

    def set_theme(self, name: str):
        name = name if name in THEMES else 'system'
        self.theme_name = name
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        for key, action in getattr(self, '_theme_actions', {}).items():
            action.setChecked(key == name)
        if name == 'system':
            app.setStyleSheet('')
            app.setPalette(app.style().standardPalette())
            return
        if name == 'dark':
            palette = QtGui.QPalette()
            palette.setColor(QtGui.QPalette.Window, QtGui.QColor(45, 45, 48))
            palette.setColor(QtGui.QPalette.WindowText, QtCore.Qt.white)
            palette.setColor(QtGui.QPalette.Base, QtGui.QColor(30, 30, 30))
            palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(45, 45, 48))
            palette.setColor(QtGui.QPalette.Text, QtCore.Qt.white)
            palette.setColor(QtGui.QPalette.Button, QtGui.QColor(45, 45, 48))
            palette.setColor(QtGui.QPalette.ButtonText, QtCore.Qt.white)
            palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(42, 130, 218))
            palette.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.black)
            app.setPalette(palette)
            app.setStyleSheet('QToolTip { color: #ffffff; background-color: #2a2a2a; }')
            return
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet('')

    def shortcuts_dialog(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle('Shortcuts')
        dialog.setObjectName('shortcuts_dialog')
        layout = QtWidgets.QVBoxLayout(dialog)
        table = QtWidgets.QTableWidget(0, 2)
        table.setObjectName('shortcuts_table')
        table.setHorizontalHeaderLabels(['Action', 'Shortcut'])
        table.horizontalHeader().setStretchLastSection(True)
        rows = []
        for name, action in self.actions_map.items():
            sequence = action.shortcut().toString() if action.shortcut() else ''
            if sequence:
                rows.append((action.text() or name, sequence))
        for view, key in SHORTCUTS.items():
            rows.append((f'View {view}', key))
        # Deduplicate while preserving order.
        seen = set()
        unique = []
        for row in rows:
            if row in seen:
                continue
            seen.add(row)
            unique.append(row)
        table.setRowCount(len(unique))
        for index, (label, shortcut) in enumerate(unique):
            table.setItem(index, 0, QtWidgets.QTableWidgetItem(label))
            table.setItem(index, 1, QtWidgets.QTableWidgetItem(shortcut))
        table.resizeColumnsToContents()
        layout.addWidget(table)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        if self.headless:
            dialog.show()
        else:
            dialog.exec()
        return dialog

    def complete_startup(self):
        """Run first-run prompts after the window is visible.

        Called from :func:`run` after ``show()`` and VTK ``start()``. Tests
        that construct a window directly skip this on purpose.
        """
        if self._startup_completed:
            return
        self._startup_completed = True
        self._maybe_run_wizard(self._startup_source)
        self._maybe_prompt_freecad()
        self._maybe_offer_recovery()

    def _maybe_run_wizard(self, source):
        from .wizard import FirstRunWizard, wizard_should_run
        if not wizard_should_run(headless=self.headless, source=source):
            return None
        wizard = FirstRunWizard(self)
        if wizard.exec() == QtWidgets.QDialog.Accepted and wizard.applied is not None:
            self.document.set_settings(wizard.applied)
            self.document.adopt_baseline(wizard.applied)
            self._sync_widgets_from_document()
            self.scene.show_build_volume(self.document.settings)
            self._refresh_undo()
        return wizard

    def _accept_freecad_path(self, path) -> str | None:
        """Save a user-picked FreeCAD path if it resolves to a runnable binary.

        The stored preference may be the ``.app`` bundle the user chose; lookup
        expands it to ``Contents/MacOS/FreeCADCmd`` (or ``FreeCAD``) at use.
        """
        from ..importers import interpret_freecad_path
        if not path:
            return None
        candidate = Path(path).expanduser()
        resolved = interpret_freecad_path(candidate)
        if resolved is None:
            self.statusBar().showMessage(
                f'not an executable FreeCAD binary: {path}', 8000)
            return None
        self.editor_preferences['freecad_path'] = str(candidate)
        save_preferences(self.editor_preferences)
        self._refresh_step_import_enabled()
        self.statusBar().showMessage(f'FreeCAD path saved: {candidate}', 5000)
        return str(resolved)

    def _maybe_prompt_freecad(self):
        """Offer to locate FreeCAD once when STEP import would otherwise stay off.

        Same skip gates as the first-run wizard (headless / NO_WIZARD / no TTY)
        so pytest and AppImage smoke never hang on a modal.
        """
        if self.headless or os.environ.get('VOXELMILL_NO_WIZARD') or not sys.stdin.isatty():
            return None
        from ..importers import FREECAD_FILE_FILTER, find_freecad
        if find_freecad(preferred=self._freecad_preferred()) is not None:
            return None
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle('FreeCAD for STEP import')
        box.setText('STEP import needs FreeCAD.')
        box.setInformativeText(
            'VoxelMill can tessellate .step/.stp files when FreeCAD is available. '
            'STL, Prepare, and slice do not need it. Locate FreeCAD now '
            '(on macOS pick FreeCAD.app — the bundle, not a file inside it), '
            'or set it later under Preferences.')
        locate = box.addButton('Locate…', QtWidgets.QMessageBox.AcceptRole)
        box.addButton('Not now', QtWidgets.QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not locate:
            return None
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Locate FreeCAD', '', FREECAD_FILE_FILTER)
        return self._accept_freecad_path(path)

    def _maybe_offer_recovery(self):
        """Offer a leftover autosave once, when someone is there to answer.

        Same skip gates as the first-run wizard and the FreeCAD prompt
        (headless / NO_WIZARD / no TTY). Without them a machine that had ever
        crashed with work open would hang every non-interactive start on a
        modal nobody can click, including the packaged acceptance smoke and
        ``--screenshot``. Skipping leaves the autosave in place, so the next
        interactive start still offers it.
        """
        path = autosave_path()
        if (self.headless or os.environ.get('VOXELMILL_NO_WIZARD')
                or not sys.stdin.isatty()):
            return None
        if not path.exists() or path.stat().st_size <= 0:
            return None
        if self.project_path and Path(self.project_path).resolve() == path.resolve():
            return None
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle('Recover autosave')
        box.setText(f'A recovery project exists at {path}.')
        box.setInformativeText('Recover, ignore for now, or delete the autosave?')
        recover = box.addButton('Recover', QtWidgets.QMessageBox.AcceptRole)
        box.addButton('Ignore', QtWidgets.QMessageBox.RejectRole)
        delete = box.addButton('Delete', QtWidgets.QMessageBox.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is recover:
            return self.offer_recovery(path)
        if clicked is delete:
            try:
                path.unlink()
            except OSError:
                pass
        return None

    def offer_recovery(self, path):
        """Load an autosave recovery project. Headless-safe entry for tests."""
        path = Path(path)
        if not path.exists() or path.stat().st_size <= 0:
            return False
        self.open_project(path)
        return True

    def notify(self, message, *, category='general', level='warning'):
        entry = self.notifications.notify(message, category=category, level=level)
        if entry is not None and level == 'error':
            self.statusBar().showMessage(message, 10000)
        return entry

    def apply_settings(self):
        self._clear_orientation_candidates()
        try:
            settings = json.loads(self.settings_json.toPlainText())
        except json.JSONDecodeError as error:
            return self._report_error({'code': 'invalid_settings_json', 'message': str(error), 'details': {}})
        if self.visibility_tier in ('advanced', 'expert') and getattr(self, 'settings_table', None):
            settings = self.settings_table.apply_to_settings(settings)
        if self.visibility_tier == 'expert':
            if getattr(self, 'motion_table', None):
                settings = self.motion_table.apply_to_settings(settings)
            if getattr(self, 'repair_expert_table', None):
                settings = self.repair_expert_table.apply_to_settings(settings)
        settings['support']['spacing_mm'] = self.spacing.value()
        settings['support']['overhang_angle_deg'] = self.overhang.value()
        settings['support']['base_type'] = self.base_type.currentText()
        if 'automatic' in settings['support']:
            settings['support']['automatic'] = self.support_auto.isChecked()
        settings['process']['layer_height_mm'] = self.layer_height.value()
        settings['process']['bottom_exposure_s'] = self.bottom_exposure.value()
        settings['process']['normal_exposure_s'] = self.normal_exposure.value()
        settings['process']['bottom_layers'] = self.bottom_layers.value()
        settings['process']['transition_layers'] = self.transition_layers.value()
        settings['repair']['aggressiveness'] = self.repair.currentText()
        settings['repair']['seal_voids'] = self.seal.isChecked()
        settings['repair']['min_orifice_area_mm2'] = self.orifice.value()
        settings['assembly']['clip_to_build_volume'] = self.clip_to_build.isChecked()
        try:
            self.document.set_settings(settings)
        except VoxelMillError as error:
            return self._report_error(error.to_dict())
        rotation = 'auto' if self.rotate_auto.isChecked() else [box.value() for box in self.rotation]
        try:
            self.document.set_transform([box.value() for box in self.scale],
                                        [box.isChecked() for box in self.mirror])
        except VoxelMillError as error:
            return self._report_error(error.to_dict())
        # Lift is the plate floor, already committed on the spinbox. Keep the
        # primary's own lift here so Apply does not flatten extra-part gaps.
        self.document.set_orientation(rotation, [box.value() for box in self.offset],
                                      self.document.model_lift_mm)
        if abs(self.lift.value() - self.document.plate_floor()) > 1e-9:
            try:
                self.document.set_plate_floor(self.lift.value())
            except VoxelMillError as error:
                return self._report_error(error.to_dict())
        self.scene.show_build_volume(self.document.settings)
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.reload()

    def _on_plate_floor_changed(self, value):
        """Commit Setup lift immediately so Apply does not have to re-apply it."""
        new_mm = float(value)
        if abs(new_mm - self.document.plate_floor()) < 1e-9:
            return
        self._clear_preview_transforms()
        try:
            command = self.document.set_plate_floor(new_mm)
        except VoxelMillError as error:
            self._report_error(error.to_dict(), 'lift')
            self._sync_widgets_from_document()
            return
        if command is None:
            return
        if self.attachment_state != 'none':
            self.set_attachment_state('none')
            self.notify('Attachments were dropped: changing height changes which faces '
                        'need supporting. Compute attachments again.',
                        category='attachments', level='warning')
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.reload()

    def reset_all_lifts(self, _checked=False):
        """Write the Setup lift onto every part, collapsing relative gaps."""
        self._clear_preview_transforms()
        try:
            command = self.document.reset_all_lifts(self.lift.value())
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'reset lifts')
        if command is None:
            return True
        if self.attachment_state != 'none':
            self.set_attachment_state('none')
            self.notify('Attachments were dropped: changing height changes which faces '
                        'need supporting. Compute attachments again.',
                        category='attachments', level='warning')
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.reload()
        return True

    # ---- pipeline ------------------------------------------------------
    def reload(self):
        self.mark_islands_stale()
        if not self.document.source:
            return
        self.last_error = None
        self.jobs.invalidate()
        document = self.document
        self.jobs.submit('place', lambda token, progress: services.load_and_place(document, token, progress))

    def rebuild(self):
        """Restart from the earliest stage whose derived state was dropped."""
        if (self._orientation_candidates_context is not None
                and self._orientation_candidates_context != self._orientation_context_snapshot()):
            self._clear_orientation_candidates()
        self.mark_islands_stale()
        derived = self.document.derived
        self.jobs.invalidate()
        document = self.document
        if derived.solid is None:
            return self.reload()
        if derived.plan is None:
            triangles, bounds, field = derived.model_triangles, derived.model_bounds, derived.column_field
            return self.jobs.submit('supports', lambda token, progress: services.build_supports(
                document, triangles, bounds, field, token, progress))
        self._assemble()

    def _assemble(self):
        derived = self.document.derived
        solid, plan, raft = derived.solid, derived.plan, derived.raft
        self.jobs.submit('union', lambda token, progress: services.assemble(solid, plan, raft, cancel=token))

    def request_layer(self, index):
        """Slice the assembly or decode an opened GOO, whichever is selected.

        A cached payload is drawn straight away.  A missing union used to
        return silently, which is indistinguishable from a hung job; it now
        says which source is unavailable and why.
        """
        index = int(index)
        self._requested_layer = index
        source = self.layers.selected_source
        cache = self.document.derived.layer_cache
        key = (source, index) if source == 'union' else (source, str(self.goo_path), index)
        cached = cache.get(key)
        if cached is not None:
            return self._finish_layer(cached)
        if source == 'goo':
            if self.goo_source is None:
                self.statusBar().showMessage(
                    'no slice file is open; use File > Open GOO or CTB for inspection', 6000)
                return
            path = self.goo_path
            self.jobs.submit('layer', lambda token, progress: services.goo_layer(
                path, index, cancel=token))
            return
        union = self.document.derived.union
        if union is None:
            self.statusBar().showMessage(
                'no assembly to slice yet; open an STL and wait for placement, repair and supports'
                if self.document.source else 'open an STL, or open a GOO to inspect an existing file',
                8000)
            return
        settings = self.document.settings
        budget = services.budget_for(self.document)
        derived = self.document.derived
        if derived.layer_slicer is None:
            derived.layer_slicer = services.LayerSlicer(union, settings, budget)
        slicer = derived.layer_slicer
        self.jobs.submit('layer', lambda token, progress: slicer.slice(index, token))

    # ---- GOO inspection --------------------------------------------------
    @property
    def goo_path(self):
        return None if self.goo_source is None else Path(self.goo_source['path'])

    def open_goo(self, path):
        self.jobs.submit('open_goo', lambda token, progress: services.open_goo(path))

    def open_goo_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Open GOO or CTB', '',
            'Slice files (*.goo *.ctb);;Elegoo GOO (*.goo);;CTB v3 (*.ctb)')
        if path:
            self.open_goo(path)

    def close_goo(self):
        if self.goo_source is None:
            return
        self.document.derived.layer_cache.drop_source('goo')
        self.goo_source = None
        self.layers.clear_goo_source()
        self._on_layer_source_changed('union')

    def verify_goo_dialog(self):
        path = self.goo_path
        if path is None:
            chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, 'Verify GOO or CTB', '',
                'Slice files (*.goo *.ctb);;Elegoo GOO (*.goo);;CTB v3 (*.ctb)')
            if not chosen:
                return
            path = Path(chosen)
        self.verify_goo(path)

    def verify_goo(self, path):
        """The same deep check ``voxelmill verify`` runs, on the same code."""
        settings = self.document.settings
        self.statusBar().showMessage(f'verifying {Path(path).name}...')
        return self.jobs.submit('verify', lambda token, progress: services.verify_goo_file(
            path, settings, cancel=token, progress=progress))

    # ---- whole-file checks -----------------------------------------------
    def measure_stl_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Measure STL', '', 'STL (*.stl)')
        if path:
            self.measure_stl(path)

    def measure_stl(self, path=None):
        """The same sizes ``voxelmill measure`` prints, for the editor's own pose."""
        document = self.document
        source = path or document.source
        if not source:
            self.statusBar().showMessage('open a model first, or choose a file to measure', 8000)
            return None
        rotation = (0.0, 0.0, 0.0) if document.rotation_deg == 'auto' else document.rotation_deg
        settings = document.settings
        self.statusBar().showMessage(f'measuring {Path(source).name}...')
        return self.jobs.submit('measure', lambda token, progress: services.measure_file(
            source, settings, rotate=rotation, center_offset=document.center_offset_mm,
            lift_mm=document.model_lift_mm, scale=document.scale_factors,
            mirror=document.mirror_axes, cancel=token, progress=progress))

    def _finish_measure(self, value):
        self.diagnostics.set_payload(value)
        size = value['placed_size_mm']
        self.statusBar().showMessage(
            'measured ' + ' x '.join(f'{v:.3f}' for v in size)
            + f" mm, fits={value['fits']}"
            + (f"; {value['transform_note']}" if value['transform_note'] else ''), 20000)

    def inspect_stl_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Inspect STL', '', 'STL (*.stl)')
        if path:
            self.inspect_stl_file(path)

    def inspect_stl_file(self, path):
        """The same inventory ``voxelmill inspect`` prints, on the same code."""
        settings = self.document.settings
        self.statusBar().showMessage(f'inspecting {Path(path).name}...')
        return self.jobs.submit('inspect', lambda token, progress: services.inspect_file(
            path, settings, cancel=token, progress=progress))

    def validate_stl_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Validate STL', '', 'STL (*.stl)')
        if path:
            self.validate_stl_file(path)

    def validate_stl_file(self, path):
        """The same check ``voxelmill validate`` runs, with nothing written."""
        settings = self.document.settings
        self.statusBar().showMessage(f'validating {Path(path).name}...')
        return self.jobs.submit('validate_file', lambda token, progress: services.validate_file(
            path, settings, cancel=token, progress=progress))

    def _finish_inspect(self, value):
        self.diagnostics.set_payload(value)
        mesh = value['meshes'][0]
        self.statusBar().showMessage(
            f"{Path(value['input']).name}: {mesh['triangle_count']} triangles, "
            f"{mesh['connected_components']} components, "
            f"{mesh['degenerate_triangles']} degenerate, "
            f"{mesh['nonmanifold_edges']} nonmanifold edges, "
            f"{mesh['self_intersections']} self-intersections", 20000)

    def _finish_validate_file(self, value):
        report = value['report']
        self._set_report(report)
        self.diagnostics.set_payload(value)
        state = 'passed' if report['passed'] else 'FAILED'
        self.statusBar().showMessage(
            f"{Path(value['input']).name}: validation {state}", 15000)

    def _on_layer_source_changed(self, source):
        if source == 'goo' and self.goo_source is not None:
            layers = self.goo_source['layer_count']
        elif self.document.derived.union is not None:
            union = self.document.derived.union
            height = self.document.settings['process']['layer_height_mm']
            top = float(np.asarray(union.bounding_box()).reshape(2, 3)[1, 2])
            layers = max(1, int(np.ceil(top / height)))
        else:
            layers = 0
        self.layers.set_range(layers)
        self.faults.set_range(layers)
        if self.tabs.currentIndex() == self.faults_tab_index:
            self.request_layer(self.faults.slider.value())
        else:
            self.request_layer(self.layers.slider.value())

    def _on_tab_changed(self, index):
        previous = getattr(self, '_previous_tab_index', index)
        self._previous_tab_index = index
        if previous == self.faults_tab_index and index != self.faults_tab_index:
            self._leave_faults_tab()
        if index == self.layers_tab_index:
            self.request_layer(self.layers.slider.value())
        elif index == self.faults_tab_index:
            self._enter_faults_tab()

    def _fault_bounds(self):
        union = self.document.derived.union
        if union is not None:
            return np.asarray(union.bounding_box(), dtype=float).reshape(2, 3)
        for role in ('model', 'supports', 'raft'):
            names = [name for name in self.scene.actors
                     if name == role or name.startswith(f'{role}:')]
            if not names:
                continue
            bounds = np.array([self.scene.actors[name].GetBounds() for name in names], dtype=float)
            return np.asarray([[bounds[:, 0].min(), bounds[:, 2].min(), bounds[:, 4].min()],
                               [bounds[:, 1].max(), bounds[:, 3].max(), bounds[:, 5].max()]],
                              dtype=float)
        build = self.document.settings['printer']['build_mm']
        return np.asarray([[-build[0] / 2, -build[1] / 2, 0.0],
                           [build[0] / 2, build[1] / 2, build[2]]], dtype=float)

    def _fault_markers(self):
        report = self.document.derived.validation
        plan = self.document.derived.plan
        return fault_overlay(report, plan, bounds=self._fault_bounds())

    def _sync_z_extent(self):
        """Keep both Z-clip controls on the same world-Z range as the scene."""
        bounds = self._fault_bounds()
        # Keep the plate at the bottom of the slider so a clip-from-below
        # of 0 mm is the bed, not the model's current lowest point.
        zmin = min(0.0, float(bounds[0, 2]))
        zmax = max(float(bounds[1, 2]), zmin + 1.0)
        if hasattr(self, 'z_clip_slider'):
            self.z_clip_slider.set_extent(zmin, zmax)
        self.faults.set_z_extent(zmin, zmax, reset=False)
        lo, hi = self.z_clip_slider.clip_limits()
        self.faults.set_clip(lo, hi)
        self._apply_z_clip(lo, hi)

    def _apply_z_clip(self, zmin, zmax):
        self.scene.set_z_clip(zmin, zmax)
        if self.viewport:
            self.viewport.render()

    def _on_view_z_clip(self, zmin, zmax):
        self.faults.set_clip(zmin, zmax)
        self._apply_z_clip(zmin, zmax)

    def _on_show_all_clip(self):
        if hasattr(self, 'z_clip_slider'):
            self.z_clip_slider.set_clip(None, None)
        self.faults.set_clip(None, None)
        self._apply_z_clip(None, None)

    def _enter_faults_tab(self):
        self._sync_z_extent()
        self.faults.set_markers(self._fault_markers())
        if self.layers.slider.isEnabled():
            self.faults.set_range(self.layers.slider.maximum() + 1)
            self.faults.slider.setValue(self.layers.slider.value())
        lo, hi = self.z_clip_slider.clip_limits()
        self.faults.set_clip(lo, hi)
        self._apply_z_clip(lo, hi)
        self._refresh_fault_glyphs()
        self.request_layer(self.faults.slider.value())

    def _leave_faults_tab(self):
        # Z clipping belongs to the 3D view, so leaving Faults must not clear
        # it. Only the fault glyphs are Faults-tab state.
        self.scene.clear('faults')
        if self.viewport:
            self.viewport.render()

    def _on_fault_clip(self, zmin, zmax):
        if hasattr(self, 'z_clip_slider'):
            self.z_clip_slider.set_clip(zmin, zmax)
        self._apply_z_clip(zmin, zmax)

    def _refresh_fault_glyphs(self):
        if getattr(self, 'tabs', None) is None or not hasattr(self, 'faults_tab_index'):
            return
        if self.tabs.currentIndex() != self.faults_tab_index:
            return
        markers = [m for m in self.faults.filtered_markers() if m.get('position_mm')]
        self.scene.set_faults(markers)
        if self.viewport:
            self.viewport.render()

    def _finish_open_goo(self, value):
        self.goo_source = value
        self.document.derived.layer_cache.drop_source('goo')
        self.layers.set_goo_source(value['path'], value['layer_count'], value['small_preview'])
        self.statusBar().showMessage(
            f"opened {Path(value['path']).name}: {value['layer_count']} layers, "
            f"{value['shape_px'][1]}x{value['shape_px'][0]} px", 10000)
        if getattr(self, 'tabs', None) is not None:
            self.tabs.setCurrentIndex(self.layers_tab_index)

    def _finish_verify(self, value):
        report = value['report']
        self.layers.set_diagnostic_codes({d['code'] for d in report['diagnostics']})
        self._set_report(report)
        # The header, the settings taken from the file and the claim boundary
        # matter as much as the checks, so the full payload replaces the
        # report-only text the list view populated.
        self.diagnostics.set_payload(value)
        state = 'passed' if report['passed'] else 'FAILED'
        self.statusBar().showMessage(
            f"GOO verification {state}: {len(report['diagnostics'])} diagnostics", 15000)

    def validate(self, path=None, drainage=True):
        self._clear_preview_transforms()
        union = self.document.derived.union
        if union is None:
            return None
        document = self.document
        if path is not None:
            target = Path(path)
        elif self._pending_export is not None:
            self._validation_path = self._new_validation_path()
            target = self._validation_path
        else:
            directory = Path(self.scratch) if self.scratch else Path(tempfile.mkdtemp(prefix='voxelmill-preview-'))
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / 'preview.stl'
        return self.jobs.submit('validate', lambda token, progress: {
            'path': str(target),
            'report': services.export_and_validate(document, union, target, token, progress,
                                                   drainage=drainage)})

    # ---- job results ---------------------------------------------------
    def _on_progress(self, label, done, total):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.statusBar().showMessage(label)

    def _on_completed(self, result):
        if result.canceled:
            if result.name in ('validate', 'goo'):
                self._discard_validation_path()
            self.statusBar().showMessage(f'{result.name} canceled', 4000)
            return
        if result.error:
            return self._report_error(result.error, result.name)
        handler = getattr(self, f'_finish_{result.name}', None)
        if handler:
            try:
                handler(result.value)
            except Exception as error:
                return self._report_error({
                    'code': 'internal_error',
                    'message': f'{type(error).__name__}: {error}',
                }, result.name)
        self.stage_changed.emit(result.name)

    def _report_error(self, error, name='job'):
        self.last_error = error
        if name in ('validate', 'goo'):
            self._discard_validation_path()
        message = f'{name} failed: {error.get("message", "")}'
        self.notify(message, category=error.get('code') or name, level='error')
        self.statusBar().showMessage(message, 10000)
        self.diagnostics.set_payload({'error': error})

    def _set_report(self, report, *, note=None):
        """Show every report field plus selectable diagnostics, losing neither."""
        payload = report.to_dict() if hasattr(report, 'to_dict') else report
        if note:
            payload = {'note': note, 'validation': payload}
        self.diagnostics.set_payload(payload)
        self.diagnostic_list.clear()
        diagnostics = report.diagnostics if hasattr(report, 'diagnostics') else report.get('diagnostics', [])
        issue_layers = {}
        for diagnostic in diagnostics:
            data = diagnostic.to_dict() if hasattr(diagnostic, 'to_dict') else (
                asdict(diagnostic) if hasattr(diagnostic, '__dataclass_fields__') else diagnostic)
            item = QtWidgets.QTreeWidgetItem([
                f"{data.get('code', 'diagnostic')}: {data.get('message', '')}",
                '' if data.get('layer') is None else str(data['layer']),
                data.get('severity', 'error'),
            ])
            item.setData(0, QtCore.Qt.UserRole, data)
            self.diagnostic_list.addTopLevelItem(item)
            layer = data.get('layer')
            if layer is not None:
                issue_layers.setdefault(data.get('code', 'diagnostic'), set()).add(int(layer))
        self.diagnostic_list.resizeColumnToContents(1)
        # Whatever report just landed, the layer view's per-issue markers and
        # Prev/Next navigation describe it, not whatever was shown before.
        self.layers.set_issue_layers({code: sorted(layers) for code, layers in issue_layers.items()})

    def _select_diagnostic(self, item, _column):
        diagnostic = item.data(0, QtCore.Qt.UserRole) or {}
        self.tabs.setCurrentIndex(self.layers_tab_index)
        layer = diagnostic.get('layer')
        if layer is not None:
            self.layers.slider.setValue(int(layer))
            self.request_layer(int(layer))

    def _sync_issue_visibility_action(self, code, visible):
        """Keep the View menu's checkbox in step with the layer view's own state."""
        action = self.actions_map.get(f'show_issue_{code}')
        if action is not None and action.isChecked() != visible:
            action.blockSignals(True)
            action.setChecked(visible)
            action.blockSignals(False)

    def _jump_issue_layer(self, direction):
        """Move the layer slider to the next/previous layer with a visible issue.

        Moving the slider already asks for that layer through the same
        ``layer_requested`` wiring a drag uses, so nothing further is needed
        here once the target is known.
        """
        index = self.layers.slider.value()
        target = (self.layers.next_issue_layer(index) if direction > 0
                  else self.layers.previous_issue_layer(index))
        if target is not None:
            self.layers.slider.setValue(target)

    def _finish_place(self, value):
        document = self.document
        placement = value['placement']
        document.source_sha256 = value['asset'].sha256
        search = getattr(placement, 'search', None) or {}
        ranked = search.get('ranked_candidates')
        context = self._orientation_context_snapshot()
        if isinstance(ranked, list) and ranked:
            self._set_orientation_candidates(ranked, search.get('selected_rank', 1))
        elif self._orientation_candidates_context != context:
            self._clear_orientation_candidates()
        document.placement = placement
        self.placed = value['placed']
        extra = value.get('extra_models') or {}
        document.derived.part_meshes = extra.get('placed_parts')
        document.derived.part_matrices = [np.asarray(placement.matrix, dtype=float)] + [
            np.asarray(part['placement']['matrix'], dtype=float)
            for part in extra.get('parts', [])]
        self._display_part_counts = [int(value['asset'].triangle_count)]
        self._display_part_counts.extend(int(part['triangles']) for part in extra.get('parts', []))
        self.scratch = value['scratch']
        self.placement_fits = bool(value.get('fits', True))
        self.placement_overflow_mm = value.get('overflow_mm') or [0.0, 0.0, 0.0]
        if not self.placement_fits:
            overflow = self.placement_overflow_mm
            note = value.get('search_note')
            self.statusBar().showMessage(
                f'model exceeds the build volume by X {overflow[0]:.2f} Y {overflow[1]:.2f} '
                f'Z {overflow[2]:.2f} mm; unreachable triangles are red'
                + (f' -- {note}' if note else ''), 20000)
        if document.rotation_deg == 'auto':
            for box, angle in zip(self.rotation, placement.rotation_deg):
                box.setValue(angle)
        self._sync_widgets_from_document()
        self._sync_measurement()
        placed, placement = value['placed'], value['placement']
        self.jobs.submit('model', lambda token, progress: services.build_model(
            document, placed, placement, token, progress))

    def _finish_model(self, value):
        derived = self.document.derived
        derived.solid, derived.repair = value['solid'], value['repair']
        derived.model_triangles, derived.model_bounds = value['triangles'], value['bounds']
        self._redisplay_models()
        self.scene.set_visible('model', self.visibility['model'].isChecked())
        self.object_panel.set_objects(self.object_specs(),
                                      selected=self.object_panel.list.currentRow())
        flagged = self.scene.out_of_bounds.get('model', 0)
        if flagged:
            self.statusBar().showMessage(
                f'display-only count: {flagged} triangles fall outside the build volume and are drawn red; '
                'the printer cannot reach them', 20000)
        if self.viewport:
            self.viewport.reset_camera()
            if self.viewport.transform_index is not None:
                self.viewport.select_transform_object(self.viewport.transform_index)
        self._sync_z_extent()
        document = self.document
        triangles, bounds = value['triangles'], value['bounds']
        self.jobs.submit('supports', lambda token, progress: services.build_supports(
            document, triangles, bounds, None, token, progress))

    def _finish_supports(self, value):
        derived = self.document.derived
        derived.column_field = value['field']
        derived.plan, derived.raft = value['plan'], value['raft']
        import manifold3d as m
        supports = (m.Manifold.batch_boolean(value['plan'].solids, m.OpType.Add)
                    if value['plan'].solids else None)
        from ..geometry import manifold_triangles
        self.scene.set_mesh('supports', manifold_triangles(supports) if supports else None)
        self.scene.set_mesh('raft', manifold_triangles(value['raft']) if value['raft'] else None)
        self.scene.set_contacts(value['contacts'])
        self._set_contact_list(value['contacts'])
        for role, box in self.visibility.items():
            self.scene.set_visible(role, box.isChecked())
        if self.viewport:
            self.viewport.render()
        self.statusBar().showMessage(
            f"{value['plan'].metrics['contacts_routed']} contacts routed, "
            f"{value['plan'].metrics['contacts_failed']} unroutable", 8000)
        self._assemble()

    def _finish_union(self, value):
        self.document.derived.union = value
        self.document.derived.layer_slicer = None
        # A new assembly invalidates every slice taken from the old one; an
        # opened GOO is untouched by it.
        self.document.derived.layer_cache.drop_source('union')
        height = self.document.settings['process']['layer_height_mm']
        top = float(np.asarray(value.bounding_box()).reshape(2, 3)[1, 2])
        layer_count = max(1, int(np.ceil(top / height)))
        if self.layers.selected_source == 'union':
            self.layers.set_range(layer_count)
            self.faults.set_range(layer_count)
        self._sync_z_extent()
        self.statusBar().showMessage(f'assembled {value.num_tri()} triangles', 6000)
        # D5: the badge is re-earned after every edit rather than left showing
        # a result that describes geometry the user has since changed.
        if self.auto_island_check and self._pending_export is None:
            self.check_islands(show_report=False)
        if self._pending_export is not None:
            self.validate()
        elif self.tabs.currentIndex() == self.layers_tab_index and self.layers.selected_source == 'union':
            self.request_layer(self.layers.slider.value())
        elif self.tabs.currentIndex() == self.faults_tab_index and self.layers.selected_source == 'union':
            self._enter_faults_tab()

    def _finish_layer(self, value):
        source = value.get('source', 'union')
        source_path = value.get('source_path', str(self.goo_path))
        key = ((source, value['index']) if source == 'union'
               else (source, source_path, value['index']))
        self.document.derived.layer_cache.put(key, value)
        if (source != self.layers.selected_source or value['index'] != self._requested_layer
                or (source == 'goo' and source_path != str(self.goo_path))):
            return
        report = self.document.derived.validation
        diagnostics = report.diagnostics if report else []
        if source == 'goo':
            # A union validation report indexes the assembly's layers, not this
            # file's; showing its markers over decoded pixels would invent a
            # correspondence that does not exist.
            diagnostics = []
        if self.tabs.currentIndex() == self.faults_tab_index:
            markers = self.faults.filtered_markers(value['index'])
            if source == 'goo':
                markers = []
            self.faults.show_layer(value, markers)
        else:
            self.layers.show_layer(value, [d for d in diagnostics if d.layer == value['index']])

    def _finish_validate(self, value):
        report = value['report']
        self.document.derived.validation = report
        self.document.saved_validation = report.to_dict()
        self.layers.set_diagnostic_codes({d.code for d in report.diagnostics})
        self.faults.set_markers(self._fault_markers())
        if self.tabs.currentIndex() == self.faults_tab_index:
            self._refresh_fault_glyphs()
            self.request_layer(self.faults.slider.value())
        self._set_report(report)
        state = 'passed' if report.passed else 'FAILED'
        self.statusBar().showMessage(f'validation {state}', 10000)
        if self._pending_export is not None:
            pending, self._pending_export = self._pending_export, None
            target, format_name = pending['path'], pending['format']
            if report.passed or self.allow_unresolved:
                if format_name in ('goo', 'ctb'):
                    self.jobs.submit('goo', lambda token, progress: services.export_goo(
                        self.document, Path(value['path']), target, token, progress,
                        allow_unresolved=self.allow_unresolved))
                    return
                Path(value['path']).replace(target)
                self._validation_path = None
                self.statusBar().showMessage(
                    f'exported {target}' + ('' if report.passed else ' (warned)'), 10000)
            else:
                self._discard_validation_path()
                self.statusBar().showMessage(
                    'export refused: validation did not pass; enable Allow warned export', 15000)

    def _finish_goo(self, value):
        self._discard_validation_path()
        self.diagnostics.set_payload(value)
        self.statusBar().showMessage(f"exported {value.get('output', 'GOO file')}", 10000)

    # ---- interaction ---------------------------------------------------
    def _on_pick(self, position, role):
        modifiers = QtWidgets.QApplication.keyboardModifiers()
        self.handle_pick(position, role, modifiers)

    def _on_paint_mode(self, mode):
        if self.viewport is not None:
            self.viewport.paint_enabled = mode in ('block', 'enforce')
        # The Setup combo and the tool strip are one choice shown twice.
        if getattr(self, 'tools', None) is not None:
            self.tools.set_tool(mode if mode in ('block', 'enforce') else
                                (self.tools.tool if self.tools.tool in ('add', 'remove') else 'select'))

    def _on_tool_changed(self, tool):
        """Paint tools drive the paint mode; click tools drive ``handle_pick``."""
        mode = tool if tool in ('block', 'enforce') else 'off'
        if self.paint_mode.currentText() != mode:
            self.paint_mode.blockSignals(True)
            self.paint_mode.setCurrentText(mode)
            self.paint_mode.blockSignals(False)
        if self.viewport is not None:
            self.viewport.paint_enabled = mode in ('block', 'enforce')
        hints = {name: tip for name, _label, tip in TOOLS}
        self.statusBar().showMessage(hints[tool], 8000)

    def _on_brush_radius_changed(self, value):
        self.paint_radius.setValue(float(value))

    def object_matrices(self):
        """One placement matrix per plate object, ``None`` where unknown yet."""
        stored = list(getattr(self.document.derived, 'part_matrices', ()) or ())
        stored.extend([None] * (len(self.document.extra_models) + 1 - len(stored)))
        return stored[:len(self.document.extra_models) + 1]

    def plate_paint(self):
        """Every object's paint in plate coordinates, for display and routing."""
        from ..paint import to_plate
        return to_plate(self.document.normalized_paint(), self.object_matrices())

    def _object_placed_triangles(self, index):
        """One object's placed triangles, from the same split the display uses."""
        displayed = self.placed if self.placed is not None else self.document.derived.model_triangles
        if displayed is None:
            return None
        displayed = np.asarray(displayed).reshape(-1, 3, 3)
        counts = list(getattr(self, '_display_part_counts', ()) or ())
        if not counts or int(sum(counts)) != len(displayed):
            return displayed if index == 0 else None
        if not 0 <= index < len(counts):
            return None
        start = int(sum(counts[:index]))
        return displayed[start:start + int(counts[index])]

    def _on_drop_to_plate_requested(self, indices):
        """Zero the lift for every named part: rest each one on the bed.

        ``lift_mm`` is already the height of the part's lowest point in its
        current rotation (see ``_placement`` in geometry.py), so setting it to
        zero is what "resting on the bed" means -- there is no extra geometry
        to compute here, which is why this looks like too little code.
        """
        self._clear_preview_transforms()
        ok = True
        for index in indices:
            if not 0 <= index <= len(self.document.extra_models):
                continue
            pose = dict(self._object_pose(index))
            pose['lift_mm'] = 0.0
            if not self._write_pose(index, pose):
                ok = False
        return ok

    def _on_zoom_to_selected_requested(self, indices):
        """Frame the selected parts' actors, the way ``fit_view`` frames the scene."""
        if self.scene is None or not indices:
            return False
        actors = [self.scene.actors.get('model' if index == 0 else f'model:{index}')
                 for index in indices]
        actors = [actor for actor in actors if actor is not None]
        if not actors:
            self.statusBar().showMessage('nothing to zoom to yet; wait for the preview', 6000)
            return False
        bounds = np.asarray([actor.GetBounds() for actor in actors], dtype=float).reshape(-1, 2, 3)
        low = bounds[:, 0, :].min(axis=0)
        high = bounds[:, 1, :].max(axis=0)
        # Same two calls ``CameraController.fit`` makes, just over the
        # selected actors' bounds rather than everything visible.
        self.camera.renderer.ResetCamera(low[0], high[0], low[1], high[1], low[2], high[2])
        self.camera.renderer.ResetCameraClippingRange()
        if self.viewport:
            self.viewport.render()
        return True

    def auto_orient_selected(self, index=None):
        """Search for a better rotation for one part, in the background.

        The plate-global search (``rotate_auto`` / ``geometry.auto_placement``
        via the ``place`` job) rotates the raw imported mesh. This instead
        feeds the search the part's *placed* geometry -- the only geometry
        available for one object among several already on the plate -- and
        composes its answer with the rotation already applied; see
        ``services.auto_orient_object`` for why that composition is needed.
        """
        self._clear_preview_transforms()
        index = self.object_panel.current_index() if index is None else int(index)
        if not 0 <= index <= len(self.document.extra_models):
            return False
        triangles = self._object_placed_triangles(index)
        if triangles is None or not len(triangles):
            self.notify('No placed geometry yet for auto-orient; wait for the preview to finish.',
                        category='orientation', level='warning')
            return False
        pose = self._object_pose(index)
        document = self.document
        self.statusBar().showMessage(f'searching for an orientation for part {index}...')
        self.jobs.submit('orient_object', lambda token, progress: {
            **services.auto_orient_object(triangles, pose['rotate'], document.settings,
                                          pose['center_offset'], pose['lift_mm'], token, progress),
            'index': index, 'pose': pose})
        return True

    def _finish_orient_object(self, value):
        index, pose = value['index'], dict(value['pose'])
        pose['rotate'] = value['rotate']
        self._write_pose(index, pose)
        self.statusBar().showMessage(f'auto-orient applied to part {index}', 6000)

    def _on_painted(self, position, object_index=0):
        """Brush one part, and store the marks in that part's own frame."""
        if self.paint_mode.currentText() == 'off':
            return
        index = int(object_index)
        triangles = self._object_placed_triangles(index)
        matrix = self.object_matrices()[index] if index < len(self.object_matrices()) else None
        if triangles is None or matrix is None:
            return
        from ..paint import brush_centroids, to_local
        try:
            marks = brush_centroids(triangles, position, self.paint_radius.value())
            local = to_local(marks, matrix)
        except VoxelMillError as error:
            self._report_error(error.to_dict(), 'paint')
            return
        self._paint_buffer.append((index, local.tolist()))

    def _on_paint_finished(self):
        kind = {'block': 'blocked', 'enforce': 'enforced'}.get(self.paint_mode.currentText())
        strokes = self._paint_buffer
        self._paint_buffer = []
        if not kind or not strokes:
            return
        grouped = {}
        for index, marks in strokes:
            grouped.setdefault(index, []).extend(marks)
        try:
            for index, marks in sorted(grouped.items()):
                self.document.paint_marks(kind, marks, index)
        except VoxelMillError as error:
            return self._report_error(error.to_dict(), 'paint')
        self._refresh_undo()
        if self.document.derived.model_triangles is not None:
            self._redisplay_models()
        self.rebuild()
        return True

    def _clear_paint(self, kind):
        """Clear this kind on the selected part, not across the whole plate."""
        self.document.clear_paint(kind, object_index=self.object_panel.current_index())
        self._refresh_undo()
        if self.document.derived.model_triangles is not None:
            self._redisplay_models()
        self.rebuild()

    def add_extra_model_dialog(self):
        """Pick one or more STLs (and STEP when FreeCAD is available).

        Asking for a center offset per file, before the part was even loaded,
        made adding a second part a guessing game about where it would land.
        The plate is arranged afterwards instead, and the part can then be
        moved with the same controls as everything else.
        """
        from ..importers import find_freecad
        step_ok = find_freecad(preferred=self._freecad_preferred()) is not None
        filters = 'STL (*.stl);;STEP (*.step *.stp)' if step_ok else 'STL (*.stl)'
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, 'Add model', '', filters)
        if not paths:
            return None
        resolved = []
        for path in paths:
            suffix = Path(path).suffix.lower()
            if suffix in ('.step', '.stp'):
                stl = self._tessellate_step_to_temp(path)
                if stl is None:
                    continue
                resolved.append(str(stl))
            else:
                resolved.append(path)
        if not resolved:
            return None
        return self.add_models(resolved)

    def handle_pick(self, position, role, modifiers=QtCore.Qt.NoModifier):
        """Shift adds a contact, Ctrl deletes, Alt moves the pending contact."""
        radius = self.document.settings['support']['spacing_mm'] / 2
        tool = self.tools.tool if getattr(self, 'tools', None) is not None else 'select'
        plain = not (modifiers & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier
                                  | QtCore.Qt.AltModifier))
        if (modifiers & QtCore.Qt.ShiftModifier or (plain and tool == 'add')) and role == 'model':
            self.document.add_contact(position)
        elif modifiers & QtCore.Qt.ControlModifier or (plain and tool == 'remove'):
            existing = self.scene.nearest_contact(position, radius)
            if existing is None:
                return None
            self.document.remove_contact(existing)
        elif modifiers & QtCore.Qt.AltModifier:
            existing = self.scene.nearest_contact(position, radius)
            if existing is None:
                return None
            self.document.move_contact(existing, position)
        else:
            return None
        self._refresh_undo()
        self.rebuild()
        return True

    # ---- files ---------------------------------------------------------
    def _autosave(self):
        """Write a crash-recovery project if the document has a source and is dirty."""
        if not self.document.source or not self.document.dirty:
            return
        path = autosave_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.document.save(path)
            self.document.dirty = True  # save() clears dirty; recovery is not a user save
            self.statusBar().showMessage(f'autosaved {path}', 4000)
        except Exception as error:
            self.notify(f'autosave failed: {error}', category='autosave', level='error')
            self.statusBar().showMessage(f'autosave failed: {error}', 8000)

    def _redisplay_models(self):
        """Draw each loaded part as its own model actor.

        The primary stays under the ``model`` key so visibility, clipping,
        out-of-bounds counts and existing tests keep working. Added parts use
        ``model:1``, ``model:2``, ... so they can be transformed independently.
        """
        self.scene.clear('model')
        displayed = self.placed if self.placed is not None else self.document.derived.model_triangles
        if displayed is None:
            return
        displayed = np.asarray(displayed)
        counts = list(getattr(self, '_display_part_counts', ()) or ())
        if not counts or int(sum(counts)) != len(displayed):
            self.scene.set_mesh('model', displayed, settings=self.document.settings,
                                paint=self._plate_paint_for(0), object_index=0)
            return
        start = 0
        for index, count in enumerate(counts):
            key = 'model' if index == 0 else f'model:{index}'
            self.scene.set_mesh(
                'model', displayed[start:start + count],
                settings=self.document.settings,
                paint=self._plate_paint_for(index),
                key=key, object_index=index)
            start += count
        self.scene.out_of_bounds['model'] = sum(
            n for name, n in self.scene.out_of_bounds.items()
            if name == 'model' or name.startswith('model:'))

    def _plate_paint_for(self, index):
        """One object's marks in plate coordinates, for coloring its actor."""
        from ..paint import to_plate
        matrices = self.object_matrices()
        if index >= len(matrices) or matrices[index] is None:
            return None
        record = self.document.object_paint(index)
        return to_plate([record], [matrices[index]])

    def open_stl(self, path):
        if not self._confirm_discard_or_save():
            return
        self.jobs.invalidate()
        self._clear_orientation_candidates()
        if self.scene is not None:
            self.scene.clear()
        self.placed = None
        self._display_part_counts = []
        self.document = Document(self.document.settings, path)
        self.project_path = None
        self._sync_widgets_from_document()
        self._refresh_undo()
        self.reload()

    def open_stl_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Open STL', '', 'STL (*.stl)')
        if path:
            self.open_stl(path)

    def import_step_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Import STEP', '', 'STEP (*.step *.stp)')
        if path:
            return self.import_step(path)
        return None

    def _tessellate_step_to_temp(self, path):
        """Tessellate STEP to a durable temp STL using document repair settings."""
        from ..importers import tessellate_step
        fd, stl = tempfile.mkstemp(suffix='.stl', prefix='voxelmill-step-')
        os.close(fd)
        stl_path = Path(stl)
        try:
            tessellate_step(path, self.document.settings, stl_path,
                            preferred=self._freecad_preferred())
        except VoxelMillError as error:
            try:
                stl_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._report_error(error.to_dict(), 'import STEP')
            return None
        return stl_path

    def import_step(self, path):
        stl_path = self._tessellate_step_to_temp(path)
        if stl_path is None:
            return None
        self.open_stl(stl_path)
        return stl_path

    def open_project_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Open project', '', 'VoxelMill (*.voxmil)')
        if path:
            self.open_project(path)

    def open_project(self, path, extract_dir=None):
        if not self._confirm_discard_or_save():
            return
        self._clear_orientation_candidates()
        self.document = Document.load(path, extract_dir or self.scratch)
        self.project_path = str(Path(path).resolve())
        self.jobs.invalidate()
        self._refresh_undo()
        self._sync_widgets_from_document()
        self.scene.show_build_volume(self.document.settings)
        if self.document.saved_validation:
            self._set_report(self.document.saved_validation,
                             note='validation as saved; revalidate before exporting')
        if self.document.source:
            self.reload()

    def save_project(self, _checked=False):
        """Overwrite the known project path, or fall through to Save As."""
        if self.project_path:
            self._clear_preview_transforms()
            self.document.save(self.project_path)
            self._refresh_window_title()
            return True
        return bool(self.save_project_as())

    def save_project_as(self, _checked=False):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save project', '', 'VoxelMill (*.voxmil)')
        if not path:
            return False
        self._clear_preview_transforms()
        path = with_suffix_if_missing(path, '.voxmil')
        self.document.save(path)
        self.project_path = str(Path(path).resolve())
        self._refresh_window_title()
        return True

    def save_project_dialog(self):
        return self.save_project_as()

    def new_project(self, _checked=False):
        if not self._confirm_discard_or_save():
            return False
        self.jobs.invalidate()
        self._clear_orientation_candidates()
        if self.scene is not None:
            self.scene.clear()
        self.placed = None
        self._display_part_counts = []
        self.last_error = None
        self._discard_validation_path()
        self._pending_export = None
        self.document.reset_for_new_project()
        self.project_path = None
        self.set_attachment_state('none')
        self._set_island_badge(None)
        self.diagnostics.clear()
        self.diagnostic_list.clear()
        self.layers.set_issue_layers({})
        self.scene.show_build_volume(self.document.settings)
        self._sync_widgets_from_document()
        self._refresh_undo()
        return True

    def export_dialog(self):
        path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, 'Export supported part', '',
            'STL (*.stl);;Elegoo GOO (*.goo);;CTB v3 (*.ctb)')
        if path:
            # The filter the user picked, not a suffix they may not have
            # typed, says which format a bare name should get.
            picked = selected_filter.rsplit('*', 1)[-1].rstrip(')') if selected_filter else '.stl'
            path = with_suffix_if_missing(path, picked or '.stl')
            suffix = Path(path).suffix.lower()
            format_name = 'goo' if suffix == '.goo' else 'ctb' if suffix == '.ctb' else 'stl'
            self.export(path, format_name=format_name)

    def export_goo_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export Elegoo GOO', '', 'Elegoo GOO (*.goo)')
        if path:
            self.export(with_suffix_if_missing(path, '.goo'), format_name='goo')

    def export_ctb_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Export CTB v3', '', 'CTB v3 (*.ctb)')
        if path:
            self.export(with_suffix_if_missing(path, '.ctb'), format_name='ctb')

    def _discard_validation_path(self):
        if self._validation_path is not None:
            Path(self._validation_path).unlink(missing_ok=True)
            self._validation_path = None

    def _new_validation_path(self):
        # Validation writes only an application-owned staging file.  In
        # particular, a rejected export must never truncate an existing STL.
        target = self._pending_export['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(prefix=f'.{target.stem}.validate-', suffix='.stl',
                                             dir=target.parent, delete=False)
        handle.close()
        return Path(handle.name)

    def export(self, path, *, format_name='stl'):
        """Validate, then keep the file only if it passed or warnings are armed.

        An edit since the last assembly leaves no union to export, so the
        rebuild is queued and the export runs when it lands.
        """
        if format_name not in ('stl', 'goo', 'ctb'):
            raise ValueError(f'unknown export format {format_name!r}')
        self._clear_preview_transforms()
        target = Path(path)
        if self.document.source and target.resolve() == self.document.source.resolve():
            return self._report_error({'code': 'source_overwrite',
                                       'message': 'Output must differ from the original source',
                                       'details': {}}, 'export')
        if target.is_symlink():
            return self._report_error({'code': 'unsafe_output',
                                       'message': 'Export destination must not be a symlink',
                                       'details': {}}, 'export')
        self._discard_validation_path()
        self._pending_export = {'path': Path(path), 'format': format_name}
        if self.document.derived.union is None:
            return self.rebuild()
        return self.validate()


def capture_window(window, path):
    """Write a PNG of ``window``, 3D view included, without any OS permission.

    Composited from two sources on purpose. ``QWidget.grab`` renders the Qt
    tree through Qt's own painter, which is correct everywhere and needs no
    screen-recording grant; it cannot see inside the native VTK child, which
    comes out blank. So the render window is read back separately with
    ``vtkWindowToImageFilter`` and drawn into place.

    ``QScreen.grabWindow`` would capture both at once, but on macOS it goes
    through the window server and needs Screen Recording permission, which a
    process started over SSH cannot be granted. This path works there.
    """
    path = Path(path)
    pixmap = window.grab()
    viewport = getattr(window, 'viewport', None)
    interactor = getattr(viewport, 'interactor', None)
    if interactor is not None:
        try:
            _draw_render_window(window, interactor, pixmap)
        except Exception as error:                  # pragma: no cover - driver dependent
            # A screenshot is evidence, not a feature: losing the 3D content is
            # worth reporting, never worth failing the capture over.
            print(f'screenshot: 3D view not captured ({error})', file=sys.stderr)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not pixmap.save(str(path), 'PNG'):
        raise VoxelMillError('screenshot', f'Could not write a PNG to {path}')
    return path


def _draw_render_window(window, interactor, pixmap):
    """Read the VTK framebuffer back and paint it over the blank child area."""
    from vtkmodules.util import numpy_support
    render_window = interactor.GetRenderWindow()
    render_window.Render()
    shot = vtk.vtkWindowToImageFilter()
    shot.SetInput(render_window)
    shot.ReadFrontBufferOff()
    shot.Update()
    image = shot.GetOutput()
    width, height, _ = image.GetDimensions()
    if width <= 0 or height <= 0:
        raise ValueError('the render window reported an empty framebuffer')
    pixels = numpy_support.vtk_to_numpy(image.GetPointData().GetScalars())
    pixels = pixels.reshape(height, width, -1)[::-1]    # VTK origin is bottom-left
    if pixels.shape[2] == 3:
        pixels = np.dstack([pixels, np.full((height, width, 1), 255, dtype=pixels.dtype)])
    pixels = np.ascontiguousarray(pixels, dtype=np.uint8)
    frame = QtGui.QImage(pixels.data, width, height, 4 * width,
                         QtGui.QImage.Format_RGBA8888).copy()
    # Logical coordinates, deliberately. The pixmap from grab() carries the
    # window's device pixel ratio, and QPainter applies that ratio itself, so
    # scaling the rectangle here as well double-counts it: on a Retina display
    # the 3D view landed at twice its size and spilled over the neighbouring
    # panel, while on a ratio-1 display the bug was invisible.
    origin = interactor.mapTo(window, QtCore.QPoint(0, 0))
    target = QtCore.QRect(origin, interactor.size())
    painter = QtGui.QPainter(pixmap)
    painter.drawImage(target, frame)
    painter.end()


def run(settings, args):
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    application.setWindowIcon(application_icon())
    # Hover text is the editor's field documentation, so its delay is a real
    # preference. Qt only reads it from the style, hence a proxy style here.
    install_hover_delay(application)
    window = MainWindow(settings, getattr(args, 'input', None))
    window.show()
    application.processEvents()
    if window.viewport:
        window.viewport.start()
    view = getattr(args, 'view', None)
    if view:
        window.set_view(view)
    goo = getattr(args, 'goo', None)
    if goo:
        window.open_goo(goo)
    window.complete_startup()
    screenshot = getattr(args, 'screenshot', None)
    if screenshot:
        # Let the first real paint and any startup job settle before capturing,
        # otherwise the evidence is a half-built window.
        delay = max(0, int(getattr(args, 'screenshot_delay_ms', 0) or 0))
        status = {'code': 0}

        def capture():
            try:
                print(f'screenshot: {capture_window(window, screenshot)}')
            except Exception as error:
                print(f'screenshot failed: {error}', file=sys.stderr)
                status['code'] = 1
            application.quit()

        QtCore.QTimer.singleShot(delay, capture)
        application.exec()
        return status['code']
    return application.exec()
