"""Editor state, undo/redo and project round trip.

The document holds only decisions: the source, the resolved settings, the
placement, and the manual support edits. Everything expensive (the repaired
solid, the column field, the routed geometry, the layer raster) is derived and
lives in :class:`DerivedState`, which is rebuilt by background jobs and is never
saved. That split is what makes undo cheap and a reopened project honest: a
saved project records what the user decided plus the validation that was true
when it was saved, never a claim that the geometry still passes.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..config import resolve_settings, validate_settings
from ..project import SCHEMA_VERSION as PROJECT_SCHEMA_VERSION
from ..contracts import VoxelMillError, Placement


@dataclass
class Command:
    """One reversible edit. ``apply``/``revert`` take the document."""
    label: str
    apply: object
    revert: object


class LayerCache:
    """Byte-bounded LRU of decoded layer payloads.

    Scrubbing is only usable if a layer already looked at comes back
    instantly, and re-slicing the union or re-decoding a GOO frame costs
    seconds each.  The bound is in bytes rather than entries because one full
    Mars 5 Ultra frame is 36.8 MB and a small preview is a few kilobytes; an
    entry count would either starve one case or blow up the other.
    """

    def __init__(self, max_bytes=256 * 1024 ** 2):
        self.max_bytes = int(max_bytes)
        self.bytes = 0
        self._entries: dict = {}

    @staticmethod
    def _size(payload):
        mask = payload.get('mask') if isinstance(payload, dict) else None
        return int(getattr(mask, 'nbytes', 0)) + 4096

    def get(self, key):
        if key not in self._entries:
            return None
        payload = self._entries.pop(key)
        self._entries[key] = payload          # move to the most-recent end
        return payload

    def put(self, key, payload):
        self.discard(key)
        size = self._size(payload)
        if size > self.max_bytes:
            return payload                     # too large to keep; still usable
        self._entries[key] = payload
        self.bytes += size
        while self.bytes > self.max_bytes and self._entries:
            self.discard(next(iter(self._entries)))
        return payload

    def discard(self, key):
        payload = self._entries.pop(key, None)
        if payload is not None:
            self.bytes -= self._size(payload)

    def drop_source(self, source):
        for key in [k for k in self._entries if k[0] == source]:
            self.discard(key)

    def clear(self):
        self._entries.clear()
        self.bytes = 0

    def __len__(self):
        return len(self._entries)


@dataclass
class DerivedState:
    solid: object = None
    repair: dict = field(default_factory=dict)
    model_triangles: object = None
    model_bounds: object = None
    part_meshes: object = None
    #: One 4x4 placement matrix per plate object, primary first. Paint is
    #: stored per object in its own frame, so this is what converts a pick into
    #: that frame and the stored marks back out to the plate.
    part_matrices: object = None
    column_field: object = None
    plan: object = None
    raft: object = None
    union: object = None
    validation: object = None
    layer_cache: LayerCache = field(default_factory=LayerCache)
    layer_slicer: object = None

    def invalidate_from(self, stage):
        order = ['solid', 'model', 'field', 'supports', 'union', 'validation']
        index = order.index(stage)
        if index <= 0:
            self.solid, self.repair = None, {}
        if index <= 1:
            self.model_triangles = self.model_bounds = self.part_meshes = None
            self.part_matrices = None
        if index <= 2:
            self.column_field = None
        if index <= 3:
            self.plan = self.raft = None
        if index <= 4:
            self.union = None
            self.layer_slicer = None
            # Only the union's own slices go stale; an opened GOO file is not
            # derived from this document and its decoded layers stay valid.
            self.layer_cache.drop_source('union')
        self.validation = None


class Document:
    def __init__(self, settings=None, source=None):
        self.source = Path(source) if source else None
        # The human name to show, independent of self.source: a reopened
        # project's source is extracted to a hash-named temp file, and the
        # object list must not show that hash in its place.
        self.source_name = self.source.name if self.source else None
        self.source_sha256 = None
        self.settings = validate_settings(deepcopy(settings)) if settings else resolve_settings()
        self.placement: Placement | None = None
        self.rotation_deg = [0.0, 0.0, 0.0]
        self.center_offset_mm = [0.0, 0.0]
        self.model_lift_mm = 5.0
        # Per-axis scale and mirrored axes. Both are model decisions, not
        # profile settings: they belong to this part, not to the machine.
        self.scale_factors = [1.0, 1.0, 1.0]
        self.mirror_axes = [False, False, False]
        self.manual_contacts: list[list[float]] = []
        self.removed_contacts: list[list[float]] = []
        self.contact_parameters: list[dict] = []
        # One record per plate object, in that object's own mesh frame, so
        # paint travels with the part instead of staying where the part was.
        self.paint: list[dict] = [{'blocked': [], 'enforced': []}]
        self.extra_models: list[dict] = []
        self.derived = DerivedState()
        # What the profile stack resolved to before any editor edit. Every
        # "modified" marker and every revert is measured against this, so the
        # editor can say which values it changed rather than only what they are.
        self.baseline_settings = deepcopy(self.settings)
        self.saved_validation: dict | None = None
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self.dirty = False

    # ---- undo/redo -----------------------------------------------------
    def run(self, command: Command):
        command.apply(self)
        self._undo.append(command)
        self._redo.clear()
        self.dirty = True
        return command

    def undo(self):
        if not self._undo:
            return None
        command = self._undo.pop()
        command.revert(self)
        self._redo.append(command)
        self.dirty = True
        return command

    def redo(self):
        if not self._redo:
            return None
        command = self._redo.pop()
        command.apply(self)
        self._undo.append(command)
        self.dirty = True
        return command

    @property
    def undo_label(self):
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self):
        return self._redo[-1].label if self._redo else None

    # ---- edits ---------------------------------------------------------
    def set_orientation(self, rotation_deg, center_offset_mm=None, lift_mm=None):
        """Set a manual rotation or the explicit ``"auto"`` placement mode.

        ``"auto"`` is a decision, rather than a list-like sequence.  Keeping
        it intact matters both to the placement service and to project files:
        converting it with ``list()`` quietly turned it into four letters and
        made an automatic placement fail on its next rebuild.
        """
        if rotation_deg == 'auto':
            rotation = 'auto'
        else:
            rotation = [float(value) for value in rotation_deg]
            if len(rotation) != 3 or not all(np.isfinite(rotation)):
                raise VoxelMillError('invalid_orientation',
                                'Rotation needs three finite degrees or "auto"')
            from ..geometry import wrap_rotation_deg
            # Rotation is periodic: wrap into (-180, 180] rather than clamp, so
            # a gizmo commit or a nudge that carries an angle past 180 is
            # stored as the same orientation instead of silently truncated.
            rotation = wrap_rotation_deg(rotation)
        offset = [float(value) for value in
                  (center_offset_mm if center_offset_mm is not None else self.center_offset_mm)]
        if len(offset) != 2 or not all(np.isfinite(offset)):
            raise VoxelMillError('invalid_orientation', 'Center offset needs two finite millimeter values')
        lift = float(lift_mm if lift_mm is not None else self.model_lift_mm)
        if not np.isfinite(lift) or lift < 0:
            raise VoxelMillError('invalid_orientation', 'Model lift must be a finite non-negative millimeter value')
        before_rotation = self.rotation_deg if self.rotation_deg == 'auto' else list(self.rotation_deg)
        before = (before_rotation, list(self.center_offset_mm), self.model_lift_mm)
        after = (rotation,
                 offset, lift)

        def assign(state):
            def apply(document):
                document.rotation_deg = state[0] if state[0] == 'auto' else list(state[0])
                document.center_offset_mm, document.model_lift_mm = list(state[1]), float(state[2])
                document.placement = None
                document.derived.invalidate_from('solid')
            return apply
        return self.run(Command('orientation', assign(after), assign(before)))

    def set_transform(self, scale=None, mirror=None):
        """Undoable per-axis scale and mirror. Validated by the same code the CLI uses."""
        from ..geometry import scale_matrix
        factors = [float(v) for v in (self.scale_factors if scale is None else scale)]
        flips = [bool(v) for v in (self.mirror_axes if mirror is None else mirror)]
        if len(factors) != 3 or len(flips) != 3:
            raise VoxelMillError('invalid_scale', 'Scale and mirror each need one value per axis')
        # scale_matrix owns the bounds and the sign rule; raising here rather
        # than at prepare time is the whole point of validating an edit.
        scale_matrix(factors, flips)
        before = (list(self.scale_factors), list(self.mirror_axes))
        after = (factors, flips)

        def assign(state):
            def apply(document):
                document.scale_factors, document.mirror_axes = list(state[0]), list(state[1])
                document.placement = None
                document.derived.invalidate_from('solid')
            return apply
        return self.run(Command('transform', assign(after), assign(before)))

    def adopt_baseline(self, settings=None):
        """Treat these settings as the unmodified starting point.

        Called when a profile is applied: after that, "modified" means changed
        from the profile the user chose, not from whatever was loaded first.
        """
        self.baseline_settings = deepcopy(dict(settings if settings is not None else self.settings))
        return self.baseline_settings

    def modified_settings(self):
        """Every leaf that differs from the baseline, as ``{section: {key: ...}}``."""
        from ..profiles import diff_settings
        return diff_settings(self.baseline_settings, self.settings)

    def is_modified(self, section, key):
        return key in (self.modified_settings().get(section) or {})

    def revert_setting(self, section, key):
        """Undoably restore one setting to its baseline value.

        A key absent from the baseline has nothing to revert to, which is a
        different answer from "it already matches" and is reported as such.
        """
        baseline = (self.baseline_settings.get(section) or {})
        if key not in baseline:
            raise VoxelMillError('invalid_setting',
                            f'{section}.{key} is not in the baseline profile, so it has no '
                            'value to revert to')
        settings = deepcopy(self.settings)
        settings[section][key] = deepcopy(baseline[key])
        return self.set_settings(settings)

    def set_settings(self, settings, stage='solid'):
        before, after = deepcopy(self.settings), validate_settings(deepcopy(settings))

        def assign(state):
            def apply(document):
                document.settings = deepcopy(state)
                document.derived.invalidate_from(stage)
            return apply
        return self.run(Command('settings', assign(after), assign(before)))

    def add_contact(self, point):
        point = [float(v) for v in point]
        if len(point) != 3 or not all(np.isfinite(point)):
            raise VoxelMillError('invalid_contact', 'A support contact needs three finite coordinates')

        def apply(document):
            document.manual_contacts.append(list(point))
            document.derived.invalidate_from('supports')

        def revert(document):
            document.manual_contacts.remove(list(point))
            document.derived.invalidate_from('supports')
        return self.run(Command('add support', apply, revert))

    def remove_contact(self, point):
        point = [float(v) for v in point]
        manual = list(point) in self.manual_contacts
        before_parameters = deepcopy(self.contact_parameters)

        def apply(document):
            if manual:
                document.manual_contacts.remove(list(point))
            else:
                document.removed_contacts.append(list(point))
            from ..contact_parameters import contact_key
            key = contact_key(point)
            document.contact_parameters = [row for row in document.contact_parameters
                                          if contact_key(row['position_mm']) != key]
            document.derived.invalidate_from('supports')

        def revert(document):
            if manual:
                document.manual_contacts.append(list(point))
            else:
                document.removed_contacts.remove(list(point))
            document.contact_parameters = deepcopy(before_parameters)
            document.derived.invalidate_from('supports')
        return self.run(Command('delete support', apply, revert))

    def move_contact(self, source, target):
        source, target = [float(v) for v in source], [float(v) for v in target]
        if len(source) != 3 or len(target) != 3 or not all(np.isfinite(source + target)):
            raise VoxelMillError('invalid_contact', 'Support contact coordinates must be finite')
        from ..contact_parameters import contact_key
        if contact_key(source) != contact_key(target) and any(
                contact_key(row['position_mm']) == contact_key(target) for row in self.contact_parameters):
            raise VoxelMillError('invalid_contact_parameters', 'Moving would duplicate contact parameters')
        manual = list(source) in self.manual_contacts
        before_parameters = deepcopy(self.contact_parameters)

        def apply(document):
            if manual:
                document.manual_contacts.remove(list(source))
            else:
                document.removed_contacts.append(list(source))
            document.manual_contacts.append(list(target))
            from ..contact_parameters import contact_key
            source_key = contact_key(source)
            for row in document.contact_parameters:
                if contact_key(row['position_mm']) == source_key:
                    row['position_mm'] = list(target)
            document.derived.invalidate_from('supports')

        def revert(document):
            document.manual_contacts.remove(list(target))
            if manual:
                document.manual_contacts.append(list(source))
            else:
                document.removed_contacts.remove(list(source))
            document.contact_parameters = deepcopy(before_parameters)
            document.derived.invalidate_from('supports')
        return self.run(Command('move support', apply, revert))

    def set_contact_parameters(self, points, parameters):
        points = [[float(v) for v in point] for point in points]
        if any(len(point) != 3 or not all(np.isfinite(point)) for point in points):
            raise VoxelMillError('invalid_contact', 'Contact parameters need finite 3D coordinates')
        if not isinstance(parameters, dict):
            raise VoxelMillError('invalid_contact_parameters', 'Contact parameters must be an object')
        from ..contact_parameters import normalize_contact_parameters
        before = deepcopy(self.contact_parameters)
        after = deepcopy(before)
        for point in points:
            row = next((item for item in after if item.get('position_mm') == point), None)
            if row is None:
                row = {'position_mm': list(point), 'parameters': {}}
                after.append(row)
            row.setdefault('parameters', {}).update(deepcopy(parameters))
        after = list(normalize_contact_parameters(after, self.settings))
        def assign(state):
            def apply(document):
                document.contact_parameters = deepcopy(state)
                document.derived.invalidate_from('supports')
            return apply
        return self.run(Command('contact parameters', assign(after), assign(before)))

    def clear_contact_parameters(self, points):
        points = [list(map(float, point)) for point in points]
        before = deepcopy(self.contact_parameters)
        from ..contact_parameters import contact_key
        keys = {contact_key(point) for point in points}
        after = [row for row in before if contact_key(row['position_mm']) not in keys]
        def assign(state):
            def apply(document):
                document.contact_parameters = deepcopy(state)
                document.derived.invalidate_from('supports')
            return apply
        return self.run(Command('reset contact parameters', assign(after), assign(before)))

    def object_paint(self, index=0):
        """One object's local-frame paint record, defaulting to empty."""
        from ..paint import empty_paint
        records = self.normalized_paint()
        return records[int(index)] if 0 <= int(index) < len(records) else empty_paint()

    def normalized_paint(self):
        """Paint padded to the current object count, so an added part has one."""
        from ..paint import normalize_object_paint
        return normalize_object_paint(self.paint, len(self.extra_models) + 1)

    def paint_marks(self, kind, points, object_index=0):
        """Add marks to one object, in that object's own frame."""
        if kind not in ('blocked', 'enforced'):
            raise VoxelMillError('invalid_paint', 'Paint kind must be blocked or enforced')
        from ..paint import normalize_object_paint, paint_key
        index = int(object_index)
        if not 0 <= index <= len(self.extra_models):
            raise VoxelMillError('invalid_paint', 'Paint object index is out of range',
                                 {'index': index})
        before = deepcopy(self.normalized_paint())
        after = deepcopy(before)
        seen = {paint_key(point) for point in after[index][kind]}
        for point in points:
            key = paint_key(point)
            if key in seen:
                continue
            seen.add(key)
            after[index][kind].append([float(v) for v in key])
        after = normalize_object_paint(after, len(self.extra_models) + 1)

        def assign(state):
            def apply(document):
                document.paint = deepcopy(state)
                document.derived.invalidate_from('supports')
            return apply
        return self.run(Command(f'paint {kind}', assign(after), assign(before)))

    def clear_paint(self, kind=None, object_index=None):
        """Clear one kind, one object, or everything painted on the plate."""
        if kind not in (None, 'blocked', 'enforced'):
            raise VoxelMillError('invalid_paint', 'Paint kind must be blocked, enforced, or omitted')
        from ..paint import empty_paint, normalize_object_paint
        before = deepcopy(self.normalized_paint())
        after = deepcopy(before)
        targets = (range(len(after)) if object_index is None
                   else [int(object_index)])
        for index in targets:
            if not 0 <= index < len(after):
                continue
            if kind is None:
                after[index] = empty_paint()
            else:
                after[index][kind] = []
        after = normalize_object_paint(after, len(self.extra_models) + 1)

        def assign(state):
            def apply(document):
                document.paint = deepcopy(state)
                document.derived.invalidate_from('supports')
            return apply
        return self.run(Command('clear paint' if kind is None else f'clear {kind} paint',
                                assign(after), assign(before)))

    def add_extra_model(self, spec):
        spec = self._normalize_extra_model(spec)
        if not spec.get('path'):
            raise VoxelMillError('invalid_model', 'Added model needs a path')
        before = deepcopy(self.extra_models)
        after = deepcopy(before) + [spec]

        def assign(state):
            def apply(document):
                document.extra_models = deepcopy(state)
                document.derived.invalidate_from('solid')
            return apply
        return self.run(Command('add model', assign(after), assign(before)))

    @staticmethod
    def _normalize_extra_model(spec):
        from ..pipeline import normalize_extra_model
        from ..geometry import wrap_rotation_deg
        result = normalize_extra_model(spec)
        # normalize_extra_model only validates that rotate is three finite
        # degrees; it does not know about the periodic-wrap convention, so
        # this is the point every added-model pose passes through -- an
        # edit, an add, and a reopened project all land here -- and it is
        # what keeps an extra model's stored rotation in (-180, 180] the same
        # way the primary model's is in Document.set_orientation.
        result['rotate'] = wrap_rotation_deg(result['rotate'])
        return result

    def set_extra_model_pose(self, index, *, rotate=None, center_offset=None, lift_mm=None,
                             scale=None, mirror=None):
        """Undoably move, rotate, scale or mirror one added model, the others untouched."""
        if not 0 <= int(index) < len(self.extra_models):
            raise VoxelMillError('invalid_model', 'Added model index is out of range')
        before = deepcopy(self.extra_models)
        changed = dict(before[int(index)])
        if rotate is not None:
            changed['rotate'] = rotate
        if center_offset is not None:
            changed['center_offset'] = center_offset
        if lift_mm is not None:
            changed['lift_mm'] = lift_mm
        if scale is not None:
            changed['scale'] = scale
        if mirror is not None:
            changed['mirror'] = mirror
        after = deepcopy(before)
        after[int(index)] = self._normalize_extra_model(changed)

        def assign(state):
            def apply(document):
                document.extra_models = deepcopy(state)
                document.derived.invalidate_from('solid')
            return apply
        return self.run(Command('move model', assign(after), assign(before)))

    def set_extra_model_overrides(self, index, overrides):
        """Undoably attach a support overlay to one added model."""
        if not 0 <= int(index) < len(self.extra_models):
            raise VoxelMillError('invalid_model', 'Added model index is out of range')
        before = deepcopy(self.extra_models)
        changed = dict(before[int(index)])
        changed['overrides'] = overrides or {}
        after = deepcopy(before)
        after[int(index)] = self._normalize_extra_model(changed)

        def assign(state):
            def apply(document):
                document.extra_models = deepcopy(state)
                document.derived.invalidate_from('supports')
            return apply
        return self.run(Command('object support overrides', assign(after), assign(before)))

    def remove_extra_model(self, index):
        if not 0 <= int(index) < len(self.extra_models):
            raise VoxelMillError('invalid_model', 'Added model index is out of range')
        before = deepcopy(self.extra_models)
        after = before[:int(index)] + before[int(index) + 1:]
        def assign(state):
            def apply(document):
                document.extra_models = deepcopy(state)
                document.derived.invalidate_from('solid')
            return apply
        return self.run(Command('remove model', assign(after), assign(before)))

    def clear_extra_models(self):
        before = deepcopy(self.extra_models)
        def assign(state):
            def apply(document):
                document.extra_models = deepcopy(state)
                document.derived.invalidate_from('solid')
            return apply
        return self.run(Command('clear extra models', assign([]), assign(before)))

    # ---- persistence ---------------------------------------------------
    def manifest(self):
        return {
            'schema_version': PROJECT_SCHEMA_VERSION,
            'settings': self.settings,
            'placement': asdict(self.placement) if self.placement else None,
            'rotation_deg': self.rotation_deg if self.rotation_deg == 'auto' else list(self.rotation_deg),
            'center_offset_mm': list(self.center_offset_mm),
            'model_lift_mm': float(self.model_lift_mm),
            'scale_factors': list(self.scale_factors),
            'mirror_axes': [bool(v) for v in self.mirror_axes],
            'edits': {'manual_contacts': self.manual_contacts,
                      'removed_contacts': self.removed_contacts,
                      'contact_parameters': deepcopy(self.contact_parameters),
                      'paint': deepcopy(self.normalized_paint()),
                      'extra_models': deepcopy(self.extra_models)},
            'support_graph': asdict(self.derived.plan.graph) if self.derived.plan else None,
            'validation': self.saved_validation,
            # The name travels here rather than being re-derived from
            # self.source, which after a project reload is a hash-named
            # extracted temp file, not the file the user opened.
            'source': ({'sha256': self.source_sha256} if self.source_sha256 else {}) | (
                {'name': self.source_name} if self.source_name else {}),
        }

    def save(self, path):
        from ..project import save_project
        state = save_project(path, self.manifest(), self.source)
        self.source_sha256 = state.get('source', {}).get('sha256', self.source_sha256)
        self.dirty = False
        return state

    @classmethod
    def load(cls, path, extract_dir=None):
        from ..project import load_project
        import tempfile
        owned_extract = None
        if extract_dir is None:
            owned_extract = tempfile.TemporaryDirectory(prefix='voxelmill-project-')
            extract_dir = owned_extract.name
        state = load_project(path, extract_dir)
        settings = deepcopy(state.get('settings'))
        if settings is not None:
            # Older schema-1 archives omit later sections and support keys.
            # Present but incomplete peel/assembly tables remain errors.
            from ..config import fill_legacy_settings
            fill_legacy_settings(settings)
        document = cls(settings)
        document.project_extract_dir = owned_extract
        source = state.get('source', {})
        document.source_sha256 = source.get('sha256')
        if source.get('extracted_path'):
            document.source = Path(source['extracted_path'])
        # project.load_project already sanitized this against a tampered
        # archive; fall back to the (possibly hash-named) extracted file only
        # if the manifest carried nothing usable.
        document.source_name = source.get('name') or (
            document.source.name if document.source else None)
        rotation = state.get('rotation_deg') or [0.0, 0.0, 0.0]
        document.rotation_deg = 'auto' if rotation == 'auto' else [float(value) for value in rotation]
        document.center_offset_mm = list(state.get('center_offset_mm') or [0.0, 0.0])
        document.model_lift_mm = float(state.get('model_lift_mm', 5.0))
        # Archives written before scale and mirror existed have neither key and
        # load as the identity, which is what they meant.
        document.scale_factors = [float(v) for v in (state.get('scale_factors') or [1.0, 1.0, 1.0])]
        document.mirror_axes = [bool(v) for v in (state.get('mirror_axes') or [False, False, False])]
        edits = state.get('edits') or {}
        document.manual_contacts = [list(map(float, p)) for p in edits.get('manual_contacts', [])]
        document.removed_contacts = [list(map(float, p)) for p in edits.get('removed_contacts', [])]
        from ..contact_parameters import normalize_contact_parameters
        document.contact_parameters = list(normalize_contact_parameters(
            edits.get('contact_parameters') or [], document.settings))
        document.extra_models = [document._normalize_extra_model(row)
                                 for row in (edits.get('extra_models') or [])]
        from ..paint import normalize_object_paint
        document.paint = normalize_object_paint(edits.get('paint'),
                                                len(document.extra_models) + 1)
        # Retained verbatim as history. It describes the geometry as it was when
        # the project was saved and is not a claim about the current build.
        document.saved_validation = state.get('validation')
        document.dirty = False
        return document
