# Stability policy

VoxelMill is pre-1.0: anything may still change, and the changes are listed in
ISSUES.md. From 1.0 on, the interfaces below follow semantic versioning. A
minor release may add to them; only a major release may remove or change the
meaning of something already there, and only after one minor release that
warns about it.

| Interface | What is promised | How it is guarded |
| --- | --- | --- |
| Command line | Command names, option names and their meaning | `tests/test_cli_surface.py` fails when a recorded command or option disappears; `tests/data/cli_surface.json` is the record |
| Exit codes | `0` success, `2` answered no (report written), `3` structured error, `64` usage error, `130` canceled ([cli.md](cli.md#exit-codes)) | Command tests |
| Report JSON | Fields in `schemas/voxelmill-report.schema.json` keep their names, types and meaning | `tests/test_report_schema.py` |
| Files VoxelMill writes and reads | Printer (`.ptr`) and resin (`.res`) profiles, presets, `.voxmil` projects | `voxelmill/versioning.py`: a new schema version needs a migration from the previous one, or an entry in `REFUSED` saying why not |
| Settings keys | `section.key` paths accepted by `--set` and profiles | Settings validation rejects unknown keys, so a rename breaks visibly |
| Printer output | GOO v3 and classic CTB v3 bytes for a given input and settings | `reports/golden/` via `scripts/equivalence.py`; layer digests in slice reports |

Not covered: Python module APIs inside `voxelmill` (the command line and the
file formats are the interface), the editor's layout, and timing or memory
figures.

Dependencies are pinned to compatible ranges in `pyproject.toml`.
`manifold3d` allows patch releases only, because boolean results can change
between minor versions and that changes output geometry.
