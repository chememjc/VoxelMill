"""Help text lookup for both GUI surfaces.

Each setting's explanation is declared with the setting itself, in
:data:`voxelmill.settings_schema.FIELDS`, keyed by its full ``section.field``
path, so a name that repeats across sections (``id``, ``name``, ``enabled``,
``voxel_size_mm``) can say the right thing in each. The repetitive
``printer.motion`` leaves are described by rule below.
"""
from __future__ import annotations

from ..settings_schema import FIELDS

#: Help for paths that are not settings fields.
HELP = {
    'schema_version': 'Version of the settings layout itself, so an older project or profile '
                      'can be read and migrated. Not a print setting.',
}


#: The eighteen ``printer.motion`` leaves are one shape repeated: a height, a
#: speed or a lamp level, optionally for bottom layers and optionally for a
#: second stage. Describing them by rule keeps the claim about them identical
#: everywhere, which matters more here than anywhere else: they are reference
#: values read from a machine profile, not a fitted model of that machine.
_MOTION_KINDS = (
    ('lift_height', 'How far the platform rises after a layer, in mm'),
    ('lift_speed', 'How fast the platform rises after a layer, in mm/min'),
    ('retract_height', 'How far the platform returns before the next layer, in mm'),
    ('retract_speed', 'How fast the platform returns before the next layer, in mm/min'),
    ('light_pwm', 'Lamp power level for the exposure, 0 to 255'),
)


def _motion_help(field):
    """Generated explanation for one ``printer.motion`` leaf."""
    stage = ' Second stage of a two-stage move.' if field.endswith('2') else ''
    core = field[:-1] if field.endswith('2') else field
    bottom = core.startswith('bottom_')
    if bottom:
        core = core[len('bottom_'):]
    for name, text in _MOTION_KINDS:
        if core == name:
            scope = ' Applies to the bottom layers only.' if bottom else ''
            return (f'{text}.{scope}{stage} Reference value carried from the machine '
                    'profile: uncalibrated, and not a timing or strength model.')
    return None


def help_for(path):
    """Explanation for a ``section.field`` path, or None."""
    if not path:
        return None
    path = str(path)
    field = FIELDS.get(path)
    if field is not None and field.help:
        return field.help
    if path.startswith('printer.motion.'):
        return _motion_help(path.split('.')[-1])
    return HELP.get(path)
