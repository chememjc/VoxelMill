"""Braced supports stay one connected, drainable solid."""
import numpy as np
import manifold3d as m
import pytest

from voxelmill.geometry import cylinder_between, manifold_triangles
from voxelmill.mesh import open_stl, write_stl
from voxelmill.pipeline import prepare
from test_pipeline import small


def read_solid(path):
    """Reopen an exported STL as a Manifold, welding exact duplicate vertices.

    The strict importer also refuses float32 rounding slivers where an exact
    union meets itself (ISSUES VM-096); topology and volume are what matter here.
    """
    with open_stl(path) as mesh:
        triangles = np.array(mesh.triangles, copy=True).reshape(-1, 3)
    vertices, faces = np.unique(triangles, axis=0, return_inverse=True)
    solid = m.Manifold(m.Mesh(vert_properties=vertices.astype(np.float32),
                              tri_verts=faces.reshape(-1, 3).astype(np.uint32)))
    assert solid.status() == m.Error.NoError
    return solid


@pytest.mark.parametrize('base_type', ['skate', 'skeleton', 'grid'])
def test_braced_sphere_exports_one_solid_with_brace_feet_inside_the_base(tmp_path, base_type):
    """Braces and their new plate feet join the base and the model as one solid.

    Under the old grid-cell contact layout these braces narrowed a drainage
    route; the hexagonal layout leaves it open. What must hold either way:
    the exported union is one connected solid above the plate, every brace
    foot stem sits inside the base, and drainage is actually evaluated.
    """
    source = tmp_path / 'sphere.stl'
    write_stl(source, manifold_triangles(m.Manifold.sphere(6, 48)))
    settings = small(support={'base_type': base_type, 'base_touch_diameter_mm': 2.4,
                             'base_thickness_mm': .8, 'base_skate_length_mm': 5},
                     repair={'support_void_policy': 'fail'})
    output = tmp_path / 'braced.stl'
    report = prepare(source, settings, output=output, components=True, drainage=True)
    assert report['validation']['passed'], report['validation']['checks']
    assert report['export']['written']
    drainage = report['validation']['metrics']['drainage']
    assert drainage['occupancy_closed']
    assert drainage['bottlenecked_components'] == 0
    complete = read_solid(output)
    assert len(complete.decompose()) == 1
    assert complete.bounding_box()[2] >= -1e-8
    base = read_solid(tmp_path / 'braced-raft.stl')
    graph = report['support_graph']
    positions = {node['id']: node['position_mm'] for node in graph['nodes']}
    assert any(edge['kind'] == 'brace' for edge in graph['edges'])
    stems = [edge for edge in graph['edges'] if edge['kind'] == 'brace_foot']
    assert stems
    for edge in stems:
        stem = cylinder_between(positions[edge['start']], positions[edge['end']], edge['radius_mm'])
        assert (stem - base).volume() == pytest.approx(0., abs=1e-8)
