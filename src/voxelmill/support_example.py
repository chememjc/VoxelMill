"""A small attachment array built by the production support router.

Both the parameter editor and CLI use this fixture. It illustrates geometry and
routing choices, never certifies strength, drainage, or a printable assembly.
"""
from __future__ import annotations

import math
import numpy as np

from .config import validate_settings
from .contracts import VoxelMillError
from .geometry import manifold_triangles
from .supports import build_column_field, route_contacts


def support_example(settings, height_mm=20.0, *, layout='array', cancel=None):
    """Return model/support/base triangle groups and routing evidence.

    Four fixed contacts sit under a floating beam. A pedestal beneath one
    contact offers a model anchor competing with a route around it to the plate.
    Contact selection is deliberately fixed so dimension edits are comparable.
    """
    import manifold3d as m
    validate_settings(settings)
    if not math.isfinite(height_mm) or not 3 <= height_mm <= 160:
        raise VoxelMillError('invalid_example', 'Example height must be between 3 and 160 mm')
    spacing = float(settings['support']['spacing_mm'])
    if not 1 <= spacing <= 30:
        raise VoxelMillError('invalid_example', 'Example supports spacing from 1 to 30 mm; '
                        'the project settings may use other validated values')
    contacts = np.array([(x, y, height_mm)
                         for x in (-spacing / 2, spacing / 2)
                         for y in (-spacing / 2, spacing / 2)])
    beam = m.Manifold.cube((2 * spacing, 2 * spacing, 2)).translate(
        (-spacing, -spacing, height_mm))
    pedestal = m.Manifold.cube((spacing * .8, spacing * .8, height_mm * .4)).translate(
        (-spacing * .9, -spacing * .9, 0))
    if layout not in ('array', 'part-to-part'):
        raise VoxelMillError('invalid_example', 'Example layout must be array or part-to-part')
    if layout == 'part-to-part':
        # A broad lower platform makes the model-to-model gap legible. The
        # router still obeys the user's allow/avoidance settings without edits.
        pedestal = m.Manifold.cube((2 * spacing, 2 * spacing, height_mm * .4)).translate(
            (-spacing, -spacing, 0))
    model = beam + pedestal
    triangles = manifold_triangles(model).astype(np.float32)
    bounds = np.asarray(model.bounding_box()).reshape(2, 3)
    field = build_column_field(triangles, bounds, settings, pitch_mm=.5, cancel=cancel)
    plan, base = route_contacts(contacts, field, settings, cancel=cancel)
    groups = {'model': triangles,
              'supports': (np.concatenate([manifold_triangles(s) for s in plan.solids])
                           if plan.solids else np.empty((0, 3, 3))),
              'raft': (manifold_triangles(base) if base is not None
                       else np.empty((0, 3, 3)))}
    return {'triangles': groups, 'contacts': contacts, 'metrics': plan.metrics,
            'layout': layout, 'hint': ('Enable part-to-part supports and set avoidance to 0 to compare model routes.'
                                      if layout == 'part-to-part' else ''),
            'height_mm': float(height_mm), 'analysis_pitch_mm': .5,
            'basis': 'four fixed contacts; production router and geometry; '
                     'illustration only, no print validation or strength proof'}


def save_example(path, example):
    """Save an illustrative STL with the same atomic writer as project meshes."""
    from .mesh import write_stl
    write_stl(path, tuple(example['triangles'].values()))
