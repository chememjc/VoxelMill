"""Printer and resin profile discovery, provenance and comparison.

A profile reference on the command line or in the editor is either an explicit
filesystem path or a bare identifier looked up in a layered library:

    VOXELMILL_PROFILE_PATH  colon-separated directories, highest priority
    user                   $XDG_CONFIG_HOME/voxelmill/profiles, else
                           ~/.config/voxelmill/profiles
    system                 /etc/voxelmill/profiles
    builtin                the profiles packaged with voxelmill

Higher layers shadow lower ones by identifier, and a listing says which file
won and which files it shadowed, because a silently preferred profile is the
same class of hazard as a silently preferred setting.

Discovery only ever *locates* a file.  ``config.resolve_settings`` remains the
single validator and the single resolution order; nothing here relaxes it.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import NoReturn

from .config import DEFAULTS, _read, resolve_settings
from .contracts import VoxelMillError
from .versioning import CURRENT_VERSIONS

PRINTER_SUFFIX = '.ptr'
RESIN_SUFFIX = '.res'
SUFFIXES = {'printer': PRINTER_SUFFIX, 'resin': RESIN_SUFFIX}
KINDS = tuple(SUFFIXES)
PATH_VARIABLE = 'VOXELMILL_PROFILE_PATH'

# The layer a resolved value last changed in.  Ordered weakest to strongest so
# a later layer overwriting an earlier one is simply a later assignment.
LAYERS = ('default', 'printer', 'resin', 'override')


def _fail(message) -> NoReturn:
    raise VoxelMillError('invalid_profile', message)


def builtin_directory() -> Path:
    """The profiles packaged with the installed voxelmill."""
    return Path(__file__).resolve().parent / 'data'


def _user_directory() -> Path:
    base = os.environ.get('XDG_CONFIG_HOME') or ''
    root = Path(base) if base.strip() else Path.home() / '.config'
    return root / 'voxelmill' / 'profiles'


def search_directories() -> tuple[tuple[str, Path], ...]:
    """Layer name and directory, highest priority first.

    Directories are returned whether or not they exist, so a listing can say
    where a profile *would* be found.
    """
    entries = []
    raw = os.environ.get(PATH_VARIABLE) or ''
    for index, part in enumerate(raw.split(os.pathsep)):
        if part.strip():
            entries.append((f'{PATH_VARIABLE}[{index}]', Path(part).expanduser()))
    entries.append(('user', _user_directory()))
    entries.append(('system', Path('/etc/voxelmill/profiles')))
    entries.append(('builtin', builtin_directory()))
    return tuple(entries)


def _identity(path: Path, kind: str):
    """Read a profile's declared id and name without resolving it.

    Returns ``(id, name, error)``.  A malformed file keeps its filename stem as
    an identifier so a listing can report it rather than omitting it silently.
    """
    stem = path.stem
    try:
        data = _read(path)
    except VoxelMillError as exc:
        return stem, None, str(exc)
    table = data.get(kind)
    if not isinstance(table, dict):
        return stem, None, f'{kind} profile requires a {kind} table'
    identifier = table.get('id')
    name = table.get('name')
    if not isinstance(identifier, str) or not identifier.strip():
        identifier = stem
    return identifier.strip(), name if isinstance(name, str) else None, None


def discover(kind: str | None = None) -> list[dict]:
    """List every discoverable profile, winners first within each identifier.

    Each entry carries its identifier, display name, kind, path, layer, any
    parse error, and the lower-priority files it shadows.
    """
    if kind is not None and kind not in SUFFIXES:
        _fail(f'Profile kind must be one of {KINDS}')
    kinds = (kind,) if kind else KINDS
    found: dict[tuple[str, str], list[dict]] = {}
    for layer, directory in search_directories():
        try:
            names = sorted(p for p in directory.iterdir() if p.is_file())
        except OSError:
            continue
        for path in names:
            for one in kinds:
                if path.suffix.lower() != SUFFIXES[one]:
                    continue
                identifier, name, error = _identity(path, one)
                entry = {'id': identifier, 'name': name, 'kind': one,
                         'path': str(path), 'layer': layer, 'error': error}
                found.setdefault((one, identifier), []).append(entry)
    listing = []
    for _key, entries in sorted(found.items()):
        winner = dict(entries[0])
        winner['shadows'] = [e['path'] for e in entries[1:]]
        listing.append(winner)
    return listing


def _looks_like_path(reference: str, kind: str) -> bool:
    # os.altsep is None on POSIX, and '' is a substring of every string, so the
    # separator test has to skip it rather than default it to the empty string.
    separators = [os.sep] + ([os.altsep] if os.altsep else [])
    return (any(sep in reference for sep in separators)
            or reference.startswith('.')
            or Path(reference).suffix.lower() == SUFFIXES[kind])


def resolve_profile_path(reference, kind: str) -> Path | None:
    """Turn a path or a library identifier into an existing file path.

    ``None`` passes through so callers can keep using optional profiles.  An
    explicit path is never searched for in the library, and a library
    identifier is never guessed at as a path: the two are distinguished by
    shape, and each reports its own failure.
    """
    if reference is None:
        return None
    if kind not in SUFFIXES:
        _fail(f'Profile kind must be one of {KINDS}')
    if isinstance(reference, Path):
        reference = str(reference)
    if not isinstance(reference, str) or not reference.strip():
        _fail(f'{kind} profile reference must be a path or a library identifier')
    reference = reference.strip()
    if _looks_like_path(reference, kind):
        path = Path(reference).expanduser()
        if not path.is_file():
            _fail(f'No {kind} profile at {path}')
        return path
    for entry in discover(kind):
        if entry['id'] == reference:
            if entry['error']:
                _fail(f'Library {kind} profile {reference!r} at {entry["path"]} is invalid: {entry["error"]}')
            return Path(entry['path'])
    known = sorted(e['id'] for e in discover(kind))
    _fail(f'No {kind} profile named {reference!r}; known identifiers: {known}')


def _leaves(settings, prefix=()):
    for key, value in settings.items():
        path = prefix + (key,)
        if isinstance(value, dict):
            yield from _leaves(value, path)
        else:
            yield path, value


def _assign(target, path, value):
    node = target
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def provenance(printer_path=None, resin_path=None, overrides=None) -> dict:
    """Which resolution layer last set each resolved value.

    Computed by resolving the same stack repeatedly with one more layer each
    time and recording where a leaf last changed.  That reuses
    ``resolve_settings`` verbatim rather than reimplementing its order, so the
    answer cannot drift away from the settings it describes.
    """
    stack = [deepcopy(DEFAULTS)]
    stack.append(resolve_settings(printer_path, None, None))
    stack.append(resolve_settings(printer_path, resin_path, None))
    stack.append(resolve_settings(printer_path, resin_path, overrides))
    result: dict = {}
    for path, _ in _leaves(stack[-1]):
        source = LAYERS[0]
        for index in range(1, len(stack)):
            if _lookup(stack[index], path) != _lookup(stack[index - 1], path):
                source = LAYERS[index]
        _assign(result, path, source)
    return result


def _lookup(settings, path):
    node = settings
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return KeyError
        node = node[key]
    return node


def diff_settings(left, right) -> dict:
    """Leaf-by-leaf differences between two resolved settings dictionaries.

    Keys present on one side only are reported with the sentinel string
    ``'<absent>'`` rather than omitted, because a missing key and an equal key
    are very different answers.
    """
    absent = '<absent>'
    paths = {path for path, _ in _leaves(left)} | {path for path, _ in _leaves(right)}
    changes: dict = {}
    for path in sorted(paths):
        a = _lookup(left, path)
        b = _lookup(right, path)
        if a is KeyError:
            a = absent
        if b is KeyError:
            b = absent
        if a != b:
            _assign(changes, path, {'left': a, 'right': b})
    return changes


def profile_settings(reference, kind: str) -> dict:
    """Resolve one profile alone against the defaults, for comparison."""
    path = resolve_profile_path(reference, kind)
    if kind == 'printer':
        return resolve_settings(path, None, None)
    # A resin profile carries a process block per printer id, so it can only be
    # resolved against the printer it is bound to; use the default printer.
    return resolve_settings(None, path, None)


def library_report(kind: str | None = None) -> dict:
    """The payload ``voxelmill profile list`` prints and the editor displays."""
    return {
        'schema_version': 1,
        'search_path': [{'layer': layer, 'directory': str(directory),
                         'exists': directory.is_dir()}
                        for layer, directory in search_directories()],
        'profiles': discover(kind),
    }


# --- writing profiles -------------------------------------------------------
#
# tomllib reads but never writes, and this project targets 3.10, so the writer
# is here.  It deliberately handles only the value shapes a validated settings
# dictionary can contain and refuses anything else rather than emitting TOML
# that would not read back.

def _toml_scalar(value):
    import json as _json
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(float(value))
        return text if ('.' in text or 'e' in text or 'E' in text) else text + '.0'
    if isinstance(value, str):
        return _json.dumps(value, ensure_ascii=False)
    _fail(f'Cannot serialize {type(value).__name__} into a profile')


def _toml_value(value):
    if isinstance(value, list):
        return '[' + ', '.join(_toml_scalar(item) for item in value) + ']'
    return _toml_scalar(value)


def _toml_table(name, table, lines):
    scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
    lines.append(f'[{name}]')
    for key, value in scalars.items():
        if value is None:
            # TOML has no null; an unset optional is written by omission and the
            # loader falls back to the default, which is what null meant.
            lines.append(f'# {key} is unset')
            continue
        lines.append(f'{_toml_key(key)} = {_toml_value(value)}')
    lines.append('')
    for key, value in table.items():
        if isinstance(value, dict):
            _toml_table(f'{name}.{_toml_key(key)}', value, lines)


def _toml_key(value):
    """Render one TOML dotted-key component, quoting unusual identifiers."""
    import re
    import json as _json
    text = str(value)
    if re.fullmatch(r'[A-Za-z0-9_-]+', text):
        return text
    return _json.dumps(text, ensure_ascii=False)


def dumps_printer_profile(settings, name=None, *, hardware_only=False) -> str:
    """Serialize resolved settings as a printer ``.ptr`` document."""
    from .config import validate_settings
    settings = validate_settings(deepcopy(dict(settings)))
    printer = deepcopy(settings['printer'])
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            _fail('Printer profile name must be a nonempty string')
        printer['name'] = name.strip()
    lines = [f"schema_version = {CURRENT_VERSIONS['profile']}", '',
             '# Written by voxelmill profile save from a fully resolved settings stack.']
    if hardware_only:
        lines.append('# This hardware-only profile supplies printer settings; other sections inherit.')
    else:
        lines.append('# Every value is explicit except resin metadata, which is omitted.')
    lines.append('')
    _toml_table('printer', printer, lines)
    if not hardware_only:
        for section in ('process', 'support', 'peel', 'repair', 'assembly', 'resources'):
            _toml_table(section, settings[section], lines)
    return '\n'.join(lines).rstrip('\n') + '\n'


def _atomic_write(path: Path, text: str):
    import tempfile
    destination = Path(path)
    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix=f'.{destination.name}.',
                                         suffix='.tmp', dir=destination.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    except OSError as exc:
        _fail(f'Cannot write profile {destination}: {exc}')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def save_printer_profile(path, settings, *, name=None, hardware_only=False) -> dict:
    """Write a ``.ptr``, then read it back and require it to resolve identically.

    A profile that does not reload to the settings it was written from is a
    silent configuration change, so the round trip is part of saving rather
    than a test that could be skipped.
    """
    from .config import validate_settings
    expected = validate_settings(deepcopy(dict(settings)))
    text = dumps_printer_profile(settings, name=name, hardware_only=hardware_only)
    if name is not None:
        expected['printer'] = dict(expected['printer'])
        expected['printer']['name'] = name.strip()
    destination = Path(path)

    # Validate a staged file before replacing an existing destination.  A
    # hardware-only profile needs the caller's other settings as context: a
    # custom printer layer-height range can make the built-in default process
    # invalid even though the current resolved settings are valid.
    import tempfile
    temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8',
                                         prefix=f'.{destination.name}.', suffix='.tmp',
                                         dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if hardware_only:
            overrides = {key: deepcopy(expected[key]) for key in
                         ('resin', 'process', 'support', 'repair', 'assembly', 'resources')}
            reloaded = resolve_settings(temporary, None, overrides)
        else:
            reloaded = resolve_settings(temporary, None, None)
        if hardware_only:
            expected = {'schema_version': 1, 'printer': expected['printer']}
            reloaded = {'schema_version': 1, 'printer': reloaded['printer']}
        else:
            # ``resin`` is metadata only and is intentionally not serialized in a
            # printer profile.  Compare exactly the sections the writer emits.
            expected = {key: expected[key] for key in
                        ('schema_version', 'printer', 'process', 'support',
                         'peel', 'repair', 'assembly', 'resources')}
            reloaded = {key: reloaded[key] for key in expected}
        differences = diff_settings(expected, reloaded)
        if differences:
            _fail(f'Saved profile {destination} does not reload identically: {differences}')
        os.replace(temporary, destination)
        temporary = None
    except OSError as exc:
        _fail(f'Cannot write profile {destination}: {exc}')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    omitted = (['resin', 'process', 'support', 'peel', 'repair', 'assembly', 'resources']
               if hardware_only else ['resin'])
    return {'schema_version': 1, 'command': 'profile save', 'path': str(destination),
            'printer_id': reloaded['printer']['id'], 'printer_name': reloaded['printer']['name'],
            'round_trip': 'identical', 'hardware_only': bool(hardware_only),
            'omitted_sections': omitted}


# --- resin profiles ---------------------------------------------------------

def resin_document(reference) -> dict:
    """Read a resin profile and report the printers its processes are bound to."""
    path = resolve_profile_path(reference, 'resin')
    data = _read(path)
    if 'resin' not in data or 'processes' not in data:
        _fail('Resin profile requires resin and processes tables')
    processes = data['processes']
    if not isinstance(processes, dict):
        _fail('Resin processes must be a table')
    return {'schema_version': 1, 'path': str(path), 'resin': deepcopy(data['resin']),
            'bound_printers': sorted(processes), 'processes': deepcopy(processes)}


def dumps_resin_profile(document) -> str:
    lines = [f"schema_version = {CURRENT_VERSIONS['profile']}", '',
             '# Written by voxelmill resin bind.  Each processes.<printer-id> block',
             '# applies only to that printer id.', '']
    _toml_table('resin', document['resin'], lines)
    for printer_id, entry in document['processes'].items():
        for section, values in entry.items():
            _toml_table(f'processes.{_toml_key(printer_id)}.{_toml_key(section)}', values, lines)
    return '\n'.join(lines).rstrip('\n') + '\n'


def bind_resin_process(reference, output, *, source_printer=None, target_printer=None) -> dict:
    """Copy one printer's process block in a resin profile onto another printer id.

    The copy is exact: exposures and support values that were calibrated for
    one machine are reused verbatim, which is only ever a *starting point*.
    The result says so, because an exposure carried across machines is not a
    calibrated exposure.
    """
    document = resin_document(reference)
    processes = document['processes']
    if not processes:
        _fail('Resin profile has no process blocks to bind')
    if source_printer is None:
        if len(processes) != 1:
            _fail(f'--from is required; this resin binds {sorted(processes)}')
        source_printer = next(iter(processes))
    if source_printer not in processes:
        _fail(f'Resin has no process for printer {source_printer!r}; it binds {sorted(processes)}')
    if not target_printer or not str(target_printer).strip():
        _fail('--to must name the printer id to bind the copied process to')
    target_printer = str(target_printer).strip()
    if target_printer == source_printer:
        _fail('--to must differ from --from; binding a printer to itself changes nothing')
    processes[target_printer] = deepcopy(processes[source_printer])
    destination = Path(output)
    if destination.resolve() == Path(document['path']).resolve():
        _fail('Refusing to overwrite the source resin profile; choose another --output')
    _atomic_write(destination, dumps_resin_profile(document))
    # Reload through the real validator so an unbindable copy fails here rather
    # than at the next slice.
    reloaded = resin_document(destination)
    return {'schema_version': 1, 'command': 'resin bind', 'path': str(destination),
            'source_printer': source_printer, 'target_printer': target_printer,
            'bound_printers': reloaded['bound_printers'],
            'calibration': 'copied verbatim from '
                           f'{source_printer}; exposures are a starting point, not a '
                           'calibration for the target machine'}


def save_resin_profile(path, settings, *, name=None) -> dict:
    """Write the resolved resin and its current printer's process block.

    A resin profile deliberately contains no printer geometry or repair
    settings.  Its process and support values are copied from the fully
    resolved settings, then the staged file is resolved with the current
    printer settings before it is published.  This proves the profile is
    usable for this printer without presenting the copied values as a
    hardware calibration.
    """
    from .config import validate_settings

    expected = validate_settings(deepcopy(dict(settings)))
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            _fail('Resin profile name must be a nonempty string')
        expected['resin'] = dict(expected['resin'])
        expected['resin']['name'] = name.strip()

    document = {'resin': deepcopy(expected['resin']),
                'processes': {
                    expected['printer']['id']: {
                        'process': deepcopy(expected['process']),
                        'support': deepcopy(expected['support']),
                    }}}
    destination = Path(path)
    text = dumps_resin_profile(document)

    # Validate before replacing the destination.  This preserves an existing
    # output if serialization or the round-trip check fails.
    import tempfile
    temporary = None
    printer_temporary = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8',
                                         prefix=f'.{destination.name}.',
                                         suffix='.tmp', dir=destination.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        # Resolve the staged resin against a staged full printer profile. An
        # override would run after resin matching and therefore cannot validate
        # a custom printer id or layer-height range correctly.
        with tempfile.NamedTemporaryFile('w', encoding='utf-8',
                                         prefix=f'.{destination.name}.printer.',
                                         suffix='.tmp', dir=destination.parent,
                                         delete=False) as printer_handle:
            printer_temporary = Path(printer_handle.name)
            printer_handle.write(dumps_printer_profile(expected))
            printer_handle.flush()
            os.fsync(printer_handle.fileno())
        reloaded = resolve_settings(printer_temporary, temporary, None)
        compared = {'schema_version': 1,
                    'printer': reloaded['printer'],
                    'resin': reloaded['resin'],
                    'process': reloaded['process'],
                    'support': reloaded['support']}
        wanted = {key: expected[key] for key in compared}
        differences = diff_settings(wanted, compared)
        if differences:
            _fail(f'Saved resin profile {destination} does not reload identically: {differences}')
        os.replace(temporary, destination)
        temporary = None
    except OSError as exc:
        _fail(f'Cannot write profile {destination}: {exc}')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if printer_temporary is not None:
            printer_temporary.unlink(missing_ok=True)

    return {'schema_version': 1, 'command': 'resin save', 'path': str(destination),
            'printer_id': expected['printer']['id'],
            'resin_id': expected['resin']['id'],
            'resin_name': expected['resin']['name'],
            'round_trip': 'identical',
            'calibration': 'process and support copied from resolved settings; '
                           'values are a starting point, not a hardware calibration'}
