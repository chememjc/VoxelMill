# Generic test shapes

Small, committed STL fixtures for the test suite and for manual CLI runs. They are
millimetres, rest on `z = 0` and fit the Mars 5 Ultra envelope, so any `voxelmill`
command accepts them unmodified.

Regenerate with `.venv/bin/python scripts/make_test_shapes.py`. The output is
byte-reproducible: `write_stl` recomputes normals and stores no timestamp, so a
regeneration changes nothing but `generated_utc` in `manifest.json`.
`tests/test_shapes.py` checks every file against that manifest.

## Valid primitives

| File | Shape |
| --- | --- |
| `cube.stl` | 20 mm cube |
| `sphere.stl` | 10 mm radius sphere |
| `cylinder.stl` | 8 mm radius, 25 mm tall |
| `cone.stl` | 10 mm radius, 20 mm tall |
| `tetrahedron.stl` | the four-triangle solid, 20 mm |
| `torus.stl` | genus one, so component and Euler counts are non-trivial |
| `cube_ascii.stl` | the cube as ASCII text, for the strict ASCII reader |

## Structures that exercise the slicer

| File | What it is for |
| --- | --- |
| `overhang_bracket.stl` | unsupported shelf at z = 30 and a 45° ramp — support planning |
| `hollow_cup.stl` | sealed cavity, no drain — `drainage_check` must **fail** |
| `drained_cup.stl` | the same shell with a drain hole — the passing control |
| `thin_wall.stl` | 0.3 mm fin on a base — near-pixel raster behaviour |
| `pin_array.stl` | plate plus nine pins starting in mid-air — islands |
| `stepped_pyramid.stl` | five slabs — abrupt per-layer area changes |

## Intentionally invalid meshes (`invalid/`)

Not repairable-by-design; they exist so the inspection and error paths have inputs.

| File | Defect it reports |
| --- | --- |
| `open_box.stl` | `boundary_edges` |
| `flipped_winding.stl` | `inconsistent_winding_edges` |
| `nonmanifold_edge.stl` | `nonmanifold_edges` |
| `degenerate.stl` | `degenerate_triangles` / `invalid_triangles` |
| `self_intersecting.stl` | `self_intersections` |
