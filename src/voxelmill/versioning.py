"""Schema versions of every file VoxelMill reads, and how old ones upgrade.

Each persisted kind carries an integer ``schema_version``. :func:`upgrade` is
the one place that checks it: data from an older version is migrated one step
at a time through :data:`MIGRATIONS`, data from a newer build is refused with
a message that says so, and a version that cannot be migrated is refused with
the reason recorded in :data:`REFUSED`.

To change a format: bump its entry in :data:`CURRENT_VERSIONS` and register
``MIGRATIONS[kind][old] = function`` taking the old document and returning the
next version's. Loaders never compare version numbers themselves.
"""
from __future__ import annotations

from typing import Callable

from .contracts import VoxelMillError

#: The version this build reads and writes, per kind.
CURRENT_VERSIONS = {
    'profile': 1,    # printer .ptr and resin .res TOML
    'settings': 1,   # a resolved settings tree (also embedded in projects)
    'preset': 1,     # portable support/process preset JSON
    'project': 2,    # .voxmil archive manifest
}

#: ``MIGRATIONS[kind][n]`` turns a version-n document into version n + 1.
MIGRATIONS: dict[str, dict[int, Callable[[dict], dict]]] = {kind: {} for kind in CURRENT_VERSIONS}

#: Versions deliberately not migrated, with the reason a user is told.
REFUSED = {
    ('project', 1): 'Schema 2 moved paint from plate-coordinate centroids to one local-frame '
                    'record per object; a schema-1 mark cannot be attributed to a part after '
                    'the fact, so it is refused rather than reinterpreted',
}

_LABELS = {'profile': 'Profile', 'settings': 'Resolved settings', 'preset': 'Preset',
           'project': 'Project'}


def upgrade(kind, data, *, code):
    """Return ``data`` at the current version of ``kind``, or raise ``code``."""
    current = CURRENT_VERSIONS[kind]
    label = _LABELS[kind]
    version = data.get('schema_version') if isinstance(data, dict) else None
    if type(version) is not int:
        raise VoxelMillError(code, f'{label} requires integer schema_version {current}')
    if version > current:
        raise VoxelMillError(code, f'{label} schema_version {version} was written by a newer '
                                   f'VoxelMill; this build reads up to {current}',
                             {'schema_version': version, 'supported': current})
    while version < current:
        step = MIGRATIONS[kind].get(version)
        if step is None:
            reason = REFUSED.get((kind, version))
            raise VoxelMillError(code, f'{label} schema_version {version} is not supported; this '
                                       f'build reads and writes {current}'
                                       + (f'. {reason}' if reason else ''),
                                 {'schema_version': version, 'supported': current})
        data = step(data)
        version += 1
        data['schema_version'] = version
    return data
