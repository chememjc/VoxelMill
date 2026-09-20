"""Low diagonal braces can narrow an otherwise open drainage route."""
import numpy as np
import manifold3d as m
import pytest

from voxelmill.geometry import cylinder_between, manifold_triangles, mesh_to_manifold
from voxelmill.mesh import open_stl, write_stl
from voxelmill.pipeline import prepare
from voxelmill.validation import analyze_drainage
from test_pipeline import small


def read_solid(path):
    with open_stl(path) as mesh:
        solid, _ = mesh_to_manifold(np.array(mesh.triangles, copy=True))
    return solid


@pytest.mark.parametrize('base_type', ['skate', 'skeleton', 'grid'])
def test_low_braces_honestly_fail_drainage_without_disconnected_geometry(tmp_path, base_type):
    source = tmp_path / 'sphere.stl'
    write_stl(source, manifold_triangles(m.Manifold.sphere(6, 48)))
    settings = small(support={'base_type': base_type, 'base_touch_diameter_mm': 2.4,
                             'base_thickness_mm': .8, 'base_skate_length_mm': 5})
    settings['support']['auto_bracing'] = False
    baseline_path = tmp_path / 'baseline.stl'
    baseline = prepare(source, settings, output=baseline_path, drainage=True)
    assert baseline['validation']['passed']
    settings['support']['auto_bracing'] = True
    rejected_path = tmp_path / 'rejected.stl'
    rejected = prepare(source, settings, output=rejected_path, drainage=True)
    assert not rejected['validation']['passed']
    assert rejected['validation']['checks']['drainage_bottlenecks'] == 'fail'
    assert not rejected['export']['written']
    assert not rejected_path.exists()
    for check in ('raster_connectivity', 'overlap', 'closed_surface', 'enclosed_voids'):
        assert rejected['validation']['checks'][check] == 'pass'
    drainage = rejected['validation']['metrics']['drainage']
    assert drainage['occupancy_closed']
    assert drainage['bottlenecked_components'] > 0
    assert drainage['enclosed_components'] == 0
    assert all(example['bottleneck_area_upper_mm2'] < drainage['min_orifice_area_mm2']
               for example in drainage['bottleneck_examples'])

    # Explicit diagnostic export retains the failed verdict. Inspect its exact
    # union and base to distinguish drainage channels from a detached foot or
    # an overlapping-shell artifact; this is not acceptance of the export.
    inspection_path = tmp_path / 'inspection.stl'
    inspection = prepare(source, settings, output=inspection_path, components=True,
                         drainage=True, allow_unresolved=True)
    assert inspection['export']['warned']
    assert inspection['validation']['checks']['drainage_bottlenecks'] == 'fail'
    complete = read_solid(inspection_path)
    assert len(complete.decompose()) == 1
    assert complete.bounding_box()[2] >= -1e-8
    base = read_solid(tmp_path / 'inspection-raft.stl')
    graph = inspection['support_graph']
    positions = {node['id']: node['position_mm'] for node in graph['nodes']}
    stems = [edge for edge in graph['edges'] if edge['kind'] == 'brace_foot']
    assert stems
    for edge in stems:
        stem = cylinder_between(positions[edge['start']], positions[edge['end']], edge['radius_mm'])
        assert (stem - base).volume() == pytest.approx(0., abs=1e-8)

    # Even the enlarged base, including the two new feet, drains before the
    # diagonals are present. The branch-created channel is the failing change.
    expanded_base_only = read_solid(baseline_path) + base
    comparison = analyze_drainage(manifold_triangles(expanded_base_only),
                                  np.asarray(expanded_base_only.bounding_box()).reshape(2, 3), settings)
    assert comparison['bottlenecked_components'] == 0
