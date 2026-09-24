"""Small attachment fixtures built by the production support router.

Both the parameter editor and CLI use these. They illustrate geometry and
routing choices, never certify strength, drainage, or a printable assembly.

Three layouts. ``array`` and ``part-to-part`` are the historical pair and obey
the caller's settings exactly, so a dimension edit is comparable before and
after. ``showcase`` exists for the opposite reason: it forces the handful of
settings that part-to-part and thin-pillar routes need and shapes geometry that
triggers every route the router can emit, so every kind is visible at once
instead of only after someone finds two settings.
"""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np

from .config import EXAMPLE_LAYOUTS as LAYOUTS, validate_settings
from .contracts import VoxelMillError
from .geometry import manifold_triangles
from .supports import build_column_field, route_contacts

#: What ``showcase`` has to switch on to show every route kind. Model anchors
#: are off by default, thin model pillars need a diameter and a length
#: threshold set together, and bracing can be off in the caller's draft, so a
#: demo that honoured the caller's settings would show nothing for four of the
#: six kinds. Only values that actually differ are reported as ``overrides``.
SHOWCASE_OVERRIDES = {
    # The brace network is one of the kinds this layout promises to show, so
    # it is forced like the rest rather than left to the caller's draft.
    'auto_bracing': True,
    'allow_part_to_part': True,
    # Not 0: avoidance 0 takes whichever route is shorter, and a model route
    # around any obstruction is always shorter than branching past it, so
    # every station would anchor on the model and the branch case would never
    # appear. A middling value lets each station win on its own geometry.
    'part_to_part_avoidance': 0.4,
    'small_pillar_mode': 'model',
    'small_pillar_diameter_mm': 0.6,
    'small_pillar_max_length_mm': 2.5,
}


def _array_geometry(m, spacing, height_mm, layout):
    """The historical beam-on-a-pedestal pair and its four fixed contacts."""
    contacts = np.array([(x, y, height_mm)
                         for x in (-spacing / 2, spacing / 2)
                         for y in (-spacing / 2, spacing / 2)])
    beam = m.Manifold.cube((2 * spacing, 2 * spacing, 2)).translate(
        (-spacing, -spacing, height_mm))
    pedestal = m.Manifold.cube((spacing * .8, spacing * .8, height_mm * .4)).translate(
        (-spacing * .9, -spacing * .9, 0))
    if layout == 'part-to-part':
        # A broad lower platform makes the model-to-model gap legible. It
        # reaches past a branch's two-spacing reach on every side, so no
        # contact can route to the plate. The router still obeys the user's
        # allow/avoidance settings without edits.
        half = 3.5 * spacing
        pedestal = m.Manifold.cube((2 * half, 2 * half, height_mm * .4)).translate(
            (-half, -half, 0))
    return beam + pedestal, contacts


def _showcase_geometry(m, spacing, height_mm):
    """One bar over five stations, each shaped to force a different route.

    Left to right: nothing below (plate route), a low blocker with a free
    neighbour (branch), a tall platform (model anchor), a post stopping just
    short of the bar (thin model pillar), and a floating slab with no material
    under it at all (island). The stations stand far enough apart that none of
    them hides its neighbour.

    The plate-route station is a pair rather than a single contact. Two
    grounded pillars close together give the brace network a destination even
    under ``brace_destination='supports'``, which only accepts an already
    grounded support node; a lone grounded pillar leaves every candidate with
    nowhere to land.
    """
    step = 2.2 * spacing
    stations = {'vertical': -2 * step, 'branched': -step, 'model_anchor': 0.0,
                'small_model': step, 'island': 2 * step}
    half_y = 0.6 * spacing
    span = 4 * step + 2 * spacing
    bar = m.Manifold.cube((span, 2 * half_y, 2)).translate(
        (-span / 2, -half_y, height_mm))
    # Blocks the column under its contact without reaching the neighbours, so
    # the router has to branch sideways rather than fail. Deliberately low:
    # anchoring on it would be a near-full-height connector, which avoidance
    # rejects in favour of the branch.
    #
    # Its width is capped against the contact height, not just the spacing.
    # The branch has to reach past this block, so its sideways travel grows
    # with the width while the drop does not; at 30 mm spacing over a 20 mm
    # bar that detour got long enough that anchoring on the block won instead,
    # and the branch case vanished from the demo.
    blocker_width = min(1.0 * spacing, .5 * height_mm)
    blocker = m.Manifold.cube((blocker_width, 2 * half_y, height_mm * .2)).translate(
        (stations['branched'] - blocker_width / 2, -half_y, 0))
    # Tall enough that anchoring on it is clearly shorter than branching round
    # it, and still far enough under the bar for a full connector rather than
    # a thin pillar.
    platform = m.Manifold.cube((1.4 * spacing, 1.4 * spacing, height_mm * .7)).translate(
        (stations['model_anchor'] - .7 * spacing, -.7 * spacing, 0))
    # Stops a hair under the bar, inside small_pillar_max_length_mm.
    gap = min(2.0, height_mm * .1)
    post = m.Manifold.cube((.7 * spacing, .7 * spacing, height_mm - gap)).translate(
        (stations['small_model'] - .35 * spacing, -.35 * spacing, 0))
    # Born mid-slice with nothing beneath it: the raster island case.
    island_z = height_mm * .5
    island = m.Manifold.cube((.7 * spacing, .7 * spacing, 1.0)).translate(
        (stations['island'] - .35 * spacing, -.35 * spacing, island_z))
    twin = 0.9 * spacing
    contacts = np.array([
        (stations['vertical'], -twin / 2, height_mm),
        (stations['vertical'], twin / 2, height_mm),
        (stations['branched'], 0.0, height_mm),
        (stations['model_anchor'], 0.0, height_mm),
        (stations['small_model'], 0.0, height_mm),
        (stations['island'], 0.0, island_z),
    ])
    return bar + blocker + platform + post + island, contacts


def support_example(settings, height_mm=20.0, *, layout='array', cancel=None):
    """Return model/support/base triangle groups and routing evidence.

    In ``array`` and ``part-to-part``, four fixed contacts sit under a floating
    beam and a pedestal beneath one contact offers a model anchor competing
    with a route around it to the plate. ``showcase`` instead spreads six
    contacts over five stations so every route kind appears, and forces the
    settings those routes need (:data:`SHOWCASE_OVERRIDES`). Contact selection
    is deliberately fixed in all three so dimension edits are comparable.
    """
    import manifold3d as m
    validate_settings(settings)
    if not math.isfinite(height_mm) or not 3 <= height_mm <= 160:
        raise VoxelMillError('invalid_example', 'Example height must be between 3 and 160 mm')
    if layout not in LAYOUTS:
        raise VoxelMillError('invalid_example',
                        'Example layout must be one of ' + ', '.join(LAYOUTS))
    if layout == 'showcase' and height_mm < SHOWCASE_MIN_HEIGHT_MM:
        raise VoxelMillError(
            'invalid_example',
            f'The showcase layout needs at least {SHOWCASE_MIN_HEIGHT_MM:g} mm of height to '
            'show every support kind; shorter bars sit below the tip and connector lengths. '
            'Use the array layout for a shorter example.')
    spacing = float(settings['support']['spacing_mm'])
    if not 1 <= spacing <= 30:
        raise VoxelMillError('invalid_example', 'Example supports spacing from 1 to 30 mm; '
                        'the project settings may use other validated values')
    overrides = {}
    if layout == 'showcase':
        # Copied, never edited in place: the caller's document keeps its own
        # settings and only this fixture routes with the demo values.
        settings = deepcopy(settings)
        for key, value in SHOWCASE_OVERRIDES.items():
            if settings['support'][key] != value:
                overrides[key] = value
            settings['support'][key] = value
        validate_settings(settings)
        model, contacts = _showcase_geometry(m, spacing, height_mm)
    else:
        model, contacts = _array_geometry(m, spacing, height_mm, layout)
    triangles = manifold_triangles(model).astype(np.float32)
    bounds = np.asarray(model.bounding_box()).reshape(2, 3)
    # The historical layouts keep their coarse analysis pitch so their numbers
    # stay comparable; the showcase uses the production pitch, because a demo
    # of anchor placement should not be the one place it is approximated.
    pitch = None if layout == 'showcase' else .5
    field = build_column_field(triangles, bounds, settings, pitch_mm=pitch, cancel=cancel)
    plan, base = route_contacts(contacts, field, settings, cancel=cancel)
    groups = {'model': triangles,
              'supports': (np.concatenate([manifold_triangles(s) for s in plan.solids])
                           if plan.solids else np.empty((0, 3, 3))),
              'raft': (manifold_triangles(base) if base is not None
                       else np.empty((0, 3, 3)))}
    blocked = layout == 'part-to-part' and not settings['support'].get('allow_part_to_part')
    hints = {'part-to-part': ('Nothing routes: every contact here sits over the lower part and '
                              'part-to-part supports are off. Enable them to route onto the '
                              'lower part.' if blocked else
                              'Every contact sits over the lower part, so each lands on it. Lower '
                              'the avoidance below 1 to let model routes compete with plate routes '
                              'where both exist.'),
             'showcase': 'Forces the part-to-part and thin-pillar settings listed '
                         'under overrides so every route kind is visible.'}
    return {'triangles': groups, 'contacts': contacts, 'metrics': plan.metrics,
            'layout': layout, 'hint': hints.get(layout, ''),
            'overrides': overrides,
            'categories': example_categories(plan.metrics, field),
            'height_mm': float(height_mm),
            'analysis_pitch_mm': float(field.grid.dx),
            'basis': f'{len(contacts)} fixed contacts; production router and geometry; '
                     'illustration only, no print validation or strength proof'}


#: The route kinds ``showcase`` guarantees. ``braces`` is deliberately not one
#: of them: the brace network is governed by brace geometry settings the
#: layout leaves to the caller, exactly so a reader can watch their own
#: bracing choices take effect, and some tunings legitimately reach nothing to
#: land on. Braces are reported either way.
SHOWCASE_ROUTE_KINDS = ('vertical', 'branched', 'model_anchor',
                        'small_model_pillar', 'raster_islands')

#: Shortest bar height at which ``showcase`` can keep its promise. Below this
#: the whole structure is smaller than the fixed features standing in it -- a
#: 2 mm tip and a 2 mm bottom connector -- and the branch station stops
#: branching, because going around the block costs more than anchoring on it.
#: Refused outright rather than quietly returning four kinds out of six.
SHOWCASE_MIN_HEIGHT_MM = 8.0


def example_categories(metrics, field):
    """How many of each support kind this example actually produced.

    Reported so a reader can tell at a glance which kinds the geometry
    exercised, rather than inferring it from the picture.
    """
    routing = dict(metrics.get('routing') or {})
    return {'vertical': int(routing.get('vertical', 0)),
            'branched': int(routing.get('branched', 0)),
            'model_anchor': int(routing.get('model_anchor', 0)),
            'small_model_pillar': int(metrics.get('small_model_pillars', 0) or 0),
            'braces': int(metrics.get('braces', 0) or 0),
            'raster_islands': len(getattr(field, 'islands', ()) or ())}


def save_example(path, example):
    """Save an illustrative STL with the same atomic writer as project meshes."""
    from .mesh import write_stl
    write_stl(path, tuple(example['triangles'].values()))
