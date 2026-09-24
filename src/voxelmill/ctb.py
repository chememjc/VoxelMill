"""Bounded reader and writer for classic, unencrypted CTB version 3.

The layout and RLE grammar follow UVtools' ``ChituboxFile`` implementation.
This module deliberately does not claim support for CTB v4/v5 or encrypted
layer payloads.  Those are distinct formats and are rejected at open time.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import struct
import tempfile
import time

import numpy as np

from .config import layer_exposure, resin_usage
from .contracts import CancellationToken, VoxelMillError, Layer, ResourceBudget, no_progress
from .raster import RasterGrid

MAGIC = 0x12FD0086
VERSION = 3
HEADER_BYTES = 112
PRINT_PARAMETERS_BYTES = 60
SLICER_INFO_BYTES = 76
LAYER_DEF_BYTES = 36
LAYER_DEF_EX_BYTES = 84
MAX_LAYERS = 1_000_000
MAX_LAYER_PIXELS = 100_000_000
MAX_LAYER_BLOB_BYTES = 512 * 1024 * 1024


def _u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def _f32(data, offset):
    return struct.unpack_from('<f', data, offset)[0]


def encode_rle(image):
    """Encode an 8-bit image using CTB's 7-bit grayscale run grammar."""
    image = np.asarray(image)
    if image.dtype != np.uint8 or image.ndim != 2 or not image.flags.c_contiguous:
        raise VoxelMillError('ctb_layer_shape', 'CTB layers must be contiguous two-dimensional uint8 images')
    from . import _native
    try:
        return _native.ctb_encode_layer(image)
    except ValueError as error:
        raise VoxelMillError('ctb_rle', str(error)) from error


def decode_rle(blob, width, height):
    """Decode one bounded CTB layer, requiring exactly the declared pixel count."""
    pixels = int(width) * int(height)
    if width < 1 or height < 1 or pixels > MAX_LAYER_PIXELS:
        raise VoxelMillError('ctb_dimensions', 'CTB resolution is invalid or exceeds the decoder limit')
    from . import _native
    try:
        return _native.ctb_decode_layer(np.frombuffer(blob, dtype=np.uint8), width, height)
    except ValueError as error:
        raise VoxelMillError('ctb_rle', str(error)) from error


@dataclass(frozen=True)
class CtbLayer:
    index: int
    z_mm: float
    exposure_s: float
    light_off_s: float
    data_offset: int
    data_size: int
    lift_height_mm: float
    lift_speed_mm_min: float
    retract_speed_mm_min: float
    light_pwm: float


class CtbReader:
    """Index a classic CTB v3 file without trusting offsets or allocations."""
    def __init__(self, path):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        if self.size < HEADER_BYTES:
            raise VoxelMillError('ctb_truncated', 'File is smaller than a CTB header')
        self.handle = open(self.path, 'rb')
        try:
            raw = self.handle.read(HEADER_BYTES)
            magic, version = _u32(raw, 0), _u32(raw, 4)
            if magic != MAGIC:
                raise VoxelMillError('ctb_magic', f'Unsupported CTB magic 0x{magic:08x}')
            if version != VERSION:
                raise VoxelMillError('ctb_version',
                                f'Unsupported CTB version {version}; only classic CTB v3 is supported')
            self.header = {
                'magic': magic, 'version': version,
                'display_width': _f32(raw, 8), 'display_height': _f32(raw, 12),
                'machine_z': _f32(raw, 16), 'total_height': _f32(raw, 28),
                'layer_height': _f32(raw, 32), 'exposure_time': _f32(raw, 36),
                'bottom_exposure_time': _f32(raw, 40), 'light_off_delay': _f32(raw, 44),
                'bottom_layer_count': _u32(raw, 48), 'resolution_x': _u32(raw, 52),
                'resolution_y': _u32(raw, 56), 'layers_address': _u32(raw, 64),
                'layer_count': _u32(raw, 68), 'print_time': _u32(raw, 76),
                'projector_type': _u32(raw, 80), 'print_parameters_address': _u32(raw, 84),
                'print_parameters_size': _u32(raw, 88), 'anti_alias_level': _u32(raw, 92),
                'light_pwm': struct.unpack_from('<H', raw, 96)[0],
                'bottom_light_pwm': struct.unpack_from('<H', raw, 98)[0],
                'encryption_key': _u32(raw, 100), 'slicer_address': _u32(raw, 104),
                'slicer_size': _u32(raw, 108),
            }
            self._validate_header()
            self.layers = self._index()
        except Exception:
            self.handle.close()
            raise

    @property
    def shape(self):
        return int(self.header['resolution_y']), int(self.header['resolution_x'])

    def _validate_header(self):
        h = self.header
        if h['encryption_key'] != 0:
            raise VoxelMillError('ctb_encryption',
                            'Encrypted CTB layer payloads are not supported; use an unencrypted v3 file')
        if h['anti_alias_level'] != 1:
            raise VoxelMillError('ctb_antialias', 'CTB reader currently supports one layer image per Z level')
        if not 1 <= h['layer_count'] <= MAX_LAYERS:
            raise VoxelMillError('ctb_layers', 'Implausible CTB layer count')
        pixels = h['resolution_x'] * h['resolution_y']
        if h['resolution_x'] < 1 or h['resolution_y'] < 1 or pixels > MAX_LAYER_PIXELS:
            raise VoxelMillError('ctb_dimensions', 'CTB resolution is invalid or exceeds the decoder limit')
        for name in ('display_width', 'display_height', 'machine_z', 'layer_height',
                     'exposure_time', 'bottom_exposure_time'):
            if not math.isfinite(h[name]) or h[name] <= 0:
                raise VoxelMillError('ctb_header', f'Header field {name} must be finite and positive')
        if h['projector_type'] not in (0, 1):
            raise VoxelMillError('ctb_header', 'Projector type must be zero or one')
        start, count = h['layers_address'], h['layer_count']
        if start < HEADER_BYTES or start + count * LAYER_DEF_BYTES > self.size:
            raise VoxelMillError('ctb_truncated', 'CTB layer table runs past the file')

    def _index(self):
        result = []
        previous_z = -math.inf
        for index in range(self.header['layer_count']):
            offset = self.header['layers_address'] + index * LAYER_DEF_BYTES
            self.handle.seek(offset); raw = self.handle.read(LAYER_DEF_BYTES)
            z, exposure, off = _f32(raw, 0), _f32(raw, 4), _f32(raw, 8)
            address = _u32(raw, 12) + _u32(raw, 20) * 4_294_967_296
            size, table_size = _u32(raw, 16), _u32(raw, 24)
            if table_size != LAYER_DEF_EX_BYTES:
                raise VoxelMillError('ctb_layer', f'Layer {index} has unsupported table size {table_size}')
            if (not all(math.isfinite(x) for x in (z, exposure, off)) or z <= previous_z
                    or exposure <= 0 or off < 0):
                raise VoxelMillError('ctb_layer', f'Layer {index} has invalid timing or Z values')
            if size < 1 or size > MAX_LAYER_BLOB_BYTES or address < LAYER_DEF_EX_BYTES or address + size > self.size:
                raise VoxelMillError('ctb_truncated', f'Layer {index} data runs past the file')
            ex_offset = address - LAYER_DEF_EX_BYTES
            self.handle.seek(ex_offset); ex = self.handle.read(LAYER_DEF_EX_BYTES)
            if len(ex) != LAYER_DEF_EX_BYTES or _u32(ex, 36) != LAYER_DEF_EX_BYTES + size:
                raise VoxelMillError('ctb_layer', f'Layer {index} extended definition is invalid')
            # Its embedded definition must identify the same payload.
            if _u32(ex, 12) != address % 4_294_967_296 or _u32(ex, 16) != size:
                raise VoxelMillError('ctb_layer', f'Layer {index} definitions disagree')
            values = [_f32(ex, n) for n in (40, 44, 56, 80)]
            if not all(math.isfinite(x) and x >= 0 for x in values):
                raise VoxelMillError('ctb_layer', f'Layer {index} extended values are invalid')
            result.append(CtbLayer(index, z, exposure, off, int(address), size,
                                   values[0], values[1], values[2], values[3]))
            previous_z = z
        return result

    def blob(self, index):
        layer = self.layers[index]
        self.handle.seek(layer.data_offset); data = self.handle.read(layer.data_size)
        if len(data) != layer.data_size:
            raise VoxelMillError('ctb_truncated', f'Layer {index} changed while reading')
        return data

    def decode(self, index):
        return decode_rle(self.blob(index), self.shape[1], self.shape[0])

    def close(self): self.handle.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class CtbWriter:
    """Collect v3 layer records, then publish one checked-size file atomically."""
    def __init__(self, path, settings, layer_count, *, volume_mm3=0.0, print_time_s=0,
                 cancel=None, progress=no_progress):
        self.path, self.settings = Path(path), settings
        self.cancel, self.progress = cancel or CancellationToken(), progress
        self.layer_count, self.volume_mm3 = int(layer_count), float(volume_mm3)
        self.print_time_s = int(print_time_s)
        if not 1 <= self.layer_count <= MAX_LAYERS: raise VoxelMillError('ctb_layers', 'Invalid CTB layer count')
        self.shape = (int(settings['printer']['pixels'][1]), int(settings['printer']['pixels'][0]))
        if self.shape[0] * self.shape[1] > MAX_LAYER_PIXELS: raise VoxelMillError('ctb_dimensions', 'Panel exceeds CTB decoder limit')
        self.layers = []
        self._temporary = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(prefix=f'.{self.path.name}.', dir=self.path.parent, delete=False)
        self._temporary = Path(handle.name); handle.close(); return self

    def add_layer(self, image, z_mm, *, exposure_s=None, light_off_s=None):
        self.cancel.check()
        image = np.asarray(image)
        if image.dtype != np.uint8 or image.shape != self.shape:
            raise VoxelMillError('ctb_layer_shape', 'Layer must be a uint8 full-LCD image',
                            {'expected_shape': list(self.shape), 'actual_shape': list(image.shape)})
        z = float(z_mm)
        if not math.isfinite(z) or z <= 0 or z > self.settings['printer']['build_mm'][2] or (self.layers and z <= self.layers[-1][0]):
            raise VoxelMillError('ctb_layer_z', 'Layer Z values must increase inside machine travel')
        index = len(self.layers)
        exposure = float(layer_exposure(self.settings, index) if exposure_s is None else exposure_s)
        off = float(0.0 if light_off_s is None else light_off_s)
        if not math.isfinite(exposure) or exposure <= 0 or not math.isfinite(off) or off < 0:
            raise VoxelMillError('ctb_layer', 'Exposure must be positive and light-off delay nonnegative')
        blob = encode_rle(np.ascontiguousarray(image))
        self.layers.append((z, exposure, off, blob)); self.progress('ctb', len(self.layers), self.layer_count)
        return len(blob)

    def _build(self):
        if len(self.layers) != self.layer_count:
            raise VoxelMillError('ctb_layers', 'Written layer count differs from the declaration')
        printer, process = self.settings['printer'], self.settings['process']
        machine = str(printer['name']).encode('ascii', 'strict')
        usage = resin_usage(self.settings, self.volume_mm3)
        print_addr = HEADER_BYTES; slicer_addr = print_addr + PRINT_PARAMETERS_BYTES
        layers_addr = slicer_addr + SLICER_INFO_BYTES + len(machine)
        data_addr = layers_addr + self.layer_count * LAYER_DEF_BYTES
        if data_addr > 0xffffffff: raise VoxelMillError('ctb_size', 'CTB metadata exceeds the 32-bit address space')
        header = bytearray(HEADER_BYTES)
        def pi(off, val): struct.pack_into('<I', header, off, int(val))
        def pf(off, val): struct.pack_into('<f', header, off, float(val))
        pi(0, MAGIC); pi(4, VERSION)
        for off, val in ((8, printer['build_mm'][0]), (12, printer['build_mm'][1]), (16, printer['build_mm'][2]),
                         (28, self.layers[-1][0]), (32, process['layer_height_mm']),
                         (36, process['normal_exposure_s']), (40, process['bottom_exposure_s']), (44, 0.0)): pf(off, val)
        for off, val in ((48, process['bottom_layers']), (52, printer['pixels'][0]), (56, printer['pixels'][1]),
                         (64, layers_addr), (68, self.layer_count), (76, self.print_time_s),
                         (80, int(bool(printer['image_mirror_x']))), (84, print_addr),
                         (88, PRINT_PARAMETERS_BYTES), (92, 1), (100, 0),
                         (104, slicer_addr), (108, SLICER_INFO_BYTES)): pi(off, val)
        motion = printer.get('motion') or {}
        struct.pack_into('<HH', header, 96, int(motion.get('light_pwm', 255)), int(motion.get('bottom_light_pwm', 255)))
        params = bytearray(PRINT_PARAMETERS_BYTES)
        floats = (motion.get('bottom_lift_height', 0), motion.get('bottom_lift_speed', 0),
                  motion.get('lift_height', 0), motion.get('lift_speed', 0), motion.get('retract_speed', 0),
                  self.volume_mm3 / 1000.0, usage['mass_g'] or 0, usage['cost'] or 0, 0, 0)
        struct.pack_into('<10fI4I', params, 0, *map(float, floats), process['bottom_layers'], 0, 0, 0, 0)
        slicer = bytearray(SLICER_INFO_BYTES)
        struct.pack_into('<6fII', slicer, 0, 0, 0, 0, 0, 0, 0, 0, len(machine))
        struct.pack_into('<BHB', slicer, 32, 7, 0, 0x30)
        struct.pack_into('<IIIfffIIII', slicer, 36, int(time.time() // 60), 1, 0x01060300,
                         0, 0, 0, process['transition_layers'], 0, 0, 0)
        tables = bytearray(self.layer_count * LAYER_DEF_BYTES); payload = bytearray()
        for index, (z, exposure, off, blob) in enumerate(self.layers):
            blob_address = data_addr + len(payload) + LAYER_DEF_EX_BYTES
            if blob_address + len(blob) > 0xffffffff: raise VoxelMillError('ctb_size', 'CTB v3 output exceeds 4 GiB')
            lift = motion.get('bottom_lift_height' if index < process['bottom_layers'] else 'lift_height', 0)
            speed = motion.get('bottom_lift_speed' if index < process['bottom_layers'] else 'lift_speed', 0)
            retract = motion.get('retract_speed', 0); pwm = motion.get('bottom_light_pwm' if index < process['bottom_layers'] else 'light_pwm', 255)
            definition = struct.pack('<fffIIIIII', z, exposure, off, blob_address, len(blob), 0,
                                     LAYER_DEF_EX_BYTES, 0, 0)
            tables[index * LAYER_DEF_BYTES:(index + 1) * LAYER_DEF_BYTES] = definition
            ex = definition + struct.pack('<I11f', LAYER_DEF_EX_BYTES + len(blob), float(lift), float(speed),
                                          0, 0, float(retract), 0, 0, 0, 0, 0, float(pwm))
            payload.extend(ex); payload.extend(blob)
        return bytes(header + params + slicer + machine + tables + payload)

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                data = self._build()
                with open(self._temporary, 'wb') as handle:
                    handle.write(data); handle.flush(); os.fsync(handle.fileno())
                os.replace(self._temporary, self.path); self._temporary = None
        finally:
            if self._temporary is not None: self._temporary.unlink(missing_ok=True)
        return False


def _file_settings(settings, header):
    import copy
    derived = copy.deepcopy(settings); p, process = derived['printer'], derived['process']
    width, height = int(header['resolution_x']), int(header['resolution_y'])
    p['pixels'] = [width, height]
    p['build_mm'] = [float(header['display_width']), float(header['display_height']), float(header['machine_z'])]
    p['pixel_pitch_mm'] = [p['build_mm'][0] / width, p['build_mm'][1] / height]
    p['image_mirror_x'] = bool(header['projector_type']); p['image_mirror_y'] = False
    process['layer_height_mm'] = float(header['layer_height'])
    process['bottom_layers'] = int(header['bottom_layer_count'])
    process['normal_exposure_s'] = float(header['exposure_time'])
    process['bottom_exposure_s'] = float(header['bottom_exposure_time'])
    record = {'printer.pixels': p['pixels'], 'printer.build_mm': p['build_mm'],
              'printer.pixel_pitch_mm': p['pixel_pitch_mm'], 'process.layer_height_mm': process['layer_height_mm'],
              'process.bottom_layers': process['bottom_layers'], 'process.normal_exposure_s': process['normal_exposure_s'],
              'process.bottom_exposure_s': process['bottom_exposure_s']}
    return derived, record


def verify_ctb(path, settings, *, cancel=None, budget=None, progress=no_progress, track_voids=True):
    """Deep-check decoded v3 pixels using the same topology analysis as STL and GOO."""
    from .validation import analyze_layers, soften_unattributed_voids
    cancel = cancel or CancellationToken(); budget = budget or ResourceBudget(**settings['resources'])
    with CtbReader(path) as reader:
        effective, record = _file_settings(settings, reader.header)
        grid = RasterGrid.for_bounds(np.array([[-effective['printer']['build_mm'][0] / 2, -effective['printer']['build_mm'][1] / 2, 0],
                                                [effective['printer']['build_mm'][0] / 2, effective['printer']['build_mm'][1] / 2, reader.header['total_height']]]), effective, crop=False)
        layers = (Layer(item.index, item.z_mm, reader.decode(item.index)) for item in reader.layers)
        report = analyze_layers(layers, grid, effective, cancel=cancel, budget=budget,
                                progress=progress, track_voids=track_voids)
        soften_unattributed_voids(report, effective)
        report.metrics['ctb'] = {'version': VERSION, 'encrypted': False, 'shape_px': list(reader.shape)}
        return {'schema_version': 1, 'format': 'ctb', 'version': VERSION, 'layers': len(reader.layers),
                'settings_from_file': record, 'report': report.to_dict(),
                'does_not_establish': ['that the CTB matches any particular source mesh',
                                       'compatibility with encrypted CTB or CTB v4/v5 firmware']}


def convert_slices(source, output, settings, *, cancel=None, progress=no_progress, print_time_s=0):
    """Convert GOO v3 to unencrypted CTB v3 or CTB v3 to GOO v3.

    Pixel dimensions and physical panel dimensions must match the target profile;
    this function never resamples exposure images.
    """
    from .formats import FORMATS
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve(): raise VoxelMillError('source_overwrite', 'Input and output must be different files')
    src, dst = source.suffix.lower(), output.suffix.lower()
    if (src, dst) not in (('.goo', '.ctb'), ('.ctb', '.goo')):
        raise VoxelMillError('convert_format', 'Conversion supports .goo to .ctb and .ctb to .goo')
    cancel = cancel or CancellationToken()
    Writer = FORMATS[dst].writer_class()
    with FORMATS[src].reader(source) as reader:
        expected = tuple(reversed(settings['printer']['pixels']))
        if reader.shape != expected:
            raise VoxelMillError('convert_dimensions', 'Source pixels differ from the target printer; resampling is not implemented',
                            {'source_shape': list(reader.shape), 'target_shape': list(expected)})
        h = reader.header
        display = (float(h['display_width']), float(h['display_height']))
        if not np.allclose(display, settings['printer']['build_mm'][:2], rtol=0, atol=2e-5):
            raise VoxelMillError('convert_dimensions', 'Source display size differs from the target printer')
        kwargs = {'print_time_s': int(h.get('print_time', print_time_s)), 'cancel': cancel, 'progress': progress}
        with Writer(output, settings, len(reader.layers), **kwargs) as writer:
            for item in reader.layers:
                cancel.check(); image = reader.decode(item.index)
                if dst == '.ctb':
                    writer.add_layer(image, item.values['position_z'], exposure_s=item.values['exposure_time'], light_off_s=item.values['light_off_delay'])
                else:
                    # GooWriter derives timing from the target profile; require equality.
                    wanted = layer_exposure(settings, item.index)
                    if not math.isclose(item.exposure_s, wanted, rel_tol=0, abs_tol=2e-5):
                        raise VoxelMillError('convert_timing', 'CTB per-layer exposure differs from the target profile')
                    writer.add_layer(image, item.z_mm)
    verifier = verify_ctb(output, settings, cancel=cancel) if dst == '.ctb' else None
    return {'schema_version': 1, 'command': 'convert', 'input': str(source), 'output': str(output),
            'source_format': src[1:], 'format': dst[1:], 'layers': len(reader.layers),
            'verification': verifier, 'limitations': ['no image resampling', 'CTB output is unencrypted v3 only']}
