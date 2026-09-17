"""Per-object placement: the object list and its placement, scale and mirror
controls.

Every Move/Rotate value here is editable three ways and they are the same
value: type an absolute number, drag the axis slider, or drag the object in
the 3D view. The panel is the single owner of that synchronisation, so the
window does not have to guess which of the three last moved.

Rotation snaps to the editor's snap increment whenever it is dragged -- the
axis slider or the object in the 3D view -- and when it is nudged. A typed
angle is taken exactly, because someone who types 37.5 means 37.5. Scale has
no snap at all.

Preview vs. commit: dragging a Move or Rotate slider emits ``pose_preview``
on every step, live, so the window can move the actor without its own
debounce. ``pose_committed`` fires exactly once, when the drag ends, a value
is typed, a nudge button is clicked, or an arrow key nudges the selection.
The existing ``pose_edited`` signal keeps firing alongside every
``pose_committed`` -- once, not on every drag step -- so callers already
wired to it see the same final pose without extra work. Scale and mirror
edits are reported only as commits, through ``scale_edited``; there is no
scale preview.

Multi-select: the object list allows selecting several rows. When more than
one is selected, a Move/Rotate edit targets every selected part, not only the
current row. ``pose_edited``, ``pose_preview`` and ``pose_committed`` signal
that by adding an ``applies_to`` key -- the full, ascending list of selected
indices -- to the emitted pose dict; its absence means the edit targets the
single ``index`` argument alone. ``scale_edited`` has no room for that in its
three fixed arguments (``index, scale, mirror``), so a caller that needs the
full target set for a scale edit reads :meth:`ObjectPanel.selected_indices`
directly -- ``index`` there is always the current row, not a target list.
Turning that into "everyone moves by the same delta" versus "everyone lands
on the same absolute number" is the window's decision, not the panel's.

Arrow keys nudge the selected part(s) in the plate's XY: Left/Right move X,
Up/Down move Y, PageUp/PageDown move Z, by :attr:`ObjectPanel.translate_step_mm`
(Shift for ten times that). They are ignored while a spin box has focus, so
typing a number is never interrupted by that number's own arrow keys.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..geometry import wrap_rotation_deg
from .appprefs import DEFAULT_SNAP_ANGLE_DEG, snap_angle

#: Sliders are integers, so every axis keeps this many steps per millimeter or
#: per degree. Two decimals is what the spin boxes already showed.
SLIDER_SCALE = 100


class AxisRow(QtWidgets.QWidget):
    """One axis: a slider, an absolute spin box, and optional nudge buttons.

    The slider and the box are two views of one number. Neither is the master:
    whichever the user touched wins, and :meth:`set_value` from outside (the
    document, or a drag in the 3D view) updates both without re-emitting.

    ``wrap=True`` is for rotation: a part cannot leave the build volume, so a
    Move row still clamps to its range, but rotation is periodic and must
    never clamp -- 200 degrees is the same orientation as -160, and clamping
    it to 180 would silently throw away the difference. A wrapped row folds
    an out-of-range value back into range instead, via the same helper the
    document uses, so a nudge that walks an angle past 180 keeps meaning what
    it always meant.
    """
    value_edited = QtCore.Signal(float)
    #: Fires on every step of a slider drag, live. ``value_edited`` still
    #: fires too, but only once the drag ends -- see the module docstring.
    value_previewed = QtCore.Signal(float)

    def __init__(self, label, *, minimum, maximum, decimals=2, suffix='', nudges=(),
                 snap=None, nudge_unit='units', wrap=False, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._emitting = False
        self._dragging = False
        self._minimum, self._maximum = float(minimum), float(maximum)
        #: Callable applied to a dragged slider value. Dragging is graphical
        #: manipulation, so it snaps; the box beside it never does.
        self.snap = snap
        #: Rotation wraps instead of clamping; see the class docstring.
        self.wrap = bool(wrap)
        #: Word used in a nudge button's tooltip ("+5 degrees", "+1 mm", ...).
        self._nudge_unit = nudge_unit

        self.caption = QtWidgets.QLabel(label)
        self.caption.setMinimumWidth(18)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setObjectName(f'axis_slider_{label.lower()}')
        self.box = QtWidgets.QDoubleSpinBox()
        self.box.setObjectName(f'axis_value_{label.lower()}')
        self.box.setDecimals(decimals)
        self.box.setSuffix(suffix)
        self.box.setKeyboardTracking(False)
        # The dock is narrow and a rotation row carries four nudge buttons as
        # well, so the field is sized to the numbers it holds rather than to
        # the widest value the range allows.
        self.box.setMinimumWidth(72)
        self.box.setMaximumWidth(104)
        self.set_range(minimum, maximum)

        layout.addWidget(self.caption)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.box)
        self.nudge_buttons = []
        for amount in nudges:
            button = QtWidgets.QToolButton()
            button.setObjectName(f'axis_nudge_{label.lower()}_{amount:+g}'.replace('.', '_'))
            button.setAutoRaise(True)
            button.setFixedWidth(30)
            button.clicked.connect(lambda _=False, step=amount: self.nudge(step))
            layout.addWidget(button)
            self.nudge_buttons.append([button, amount])
        self.set_nudges(nudges)

        self.slider.valueChanged.connect(self._on_slider)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.box.valueChanged.connect(self._on_box)

    # ---- configuration --------------------------------------------------
    def set_range(self, minimum, maximum):
        self._minimum, self._maximum = float(minimum), float(maximum)
        self.box.setRange(self._minimum, self._maximum)
        self.slider.setRange(int(round(self._minimum * SLIDER_SCALE)),
                             int(round(self._maximum * SLIDER_SCALE)))

    def set_nudges(self, amounts):
        """Relabel the nudge buttons, which is how a snap change reaches them."""
        amounts = list(amounts)
        for (button, _), amount in zip(self.nudge_buttons, amounts):
            button.setText(f'{amount:+g}')
            button.setToolTip(f'{amount:+g} {self._nudge_unit}')
        for index, amount in enumerate(amounts):
            if index < len(self.nudge_buttons):
                self.nudge_buttons[index][1] = float(amount)
        # A zero snap increment has no meaning as a button; hide rather than
        # offer a control that does nothing.
        for button, amount in self.nudge_buttons:
            button.setVisible(bool(amount))

    # ---- value ----------------------------------------------------------
    def value(self):
        return float(self.box.value())

    def _normalize(self, value):
        """Fold a rotation row's value back into range; clamp everything else."""
        if self.wrap:
            return wrap_rotation_deg(float(value))
        return max(self._minimum, min(self._maximum, float(value)))

    def set_value(self, value, *, notify=False):
        value = self._normalize(value)
        was, self._emitting = self._emitting, True
        self.box.setValue(value)
        self.slider.setValue(int(round(value * SLIDER_SCALE)))
        self._emitting = was
        if notify:
            self.value_edited.emit(self.value())

    def nudge(self, step):
        self.set_value(self.value() + float(step), notify=True)

    def _on_slider(self, position):
        if self._emitting:
            return
        value = position / SLIDER_SCALE
        if self.snap is not None:
            value = self._normalize(self.snap(value))
        self._emitting = True
        self.box.setValue(value)
        self.slider.setValue(int(round(value * SLIDER_SCALE)))
        self._emitting = False
        # Mid-drag, this is a preview: the window moves the actor live and
        # commits nothing yet. Not dragging (a click on the groove, a keyboard
        # step) is itself a single committed edit.
        if self._dragging:
            self.value_previewed.emit(self.value())
        else:
            self.value_edited.emit(self.value())

    def _on_slider_pressed(self):
        self._dragging = True

    def _on_slider_released(self):
        self._dragging = False
        # Steps during the drag only ever previewed; report the final value
        # as the one and only commit for this drag.
        self.value_edited.emit(self.value())

    def _on_box(self, value):
        if self._emitting:
            return
        self._emitting = True
        self.slider.setValue(int(round(value * SLIDER_SCALE)))
        self._emitting = False
        self.value_edited.emit(self.value())


class ObjectPanel(QtWidgets.QWidget):
    """The plate's object list and the selected object's placement controls."""
    selection_changed = QtCore.Signal(int)
    #: All selected rows, ascending, alongside the single-row signal above.
    selection_indices_changed = QtCore.Signal(object)
    pose_edited = QtCore.Signal(int, dict)
    #: Live, one emission per slider step while a Move/Rotate drag is in flight.
    pose_preview = QtCore.Signal(int, object)
    #: Exactly one emission per committed Move/Rotate edit -- drag release,
    #: typed value, nudge button, or arrow-key nudge.
    pose_committed = QtCore.Signal(int, object)
    #: (index, [sx, sy, sz], [mx, my, mz]) -- see the module docstring for how
    #: a multi-select target set reaches a caller despite the fixed arity.
    scale_edited = QtCore.Signal(int, object, object)
    visibility_changed = QtCore.Signal(int, bool)
    duplicate_requested = QtCore.Signal(int, int)
    remove_requested = QtCore.Signal(int)
    add_requested = QtCore.Signal()
    arrange_requested = QtCore.Signal()
    supports_requested = QtCore.Signal(int)
    drop_received = QtCore.Signal(list)
    #: The selected index list -- the window zeroes lift_mm on each.
    drop_to_plate_requested = QtCore.Signal(object)
    #: The selected index list -- the window frames those parts in the 3D view.
    zoom_to_selected_requested = QtCore.Signal(object)
    #: The current index -- the window runs the orientation search on it.
    auto_orient_requested = QtCore.Signal(int)

    #: Arrow key -> (axis, sign). Held here as a class attribute since it
    #: never depends on instance state.
    _NUDGE_KEYS = {
        QtCore.Qt.Key_Left: ('X', -1.0),
        QtCore.Qt.Key_Right: ('X', 1.0),
        QtCore.Qt.Key_Down: ('Y', -1.0),
        QtCore.Qt.Key_Up: ('Y', 1.0),
        QtCore.Qt.Key_PageDown: ('Z', -1.0),
        QtCore.Qt.Key_PageUp: ('Z', 1.0),
    }

    def __init__(self, parent=None, *, snap_angle_deg=DEFAULT_SNAP_ANGLE_DEG):
        super().__init__(parent)
        self._snap = float(snap_angle_deg)
        self._syncing = False
        self._objects = []
        #: Millimeters. The arrow-key nudge step, and what the Move rows'
        #: nudge buttons default to -- see :meth:`set_translate_step`.
        self._translate_step = 1.0
        # Six axis rows, four nudge buttons each on the rotations, and a four
        # button toolbar do not fit an arbitrarily narrow dock; without this the
        # dock opens clipped and every row needs horizontal scrolling.
        self.setMinimumWidth(360)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.list = QtWidgets.QListWidget()
        self.list.setObjectName('object_panel_list')
        self.list.setMinimumHeight(96)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setToolTip(
            'Every part on the plate. The check box hides a part in the 3D view only; '
            'a hidden part is still placed, still checked and still exported. '
            'Shift/Ctrl-click selects more than one part for a shared Move/Rotate/Scale edit.')
        self.list.currentRowChanged.connect(self._on_row_changed)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list)

        buttons = QtWidgets.QHBoxLayout()
        for text, name, slot, tip in (
                ('Add...', 'object_add', lambda: self.add_requested.emit(),
                 'Place another STL on the plate. Files can also be dropped on the 3D view.'),
                ('Duplicate', 'object_duplicate', self._request_duplicate,
                 'Copy the selected part, reusing the mesh already loaded.'),
                ('Remove', 'object_remove', self._request_remove,
                 'Remove the selected added part. The first part cannot be removed.'),
                ('Arrange', 'object_arrange', lambda: self.arrange_requested.emit(),
                 'Lay every part out without overlap inside the build envelope. '
                 'Refuses rather than overlapping when they do not all fit.')):
            button = QtWidgets.QPushButton(text)
            button.setObjectName(name)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, handler=slot: handler())
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self.translate_rows = {}
        self.rotate_rows = {}
        self.scale_rows = {}
        form = QtWidgets.QVBoxLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.addWidget(self._heading('Move (mm)'))
        for axis in ('X', 'Y', 'Z'):
            row = AxisRow(axis, minimum=-200, maximum=200, suffix=' mm',
                          nudges=(-self._translate_step, self._translate_step), nudge_unit='mm')
            row.value_edited.connect(lambda _=0.0: self._emit_pose())
            row.value_previewed.connect(lambda _=0.0: self._emit_pose_preview())
            self.translate_rows[axis] = row
            form.addWidget(row)
        form.addWidget(self._heading('Rotate (degrees)'))
        for axis in ('X', 'Y', 'Z'):
            row = AxisRow(axis, minimum=-180, maximum=180, suffix=' deg',
                          nudges=(-45, -self._snap, self._snap, 45), snap=self.snap,
                          nudge_unit='degrees', wrap=True)
            row.value_edited.connect(lambda _=0.0: self._emit_pose())
            row.value_previewed.connect(lambda _=0.0: self._emit_pose_preview())
            self.rotate_rows[axis] = row
            form.addWidget(row)

        form.addWidget(self._heading('Scale'))
        self.scale_uniform = QtWidgets.QCheckBox('Uniform')
        self.scale_uniform.setObjectName('object_scale_uniform')
        self.scale_uniform.setChecked(True)
        self.scale_uniform.setToolTip(
            'Keep X, Y and Z scale equal. Editing any one axis sets the other two to match.')
        form.addWidget(self.scale_uniform)
        for axis in ('X', 'Y', 'Z'):
            row = AxisRow(axis, minimum=0.01, maximum=100, suffix=' x', nudge_unit='x')
            row.set_value(1.0)  # unscaled, not the 0.01 the range would otherwise clamp to
            row.value_edited.connect(lambda _=0.0, ax=axis: self._on_scale_edited(ax))
            self.scale_rows[axis] = row
            form.addWidget(row)
        self.reset_scale = QtWidgets.QPushButton('Reset scale')
        self.reset_scale.setObjectName('object_reset_scale')
        self.reset_scale.setToolTip('Set X, Y and Z scale back to 1.')
        self.reset_scale.clicked.connect(self._reset_scale)
        form.addWidget(self.reset_scale)

        form.addWidget(self._heading('Mirror'))
        self.mirror_boxes = {}
        mirror_row = QtWidgets.QHBoxLayout()
        for axis in ('X', 'Y', 'Z'):
            box = QtWidgets.QCheckBox(axis)
            box.setObjectName(f'object_mirror_{axis.lower()}')
            box.toggled.connect(lambda _checked=False: self._emit_scale())
            self.mirror_boxes[axis] = box
            mirror_row.addWidget(box)
        mirror_row.addStretch(1)
        form.addLayout(mirror_row)
        layout.addLayout(form)

        self.reset_rotation = QtWidgets.QPushButton('Reset rotation')
        self.reset_rotation.setObjectName('object_reset_rotation')
        self.reset_rotation.clicked.connect(self._reset_rotation)
        self.compute_supports = QtWidgets.QPushButton('Compute attachments')
        self.compute_supports.setDefault(True)
        self.compute_supports.setObjectName('object_compute_supports')
        self.compute_supports.setToolTip(
            'Route supports for the plate as it stands now. Nothing is attached on import, '
            'so parts can be positioned first.')
        self.compute_supports.clicked.connect(
            lambda: self.supports_requested.emit(self.current_index()))
        actions = QtWidgets.QHBoxLayout()
        actions.addWidget(self.reset_rotation)
        actions.addWidget(self.compute_supports)
        layout.addLayout(actions)

        self.drop_to_plate = QtWidgets.QPushButton('Drop to plate')
        self.drop_to_plate.setObjectName('object_drop_to_plate')
        self.drop_to_plate.setToolTip(
            'Set the lift to zero so the part rests on the bed, measured from its '
            'lowest point in its current rotation.')
        self.drop_to_plate.clicked.connect(
            lambda: self.drop_to_plate_requested.emit(self._selection_or_current()))
        self.zoom_selected = QtWidgets.QPushButton('Zoom to selected')
        self.zoom_selected.setObjectName('object_zoom_selected')
        self.zoom_selected.clicked.connect(
            lambda: self.zoom_to_selected_requested.emit(self._selection_or_current()))
        self.auto_orient = QtWidgets.QPushButton('Auto-orient')
        self.auto_orient.setObjectName('object_auto_orient')
        self.auto_orient.clicked.connect(
            lambda: self.auto_orient_requested.emit(self.current_index()))
        actions2 = QtWidgets.QHBoxLayout()
        actions2.addWidget(self.drop_to_plate)
        actions2.addWidget(self.zoom_selected)
        actions2.addWidget(self.auto_orient)
        layout.addLayout(actions2)

        self.status = QtWidgets.QLabel('')
        self.status.setObjectName('object_panel_status')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.setAcceptDrops(True)
        # Arrow keys nudge the selection; both the panel and the list itself
        # can hold focus, so both need the filter.
        self.installEventFilter(self)
        self.list.installEventFilter(self)

    @staticmethod
    def _heading(text):
        label = QtWidgets.QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    # ---- snap -----------------------------------------------------------
    @property
    def snap_angle_deg(self):
        return self._snap

    def set_snap_angle(self, degrees):
        self._snap = float(degrees)
        for row in self.rotate_rows.values():
            row.set_nudges((-45, -self._snap, self._snap, 45))

    def snap(self, angle):
        return snap_angle(angle, self._snap)

    # ---- translate step --------------------------------------------------
    @property
    def translate_step_mm(self):
        return self._translate_step

    def set_translate_step(self, mm):
        """Set the arrow-key nudge step; the Move rows' nudge buttons follow it."""
        self._translate_step = float(mm)
        for row in self.translate_rows.values():
            row.set_nudges((-self._translate_step, self._translate_step))

    # ---- object list ----------------------------------------------------
    def set_objects(self, objects, *, selected=None):
        """Replace the list. ``objects`` are dicts with name/visible/supports."""
        self._objects = [dict(item) for item in objects]
        previous = self.list.currentRow() if selected is None else int(selected)
        was, self._syncing = self._syncing, True
        self.list.clear()
        for index, entry in enumerate(self._objects):
            item = QtWidgets.QListWidgetItem(self._label(index, entry))
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if entry.get('visible', True) else QtCore.Qt.Unchecked)
            self.list.addItem(item)
        if self._objects:
            self.list.setCurrentRow(max(0, min(len(self._objects) - 1, previous)))
        self._syncing = was
        self._sync_buttons()

    @staticmethod
    def _label(index, entry):
        name = entry.get('name') or f'part {index}'
        role = 'primary' if index == 0 else f'added {index}'
        state = entry.get('supports') or 'none'
        return f'{name}  [{role}; attachments: {state}]'

    def current_index(self):
        return max(0, self.list.currentRow())

    def selected_indices(self):
        """Every selected row, ascending. Empty when nothing is selected."""
        return sorted(self.list.row(item) for item in self.list.selectedItems())

    def _selection_or_current(self):
        return self.selected_indices() or [self.current_index()]

    def _sync_buttons(self):
        index = self.list.currentRow()
        self.findChild(QtWidgets.QPushButton, 'object_remove').setEnabled(index > 0)
        self.findChild(QtWidgets.QPushButton, 'object_duplicate').setEnabled(index >= 0)
        self.auto_orient.setEnabled(index >= 0)

    def _on_row_changed(self, row):
        self._sync_buttons()
        if self._syncing or row < 0:
            return
        self.selection_changed.emit(int(row))

    def _on_item_changed(self, item):
        if self._syncing:
            return
        row = self.list.row(item)
        self.visibility_changed.emit(int(row), item.checkState() == QtCore.Qt.Checked)

    def _on_selection_changed(self):
        if self._syncing:
            return
        indices = self.selected_indices()
        self.selection_indices_changed.emit(indices)
        if len(indices) > 1:
            self.set_status(f'{len(indices)} parts selected.')

    def _request_duplicate(self):
        index = self.list.currentRow()
        if index < 0:
            return
        count, ok = QtWidgets.QInputDialog.getInt(self, 'Duplicate', 'Copies', 1, 1, 64, 1)
        if ok:
            self.duplicate_requested.emit(int(index), int(count))

    def _request_remove(self):
        index = self.list.currentRow()
        if index > 0:
            self.remove_requested.emit(int(index))

    # ---- pose -----------------------------------------------------------
    def set_limits(self, build_mm):
        """Clamp the move sliders to the build envelope."""
        width, depth, height = (float(v) for v in build_mm)
        self.translate_rows['X'].set_range(-width / 2, width / 2)
        self.translate_rows['Y'].set_range(-depth / 2, depth / 2)
        self.translate_rows['Z'].set_range(0, height)

    def set_pose(self, pose):
        """Show a pose, scale and mirror without emitting.

        Called for the document's stored values and for a live 3D drag; both
        must land silently or they would immediately bounce back as an edit.
        """
        was, self._syncing = self._syncing, True
        offset = list(pose.get('center_offset') or (0.0, 0.0))
        for axis, value in zip(('X', 'Y'), offset):
            self.translate_rows[axis].set_value(value)
        self.translate_rows['Z'].set_value(pose.get('lift_mm', 0.0))
        for axis, value in zip(('X', 'Y', 'Z'), pose.get('rotate') or (0.0, 0.0, 0.0)):
            self.rotate_rows[axis].set_value(value)
        for axis, value in zip(('X', 'Y', 'Z'), pose.get('scale') or (1.0, 1.0, 1.0)):
            self.scale_rows[axis].set_value(value)
        for axis, value in zip(('X', 'Y', 'Z'), pose.get('mirror') or (False, False, False)):
            self.mirror_boxes[axis].setChecked(bool(value))
        self._syncing = was

    def pose(self):
        return {
            'center_offset': [self.translate_rows['X'].value(), self.translate_rows['Y'].value()],
            'lift_mm': self.translate_rows['Z'].value(),
            'rotate': [self.rotate_rows[axis].value() for axis in ('X', 'Y', 'Z')],
        }

    def _pose_payload(self):
        """``pose()`` plus ``applies_to`` when the edit targets more than one part."""
        pose = self.pose()
        indices = self.selected_indices()
        if len(indices) > 1:
            pose = dict(pose)
            pose['applies_to'] = indices
        return pose

    def _emit_pose(self):
        if self._syncing:
            return
        index = self.current_index()
        pose = self._pose_payload()
        self.pose_edited.emit(index, pose)
        self.pose_committed.emit(index, pose)

    def _emit_pose_preview(self):
        if self._syncing:
            return
        self.pose_preview.emit(self.current_index(), self._pose_payload())

    def _reset_rotation(self):
        was, self._syncing = self._syncing, True
        for row in self.rotate_rows.values():
            row.set_value(0.0)
        self._syncing = was
        self._emit_pose()

    def set_status(self, text):
        self.status.setText(text)

    # ---- scale and mirror -------------------------------------------------
    def scale(self):
        return [self.scale_rows[axis].value() for axis in ('X', 'Y', 'Z')]

    def mirror(self):
        return [self.mirror_boxes[axis].isChecked() for axis in ('X', 'Y', 'Z')]

    def _on_scale_edited(self, axis):
        if self._syncing:
            return
        if self.scale_uniform.isChecked():
            value = self.scale_rows[axis].value()
            for other, row in self.scale_rows.items():
                if other != axis:
                    row.set_value(value)
        self._emit_scale()

    def _reset_scale(self):
        was, self._syncing = self._syncing, True
        for row in self.scale_rows.values():
            row.set_value(1.0)
        self._syncing = was
        self._emit_scale()

    def _emit_scale(self):
        if self._syncing:
            return
        self.scale_edited.emit(self.current_index(), self.scale(), self.mirror())

    # ---- arrow-key nudge ----------------------------------------------
    def eventFilter(self, obj, event):
        if obj in (self, self.list) and event.type() == QtCore.QEvent.KeyPress:
            if self._nudge_from_key(event):
                return True
        return super().eventFilter(obj, event)

    def _nudge_from_key(self, event):
        mapping = self._NUDGE_KEYS.get(event.key())
        if mapping is None or not self._objects:
            return False
        # A spin box owns its own arrow keys (single-step the number); do not
        # steal them just because the panel also has an event filter.
        focus = QtWidgets.QApplication.focusWidget()
        if isinstance(focus, QtWidgets.QAbstractSpinBox):
            return False
        axis, sign = mapping
        multiplier = 10.0 if event.modifiers() & QtCore.Qt.ShiftModifier else 1.0
        self.translate_rows[axis].nudge(sign * self._translate_step * multiplier)
        return True

    # ---- drag and drop --------------------------------------------------
    @staticmethod
    def _dropped_models(mime):
        return [url.toLocalFile() for url in mime.urls()
                if url.isLocalFile() and url.toLocalFile().lower().endswith('.stl')]

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self._dropped_models(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and self._dropped_models(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = self._dropped_models(event.mimeData())
        if not paths:
            return super().dropEvent(event)
        event.acceptProposedAction()
        self.drop_received.emit(paths)


#: The support values worth a control on a per-part basis. Everything else in
#: the ``support`` table stays reachable through the overlay JSON field, which
#: is the same split the Setup tab already makes between compact controls and
#: the complete resolved-settings box.
ATTACHMENT_FIELDS = (
    ('spacing_mm', 'contact spacing mm', 0.1, 30.0, 2),
    ('overhang_angle_deg', 'overhang angle deg', 0.0, 90.0, 1),
    ('pillar_diameter_mm', 'pillar diameter mm', 0.05, 10.0, 2),
    ('contact_diameter_mm', 'contact diameter mm', 0.05, 5.0, 2),
    ('pillar_angle_deg', 'pillar angle deg', 0.0, 90.0, 1),
)


class AttachmentSettings(QtWidgets.QWidget):
    """Attachment values for the selected part.

    Row 0 is the primary part, whose attachment values *are* the plate
    settings, so editing them here edits the document. An added part carries an
    overlay instead: unticking the override box clears it and the part inherits
    the plate again, which is a different state from an overlay that happens to
    hold the same numbers.
    """
    plate_edited = QtCore.Signal(dict)
    overlay_edited = QtCore.Signal(int, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._index = 0
        self._syncing = False
        self._inherited = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.override = QtWidgets.QCheckBox('override the plate attachment settings')
        self.override.setObjectName('attachment_override')
        self.override.setToolTip(
            'Off: this part inherits every plate attachment value. On: the values below '
            'apply to this part only. The plate still shares one collision field.')
        self.override.toggled.connect(self._on_override)
        layout.addWidget(self.override)

        self.rows = {}
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        for key, label, low, high, decimals in ATTACHMENT_FIELDS:
            box = QtWidgets.QDoubleSpinBox()
            box.setObjectName(f'attachment_{key}')
            box.setRange(low, high)
            box.setDecimals(decimals)
            box.setKeyboardTracking(False)
            box.setToolTip(f'Config key: support.{key}.')
            box.valueChanged.connect(lambda _value: self._emit())
            self.rows[key] = box
            form.addRow(label, box)
        layout.addLayout(form)

        self.note = QtWidgets.QLabel('')
        self.note.setObjectName('attachment_note')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def load(self, index, plate_support, overlay):
        """Show the values for one part without reporting them back as edits."""
        was, self._syncing = self._syncing, True
        self._index = int(index)
        self._inherited = dict(plate_support)
        overlay = dict(overlay or {})
        primary = self._index == 0
        self.override.setVisible(not primary)
        self.override.setChecked(bool(overlay) if not primary else True)
        effective = dict(plate_support)
        effective.update(overlay)
        for key, box in self.rows.items():
            if key in effective:
                box.setValue(float(effective[key]))
            box.setEnabled(primary or bool(overlay))
        self.note.setText(
            'These are the plate attachment values; the first part uses them directly.'
            if primary else
            ('Overriding the plate for this part only.' if overlay else
             'Inheriting every plate attachment value.'))
        self._syncing = was

    def values(self):
        return {key: box.value() for key, box in self.rows.items()
                if key in self._inherited}

    def _on_override(self, enabled):
        for key, box in self.rows.items():
            box.setEnabled(bool(enabled))
        if self._syncing:
            return
        if not enabled:
            # Clearing the box is what returns the part to the plate values, so
            # it reports an empty overlay rather than a copy of them.
            self.overlay_edited.emit(self._index, {})
            self.note.setText('Inheriting every plate attachment value.')
            return
        self._emit()

    def _emit(self):
        if self._syncing:
            return
        if self._index == 0:
            self.plate_edited.emit(self.values())
        elif self.override.isChecked():
            changed = {key: value for key, value in self.values().items()
                       if round(value, 6) != round(float(self._inherited.get(key, value)), 6)}
            self.overlay_edited.emit(self._index, changed)


#: What a plain click on the model does. These were previously only reachable
#: as Shift/Ctrl/Alt modifiers on a click, which meant nothing on screen said
#: they existed. The modifiers still work; this makes them visible.
TOOLS = (
    ('select', 'Select', 'Click a part to select it. Shift, Ctrl and Alt clicks still '
                         'add, remove and move an attachment point.'),
    ('add', 'Add point', 'Click the model to add an attachment point where you clicked.'),
    ('remove', 'Remove point', 'Click a displayed attachment point to suppress it. '
                               'It stays suppressed through later automatic passes.'),
    ('enforce', 'Paint enforced', 'Drag on the model to require attachment points on those '
                                  'faces. Enforced faces are green.'),
    ('block', 'Paint blocked', 'Drag on the model to keep automatic points off those faces. '
                               'Blocked faces are magenta. Island births are never blocked, '
                               'because an unsupported island will not print.'),
)


class ToolSelector(QtWidgets.QWidget):
    """Exclusive model-editing tool, with the brush size when one needs it."""
    tool_changed = QtCore.Signal(str)
    radius_changed = QtCore.Signal(float)

    #: Tools that paint rather than click.
    BRUSH_TOOLS = ('enforce', 'block')

    def __init__(self, parent=None, *, radius_mm=3.0):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for name, label, tip in TOOLS:
            button = QtWidgets.QToolButton()
            button.setObjectName(f'tool_{name}')
            button.setText(label)
            button.setToolTip(tip)
            button.setCheckable(True)
            button.setAutoRaise(True)
            self.group.addButton(button)
            self.buttons[name] = button
            layout.addWidget(button)
            button.toggled.connect(
                lambda checked, key=name: checked and self.tool_changed.emit(key))
        self.buttons['select'].setChecked(True)

        layout.addSpacing(12)
        self.radius_label = QtWidgets.QLabel('brush mm')
        self.radius = QtWidgets.QDoubleSpinBox()
        self.radius.setObjectName('tool_brush_radius')
        self.radius.setRange(0.05, 50.0)
        self.radius.setDecimals(2)
        self.radius.setValue(float(radius_mm))
        self.radius.setToolTip('Brush radius in millimeters.')
        self.radius.valueChanged.connect(self.radius_changed)
        layout.addWidget(self.radius_label)
        layout.addWidget(self.radius)
        layout.addStretch(1)
        self.tool_changed.connect(self._sync_brush)
        self._sync_brush('select')

    @property
    def tool(self):
        for name, button in self.buttons.items():
            if button.isChecked():
                return name
        return 'select'

    def set_tool(self, name):
        button = self.buttons.get(name)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def _sync_brush(self, tool):
        painting = tool in self.BRUSH_TOOLS
        self.radius_label.setVisible(painting)
        self.radius.setVisible(painting)
