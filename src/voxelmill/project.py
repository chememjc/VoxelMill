"""Versioned .voxmil ZIP archives with streamed, verified source preservation.

Recognized members are manifest.json, source/original.stl, and optional
source/models/N.stl extra-model meshes. Reading never follows external source
paths or extracts arbitrary archive paths.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tempfile
import zipfile

from .contracts import VoxelMillError

MANIFEST_NAME = 'manifest.json'
SOURCE_NAME = 'source/original.stl'
EXTRA_SOURCE_RE = re.compile(r'source/models/([0-9]+)\.stl')
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024**3
MAX_SOURCE_BYTES = 16 * 1024**3
CHUNK_BYTES = 1024 * 1024


def _fail(message):
    raise VoxelMillError('invalid_project', message)


def _json_default(value):
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f'Unsupported project value: {type(value).__name__}')


def _encode(manifest):
    try:
        data = json.dumps(manifest, allow_nan=False, default=_json_default, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    except (ValueError, TypeError, RecursionError) as exc:
        raise VoxelMillError('invalid_project', f'Project state must be finite JSON: {exc}') from exc
    if len(data) > MAX_MANIFEST_BYTES:
        _fail('Project manifest exceeds 16 MiB limit')
    return data


def _reject_constant(value):
    _fail(f'Nonfinite JSON value: {value}')


def _display_name(name, fallback_path):
    """Sanitize a stored display name; it is UI text, never a filesystem path.

    A hand-edited or crafted manifest could otherwise smuggle a separator or a
    ``..`` component into a field that is only ever rendered as a label, so
    anything not plainly a filename falls back to the basename of the file it
    names.
    """
    fallback = Path(fallback_path).name
    if (not isinstance(name, str) or not name or '\x00' in name
            or '/' in name or '\\' in name or name in ('.', '..')):
        return fallback
    return name


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


#: Bumped to 2 when paint became one local-frame record per plate object.
SCHEMA_VERSION = 2


def _decode(data):
    try:
        manifest = json.loads(data, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise VoxelMillError('invalid_project', f'Invalid JSON manifest: {exc}') from exc
    if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int:
        _fail(f'Project requires schema_version integer {SCHEMA_VERSION}')
    if manifest['schema_version'] != SCHEMA_VERSION:
        # Schema 2 moved paint from plate-coordinate centroids to one
        # local-frame record per object. A schema-1 mark cannot be attributed
        # to a part after the fact, so it is refused rather than silently
        # reinterpreted as belonging to the primary.
        _fail(f'Project schema_version {manifest["schema_version"]} is not supported; '
              f'this build reads and writes {SCHEMA_VERSION}')
    # Also rejects exponent overflows (e.g. 1e999), which parse_constant misses.
    _encode(manifest)
    return manifest


def save_project(path, manifest: dict, source_path=None):
    """Atomically save finite state and optionally embed an original STL.

    Caller state may include settings, placement, edits, support_graph, validation,
    and input hashes. Supplied source.sha256 is checked against streamed bytes.
    Returns the saved manifest, with schema and source metadata filled in.
    """
    path = Path(path)
    if not isinstance(manifest, dict):
        _fail('Project manifest must be a dictionary')
    state = deepcopy(manifest)
    state.setdefault('schema_version', SCHEMA_VERSION)
    _decode(_encode(state))
    source_path = Path(source_path) if source_path is not None else None
    if source_path is not None and source_path.resolve() == path.resolve():
        _fail('Project output cannot replace its source')
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            if source_path is not None:
                source_state = state.get('source', {})
                if not isinstance(source_state, dict):
                    _fail('source must be a JSON object')
                digest = hashlib.sha256()
                total = 0
                with source_path.open('rb') as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SOURCE_BYTES:
                        _fail('Source must be a regular file of at most 16 GiB')
                    with archive.open(SOURCE_NAME, 'w', force_zip64=True) as destination:
                        while chunk := source.read(CHUNK_BYTES):
                            total += len(chunk)
                            if total > MAX_SOURCE_BYTES:
                                _fail('Source exceeds 16 GiB limit')
                            digest.update(chunk)
                            destination.write(chunk)
                    after = os.fstat(source.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        _fail('Source changed while saving project')
                actual_hash = digest.hexdigest()
                expected_hash = source_state.get('sha256')
                if expected_hash is not None and expected_hash != actual_hash:
                    _fail('Source SHA-256 does not match supplied input hash')
                state['source'] = {
                    **source_state, 'name': _display_name(source_state.get('name'), source_path),
                    'original_path': str(source_path.resolve()),
                    'sha256': actual_hash, 'size_bytes': total, 'archive_member': SOURCE_NAME,
                }
            elif isinstance(state.get('source'), dict) and state['source'].get('archive_member'):
                _fail('Embedding metadata requires source_path')
            edits = state.get('edits') or {}
            extras = (edits.get('extra_models') or []) if isinstance(edits, dict) else []
            for index, row in enumerate(extras):
                if not isinstance(row, dict) or not row.get('path'):
                    _fail('Each added model needs a path')
                extra_path = Path(row['path'])
                member = f'source/models/{index}.stl'
                digest = hashlib.sha256()
                total = 0
                with extra_path.open('rb') as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SOURCE_BYTES:
                        _fail('Added model must be a regular file of at most 16 GiB')
                    with archive.open(member, 'w', force_zip64=True) as destination:
                        while chunk := source.read(CHUNK_BYTES):
                            total += len(chunk)
                            if total > MAX_SOURCE_BYTES:
                                _fail('Added model exceeds 16 GiB limit')
                            digest.update(chunk)
                            destination.write(chunk)
                    after = os.fstat(source.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        _fail('Added model changed while saving project')
                row['original_path'] = str(extra_path.resolve())
                row['archive_member'] = member
                row['sha256'] = digest.hexdigest()
                row['size_bytes'] = total
                row['name'] = _display_name(row.get('name'), extra_path)
            state = _decode(_encode(state))
            archive.writestr(MANIFEST_NAME, _encode(state))
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        return state
    except VoxelMillError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise VoxelMillError('project_write', f'Cannot save project: {exc}') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _preflight(path):
    """Bound central-directory allocation before constructing ZipFile."""
    with open(path, 'rb') as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size > MAX_ARCHIVE_BYTES:
            _fail('Archive exceeds 32 GiB limit')
        handle.seek(max(0, size - 65557))
        tail = handle.read(65557)
        offset = tail.rfind(b'PK\x05\x06')
        if offset < 0 or len(tail) - offset < 22:
            _fail('Missing or truncated ZIP end record')
        _, disk, cd_disk, disk_entries, entries, cd_size, cd_offset, comment_size = struct.unpack_from('<4s4H2LH', tail, offset)
        if len(tail) - offset != 22 + comment_size or disk or cd_disk:
            _fail('Invalid ZIP end record or unsupported multidisk archive')
        if entries == 65535 or cd_size == 0xffffffff or cd_offset == 0xffffffff:
            end_position = size - len(tail) + offset
            if end_position < 20:
                _fail('Missing ZIP64 locator')
            handle.seek(end_position - 20)
            signature, zip_disk, zip_offset, disks = struct.unpack('<4sLQL', handle.read(20))
            if signature != b'PK\x06\x07' or zip_disk or disks != 1 or zip_offset + 56 > end_position - 20:
                _fail('Invalid ZIP64 locator')
            handle.seek(zip_offset)
            fields = struct.unpack('<4sQ2H2L4Q', handle.read(56))
            signature, record_size, _, _, disk, cd_disk, disk_entries, entries, cd_size, cd_offset = fields
            if signature != b'PK\x06\x06' or record_size != 44 or disk or cd_disk:
                _fail('Invalid ZIP64 end record')
        if disk_entries != entries or not 1 <= entries <= 1026 or cd_size > 1024 * 1024 or cd_offset + cd_size > size:
            _fail('Project ZIP directory exceeds entry or size limits')


def _check_members(archive):
    infos = archive.infolist()
    if not 1 <= len(infos) <= 1026:
        _fail('Project contains too many archive members')
    seen = set()
    total = 0
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        recognized = name in (MANIFEST_NAME, SOURCE_NAME) or EXTRA_SOURCE_RE.fullmatch(name)
        if name in seen or not recognized or '\\' in name or '..' in parts or name.startswith('/'):
            _fail(f'Unexpected or unsafe archive member: {name!r}')
        seen.add(name)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
            _fail('Archive member must be a regular file')
        if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            _fail('Encrypted or unsupported archive encoding')
        limit = MAX_MANIFEST_BYTES if name == MANIFEST_NAME else MAX_SOURCE_BYTES
        if info.file_size > limit or info.file_size > max(info.compress_size, 1) * 256:
            _fail('Archive member exceeds size or compression-ratio limit')
        total += info.file_size
    if MANIFEST_NAME not in seen or total > MAX_ARCHIVE_BYTES:
        _fail('Missing manifest or archive exceeds size limit')
    return seen


def _safe_extract_dir(path):
    path = Path(os.path.abspath(path))
    # Do not follow symlinks while writing embedded source material.
    for parent in (path, *path.parents):
        if parent.is_symlink():
            _fail('Extraction directory cannot contain symlinks')
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_project(path, extract_dir=None):
    """Read state and verify embedded source SHA-256 without loading it into RAM.

    With extract_dir, atomically materialize a hash-named STL in that directory and
    add source.extracted_path to the returned state. No source paths are followed.
    """
    temporary = None
    destination = None
    try:
        _preflight(path)
        with zipfile.ZipFile(path, 'r') as archive:
            members = _check_members(archive)
            with archive.open(MANIFEST_NAME) as stream:
                data = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(data) > MAX_MANIFEST_BYTES:
                _fail('Manifest exceeds size limit')
            state = _decode(data)
            source = state.get('source')
            if isinstance(source, dict):
                # Sanitize on read too: the manifest came out of an archive,
                # which may have been hand-edited after it was written.
                source['name'] = _display_name(source.get('name'), source.get('original_path') or SOURCE_NAME)
            if SOURCE_NAME not in members:
                if isinstance(source, dict) and source.get('archive_member'):
                    _fail('Manifest references a missing embedded source')
                if any(EXTRA_SOURCE_RE.fullmatch(name) for name in members):
                    _fail('Added-model members require an embedded primary source')
                return state
            if not isinstance(source, dict) or source.get('archive_member') != SOURCE_NAME:
                _fail('Embedded source requires matching source metadata')
            expected_hash, expected_size = source.get('sha256'), source.get('size_bytes')
            if not isinstance(expected_hash, str) or not re.fullmatch('[0-9a-f]{64}', expected_hash):
                _fail('Invalid source SHA-256')
            if type(expected_size) is not int or expected_size != archive.getinfo(SOURCE_NAME).file_size:
                _fail('Invalid source byte count')
            if extract_dir is not None:
                directory = _safe_extract_dir(extract_dir)
                destination = directory / f'{expected_hash}.stl'
                handle = tempfile.NamedTemporaryFile(prefix='.voxmil-source-', dir=directory, delete=False)
                temporary = Path(handle.name)
            else:
                handle = None
            digest = hashlib.sha256()
            total = 0
            try:
                with archive.open(SOURCE_NAME) as stream:
                    while chunk := stream.read(CHUNK_BYTES):
                        total += len(chunk)
                        if total > expected_size:
                            _fail('Source expands beyond advertised size')
                        digest.update(chunk)
                        if handle is not None:
                            handle.write(chunk)
                if total != expected_size or digest.hexdigest() != expected_hash:
                    _fail('Embedded source hash or byte count mismatch')
                if handle is not None:
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if handle is not None:
                    handle.close()
            if temporary is not None:
                if destination.is_symlink():
                    _fail('Extracted destination cannot be a symlink')
                os.replace(temporary, destination)
                temporary = None
                state['source']['extracted_path'] = str(destination)
            edits = state.get('edits') or {}
            extras = (edits.get('extra_models') or []) if isinstance(edits, dict) else []
            expected_members = set()
            for index, row in enumerate(extras):
                if not isinstance(row, dict):
                    _fail('Added-model state must be an object')
                row['name'] = _display_name(row.get('name'),
                                            row.get('original_path') or row.get('path')
                                            or f'source/models/{index}.stl')
                member = row.get('archive_member')
                if member is None:
                    # Schema-1 projects written before portable added meshes
                    # deliberately remain usable through their external path.
                    continue
                expected = f'source/models/{index}.stl'
                if member != expected or member not in members:
                    _fail('Added model requires its matching embedded source')
                expected_members.add(member)
                expected_hash, expected_size = row.get('sha256'), row.get('size_bytes')
                if not isinstance(expected_hash, str) or not re.fullmatch('[0-9a-f]{64}', expected_hash):
                    _fail('Invalid added-model SHA-256')
                info = archive.getinfo(member)
                if type(expected_size) is not int or expected_size != info.file_size:
                    _fail('Invalid added-model byte count')
                digest = hashlib.sha256()
                total = 0
                payload = None
                if extract_dir is not None:
                    directory = _safe_extract_dir(extract_dir)
                    payload = tempfile.NamedTemporaryFile(prefix='.voxmil-model-', dir=directory,
                                                           delete=False)
                try:
                    with archive.open(member) as stream:
                        while chunk := stream.read(CHUNK_BYTES):
                            total += len(chunk)
                            if total > expected_size:
                                _fail('Added model expands beyond advertised size')
                            digest.update(chunk)
                            if payload is not None:
                                payload.write(chunk)
                    if total != expected_size or digest.hexdigest() != expected_hash:
                        _fail('Embedded added-model hash or byte count mismatch')
                    if payload is not None:
                        payload.flush(); os.fsync(payload.fileno()); payload.close()
                        target = directory / f'{expected_hash}.stl'
                        if target.is_symlink():
                            _fail('Extracted added-model destination cannot be a symlink')
                        os.replace(payload.name, target)
                        row['path'] = str(target)
                        payload = None
                finally:
                    if payload is not None:
                        name = payload.name
                        payload.close()
                        Path(name).unlink(missing_ok=True)
            actual_members = {name for name in members if EXTRA_SOURCE_RE.fullmatch(name)}
            if actual_members != expected_members:
                _fail('Archive contains an unreferenced added-model source')
            return state
    except VoxelMillError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, EOFError, struct.error) as exc:
        raise VoxelMillError('invalid_project', f'Cannot read project: {exc}') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
