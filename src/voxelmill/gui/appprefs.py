"""Editor preferences that belong to the person, not to the print.

``resources.*`` lives in the settings table because it changes what is
computed. Snap increments and similar interaction choices change nothing about
the output, so they must not travel inside a printer profile or a ``.voxmil``
project: they persist next to the notification suppressions instead.
"""
from __future__ import annotations

import base64
import binascii
import json

from PySide6 import QtWidgets

from .notifications import config_dir

#: Degrees. Five is fine enough to place a part deliberately and coarse enough
#: that a dragged rotation still lands somewhere repeatable.
DEFAULT_SNAP_ANGLE_DEG = 5.0

#: Offered in the preferences dialog. Zero is "no snapping", kept explicit
#: rather than implied by an empty field.
SNAP_ANGLE_CHOICES = (0.0, 1.0, 2.5, 5.0, 10.0, 15.0, 22.5, 30.0, 45.0, 90.0)

#: How a gizmo drag or a nudge is interpreted: on top of the part's current
#: pose (default), or as a pose measured from its import pose. See
#: ``MainWindow.motion_mode``.
DEFAULT_MOTION_MODE = 'relative'
MOTION_MODE_CHOICES = ('relative', 'absolute')

#: Millimetres one arrow-key press or one +/- button moves the selected part.
#: One millimetre is coarse enough to see and fine enough to place against a
#: plate feature; Shift multiplies it by ten.
DEFAULT_TRANSLATE_STEP_MM = 1.0

#: Milliseconds the pointer must rest on a control before its hover text
#: appears. Qt's own default is around 700 ms and is not configurable per
#: application without a style, so the editor installs one. 0 shows hover text
#: immediately; the cap keeps a typo from making hover text unreachable.
DEFAULT_TOOLTIP_DELAY_MS = 1000
MAX_TOOLTIP_DELAY_MS = 10000

#: Window geometry and dock/toolbar layout, as ``QMainWindow.saveGeometry()``/
#: ``saveState()`` produce them. ``None`` means "nothing remembered yet": the
#: editor's own built-in sizing is the default, not an empty memory of one.
DEFAULTS = {'snap_angle_deg': DEFAULT_SNAP_ANGLE_DEG, 'motion_mode': DEFAULT_MOTION_MODE,
           'translate_step_mm': DEFAULT_TRANSLATE_STEP_MM,
           'tooltip_delay_ms': DEFAULT_TOOLTIP_DELAY_MS,
           'window_geometry': None, 'window_state': None,
           # Empty means unset; FreeCAD is only needed for STEP import.
           'freecad_path': '',
           # Action name -> QKeySequence.PortableText override. Only rows that
           # differ from the action's built-in default are ever stored; an
           # action name the editor no longer has is dropped on load rather
           # than kept around inert. See MainWindow._apply_shortcut_overrides.
           'shortcuts': {}}


def preferences_path():
    return config_dir() / 'editor.json'


def _valid_shortcut_text(value):
    """Whether ``value`` is storable as one action's shortcut override.

    Empty means "no shortcut" and is valid on purpose -- the shortcut editor
    lets a built-in shortcut be cleared, not just reassigned. Anything else
    must round-trip through :class:`QKeySequence`'s portable format into at
    least one real key; a hand-edited or stale entry that does not is dropped
    rather than applied to whatever action its name still matches.
    """
    if not isinstance(value, str):
        return False
    if value == '':
        return True
    from PySide6 import QtGui
    sequence = QtGui.QKeySequence(value, QtGui.QKeySequence.PortableText)
    return not sequence.isEmpty() and sequence.toString(QtGui.QKeySequence.PortableText) == value


def _valid_base64_text(value):
    """Whether ``value`` is a string that decodes as base64.

    A hand-edited or truncated preferences file is not worth stopping the
    editor for, so a value that fails this is dropped rather than raised.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        base64.b64decode(value.encode('ascii'), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        return False
    return True


def load_preferences() -> dict:
    """Stored editor preferences, with every missing key defaulted.

    An unreadable or corrupt file is not an error worth stopping the editor
    for: the defaults are as good a starting point as the file was.
    """
    path = preferences_path()
    values = dict(DEFAULTS)
    values['shortcuts'] = dict(DEFAULTS['shortcuts'])  # DEFAULTS' only mutable value
    if not path.exists():
        return values
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return values
    if not isinstance(data, dict):
        return values
    snap = data.get('snap_angle_deg')
    try:
        snap = float(snap)
    except (TypeError, ValueError):
        snap = None
    if snap is not None and snap == snap and 0 <= snap <= 180:
        values['snap_angle_deg'] = snap
    mode = data.get('motion_mode')
    if mode in MOTION_MODE_CHOICES:
        values['motion_mode'] = mode
    step = data.get('translate_step_mm')
    try:
        step = float(step)
    except (TypeError, ValueError):
        step = None
    if step is not None and step == step and 0 < step <= 50:
        values['translate_step_mm'] = step
    delay = data.get('tooltip_delay_ms')
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        delay = None
    if delay is not None and 0 <= delay <= MAX_TOOLTIP_DELAY_MS:
        values['tooltip_delay_ms'] = delay
    for key in ('window_geometry', 'window_state'):
        stored = data.get(key)
        if _valid_base64_text(stored):
            values[key] = stored
    freecad = data.get('freecad_path')
    if isinstance(freecad, str) and freecad:
        values['freecad_path'] = freecad
    shortcuts = data.get('shortcuts')
    if isinstance(shortcuts, dict):
        values['shortcuts'] = {name: text for name, text in shortcuts.items()
                               if isinstance(name, str) and name and _valid_shortcut_text(text)}
    return values


def save_preferences(values) -> None:
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = dict(DEFAULTS)
    stored.update({key: values[key] for key in DEFAULTS if key in values})
    path.write_text(json.dumps(stored, indent=2) + '\n')


def snap_angle(value, step):
    """Round ``value`` to the nearest multiple of ``step``.

    A zero or negative step means no snapping, so a caller can pass the stored
    preference straight through without branching on it.
    """
    value = float(value)
    step = float(step)
    if not step > 0:
        return value
    return round(value / step) * step


class HoverDelayStyle(QtWidgets.QProxyStyle):
    """Applies the stored hover-text delay to every widget in the editor.

    Qt reads the wake-up delay from the style, not from the widget or the
    tooltip, so there is no per-control setting to change and no signal to
    connect: a style is the only place the number can come from. Wrapping the
    current style rather than naming one keeps the platform look.
    """

    def __init__(self, delay_ms=DEFAULT_TOOLTIP_DELAY_MS, base=None):
        super().__init__(base) if base is not None else super().__init__()
        self._delay_ms = DEFAULT_TOOLTIP_DELAY_MS
        self.set_delay(delay_ms)

    def delay(self):
        return self._delay_ms

    def set_delay(self, delay_ms):
        """Clamp and store a new delay; takes effect on the next hover."""
        try:
            delay_ms = int(delay_ms)
        except (TypeError, ValueError):
            return self._delay_ms
        self._delay_ms = max(0, min(int(MAX_TOOLTIP_DELAY_MS), delay_ms))
        return self._delay_ms

    def styleHint(self, hint, option=None, widget=None, data=None):
        if hint == QtWidgets.QStyle.SH_ToolTip_WakeUpDelay:
            return self._delay_ms
        # Once shown, a tooltip should not vanish faster than it appeared.
        if hint == QtWidgets.QStyle.SH_ToolTip_FallAsleepDelay:
            return max(self._delay_ms, super().styleHint(hint, option, widget, data))
        return super().styleHint(hint, option, widget, data)


def install_hover_delay(application, delay_ms=None):
    """Put a :class:`HoverDelayStyle` on ``application``, or retune the one there.

    Called again on every Apply in the preferences dialog, so it has to be
    idempotent: installing a second proxy over the first would nest them.
    """
    if application is None:
        return None
    if delay_ms is None:
        delay_ms = load_preferences()['tooltip_delay_ms']
    current = application.style()
    if isinstance(current, HoverDelayStyle):
        current.set_delay(delay_ms)
        return current
    style = HoverDelayStyle(delay_ms, current)
    application.setStyle(style)
    return style
