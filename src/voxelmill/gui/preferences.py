"""Application execution preferences backed by the portable settings table."""
from copy import deepcopy

from PySide6 import QtCore, QtWidgets

from ..acceleration import cuda_status, resolve_backend
from ..config import validate_settings
from ..contracts import VoxelMillError
from .appprefs import (DEFAULT_TRANSLATE_STEP_MM, MAX_TOOLTIP_DELAY_MS,
                       SNAP_ANGLE_CHOICES, install_hover_delay, load_preferences,
                       save_preferences)


class PreferencesDialog(QtWidgets.QDialog):
    settings_applied = QtCore.Signal(object)
    #: Editor preferences change nothing about the output, so they are applied
    #: and persisted separately from the settings table.
    editor_preferences_applied = QtCore.Signal(object)

    def __init__(self, document, parent=None, *, headless=False):
        super().__init__(parent)
        self.document = document
        self.applied = None
        self.setWindowTitle('Preferences')
        form = QtWidgets.QFormLayout(self)
        resources = document.settings['resources']
        self.acceleration = QtWidgets.QComboBox()
        self.acceleration.setObjectName('acceleration_backend')
        self.acceleration.addItems(['auto', 'cpu', 'cuda'])
        self.acceleration.setCurrentText(resources['acceleration'])
        self.acceleration.setToolTip(
            'Config key: resources.acceleration. CLI: --acceleration auto|cpu|cuda. '
            'Auto uses CUDA only after a successful runtime and device probe.')
        self.cuda_device = QtWidgets.QSpinBox()
        self.cuda_device.setRange(0, 255)
        self.cuda_device.setValue(resources['cuda_device'])
        self.cuda_device.setToolTip('Config key: resources.cuda_device. CLI: --cuda-device N.')
        self.workers = QtWidgets.QSpinBox()
        # 0 is the derive sentinel. The range has to admit it or the spin box
        # clamps the default up to 1 and Apply then writes single-worker mode
        # back into the document, which is the slowest possible setting.
        self.workers.setRange(0, 32)
        from ..topology import default_workers
        self.workers.setSpecialValueText(f'auto ({default_workers()})')
        self.workers.setValue(resources['workers'])
        self.memory = QtWidgets.QDoubleSpinBox()
        self.memory.setRange(.25, 1024)
        self.memory.setValue(resources['memory_gib'])
        self.memory.setSuffix(' GiB')
        self.status = QtWidgets.QLabel()
        self.status.setObjectName('acceleration_status')
        self.status.setWordWrap(True)
        self.editor_preferences = load_preferences()
        self.snap_angle = QtWidgets.QComboBox()
        self.snap_angle.setObjectName('snap_angle_deg')
        for choice in SNAP_ANGLE_CHOICES:
            self.snap_angle.addItem('off' if not choice else f'{choice:g} deg', choice)
        stored = self.snap_angle.findData(self.editor_preferences['snap_angle_deg'])
        self.snap_angle.setCurrentIndex(stored if stored >= 0 else 0)
        self.snap_angle.setToolTip(
            'Increment a rotation snaps to when a part is dragged in the 3D view or nudged '
            'with the +/- buttons. A typed angle is always taken exactly. This is an editor '
            'preference: it is not stored in a profile or a project.')
        form.addRow('Acceleration', self.acceleration)
        form.addRow('CUDA device', self.cuda_device)
        form.addRow('CPU workers', self.workers)
        self.translate_step = QtWidgets.QDoubleSpinBox()
        self.translate_step.setObjectName('translate_step_mm')
        self.translate_step.setDecimals(3)
        self.translate_step.setRange(0.001, 50.0)
        self.translate_step.setSuffix(' mm')
        self.translate_step.setValue(self.editor_preferences['translate_step_mm'])
        self.translate_step.setToolTip(
            'Distance one arrow-key press or one +/- button moves the selected part. '
            'Shift moves ten times as far. Editor preference: not stored in a profile '
            'or a project.')
        self.tooltip_delay = QtWidgets.QSpinBox()
        self.tooltip_delay.setObjectName('tooltip_delay_ms')
        self.tooltip_delay.setRange(0, int(MAX_TOOLTIP_DELAY_MS))
        self.tooltip_delay.setSingleStep(100)
        self.tooltip_delay.setSuffix(' ms')
        self.tooltip_delay.setSpecialValueText('immediately')
        self.tooltip_delay.setValue(int(self.editor_preferences['tooltip_delay_ms']))
        self.tooltip_delay.setToolTip(
            'How long the pointer must rest on a control before its hover text appears. '
            'Every option in the editor carries hover text explaining what it does, so '
            'raise this if the text gets in the way and lower it while learning the '
            'settings. Applies at once, to this window too. Editor preference: not '
            'stored in a profile or a project.')
        self.freecad_path = QtWidgets.QLineEdit()
        self.freecad_path.setObjectName('freecad_path')
        self.freecad_path.setText(self.editor_preferences.get('freecad_path') or '')
        self.freecad_path.setPlaceholderText('optional — only for STEP import')
        self.freecad_path.setToolTip(
            'Path to a FreeCAD binary, AppImage, or macOS FreeCAD.app bundle. Only '
            'needed for STEP (.step/.stp) import; STL, Prepare, and slice do not '
            'use it. Editor preference: not stored in a profile or a project.')
        browse = QtWidgets.QPushButton('Browse…')
        browse.setObjectName('freecad_path_browse')
        browse.clicked.connect(self._browse_freecad)
        freecad_row = QtWidgets.QHBoxLayout()
        freecad_row.addWidget(self.freecad_path, 1)
        freecad_row.addWidget(browse)
        self.island_passes = QtWidgets.QSpinBox()
        self.island_passes.setObjectName('max_island_passes')
        self.island_passes.setRange(1, 10)
        self.island_passes.setValue(document.settings['support']['max_island_passes'])
        self.island_passes.setToolTip(
            'Config key: support.max_island_passes. CLI: --max-passes. How many times '
            'Compute attachments may place attachments under the islands it found and '
            'look again. The last pass always rescans the whole build.')
        form.addRow('Memory ceiling', self.memory)
        form.addRow('Rotation snap', self.snap_angle)
        form.addRow('Nudge step', self.translate_step)
        form.addRow('Hover text delay', self.tooltip_delay)
        form.addRow('FreeCAD (STEP import)', freecad_row)
        form.addRow('Island correction passes', self.island_passes)
        form.addRow('Detected', self.status)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Apply | QtWidgets.QDialogButtonBox.Close)
        buttons.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.acceleration.currentTextChanged.connect(self._refresh)
        self.cuda_device.valueChanged.connect(self._refresh)
        self._refresh()

    def settings(self):
        settings = deepcopy(self.document.settings)
        settings['resources'].update({
            'acceleration': self.acceleration.currentText(),
            'cuda_device': self.cuda_device.value(),
            'workers': self.workers.value(),
            'memory_gib': self.memory.value(),
        })
        settings['support']['max_island_passes'] = self.island_passes.value()
        return validate_settings(settings)

    def _browse_freecad(self):
        from ..importers import FREECAD_FILE_FILTER, interpret_freecad_path
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Locate FreeCAD', self.freecad_path.text() or '',
            FREECAD_FILE_FILTER)
        if not path:
            return
        if interpret_freecad_path(path) is None:
            QtWidgets.QMessageBox.warning(
                self, 'Locate FreeCAD',
                f'{path} is not an executable FreeCAD binary.\n'
                'On macOS choose FreeCAD.app (the application bundle).')
            return
        self.freecad_path.setText(path)

    def _refresh(self, *_):
        status = cuda_status()
        probe = {'acceleration': self.acceleration.currentText(),
                 'cuda_device': self.cuda_device.value()}
        try:
            selected = resolve_backend(probe)
            suffix = f'; selected backend: {selected}'
        except VoxelMillError as error:
            suffix = f'; {error}'
        compiled = 'CUDA build' if status['compiled'] else 'CPU-only build'
        self.status.setText(
            f"{compiled}; {status['device_count']} CUDA device(s); "
            f"{status.get('reason') or 'runtime ready'}{suffix}")

    def apply(self):
        try:
            settings = self.settings()
            # Resolve now so an explicit CUDA choice cannot be stored when it
            # cannot run. Auto remains portable and falls back to CPU.
            resolve_backend(settings['resources'])
        except VoxelMillError as error:
            self.status.setText(str(error))
            return None
        self.document.set_settings(settings)
        self.applied = deepcopy(settings)
        self.settings_applied.emit(self.applied)
        # Merge rather than replace: the stored file also holds the motion mode,
        # FreeCAD path, and the window layout, and rebuilding it from the snap
        # fields alone would discard them every time someone touched Apply.
        self.editor_preferences = {**load_preferences(),
                                   'snap_angle_deg': float(self.snap_angle.currentData()),
                                   'translate_step_mm': float(self.translate_step.value()),
                                   'tooltip_delay_ms': int(self.tooltip_delay.value()),
                                   'freecad_path': self.freecad_path.text().strip()}
        save_preferences(self.editor_preferences)
        # Retune the live style rather than waiting for a restart: a delay you
        # cannot feel immediately is a delay you cannot choose.
        install_hover_delay(QtWidgets.QApplication.instance(),
                            self.editor_preferences['tooltip_delay_ms'])
        self.editor_preferences_applied.emit(dict(self.editor_preferences))
        self._refresh()
        return settings
