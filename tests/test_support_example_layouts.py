"""The three support example layouts, and the gap they must not have.

A strut that stops short of the surface it was routed to is the defect the
0.5.3 editor showed. It cannot be detected by sampling the column under a
contact -- a branched or slanted strut legitimately leaves that column -- so
the check here unions the router's own solids and counts connected components.
One component means every strut reaches what it was aimed at.
"""
from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest

from voxelmill.config import DEFAULTS
from voxelmill.contracts import VoxelMillError
from voxelmill.support_example import (LAYOUTS, SHOWCASE_OVERRIDES,
                                       SHOWCASE_ROUTE_KINDS, support_example,
                                       save_example)

pytest.importorskip('manifold3d')


def _settings(**support):
    settings = deepcopy(DEFAULTS)
    settings['support'].update(support)
    return settings


def _example(layout, height_mm=20.0, **support):
    return support_example(_settings(**support), height_mm, layout=layout)


def test_layouts_are_the_three_documented_ones():
    assert LAYOUTS == ('array', 'part-to-part', 'showcase')
    with pytest.raises(VoxelMillError) as error:
        _example('spiral')
    assert error.value.code == 'invalid_example'


def test_showcase_routes_every_contact_on_stock_settings():
    """The showcase has to work with no settings hunt; that is why it exists."""
    example = _example('showcase')
    assert example['layout'] == 'showcase'
    assert example['metrics']['contacts_failed'] == 0
    assert example['metrics']['contacts_routed'] == len(example['contacts'])
    assert len(example['triangles']['model'])
    assert len(example['triangles']['supports'])


@pytest.mark.parametrize('layout', ('array', 'part-to-part'))
def test_historical_layouts_route_once_part_to_part_is_enabled(layout):
    example = _example(layout, allow_part_to_part=True, part_to_part_avoidance=0.0)
    assert example['metrics']['contacts_failed'] == 0
    assert example['metrics']['contacts_routed'] == len(example['contacts'])
    assert len(example['triangles']['supports'])


def test_part_to_part_layout_is_empty_until_its_setting_is_enabled():
    """Recorded, not endorsed: the historical layouts obey the caller exactly.

    ``part-to-part``'s broad lower platform blocks every plate route, so with
    ``allow_part_to_part`` off -- the default -- all four contacts are
    unroutable and the picture is bare. That is what the editor's "Show
    part-to-part supports" button exists to fix, and what the ``showcase``
    layout avoids by forcing the setting itself.
    """
    example = _example('part-to-part')
    assert example['metrics']['contacts_routed'] == 0
    assert example['metrics']['contacts_failed'] == len(example['contacts'])
    assert example['hint']


def test_showcase_exercises_every_support_category():
    example = _example('showcase')
    categories = example['categories']
    # The whole point of this layout: nothing is zero, so a reader can see
    # each kind at once instead of after finding two settings.
    assert set(categories) == set(SHOWCASE_ROUTE_KINDS) | {'braces'}
    for kind, count in categories.items():
        assert count > 0, (kind, categories)


def test_showcase_holds_its_promise_from_a_hostile_draft():
    """Every kind, whatever the caller's draft says.

    Bracing off and tree supports on is exactly the state the packaged
    acceptance run reaches by the time it gets to this layout, and it is what
    caught ``auto_bracing`` missing from the forced set.
    """
    example = _example('showcase', auto_bracing=False, tree_supports=True,
                       allow_part_to_part=False, part_to_part_avoidance=1.0,
                       small_pillar_mode='middle', small_pillar_diameter_mm=0.0,
                       small_pillar_max_length_mm=0.0)
    for kind in SHOWCASE_ROUTE_KINDS:
        assert example['categories'][kind] > 0, (kind, example['categories'])
    assert set(example['overrides']) == set(SHOWCASE_OVERRIDES)


@pytest.mark.parametrize('bracing', [
    {'brace_destination': 'both', 'brace_pattern': 'single'},
    {'brace_destination': 'base', 'brace_pattern': 'single'},
    {'brace_destination': 'both', 'brace_pattern': 'alternating'},
    {'brace_destination': 'both', 'brace_pattern': 'x'},
])
def test_showcase_route_kinds_survive_any_bracing_choice(bracing):
    """Brace settings steer the network, never the routes.

    The layout leaves brace geometry to the caller so their own choices are
    visible in the picture; a tuning that reaches nothing to land on may show
    no braces, but it must never cost a route kind.
    """
    example = _example('showcase', **bracing)
    for kind in SHOWCASE_ROUTE_KINDS:
        assert example['categories'][kind] > 0, (kind, example['categories'])
    assert example['categories']['braces'] > 0, bracing


@pytest.mark.parametrize('height_mm', [10.0, 20.0, 40.0, 160.0])
def test_showcase_categories_survive_the_height_range(height_mm):
    categories = _example('showcase', height_mm)['categories']
    for kind, count in categories.items():
        assert count > 0, (height_mm, kind, categories)


def test_showcase_reports_what_it_forced_and_leaves_the_caller_alone():
    settings = _settings()
    before = deepcopy(settings)
    example = support_example(settings, 20.0, layout='showcase')
    assert settings == before, 'the caller\'s settings must not be edited'
    assert example['overrides']
    assert set(example['overrides']) <= set(SHOWCASE_OVERRIDES)
    for key, value in example['overrides'].items():
        assert value == SHOWCASE_OVERRIDES[key]
        assert DEFAULTS['support'][key] != value


def test_showcase_analyses_at_the_production_pitch():
    # The older layouts keep a coarse 0.5 mm pitch so their numbers stay
    # comparable; a demo of anchor placement must not be the one place the
    # surface below an anchor is approximated more coarsely than in a real job.
    assert _example('showcase')['analysis_pitch_mm'] < 0.2
    assert _example('array')['analysis_pitch_mm'] == pytest.approx(0.5)


@pytest.mark.parametrize('layout', LAYOUTS)
def test_supports_reach_the_model_without_gaps(layout):
    import manifold3d as m
    from voxelmill.geometry import manifold_triangles
    from voxelmill.support_example import _array_geometry, _showcase_geometry
    from voxelmill.supports import build_column_field, route_contacts

    settings = _settings(**(SHOWCASE_OVERRIDES if layout == 'showcase'
                            else {'allow_part_to_part': True,
                                  'part_to_part_avoidance': 0.0}))
    spacing = settings['support']['spacing_mm']
    if layout == 'showcase':
        model, contacts = _showcase_geometry(m, spacing, 20.0)
    else:
        model, contacts = _array_geometry(m, spacing, 20.0, layout)
    triangles = manifold_triangles(model).astype(np.float32)
    bounds = np.asarray(model.bounding_box()).reshape(2, 3)
    field = build_column_field(triangles, bounds, settings,
                               pitch_mm=None if layout == 'showcase' else .5)
    plan, base = route_contacts(contacts, field, settings)
    assert plan.solids
    whole = model
    for solid in plan.solids:
        whole = whole + solid
    if base is not None:
        whole = whole + base
    pieces = whole.decompose()
    assert len(pieces) == 1, [piece.volume() for piece in pieces]


def test_saving_an_example_writes_every_group(tmp_path):
    example = _example('showcase')
    path = tmp_path / 'showcase.stl'
    save_example(path, example)
    assert path.exists() and path.stat().st_size > 0
