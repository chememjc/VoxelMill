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

#: Window geometry and dock/toolbar layout, as ``QMainWindow.saveGeometry()``/
#: ``saveState()`` produce them. ``None`` means "nothing remembered yet": the
#: editor's own built-in sizing is the default, not an empty memory of one.
DEFAULTS = {'snap_angle_deg': DEFAULT_SNAP_ANGLE_DEG, 'motion_mode': DEFAULT_MOTION_MODE,
           'translate_step_mm': DEFAULT_TRANSLATE_STEP_MM,
           'window_geometry': None, 'window_state': None,
           # Empty means unset; FreeCAD is only needed for STEP import.
           'freecad_path': ''}


def preferences_path():
    return config_dir() / 'editor.json'


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
    for key in ('window_geometry', 'window_state'):
        stored = data.get(key)
        if _valid_base64_text(stored):
            values[key] = stored
    freecad = data.get('freecad_path')
    if isinstance(freecad, str) and freecad:
        values['freecad_path'] = freecad
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
