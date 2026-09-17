#!/usr/bin/env python3
"""Regenerate the committed generic test shapes under fixtures/shapes/.

Every shape is millimeters, sits on z=0 and fits the Mars 5 Ultra envelope, so
it can be fed to any voxelmill command unmodified. Output is byte-reproducible:
write_stl recomputes normals and stores no timestamp, so re-running this script
changes nothing but the manifest's generated_utc.
"""
import hashlib
import json
import time
from pathlib import Path
import numpy as np
import manifold3d as m
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import inspect_mesh, write_stl

ROOT = Path(__file__).resolve().parents[1]
SHAPES = ROOT / 'fixtures/shapes'
SEGMENTS = 64
COUNTERS = ('boundary_edges', 'nonmanifold_edges', 'inconsistent_winding_edges',
            'degenerate_triangles', 'invalid_triangles', 'self_intersections',
            'connected_components', 'unique_vertices')


def on_plate(solid):
    """Drop a solid so its lowest point rests on the build plate."""
    return solid.translate((0, 0, -solid.bounding_box()[2]))


def box_triangles(size, origin=(0., 0., 0.)):
    """Axis-aligned closed box as raw outward-wound triangles."""
    (sx, sy, sz), (ox, oy, oz) = size, origin
    v = np.array([[ox, oy, oz], [ox+sx, oy, oz], [ox+sx, oy+sy, oz], [ox, oy+sy, oz],
                  [ox, oy, oz+sz], [ox+sx, oy, oz+sz], [ox+sx, oy+sy, oz+sz], [ox, oy+sy, oz+sz]],
                 dtype=np.float32)
    faces = [[0, 2, 1], [0, 3, 2],   # bottom
             [4, 5, 6], [4, 6, 7],   # top
             [0, 1, 5], [0, 5, 4],   # -y
             [2, 3, 7], [2, 7, 6],   # +y
             [1, 2, 6], [1, 6, 5],   # +x
             [3, 0, 4], [3, 4, 7]]   # -x
    return v[np.array(faces)]


def tetra_triangles(scale=20.):
    """The four-triangle solid used by tests/test_mesh.py, scaled to millimeters."""
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32) * scale
    return v[[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]]


# --- valid primitives -------------------------------------------------------

def cube():
    """A 20 mm cube."""
    return on_plate(m.Manifold.cube((20, 20, 20), True))


def sphere():
    """A 10 mm radius sphere, tangent to the plate."""
    return on_plate(m.Manifold.sphere(10, SEGMENTS))


def cylinder():
    """An upright 8 mm radius, 25 mm tall cylinder."""
    return m.Manifold.cylinder(25, 8, 8, SEGMENTS)


def cone():
    """A 10 mm radius, 20 mm tall cone."""
    return m.Manifold.cylinder(20, 10, 0, SEGMENTS)


def torus():
    """A genus one solid: components and Euler counts are not trivial here."""
    ring = m.CrossSection.circle(4, SEGMENTS // 2).translate((12, 0))
    return on_plate(ring.revolve(48))


# --- structures that exercise the slicer ------------------------------------

def overhang_bracket():
    """Column, an unsupported horizontal shelf and a 45 degree ramp."""
    column = m.Manifold.cube((10, 20, 40))
    shelf = m.Manifold.cube((30, 20, 5)).translate((10, 0, 30))
    wedge = m.CrossSection([[(0, 0), (20, 0), (0, 20)]]).extrude(20)
    ramp = wedge.rotate((90, 0, 0)).translate((10, 20, 0))
    return column + shelf + ramp


def hollow_cup():
    """Sealed shell: the cavity has no path to the outside, so resin is trapped."""
    return m.Manifold.cylinder(30, 12, 12, SEGMENTS) - \
        m.Manifold.cylinder(26, 9, 9, SEGMENTS).translate((0, 0, 2))


def drained_cup():
    """The sealed shell with a drain hole through the lid: the passing control."""
    return hollow_cup() - m.Manifold.cylinder(10, 2, 2, SEGMENTS).translate((0, 0, 25))


def thin_wall():
    """A 0.3 mm fin on a base: near the 0.018 mm pixel pitch, far below a support."""
    base = m.Manifold.cube((30, 10, 2))
    fin = m.Manifold.cube((30, .3, 15)).translate((0, 4.85, 2))
    return base + fin


def pin_array():
    """Base plate plus pins that start in mid-air: islands on their first layer."""
    solid = m.Manifold.cube((30, 30, 2))
    for x in (6, 15, 24):
        for y in (6, 15, 24):
            solid += m.Manifold.cylinder(6, 1.5, 1.5, SEGMENTS).translate((x, y, 10))
    return solid


def stepped_pyramid():
    """Five slabs: the cross-section area changes abruptly between layers."""
    slabs = [m.Manifold.cube((30 - 5 * step, 30 - 5 * step, 4))
             .translate((step * 2.5, step * 2.5, step * 4)) for step in range(5)]
    return m.Manifold.batch_boolean(slabs, m.OpType.Add)


# --- intentionally invalid meshes -------------------------------------------

def open_box():
    """Cube missing its top face: an open boundary."""
    return np.delete(box_triangles((20, 20, 20)), [2, 3], axis=0)


def flipped_winding():
    """Cube with one face wound inward."""
    triangles = box_triangles((20, 20, 20)).copy()
    triangles[[2, 3]] = triangles[[2, 3]][:, ::-1]
    return triangles


def nonmanifold_edge():
    """Three triangles sharing a single edge."""
    v = np.array([[0, 0, 0], [20, 0, 0], [0, 20, 0], [0, 0, 20], [0, -14, -14]], dtype=np.float32)
    return v[[[0, 1, 2], [0, 1, 3], [0, 1, 4]]]


def degenerate():
    """A valid tetrahedron plus zero-area triangles."""
    zero = np.array([[[0, 0, 0], [10, 0, 0], [10, 0, 0]],
                     [[5, 5, 5], [5, 5, 5], [5, 5, 5]],
                     [[0, 0, 0], [4, 4, 4], [8, 8, 8]]], dtype=np.float32)
    return np.concatenate([tetra_triangles(), zero])


def self_intersecting():
    """Two interpenetrating tetrahedra: closed everywhere, still not a solid."""
    return np.concatenate([tetra_triangles(), tetra_triangles() + np.float32([5, 5, 5])])


# --- ASCII variant ----------------------------------------------------------

def write_ascii_stl(path, triangles, name='cube'):
    """write_stl is binary only; this exercises the strict ASCII reader instead."""
    lines = [f'solid {name}']
    for triangle in np.asarray(triangles, dtype=np.float32):
        edge = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
        length = float(np.linalg.norm(edge))
        normal = edge / length if length else edge
        lines.append('facet normal %.6e %.6e %.6e' % tuple(normal))
        lines.append('outer loop')
        lines += ['vertex %.6e %.6e %.6e' % tuple(vertex) for vertex in triangle]
        lines += ['endloop', 'endfacet']
    lines.append(f'endsolid {name}')
    path.write_text('\n'.join(lines) + '\n')


SOLIDS = {'cube': cube, 'sphere': sphere, 'cylinder': cylinder, 'cone': cone,
          'torus': torus, 'overhang_bracket': overhang_bracket, 'hollow_cup': hollow_cup,
          'drained_cup': drained_cup, 'thin_wall': thin_wall, 'pin_array': pin_array,
          'stepped_pyramid': stepped_pyramid}
RAW = {'tetrahedron': tetra_triangles}
INVALID = {'open_box': open_box, 'flipped_winding': flipped_winding,
           'nonmanifold_edge': nonmanifold_edge, 'degenerate': degenerate,
           'self_intersecting': self_intersecting}


def note(build):
    return (build.__doc__ or '').strip().splitlines()[0]


def record(path, note):
    path.chmod(0o644)  # write_stl inherits the private mode of its scratch file
    result = inspect_mesh(path)
    entry = {'path': str(path.relative_to(ROOT)), 'note': note,
             'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
             'triangle_count': int(result['triangle_count']),
             'bounds': [[round(float(value), 6) for value in corner] for corner in result['bounds']],
             'signed_volume_mm3': round(float(result['signed_volume_mm3']), 6)}
    entry.update({key: int(result[key]) for key in COUNTERS})
    entry['self_intersections_status'] = result['self_intersections_status']
    return entry


if __name__ == '__main__':
    (SHAPES / 'invalid').mkdir(parents=True, exist_ok=True)
    shapes = []
    for name, build in sorted(SOLIDS.items()):
        path = SHAPES / f'{name}.stl'
        write_stl(path, manifold_triangles(build()))
        shapes.append(record(path, note(build)))
        print(path.name, shapes[-1]['triangle_count'], flush=True)
    for name, build in sorted(RAW.items()):
        path = SHAPES / f'{name}.stl'
        write_stl(path, build())
        shapes.append(record(path, note(build)))
        print(path.name, shapes[-1]['triangle_count'], flush=True)
    for name, build in sorted(INVALID.items()):
        path = SHAPES / 'invalid' / f'{name}.stl'
        write_stl(path, build())
        shapes.append(record(path, note(build)))
        print(path.name, shapes[-1]['triangle_count'], flush=True)
    ascii_path = SHAPES / 'cube_ascii.stl'
    write_ascii_stl(ascii_path, manifold_triangles(cube()))
    shapes.append(record(ascii_path, 'The 20 mm cube as ASCII text, for the ASCII reader.'))
    shapes[-1]['source_format'] = 'ascii_stl'
    print(ascii_path.name, shapes[-1]['triangle_count'], flush=True)
    manifest = {'schema_version': 1,
                'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'method': 'scripts/make_test_shapes.py; manifold3d CSG and raw triangle '
                          'arrays, written with voxelmill.mesh.write_stl and measured with '
                          'voxelmill.mesh.inspect_mesh',
                'shapes': shapes}
    (SHAPES / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
