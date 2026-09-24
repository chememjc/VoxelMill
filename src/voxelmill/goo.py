"""ELEGOO GOO v3.0 reading and writing.

Field offsets, types, endianness, the RLE chunk grammar and the checksum are
documented and verified in ``docs/goo-format.md``: every header field was read
back from the supplied reference file, and the encoder reproduces that file's
layer blobs byte for byte. This module owns only framing and settings mapping;
the RLE itself is the native codec.

Machine motion values are copied from the reference file through the printer
profile's ``motion`` table. They are compatibility evidence for this printer,
not firmware-verified values, and nothing here invents one.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import struct
import tempfile
import time

import numpy as np

from .config import layer_exposure, resin_usage
from .contracts import CancellationToken, VoxelMillError, Diagnostic, no_progress
from .timing import estimate_print_time_s

MAGIC = b'\x07\x00\x00\x00DLP\x00'
VERSION = b'V3.0'
DELIMITER = b'\x0d\x0a'
HEADER_BYTES = 195477
LAYER_DEF_BYTES = 70
FOOTER = b'\x00\x00\x00' + MAGIC
SMALL_PREVIEW = 116
BIG_PREVIEW = 290
MAX_LAYERS = 1_000_000
# Mars 5 Ultra layers are 36,806,400 pixels.  This deliberately leaves room
# for larger currently-known panels without allowing a malformed header to ask
# NumPy to allocate arbitrary multi-gigabyte images.
MAX_LAYER_PIXELS = 100_000_000
MAX_LAYER_BLOB_BYTES = 512 * 1024 * 1024

#: ``(name, struct format, offset)``. Strings are fixed-length NUL padded.
HEADER_FIELDS = [
    ('software_name', '32s', 12), ('software_version', '24s', 44),
    ('file_create_time', '24s', 68), ('machine_name', '32s', 92),
    ('machine_type', '32s', 124), ('profile_name', '32s', 156),
    ('anti_aliasing_level', 'H', 188), ('gray_level', 'H', 190), ('blur_level', 'H', 192),
    ('layer_count', 'I', 195310), ('resolution_x', 'H', 195314), ('resolution_y', 'H', 195316),
    ('mirror_x', 'B', 195318), ('mirror_y', 'B', 195319),
    ('display_width', 'f', 195320), ('display_height', 'f', 195324), ('machine_z', 'f', 195328),
    ('layer_height', 'f', 195332), ('exposure_time', 'f', 195336),
    ('delay_mode', 'B', 195340), ('light_off_delay', 'f', 195341),
    ('bottom_wait_after_cure', 'f', 195345), ('bottom_wait_after_lift', 'f', 195349),
    ('bottom_wait_before_cure', 'f', 195353), ('wait_after_cure', 'f', 195357),
    ('wait_after_lift', 'f', 195361), ('wait_before_cure', 'f', 195365),
    ('bottom_exposure_time', 'f', 195369), ('bottom_layer_count', 'I', 195373),
    ('bottom_lift_height', 'f', 195377), ('bottom_lift_speed', 'f', 195381),
    ('lift_height', 'f', 195385), ('lift_speed', 'f', 195389),
    ('bottom_retract_height', 'f', 195393), ('bottom_retract_speed', 'f', 195397),
    ('retract_height', 'f', 195401), ('retract_speed', 'f', 195405),
    ('bottom_lift_height2', 'f', 195409), ('bottom_lift_speed2', 'f', 195413),
    ('lift_height2', 'f', 195417), ('lift_speed2', 'f', 195421),
    ('bottom_retract_height2', 'f', 195425), ('bottom_retract_speed2', 'f', 195429),
    ('retract_height2', 'f', 195433), ('retract_speed2', 'f', 195437),
    ('bottom_light_pwm', 'H', 195441), ('light_pwm', 'H', 195443),
    ('per_layer_settings', 'B', 195445), ('print_time', 'I', 195446),
    ('volume_mm3', 'f', 195450), ('material_grams', 'f', 195454),
    ('material_cost', 'f', 195458), ('price_currency', '8s', 195462),
    ('layer_def_address', 'I', 195470), ('grayscale_level', 'B', 195474),
    ('transition_layer_count', 'H', 195475),
]

LAYER_FIELDS = [
    ('pause', 'H', 0), ('pause_position_z', 'f', 2), ('position_z', 'f', 6),
    ('exposure_time', 'f', 10), ('light_off_delay', 'f', 14),
    ('wait_after_cure', 'f', 18), ('wait_after_lift', 'f', 22), ('wait_before_cure', 'f', 26),
    ('lift_height', 'f', 30), ('lift_speed', 'f', 34),
    ('lift_height2', 'f', 38), ('lift_speed2', 'f', 42),
    ('retract_height', 'f', 46), ('retract_speed', 'f', 50),
    ('retract_height2', 'f', 54), ('retract_speed2', 'f', 58),
    ('light_pwm', 'H', 62),
]

#: Motion keys a printer profile may supply, with the GOO field they fill.
MOTION_KEYS = ('bottom_lift_height', 'bottom_lift_speed', 'lift_height', 'lift_speed',
               'bottom_retract_height', 'bottom_retract_speed', 'retract_height', 'retract_speed',
               'bottom_lift_height2', 'bottom_lift_speed2', 'lift_height2', 'lift_speed2',
               'bottom_retract_height2', 'bottom_retract_speed2', 'retract_height2', 'retract_speed2',
               'bottom_light_pwm', 'light_pwm')


def _pack(buffer, offset, code, value):
    try:
        if code.endswith('s'):
            size = int(code[:-1])
            raw = value.encode('ascii', 'strict') if isinstance(value, str) else bytes(value)
            if len(raw) > size:
                raise VoxelMillError('goo_field', f'String at offset {offset} exceeds {size} bytes')
            buffer[offset:offset + size] = raw.ljust(size, b'\0')
            return
        struct.pack_into('>' + code, buffer, offset, value)
    except (TypeError, ValueError, UnicodeError, struct.error, OverflowError) as error:
        raise VoxelMillError('goo_field', f'Cannot encode GOO field at offset {offset}: {error}') from error


def _unpack(data, offset, code):
    if code.endswith('s'):
        size = int(code[:-1])
        return data[offset:offset + size].split(b'\0', 1)[0].decode('ascii', 'replace')
    return struct.unpack_from('>' + code, data, offset)[0]


def rgb565(image):
    """Pack an (h,w,3) uint8 image into big-endian RGB565 bytes."""
    image = np.asarray(image, dtype=np.uint8)
    words = ((image[..., 0].astype(np.uint16) >> 3) << 11
             | (image[..., 1].astype(np.uint16) >> 2) << 5
             | (image[..., 2].astype(np.uint16) >> 3))
    return words.astype('>u2').tobytes()


def unpack_rgb565(data, width, height):
    words = np.frombuffer(data[:width * height * 2], dtype='>u2').reshape(height, width)
    out = np.empty((height, width, 3), dtype=np.uint8)
    out[..., 0] = ((words >> 11) & 0x1f) << 3
    out[..., 1] = ((words >> 5) & 0x3f) << 2
    out[..., 2] = (words & 0x1f) << 3
    return out


def preview_from_heightmap(heights, size):
    """Gray top-down thumbnail scaled into a square preview of ``size`` pixels."""
    heights = np.asarray(heights, dtype=np.float32)
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    if not heights.size or not heights.max():
        return canvas
    scale = max(heights.shape[0] / size, heights.shape[1] / size)
    rows = np.clip((np.arange(size) * scale).astype(int), 0, heights.shape[0] - 1)
    columns = np.clip((np.arange(size) * scale).astype(int), 0, heights.shape[1] - 1)
    sampled = heights[np.ix_(rows, columns)]
    shade = (40 + 200 * sampled / heights.max()).astype(np.uint8)
    canvas[..., 0] = canvas[..., 1] = canvas[..., 2] = np.where(sampled > 0, shade, 0)
    return canvas


def header_from_settings(settings, layer_count, *, volume_mm3=0.0, print_time_s=0,
                         software='voxelmill', software_version=None, created=None):
    """Map resolved settings onto GOO header values. Motion comes from the profile."""
    if software_version is None:
        from . import __version__ as software_version
    printer, process, resin = settings['printer'], settings['process'], settings['resin']
    if not 1 <= layer_count <= MAX_LAYERS:
        raise VoxelMillError('goo_layers', f'Layer count must be between 1 and {MAX_LAYERS}')
    if not isinstance(volume_mm3, (int, float)) or not math.isfinite(volume_mm3) or volume_mm3 < 0:
        raise VoxelMillError('goo_volume', 'volume_mm3 must be a finite nonnegative number')
    usage = resin_usage(settings, float(volume_mm3))
    values = {
        'software_name': software, 'software_version': software_version,
        'file_create_time': time.strftime('%Y-%m-%d %H:%M:%S', created or time.localtime()),
        'machine_name': printer['name'], 'machine_type': printer['name'],
        'profile_name': resin['name'],
        'anti_aliasing_level': 0, 'gray_level': 0, 'blur_level': 0,
        'layer_count': layer_count,
        'resolution_x': printer['pixels'][0], 'resolution_y': printer['pixels'][1],
        'mirror_x': int(bool(printer['image_mirror_x'])), 'mirror_y': int(bool(printer['image_mirror_y'])),
        'display_width': printer['build_mm'][0], 'display_height': printer['build_mm'][1],
        'machine_z': printer['build_mm'][2],
        'layer_height': process['layer_height_mm'],
        'exposure_time': process['normal_exposure_s'],
        'delay_mode': 1, 'light_off_delay': 0.0,
        'bottom_wait_after_cure': process['bottom_rest_after_exposure_s'],
        'bottom_wait_after_lift': process['bottom_wait_after_lift_s'],
        'bottom_wait_before_cure': process['bottom_settle_before_exposure_s'],
        'wait_after_cure': process['normal_rest_after_exposure_s'],
        'wait_after_lift': process['normal_wait_after_lift_s'],
        'wait_before_cure': process['normal_settle_before_exposure_s'],
        'bottom_exposure_time': process['bottom_exposure_s'],
        'bottom_layer_count': process['bottom_layers'],
        # The official format defines 1 as advanced/per-layer mode.  We always
        # emit layer records for transition exposure and therefore opt in even
        # though the supplied Satellite file happens to vary records with 0.
        'per_layer_settings': 1, 'print_time': int(print_time_s),
        # Weight and cost are derived from the resin profile.  Both stay 0
        # when it supplies no density or price, which is what the format's
        # unknown looks like and what every export wrote before those fields
        # existed.
        'volume_mm3': float(volume_mm3),
        'material_grams': float(usage['mass_g'] or 0.0),
        'material_cost': float(usage['cost'] or 0.0),
        'price_currency': resin['currency'],
        'layer_def_address': HEADER_BYTES, 'grayscale_level': 0,
        'transition_layer_count': process['transition_layers'],
    }
    if (not isinstance(print_time_s, (int, float)) or not math.isfinite(print_time_s)
            or print_time_s < 0 or print_time_s > 0xffffffff or int(print_time_s) != print_time_s):
        raise VoxelMillError('goo_timing', 'print_time_s must be a whole number from 0 to 4294967295')
    motion = printer.get('motion') or {}
    missing = [key for key in MOTION_KEYS if key not in motion]
    if missing:
        raise VoxelMillError('goo_motion',
                        'The printer profile must supply every machine motion value used by GOO; '
                        'nothing here is invented',
                        {'missing': missing, 'section': 'printer.motion'})
    for key in MOTION_KEYS:
        value = motion[key]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise VoxelMillError('goo_motion', f'printer.motion.{key} must be a finite nonnegative number')
        if key.endswith('pwm'):
            if int(value) != value or value > 0xffff:
                raise VoxelMillError('goo_motion', f'printer.motion.{key} must be an integer from 0 to 65535')
            values[key] = int(value)
        else:
            values[key] = float(value)
    return values


def _layer_values(header, settings, index, z_mm, *, exposure_s=None):
    exposure = float(layer_exposure(settings, index) if exposure_s is None else exposure_s)
    if not math.isfinite(exposure) or exposure <= 0:
        raise VoxelMillError('goo_layer', 'Layer exposure_s must be a positive finite number',
                        {'exposure_s': exposure})
    bottom = index < settings['process']['bottom_layers']
    prefix = 'bottom_' if bottom else ''
    return {
        'pause': 0, 'pause_position_z': header['machine_z'], 'position_z': z_mm,
        'exposure_time': exposure, 'light_off_delay': 0.0,
        'wait_after_cure': header[f'{prefix}wait_after_cure'],
        'wait_after_lift': header[f'{prefix}wait_after_lift'],
        'wait_before_cure': header[f'{prefix}wait_before_cure'],
        'lift_height': header[f'{prefix}lift_height'], 'lift_speed': header[f'{prefix}lift_speed'],
        'lift_height2': header[f'{prefix}lift_height2'], 'lift_speed2': header[f'{prefix}lift_speed2'],
        'retract_height': header[f'{prefix}retract_height'],
        'retract_speed': header[f'{prefix}retract_speed'],
        'retract_height2': header[f'{prefix}retract_height2'],
        'retract_speed2': header[f'{prefix}retract_speed2'],
        'light_pwm': header[f'{prefix}light_pwm'],
    }


def compensation_radius_px(settings, index):
    """Erosion radii in pixels for one layer, and why they came out that way.

    The compensation is a radius in millimeters that ramps linearly to zero:
    layer 0 gets all of it, the layer at ``elephant_foot_layers`` gets none.
    Pixel pitch can differ per axis, so the two radii are computed separately
    and rounded independently.  A request that rounds to zero pixels is a
    request that does nothing, and it is reported as such rather than silently
    ignored.
    """
    process = settings['process']
    millimeters = float(process['elephant_foot_compensation_mm'])
    layers = int(process['elephant_foot_layers']) or int(process['bottom_layers'])
    if millimeters <= 0.0 or layers <= 0 or index >= layers:
        return (0, 0), {'applied': False, 'reason': (
            'disabled' if millimeters <= 0.0 or layers <= 0 else 'above the compensated layers'),
            'mm': 0.0, 'layers': layers}
    scale = (layers - index) / layers
    wanted = millimeters * scale
    pitch = settings['printer']['pixel_pitch_mm']
    radii = tuple(int(round(wanted / float(pitch[axis]))) for axis in (0, 1))
    return radii, {
        'applied': bool(radii[0] or radii[1]), 'mm': wanted, 'layers': layers,
        'radius_px': list(radii),
        'reason': None if (radii[0] or radii[1]) else
                  'rounds to zero pixels at this pixel pitch, so nothing is removed',
    }


def _elliptical_footprint(rx, ry):
    y, x = np.ogrid[-ry:ry + 1, -rx:rx + 1]
    return ((x / rx) ** 2 if rx else (x * 0)) + ((y / ry) ** 2 if ry else (y * 0)) <= 1.0


def _mask_scale_center(mask, settings, grid):
    """Continuous (row, column) center for XY shrinkage.

    Prefer the plate center when the crop's grid origin is known; otherwise the
    cropped mask's own center.  Coordinates match ``affine_transform`` indices.
    """
    height, width = np.asarray(mask).shape
    if grid is None:
        return (height - 1) / 2.0, (width - 1) / 2.0
    pixels = settings['printer']['pixels']
    return ((pixels[1] - 1) / 2.0 - grid.row_offset,
            (pixels[0] - 1) / 2.0 - grid.column_offset)


def shrink_mask_xy(mask, percent, center):
    """Scale occupancy about ``center`` and crop/pad back to the input shape.

    ``percent`` is the resin shrinkage to compensate: positive enlarges the
    exposure so the cured part lands closer to the designed size.  ``0`` is a
    no-op and returns the input mask unchanged.
    """
    percent = float(percent)
    if percent == 0.0:
        return mask, {'applied': False, 'reason': 'disabled', 'percent': 0.0,
                      'scale': 1.0, 'uncalibrated': False}
    scale = 1.0 + percent / 100.0
    if scale <= 0.0:
        raise VoxelMillError('goo_compensation',
                        'process.shrink_percent_xy produces a non-positive scale',
                        {'percent': percent, 'scale': scale})
    from scipy import ndimage as ndi
    occupied = np.asarray(mask) != 0
    before = int(np.count_nonzero(occupied))
    cy, cx = center
    inv = 1.0 / scale
    matrix = np.array([[inv, 0.0], [0.0, inv]], dtype=np.float64)
    offset = np.array([cy * (1.0 - inv), cx * (1.0 - inv)], dtype=np.float64)
    scaled = ndi.affine_transform(occupied.astype(np.uint8), matrix, offset=offset,
                                  output_shape=occupied.shape, order=0,
                                  mode='constant', cval=0)
    after = int(np.count_nonzero(scaled))
    record = {
        'applied': after != before, 'percent': percent, 'scale': scale,
        'center_px': [cy, cx], 'pixels_before': before, 'pixels_after': after,
        'pixels_changed': abs(after - before),
        'erased_layer': bool(before and not after),
        'uncalibrated': True,
        'reason': None if after != before else
                 'scale left occupancy unchanged at this resolution',
    }
    return scaled.astype(np.uint8), record


def tolerance_radius_px(settings, index):
    """Morphological radius for tolerance compensation on one layer.

    Positive millimeters erode (compensate over-cure); negative dilate.
    Bottom layers use ``bottom_tolerance_offset_mm`` when it is nonzero,
    otherwise ``tolerance_offset_mm``.
    """
    process = settings['process']
    bottom_layers = int(process['bottom_layers'])
    bottom_mm = float(process['bottom_tolerance_offset_mm'])
    if index < bottom_layers and bottom_mm != 0.0:
        millimeters = bottom_mm
        source = 'bottom_tolerance_offset_mm'
    else:
        millimeters = float(process['tolerance_offset_mm'])
        source = 'tolerance_offset_mm'
    if millimeters == 0.0:
        return (0, 0), {'applied': False, 'reason': 'disabled', 'mm': 0.0,
                        'source': source, 'uncalibrated': False}
    pitch = settings['printer']['pixel_pitch_mm']
    radii = tuple(int(round(abs(millimeters) / float(pitch[axis]))) for axis in (0, 1))
    return radii, {
        'applied': bool(radii[0] or radii[1]), 'mm': millimeters,
        'source': source, 'radius_px': list(radii), 'uncalibrated': True,
        'operation': 'erode' if millimeters > 0.0 else 'dilate',
        'reason': None if (radii[0] or radii[1]) else
                 'rounds to zero pixels at this pixel pitch, so nothing changes',
    }


def apply_tolerance(mask, settings, index):
    """Erode or dilate a layer mask by the configured tolerance offset."""
    (rx, ry), record = tolerance_radius_px(settings, index)
    if not record['applied']:
        return mask, record
    occupied = np.asarray(mask) != 0
    before = int(np.count_nonzero(occupied))
    from .acceleration import binary_morphology
    out, backend = binary_morphology(occupied, rx, ry,
                                     erode=record['operation'] == 'erode',
                                     resources=settings['resources'])
    after = int(np.count_nonzero(out))
    record.update({'pixels_before': before, 'pixels_after': after,
                   'pixels_changed': abs(after - before),
                   'erased_layer': bool(before and not after),
                   'acceleration_backend': backend})
    return out.astype(np.uint8), record


def apply_dimensional_compensation(mask, settings, index, grid=None):
    """XY shrinkage then tolerance offset; Z shrinkage is reported, not applied.

    Both the GOO write pass and the post-write verification pass must call this
    (via ``export_mask``) so ``decoded_pixels`` still matches the intended
    exposure.  Defaults are zero and uncalibrated — nonzero values are flagged.
    """
    process = settings['process']
    z_percent = float(process['shrink_percent_z'])
    z_record = {
        'requested': z_percent, 'applied': False, 'uncalibrated': z_percent != 0.0,
        'reason': None if z_percent == 0.0 else (
            'Z shrinkage cannot be applied without changing layer count; '
            'layers left unchanged'),
    }
    xy_percent = float(process['shrink_percent_xy'])
    center = _mask_scale_center(mask, settings, grid)
    mask, shrink = shrink_mask_xy(mask, xy_percent, center)
    if shrink.get('erased_layer'):
        return mask, {
            'applied': True, 'erased_layer': True, 'shrink_xy': shrink,
            'tolerance': {'applied': False, 'reason': 'skipped; shrink erased the layer'},
            'shrink_z': z_record, 'pixels_changed': shrink.get('pixels_changed', 0),
            'uncalibrated': True,
        }
    mask, tolerance = apply_tolerance(mask, settings, index)
    changed = int(shrink.get('pixels_changed', 0)) + int(tolerance.get('pixels_changed', 0))
    applied = bool(shrink.get('applied') or tolerance.get('applied'))
    return mask, {
        'applied': applied,
        'erased_layer': bool(tolerance.get('erased_layer')),
        'shrink_xy': shrink, 'tolerance': tolerance, 'shrink_z': z_record,
        'pixels_changed': changed,
        'uncalibrated': bool(
            xy_percent != 0.0 or z_percent != 0.0
            or float(process['tolerance_offset_mm']) != 0.0
            or float(process['bottom_tolerance_offset_mm']) != 0.0),
        'reason': None if applied else 'disabled',
    }


def compensate_mask(mask, settings, index):
    """Shrink a bottom layer's mask by the configured elephant-foot radius.

    Erosion runs on the cropped mask with a zero border, which is identical to
    eroding the full frame because everything outside the crop is empty, and
    far cheaper than eroding 36.8 Mpx.  Returns the mask and a record naming
    exactly how many pixels were removed.
    """
    (rx, ry), record = compensation_radius_px(settings, index)
    if not record['applied']:
        return mask, record
    occupied = np.asarray(mask) != 0
    before = int(np.count_nonzero(occupied))
    from .acceleration import binary_morphology
    eroded, backend = binary_morphology(occupied, rx, ry, erode=True,
                                        resources=settings['resources'])
    after = int(np.count_nonzero(eroded))
    record.update({'pixels_before': before, 'pixels_after': after,
                   'pixels_removed': before - after,
                   'erased_layer': bool(before and not after),
                   'acceleration_backend': backend})
    return eroded.astype(np.uint8), record


def export_mask(mask, settings, index, grid=None):
    """Dimensional compensation then elephant-foot, shared by write and verify.

    Returning one pipeline for both passes is what keeps ``decoded_pixels``
    honest: the file is compared against the compensated exposure, not the raw
    raster.
    """
    mask, dimensional = apply_dimensional_compensation(mask, settings, index, grid=grid)
    mask, elephant = compensate_mask(mask, settings, index)
    erased = bool(dimensional.get('erased_layer') or elephant.get('erased_layer'))
    return mask, dimensional, elephant, erased


def _frame_placement(mask, grid, printer):
    """Where a cropped mask lands in the full LCD frame, ready to paste.

    Returns ``(row, column, pixels)``: the crop already scaled to LCD intensity
    and mirrored, at the offset it occupies in the mirrored frame. Everything
    outside it is dark. Working on the crop keeps every per-layer pass
    proportional to the part, not to the 36.8 Mpx panel.
    """
    width, height = printer['pixels']
    row, column = grid.row_offset, grid.column_offset
    rows, columns = mask.shape
    if row < 0 or column < 0 or row + rows > height or column + columns > width:
        raise VoxelMillError('goo_frame', 'Layer crop falls outside the printer pixel grid',
                        {'crop_origin': [column, row], 'crop_size': [columns, rows],
                         'pixels': [width, height]})
    mask = np.asarray(mask)
    if mask.dtype != np.uint8:
        raise VoxelMillError('goo_frame', 'Raster mask must use uint8 pixels')
    # Geometry raster masks are occupancy (0/1). GOO binary exposure requires
    # full 8-bit LCD intensity (0/255); preserving values above one leaves the
    # door open for a separately validated grayscale/AA path.
    pixels = mask * np.uint8(255) if not mask.size or int(mask.max()) <= 1 else mask
    if printer['image_mirror_x']:
        pixels, column = pixels[:, ::-1], width - (column + columns)
    if printer['image_mirror_y']:
        pixels, row = pixels[::-1, :], height - (row + rows)
    return row, column, pixels


def _full_frame(mask, grid, printer):
    """Place a cropped mask into the full LCD frame at its physical position."""
    width, height = printer['pixels']
    row, column, pixels = _frame_placement(mask, grid, printer)
    frame = np.zeros((height, width), dtype=np.uint8)
    frame[row:row + pixels.shape[0], column:column + pixels.shape[1]] = pixels
    return frame


def _frame_mismatch(reader, index, mask, grid, printer, atol):
    """Pixels where a written layer differs from the expected exposure by more than ``atol``.

    The layer is decoded from the file's own chunk stream and compared run by
    run against the placed crop, so neither frame is ever materialized; the
    decoder applies every framing and checksum check.
    """
    from . import _native
    row, column, pixels = _frame_placement(mask, grid, printer)
    height, width = reader.shape
    return int(_native.goo_verify_placed(np.frombuffer(reader.blob(index), dtype=np.uint8),
                                         np.ascontiguousarray(pixels), int(row), int(column),
                                         int(width), int(height), int(atol)))


class GooWriter:
    """Streams a GOO file to a scratch path and renames it into place atomically."""

    def __init__(self, path, settings, layer_count, *, volume_mm3=0.0, print_time_s=0,
                 previews=None, cancel=None, progress=no_progress):
        from . import _native
        self._native = _native
        self.path = Path(path)
        self.settings = settings
        self.cancel = cancel or CancellationToken()
        self.progress = progress
        self.header = header_from_settings(settings, layer_count, volume_mm3=volume_mm3,
                                           print_time_s=print_time_s)
        self.layer_count = layer_count
        self.shape = (int(self.header['resolution_y']), int(self.header['resolution_x']))
        self.previews = previews or {}
        self.written = 0
        self.bytes_written = 0
        self.layer_digest = hashlib.sha256()
        self._temporary = None
        self._handle = None
        self._last_z = -math.inf

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(prefix=f'.{self.path.name}.', dir=self.path.parent,
                                             delete=False)
        self._temporary = Path(handle.name)
        self._handle = handle
        handle.write(self._header_bytes())
        return self

    def _header_bytes(self):
        buffer = bytearray(HEADER_BYTES)
        buffer[0:4] = VERSION
        buffer[4:12] = MAGIC
        for name, code, offset in HEADER_FIELDS:
            _pack(buffer, offset, code, self.header[name])
        small = self.previews.get('small')
        big = self.previews.get('big')
        if small is None:
            small = np.zeros((SMALL_PREVIEW, SMALL_PREVIEW, 3), np.uint8)
        if big is None:
            big = np.zeros((BIG_PREVIEW, BIG_PREVIEW, 3), np.uint8)
        small, big = np.asarray(small), np.asarray(big)
        if small.shape != (SMALL_PREVIEW, SMALL_PREVIEW, 3) or big.shape != (BIG_PREVIEW, BIG_PREVIEW, 3):
            raise VoxelMillError('goo_preview', 'Previews must be uint8 RGB images exactly 116x116 and 290x290 pixels')
        buffer[194:194 + SMALL_PREVIEW * SMALL_PREVIEW * 2] = rgb565(small)
        buffer[27106:27108] = DELIMITER
        buffer[27108:27108 + BIG_PREVIEW * BIG_PREVIEW * 2] = rgb565(big)
        buffer[195308:195310] = DELIMITER
        return bytes(buffer)

    def add_layer(self, mask, z_mm, *, exposure_s=None):
        self.cancel.check()
        if self.written >= self.layer_count:
            raise VoxelMillError('goo_layers', 'More layers were written than the header declares')
        mask = np.asarray(mask)
        if mask.dtype != np.uint8 or mask.shape != self.shape:
            raise VoxelMillError('goo_layer_shape', 'Layer must be a uint8 full-LCD image',
                            {'expected_shape': list(self.shape), 'actual_shape': list(mask.shape),
                             'dtype': str(mask.dtype)})
        z_mm = float(z_mm)
        if not math.isfinite(z_mm) or not 0 < z_mm <= self.header['machine_z']:
            raise VoxelMillError('goo_layer_z', 'Layer Z must be finite, above the plate, and inside machine Z travel',
                            {'z_mm': z_mm, 'machine_z_mm': self.header['machine_z']})
        if self.written and z_mm <= self._last_z:
            raise VoxelMillError('goo_layer_z', 'Layer Z values must increase strictly')
        return self._write_blob(self._native.goo_encode_layer(np.ascontiguousarray(mask)), z_mm, exposure_s)

    def add_placed_layer(self, row, column, pixels, z_mm, *, exposure_s=None):
        """Add a layer that is dark except ``pixels`` at ``(row, column)``.

        Byte-identical to :meth:`add_layer` on the full frame, without building
        it: the encoder derives the dark runs from the placement.
        """
        self.cancel.check()
        if self.written >= self.layer_count:
            raise VoxelMillError('goo_layers', 'More layers were written than the header declares')
        pixels = np.asarray(pixels)
        if pixels.dtype != np.uint8 or pixels.ndim != 2:
            raise VoxelMillError('goo_layer_shape', 'Layer crop must be a 2-D uint8 image',
                            {'dtype': str(pixels.dtype), 'ndim': int(pixels.ndim)})
        z_mm = float(z_mm)
        if not math.isfinite(z_mm) or not 0 < z_mm <= self.header['machine_z']:
            raise VoxelMillError('goo_layer_z', 'Layer Z must be finite, above the plate, and inside machine Z travel',
                            {'z_mm': z_mm, 'machine_z_mm': self.header['machine_z']})
        if self.written and z_mm <= self._last_z:
            raise VoxelMillError('goo_layer_z', 'Layer Z values must increase strictly')
        height, width = self.shape
        blob = self._native.goo_encode_placed(np.ascontiguousarray(pixels), int(row), int(column),
                                              int(width), int(height))
        return self._write_blob(blob, z_mm, exposure_s)

    def _write_blob(self, blob, z_mm, exposure_s):
        record = bytearray(LAYER_DEF_BYTES)
        values = _layer_values(self.header, self.settings, self.written, z_mm,
                               exposure_s=exposure_s)
        for name, code, offset in LAYER_FIELDS:
            _pack(record, offset, code, values[name])
        record[64:66] = DELIMITER
        struct.pack_into('>I', record, 66, len(blob))
        self._handle.write(bytes(record))
        self._handle.write(blob)
        self._handle.write(DELIMITER)
        self.layer_digest.update(blob)
        self.bytes_written += LAYER_DEF_BYTES + len(blob) + 2
        self.written += 1
        self._last_z = z_mm
        self.progress('goo', self.written, self.layer_count)
        return len(blob)

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                if self.written != self.layer_count:
                    raise VoxelMillError('goo_layers', 'Fewer layers were written than the header declares',
                                    {'written': self.written, 'declared': self.layer_count})
                self._handle.write(FOOTER)
                self._handle.flush()
                os.fsync(self._handle.fileno())
        finally:
            self._handle.close()
        if kind is None:
            os.replace(self._temporary, self.path)
            self._temporary = None
        elif self._temporary is not None:
            self._temporary.unlink(missing_ok=True)
            self._temporary = None
        return False


@dataclass
class GooLayer:
    index: int
    offset: int
    values: dict
    data_length: int
    blob_offset: int


class GooReader:
    """Bounded reader. Every framing rule is checked before any data is trusted."""

    def __init__(self, path):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        if self.size < HEADER_BYTES + len(FOOTER):
            raise VoxelMillError('goo_truncated', 'File is smaller than a GOO header plus footer')
        self.handle = open(self.path, 'rb')
        try:
            data = self.handle.read(HEADER_BYTES)
            if data[0:4] != VERSION:
                raise VoxelMillError('goo_version', f'Unsupported GOO version {data[0:4]!r}; only V3.0 is read')
            if data[4:12] != MAGIC:
                raise VoxelMillError('goo_magic', 'Header magic does not match')
            if data[27106:27108] != DELIMITER or data[195308:195310] != DELIMITER:
                raise VoxelMillError('goo_magic', 'Preview delimiters are missing')
            self.header = {name: _unpack(data, offset, code) for name, code, offset in HEADER_FIELDS}
            self._validate_header()
            self.small_preview = unpack_rgb565(data[194:27106], SMALL_PREVIEW, SMALL_PREVIEW)
            self.big_preview = unpack_rgb565(data[27108:195308], BIG_PREVIEW, BIG_PREVIEW)
            self.handle.seek(-len(FOOTER), os.SEEK_END)
            if self.handle.read(len(FOOTER)) != FOOTER:
                raise VoxelMillError('goo_footer', 'Ending magic does not match')
            if not 1 <= self.header['layer_count'] <= MAX_LAYERS:
                raise VoxelMillError('goo_layers', 'Implausible layer count')
            if self.header['layer_def_address'] != HEADER_BYTES:
                raise VoxelMillError('goo_header', 'First layer definition is not at the fixed header size')
            self.layers = self._index()
        except Exception:
            self.handle.close()
            raise

    @property
    def shape(self):
        return int(self.header['resolution_y']), int(self.header['resolution_x'])

    def _validate_header(self):
        header = self.header
        width, height = header['resolution_x'], header['resolution_y']
        if width < 1 or height < 1 or width * height > MAX_LAYER_PIXELS:
            raise VoxelMillError('goo_dimensions', 'GOO resolution is invalid or exceeds the decoder limit',
                            {'resolution': [width, height], 'max_pixels': MAX_LAYER_PIXELS})
        if header['mirror_x'] not in (0, 1) or header['mirror_y'] not in (0, 1):
            raise VoxelMillError('goo_header', 'Mirror flags must be zero or one')
        if header['delay_mode'] not in (0, 1):
            raise VoxelMillError('goo_header', 'Delay mode must be zero or one')
        for name in ('display_width', 'display_height', 'machine_z', 'layer_height',
                     'exposure_time', 'bottom_exposure_time'):
            if not math.isfinite(header[name]) or header[name] <= 0:
                raise VoxelMillError('goo_header', f'Header field {name} must be finite and positive')
        for name, value in header.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise VoxelMillError('goo_header', f'Header field {name} must be finite')

    def _index(self):
        layers = []
        offset = self.header['layer_def_address']
        end = self.size - len(FOOTER)
        for index in range(self.header['layer_count']):
            if offset + LAYER_DEF_BYTES > end:
                raise VoxelMillError('goo_truncated', f'Layer {index} definition runs past the footer')
            self.handle.seek(offset)
            record = self.handle.read(LAYER_DEF_BYTES)
            if record[64:66] != DELIMITER:
                raise VoxelMillError('goo_layer', f'Layer {index} delimiter is missing')
            length = struct.unpack_from('>I', record, 66)[0]
            if length < 3 or length > MAX_LAYER_BLOB_BYTES or offset + LAYER_DEF_BYTES + length + 2 > end:
                raise VoxelMillError('goo_truncated', f'Layer {index} blob runs past the footer')
            values = {name: _unpack(record, position, code) for name, code, position in LAYER_FIELDS}
            if values['pause'] not in (0, 1) or any(not math.isfinite(value)
                                                    for name, value in values.items()
                                                    if isinstance(value, float)):
                raise VoxelMillError('goo_layer', f'Layer {index} has invalid numeric fields')
            self.handle.seek(offset + LAYER_DEF_BYTES + length)
            if self.handle.read(2) != DELIMITER:
                raise VoxelMillError('goo_layer', f'Layer {index} trailing delimiter is missing')
            layers.append(GooLayer(index, offset, values, length, offset + LAYER_DEF_BYTES))
            offset += LAYER_DEF_BYTES + length + 2
        self.handle.seek(offset - 2)
        if self.handle.read(2) != DELIMITER:
            raise VoxelMillError('goo_layer', 'Final layer delimiter is missing')
        if offset != end:
            raise VoxelMillError('goo_chain', 'Layer chain does not end exactly at the footer',
                            {'chain_end': offset, 'footer_start': end})
        return layers

    def blob(self, index):
        layer = self.layers[index]
        self.handle.seek(layer.blob_offset)
        blob = self.handle.read(layer.data_length)
        if len(blob) != layer.data_length:
            raise VoxelMillError('goo_truncated', f'Layer {layer.index} changed or was truncated while reading')
        return blob

    def decode(self, index):
        from . import _native
        height, width = self.shape
        return _native.goo_decode_layer(np.frombuffer(self.blob(index), dtype=np.uint8), width, height)

    def close(self):
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _finite_equal(actual, expected, *, atol=2e-5):
    """Compare values after the format's unavoidable float32 quantisation."""
    if isinstance(expected, float):
        return math.isfinite(actual) and math.isclose(actual, expected, rel_tol=0.0, abs_tol=atol)
    return actual == expected


def _check_export_bounds(bounds, settings):
    """Layer count for a source, refusing or clipping it to the build volume.

    The printer cannot expose a pixel outside its LCD or move past its machine
    height, so geometry out there cannot be printed by any means.  With
    ``assembly.clip_to_build_volume`` off -- the default -- that is a refusal,
    because silently discarding part of a model is worse than not starting.
    With it on, the export proceeds against the reachable volume and the amount
    removed is recorded and raised as an error-severity diagnostic, so it can
    never pass unnoticed.

    Returns ``(layer_count, clip)`` where ``clip`` is ``None`` when everything
    fits.  Nothing is ever scaled.

    The rasterizer already clips correctly in XY: ``RasterGrid.for_bounds``
    clamps its crop to the panel, ``clamped_row`` clamps an edge's row range and
    ``clamped_col`` clamps a crossing's column, so an off-panel span
    contributes no pixels rather than wrapping.  Only Z needs clamping here,
    because the layer count is derived from the bounds rather than the panel.
    """
    from .geometry import envelope_fits, envelope_overflow_mm
    bounds = np.asarray(bounds, dtype=float)
    height = settings['process']['layer_height_mm']
    fits = envelope_fits(bounds, settings)
    clipping = bool(settings['assembly'].get('clip_to_build_volume', False))
    if not fits and not clipping:
        raise VoxelMillError('goo_envelope', 'The STL is outside the usable printer envelope; refusing to clip pixels',
                        {'bounds': bounds.tolist(), 'build_mm': settings['printer']['build_mm'],
                         'edge_clearance_mm': settings['printer']['edge_clearance_mm'],
                         'overflow_mm': envelope_overflow_mm(bounds, settings),
                         'enable_with': 'assembly.clip_to_build_volume = true'})
    machine_z = float(settings['printer']['build_mm'][2])
    top_mm = float(bounds[1, 2])
    clipped_top = min(top_mm, machine_z) if clipping else top_mm
    layer_count = int(math.ceil(clipped_top / height))
    if not 1 <= layer_count <= MAX_LAYERS:
        raise VoxelMillError('goo_layers', 'Source bounds produce an invalid GOO layer count',
                        {'max_z_mm': top_mm, 'layer_height_mm': height,
                         'layer_count': layer_count, 'machine_z_mm': machine_z})
    if fits:
        return layer_count, None
    overflow = envelope_overflow_mm(bounds, settings)
    return layer_count, {
        'overflow_mm': overflow, 'bounds': bounds.tolist(),
        'build_mm': list(settings['printer']['build_mm']),
        'edge_clearance_mm': settings['printer']['edge_clearance_mm'],
        'clipped_layers': max(0, int(math.ceil(top_mm / height)) - layer_count),
        'retained_layers': layer_count,
        'clipped_pixels': 0,   # filled in as layers are rasterized
        'note': 'geometry outside the build volume is not exported; nothing was scaled',
    }


def _clipped_triangles(triangles, settings, cancel):
    """Source triangles with any vertex outside the usable envelope."""
    from .geometry import CHUNK_SIZE
    build = np.asarray(settings['printer']['build_mm'], dtype=float)
    clearance = float(settings['printer'].get('edge_clearance_mm', 2.))
    limit = build[:2] / 2 - clearance
    count = 0
    for start in range(0, len(triangles), CHUNK_SIZE):
        cancel.check()
        chunk = np.asarray(triangles[start:start + CHUNK_SIZE], dtype=np.float64)
        outside = ((chunk[..., 0] < -limit[0]) | (chunk[..., 0] > limit[0])
                   | (chunk[..., 1] < -limit[1]) | (chunk[..., 1] > limit[1])
                   | (chunk[..., 2] < 0.0) | (chunk[..., 2] > build[2]))
        count += int(np.count_nonzero(outside.any(axis=1)))
    return count


def _measure_clip(triangles, bounds, settings, retained_layers, *, budget, cancel, progress):
    """Exactly how many exposed pixels the build volume removes.

    One extra raster pass on a grid extended past the panel at the printer's
    own pitch, with cell centers aligned to the panel's, so the in-panel window
    is an exact sub-array rather than a resampling.  Every pixel the source
    would expose is counted, and the ones inside the panel on a retained layer
    are subtracted; what is left is what the machine cannot reach.

    This runs only when clipping was explicitly requested.  Losing geometry is
    not something to estimate.
    """
    from . import _native
    from .raster import RasterGrid
    bounds = np.asarray(bounds, dtype=float)
    printer = settings['printer']
    width, height = int(printer['pixels'][0]), int(printer['pixels'][1])
    dx, dy = float(printer['pixel_pitch_mm'][0]), float(printer['pixel_pitch_mm'][1])
    x0, y0 = -float(printer['build_mm'][0]) / 2, -float(printer['build_mm'][1]) / 2
    pad_left = max(0, int(math.ceil((x0 - float(bounds[0, 0])) / dx)) + 1)
    pad_right = max(0, int(math.ceil((float(bounds[1, 0]) - (x0 + width * dx)) / dx)) + 1)
    pad_bottom = max(0, int(math.ceil((y0 - float(bounds[0, 1])) / dy)) + 1)
    pad_top = max(0, int(math.ceil((float(bounds[1, 1]) - (y0 + height * dy)) / dy)) + 1)
    grid = RasterGrid(width + pad_left + pad_right, height + pad_bottom + pad_top,
                      x0 - pad_left * dx, y0 - pad_bottom * dy, dx, dy)
    layer_height = settings['process']['layer_height_mm']
    source_layers = max(0, int(math.ceil(float(bounds[1, 2]) / layer_height)))
    budget.require(grid.width * grid.height * 3 + len(triangles) * 80 + 192 * 1024 ** 2,
                   'build volume clip measurement')
    raster = _native.Rasterizer(np.asarray(triangles), cancel.check)
    source_pixels = retained_pixels = 0
    dropped_layer_pixels = 0
    for index in range(source_layers):
        cancel.check()
        mask = raster.slice((index + 0.5) * layer_height, grid.width, grid.height,
                            grid.x0, grid.y0, grid.dx, grid.dy, cancel.check, 'nonzero')['mask']
        total = int(np.count_nonzero(mask))
        source_pixels += total
        if index < retained_layers:
            window = mask[pad_bottom:pad_bottom + height, pad_left:pad_left + width]
            retained_pixels += int(np.count_nonzero(window))
        else:
            dropped_layer_pixels += total
        progress('clip_measure', index + 1, source_layers)
    return {'source_pixels': source_pixels, 'retained_pixels': retained_pixels,
            'clipped_pixels': source_pixels - retained_pixels,
            'clipped_pixels_above_machine_z': dropped_layer_pixels,
            'source_layers': source_layers,
            'measurement_grid': [grid.width, grid.height],
            'panel_origin_px': [pad_left, pad_bottom]}


#: Observed in the two reference files for this machine. They disagree, which is
#: why an unverified orientation is reported on every export rather than left as
#: a comment in a profile.
REFERENCE_MIRRORS = {'CHITUBOX Basic v2.3.1': {'mirror_x': 1, 'mirror_y': 0},
                     'ELEGOO SatelLite': {'mirror_x': 0, 'mirror_y': 1}}


def orientation_warning(settings):
    """A loud, per-export note that the LCD orientation is not established.

    Suppressed only by ``printer.image_mirror_verified``, which a user sets
    after checking a printed part. A mirrored threaded or keyed part is scrap
    and the failure is invisible until it is assembled, so the default is to
    say so every time rather than to bury it in a profile comment.
    """
    printer = settings['printer']
    if printer.get('image_mirror_verified', False):
        return None
    return Diagnostic(
        'goo_orientation_unverified',
        'The physical LCD orientation for this printer is not established. The two reference '
        'files for this machine disagree on the mirror flags, and this export uses the profile '
        'values without confirmation. A mirrored threaded or keyed part is scrap and looks '
        'correct until it is assembled.',
        severity='warning',
        details={
            'profile': {'image_mirror_x': bool(printer['image_mirror_x']),
                        'image_mirror_y': bool(printer['image_mirror_y'])},
            'reference_files_disagree': REFERENCE_MIRRORS,
            'clears_when': 'printer.image_mirror_verified is set true after checking a printed part',
            'offline_check': 'scripts/goo_orientation_check.py GOO STL --output REPORT.json; '
                             'it reported inconclusive against both references because each '
                             'slicer posed the model itself',
        })


def _expected_record(settings, index, z_mm, machine_z):
    """Independent settings calculation used after reopening an exported file."""
    process, motion = settings['process'], settings['printer']['motion']
    bottom = index < process['bottom_layers']
    process_prefix = 'bottom_' if bottom else 'normal_'
    motion_prefix = 'bottom_' if bottom else ''
    return {
        'pause': 0, 'pause_position_z': float(machine_z), 'position_z': float(z_mm),
        'exposure_time': float(layer_exposure(settings, index)), 'light_off_delay': 0.0,
        'wait_after_cure': float(process[f'{process_prefix}rest_after_exposure_s']),
        'wait_after_lift': float(process[f'{process_prefix}wait_after_lift_s']),
        'wait_before_cure': float(process[f'{process_prefix}settle_before_exposure_s']),
        'lift_height': float(motion[f'{motion_prefix}lift_height']),
        'lift_speed': float(motion[f'{motion_prefix}lift_speed']),
        'lift_height2': float(motion[f'{motion_prefix}lift_height2']),
        'lift_speed2': float(motion[f'{motion_prefix}lift_speed2']),
        'retract_height': float(motion[f'{motion_prefix}retract_height']),
        'retract_speed': float(motion[f'{motion_prefix}retract_speed']),
        'retract_height2': float(motion[f'{motion_prefix}retract_height2']),
        'retract_speed2': float(motion[f'{motion_prefix}retract_speed2']),
        'light_pwm': int(motion[f'{motion_prefix}light_pwm']),
    }


def _verify_header_settings(header, settings, layer_count):
    """Verify machine, process, transition and orientation settings post-write."""
    printer, process = settings['printer'], settings['process']
    expected = {
        'layer_count': layer_count,
        'resolution_x': printer['pixels'][0], 'resolution_y': printer['pixels'][1],
        'mirror_x': int(printer['image_mirror_x']), 'mirror_y': int(printer['image_mirror_y']),
        'display_width': float(printer['build_mm'][0]), 'display_height': float(printer['build_mm'][1]),
        'machine_z': float(printer['build_mm'][2]), 'layer_height': float(process['layer_height_mm']),
        'exposure_time': float(process['normal_exposure_s']),
        'bottom_exposure_time': float(process['bottom_exposure_s']),
        'bottom_layer_count': int(process['bottom_layers']),
        'transition_layer_count': int(process['transition_layers']),
        'per_layer_settings': 1, 'delay_mode': 1,
    }
    mismatches = {name: {'expected': value, 'actual': header.get(name)} for name, value in expected.items()
                  if not _finite_equal(header.get(name), value)}
    if mismatches:
        raise VoxelMillError('goo_verify_header', 'Reopened GOO header does not match the requested settings',
                        {'mismatches': mismatches})


def _previewing(stream, heightmap):
    """Feed validation while retaining only a one-value-per-crop-pixel preview map."""
    for layer in stream:
        heightmap[layer.mask != 0] = layer.index + 1
        yield layer


def _default_previews(heightmap):
    return {'small': preview_from_heightmap(heightmap, SMALL_PREVIEW),
            'big': preview_from_heightmap(heightmap, BIG_PREVIEW)}


class GooLayerStream:
    """Decoded GOO frames as :class:`Layer` records on the full LCD lattice.

    A deliberate structural counterpart to
    :class:`~voxelmill.raster.MeshLayerStream` — same ``grid`` and
    ``layer_count`` attributes, the same ``Layer`` yield and the same
    ``diagnostics()`` — so :func:`~voxelmill.validation.analyze_layers` consumes
    it with no change at all.

    Mirroring is undone before a frame is analyzed.  Layer topology survives an
    axis flip, but ``position_mm`` in a diagnostic does not, and a diagnostic
    that points at the wrong side of the plate is worse than none.
    """

    def __init__(self, reader, *, cancel=None, budget=None, progress=no_progress, crop=None):
        from .contracts import Layer
        from .raster import RasterGrid
        self._layer = Layer
        self.reader = reader
        header = reader.header
        width, height = int(header['resolution_x']), int(header['resolution_y'])
        dx = float(header['display_width']) / width
        dy = float(header['display_height']) / height
        x0 = -float(header['display_width']) / 2.0
        y0 = -float(header['display_height']) / 2.0
        self.crop = crop
        if crop is None:
            self.grid = RasterGrid(width, height, x0, y0, dx, dy)
        else:
            r0, r1, c0, c1 = crop
            # Same physical lattice, a window of it: x0/y0 move with the crop so
            # every ``grid.xy`` stays in plate coordinates and a diagnostic's
            # position is identical to the uncropped one.
            self.grid = RasterGrid(c1 - c0, r1 - r0, x0 + c0 * dx, y0 + r0 * dy, dx, dy, c0, r0)
        self.layer_count = len(reader.layers)
        self.layer_height = float(header['layer_height'])
        self.mirror_x = bool(header['mirror_x'])
        self.mirror_y = bool(header['mirror_y'])
        self.cancel = cancel or CancellationToken()
        self.progress = progress
        self.consumed = False
        self.filled_pixels = 0
        self.blank_layers = 0
        if budget is not None:
            # The decoded panel is always full size; labels, the previous frame
            # and a distance field are only as large as the analyzed window.
            budget.require(width * height + self.grid.width * self.grid.height * 72
                           + 192 * 1024 ** 2, 'GOO layer verification')

    def unmirrored(self, index):
        """One decoded frame in plate orientation, uncropped."""
        frame = self.reader.decode(index)
        if self.mirror_x:
            frame = frame[:, ::-1]
        if self.mirror_y:
            frame = frame[::-1, :]
        return frame

    def __iter__(self):
        if self.consumed:
            raise VoxelMillError('stream_consumed', 'Create a fresh GOO layer stream for another pass')
        self.consumed = True
        for index in range(self.layer_count):
            self.cancel.check()
            frame = self.unmirrored(index)
            if self.crop is not None:
                r0, r1, c0, c1 = self.crop
                frame = frame[r0:r1, c0:c1]
            frame = np.ascontiguousarray(frame)
            filled = int(np.count_nonzero(frame))
            self.filled_pixels += filled
            self.blank_layers += int(filled == 0)
            self.progress('goo_decode', index + 1, self.layer_count)
            yield self._layer(index, float(self.reader.layers[index].values['position_z']), frame)


    def diagnostics(self):
        """A decoded file has no scanline contour to leave open."""
        return []


def occupied_crop(reader, *, cancel=None, progress=no_progress, margin=1):
    """Window of the panel every exposed pixel falls inside, plus a border.

    Costs one extra decode pass and saves far more than it costs: labeling a
    36.8 Mpx frame dominates decoding it, and no real part fills the panel. A
    500-layer sphere measured minutes of labeling against 32 s to slice the
    same geometry, which is what this exists to remove.

    The one-pixel border keeps outside air connected all the way around the
    geometry, exactly as ``RasterGrid.for_bounds`` does on the mesh path, so
    the void analysis sees the same connectivity it would on the full panel.
    Returns ``None`` when the file exposes nothing anywhere.
    """
    cancel = cancel or CancellationToken()
    stream = GooLayerStream(reader, cancel=cancel)
    height, width = reader.shape
    top, bottom, left, right = height, -1, width, -1
    for index in range(len(reader.layers)):
        cancel.check()
        frame = stream.unmirrored(index)
        rows = np.flatnonzero(frame.any(axis=1))
        if len(rows):
            columns = np.flatnonzero(frame.any(axis=0))
            top, bottom = min(top, int(rows[0])), max(bottom, int(rows[-1]))
            left, right = min(left, int(columns[0])), max(right, int(columns[-1]))
        progress('goo_extent', index + 1, len(reader.layers))
    if bottom < 0:
        return None
    return (max(0, top - margin), min(height, bottom + 1 + margin),
            max(0, left - margin), min(width, right + 1 + margin))

#: Settings :func:`verify_goo` takes from the file rather than from a profile,
#: paired with the header field that supplies each one.
DERIVED_FROM_HEADER = (
    (('process', 'layer_height_mm'), 'layer_height'),
    (('process', 'bottom_layers'), 'bottom_layer_count'),
    (('process', 'transition_layers'), 'transition_layer_count'),
    (('process', 'normal_exposure_s'), 'exposure_time'),
    (('process', 'bottom_exposure_s'), 'bottom_exposure_time'),
)


def _settings_from_header(settings, header):
    """Prefer the file's own geometry over a profile that may not describe it.

    A GOO carries its panel, its physical display size and its layer pitch, so
    a check driven by those values describes the file.  A profile selected on
    the command line may belong to a different machine entirely; where the two
    disagree the difference is recorded rather than reconciled.
    """
    import copy
    derived = copy.deepcopy(settings)
    width, height = int(header['resolution_x']), int(header['resolution_y'])
    display = [float(header['display_width']), float(header['display_height'])]
    printer = derived['printer']
    supplied = {'pixels': list(printer['pixels']),
                'build_mm': list(printer['build_mm']),
                'pixel_pitch_mm': list(printer['pixel_pitch_mm'])}
    printer['pixels'] = [width, height]
    printer['build_mm'] = [display[0], display[1], float(header['machine_z'])]
    printer['pixel_pitch_mm'] = [display[0] / width, display[1] / height]
    printer['image_mirror_x'] = int(header['mirror_x'])
    printer['image_mirror_y'] = int(header['mirror_y'])
    record = {'printer.pixels': [width, height],
              'printer.build_mm': printer['build_mm'],
              'printer.pixel_pitch_mm': printer['pixel_pitch_mm']}
    for (section, key), field_name in DERIVED_FROM_HEADER:
        value = header[field_name]
        derived[section][key] = type(derived[section][key])(value)
        record[f'{section}.{key}'] = derived[section][key]
    # Every float in the header is float32, so a profile value that round-trips
    # through it comes back a few ULPs away. Comparing exactly would report the
    # printer's own build volume as a mismatch on every single export, which is
    # the fastest way to teach someone to ignore the field. This is the same
    # tolerance ``_finite_equal`` uses to verify a written header.
    mismatches = {}
    for name, was in (('printer.pixels', supplied['pixels']),
                      ('printer.build_mm', supplied['build_mm']),
                      ('printer.pixel_pitch_mm', supplied['pixel_pitch_mm'])):
        if not np.allclose(np.asarray(was, dtype=float), np.asarray(record[name], dtype=float),
                           rtol=1e-6, atol=2e-5):
            mismatches[name] = {'profile': was, 'file': record[name]}
    for (section, key), _field in DERIVED_FROM_HEADER:
        was = settings[section][key]
        now = derived[section][key]
        if isinstance(now, float):
            if not math.isclose(float(was), now, rel_tol=1e-6, abs_tol=2e-5):
                mismatches[f'{section}.{key}'] = {'profile': was, 'file': now}
        elif was != now:
            mismatches[f'{section}.{key}'] = {'profile': was, 'file': now}
    return derived, record, mismatches


def verify_goo(path, settings, *, cancel=None, budget=None, progress=no_progress,
               track_voids=True, full_panel=False):
    """Deep topology check of a finished GOO, without a source mesh.

    Geometry comes from the file; topology thresholds come from the caller's
    resolved settings and are recorded separately in the returned payload.

    Framing, the layer chain and the checksums are proved by opening it; this
    adds what ``goo-info --verify`` throws away, running every decoded frame
    through the same :func:`~voxelmill.validation.analyze_layers` the STL path
    uses.  Islands, growth spans, enclosed voids and transient traps are
    therefore found in the pixels the printer will expose, not in the mesh
    somebody hoped it came from.

    By default the analysis window is cropped to everything the file actually
    exposes, plus a one-pixel border, which costs one extra decode pass and
    removes minutes of labeling empty panel. ``full_panel=True`` analyzes the
    whole LCD instead; the two give identical results and a test asserts it.

    It does **not** establish that the file matches any particular source mesh.
    That claim belongs to :func:`slice_stl`'s post-write pixel comparison, and
    nothing here can substitute for it.
    """
    from .contracts import ResourceBudget
    from .validation import analyze_layers

    cancel = cancel or CancellationToken()
    budget = budget or ResourceBudget(**settings['resources'])
    started = time.monotonic()
    path = Path(path)
    with GooReader(path) as reader:
        derived, derived_from_file, mismatches = _settings_from_header(settings, reader.header)
        crop = None if full_panel else occupied_crop(reader, cancel=cancel, progress=progress)
        stream = GooLayerStream(reader, cancel=cancel, budget=budget, progress=progress,
                                crop=crop)
        report = analyze_layers(stream, stream.grid, derived, cancel=cancel, budget=budget,
                                progress=progress, track_voids=track_voids)
        report.diagnostics.extend(stream.diagnostics())
        report.metrics['peel_risk'] = {
            'status': 'not_run', 'reason': 'GOO masks do not retain oriented STL surface regions',
            'scope': 'standalone GOO verification checks raster evidence only'}
        report.metrics['goo'] = {
            'layers': stream.layer_count,
            # The file's own panel, not the analysis window; the window is
            # reported separately below so the two are never confused.
            'shape_px': list(reader.shape),
            'unmirrored_for_analysis': {'x': stream.mirror_x, 'y': stream.mirror_y},
            'blank_layers': stream.blank_layers,
            'exposed_pixels': stream.filled_pixels,
            'analysis_window_px': [stream.grid.width, stream.grid.height],
            'analysis_origin_px': [stream.grid.column_offset, stream.grid.row_offset],
            'decode_passes': 1 if full_panel else 2,
            'window_note': ('the whole panel was analyzed'
                            if full_panel else
                            'cropped to the exposed pixels plus a one-pixel border; the lattice '
                            'and every diagnostic position are unchanged'),
            'software': reader.header['software_name'],
            'machine': reader.header['machine_name'],
        }
        header = dict(reader.header)
    if mismatches:
        report.diagnostics.append(Diagnostic(
            'goo_settings_differ',
            'The file describes a different machine or process than the supplied profile; '
            'the file was used',
            severity='warning', details=mismatches))
    return {
        'schema_version': 1, 'command': 'verify', 'input': str(path),
        'header': header, 'layers': header['layer_count'],
        'settings_from_file': derived_from_file,
        # These thresholds have no GOO header field. Record the resolved
        # caller values (including defaults), not an inferred file setting.
        'settings_from_caller': {
            'support.min_overlap_pixels': derived['support']['min_overlap_pixels'],
            'support.max_span_mm': derived['support']['max_span_mm'],
            'repair.min_void_volume_mm3': derived['repair']['min_void_volume_mm3'],
        },
        'analysis_options': {'track_voids': bool(track_voids)},
        'settings_mismatches': mismatches,
        'establishes': ['the file opens, frames and decodes',
                        'the decoded layers satisfy the configured layer topology rules'],
        'does_not_establish': ['that these pixels came from any particular source mesh',
                               'surface peel-risk evidence, which requires oriented STL triangles',
                               'that the machine motion or exposure values are calibrated'],
        'report': report.to_dict(), 'seconds': time.monotonic() - started,
    }


def slice_stl(source, output, settings, *, allow_unresolved=False, cancel=None,
              progress=no_progress, scratch_dir=None, previews=None, print_time_s=0,
              report_path=None):
    """Slice one prepared STL to a GOO or unencrypted CTB v3 file.

    The input is deliberately an STL path, rather than a one-shot layer stream:
    the source is rasterized independently for validation and once again after
    writing so every decoded LCD pixel is compared without retaining a build
    volume.  ``allow_unresolved`` only permits a warned source-validation
    result.  It never bypasses envelope, serialization, framing, timing, or
    exact-pixel verification failures when ``process.antialias_levels`` is 1.
    With grayscale AA (levels 2 or 4), decoded 8-bit coverage may differ by 1
    from the expected value because of codec rounding, so verification uses
    ``atol=1`` instead of exact equality. Binary layers remain exact.

    ``print_time_s`` overrides the header ``PrintTime`` when nonzero.  A value
    of zero fills the header from :func:`~voxelmill.timing.estimate_print_time_s`
    (exposure + process waits + motion travel, labeled uncalibrated on the
    report as ``estimated_print_time``).  Per-layer exposure and wait timings
    are still written and verified exactly either way.

    After a successful publish, ``resources.post_slice_hook`` runs when set.
    Hook failure is recorded on the report and never deletes the GOO.
    ``report_path`` is passed to the hook as ``VOXELMILL_REPORT``.
    """
    from .contracts import Diagnostic, ResourceBudget
    from .mesh import open_stl
    from .raster import MeshLayerStream
    from .validation import analyze_drainage, analyze_layers, drainage_check

    cancel = cancel or CancellationToken()
    budget = ResourceBudget(**settings['resources'])
    source, published = Path(source), Path(output)
    try:
        source_real = source.resolve(strict=True)
    except OSError as error:
        raise VoxelMillError('mesh_io', f'Cannot resolve source STL: {error}', {'path': str(source)}) from error
    if published.is_symlink():
        raise VoxelMillError('goo_output', 'Refusing a symlink output path for an atomic export', {'path': str(published)})
    if published.exists() and os.path.samefile(source_real, published):
        raise VoxelMillError('goo_output', 'Source STL and output file resolve to the same file')
    if source_real == published.resolve(strict=False):
        raise VoxelMillError('goo_output', 'Source STL and output file resolve to the same path')
    suffix = published.suffix.lower()
    if suffix not in ('.goo', '.ctb'):
        raise VoxelMillError('slice_format', 'slice writes .goo or unencrypted CTB v3 (.ctb)')
    published.parent.mkdir(parents=True, exist_ok=True)
    output = published
    ctb_goo = None
    if suffix == '.ctb':
        handle = tempfile.NamedTemporaryFile(
            prefix=f'.{published.name}.', suffix='.goo', dir=published.parent, delete=False)
        handle.close()
        ctb_goo = Path(handle.name)
        ctb_goo.unlink()
        output = ctb_goo
    started = time.monotonic()
    stage = None
    antialias_levels = int(settings['process'].get('antialias_levels', 1))
    antialias_supports = bool(settings['process'].get('antialias_supports', False))
    antialias_supports_unseparated = False
    try:
        # Pass one validates the precise input image and creates the previews.
        with open_stl(source, budget, cancel, progress) as mesh:
            bounds = np.asarray(mesh.asset.bounds, dtype=float)
            layer_count, clip = _check_export_bounds(bounds, settings)
            asset = {'path': str(mesh.asset.path), 'sha256': mesh.asset.sha256,
                     'triangles': mesh.asset.triangle_count, 'bounds': mesh.asset.bounds}
            stream = MeshLayerStream(mesh.triangles, bounds, settings, budget=budget,
                                     cancel=cancel, progress=progress)
            antialias_supports_unseparated = bool(
                getattr(stream, 'antialias_supports_unseparated', False))
            if clip is not None:
                # Every pass walks only the layers the machine can reach, so
                # validation, the written file and the verification compare the
                # same set.  The source's own layer count is recorded in the
                # clip record rather than silently dropped.
                stream.layer_count = layer_count
            elif stream.layer_count != layer_count:
                raise VoxelMillError('goo_layers', 'Raster layer count disagrees with source bounds')
            heightmap = None
            if previews is None:
                budget.require(stream.grid.width * stream.grid.height * 4, 'GOO preview height map')
                heightmap = np.zeros((stream.grid.height, stream.grid.width), dtype=np.uint32)
                validation = analyze_layers(_previewing(stream, heightmap), stream.grid, settings,
                                            cancel=cancel, budget=budget, progress=progress)
                previews = _default_previews(heightmap)
                del heightmap
            else:
                validation = analyze_layers(stream, stream.grid, settings, cancel=cancel,
                                            budget=budget, progress=progress)
            validation.diagnostics.extend(stream.diagnostics())
            validation.checks['closed_surface'] = 'fail' if stream.open_rows else 'pass'
            validation.metrics['open_rows'] = stream.open_rows
            validation.metrics['antialias_levels'] = antialias_levels
            validation.metrics['antialias_supports'] = antialias_supports
            if antialias_supports_unseparated:
                validation.metrics['antialias_supports_unseparated'] = True
            orientation = orientation_warning(settings)
            if orientation is not None:
                validation.diagnostics.append(orientation)
                validation.checks['image_orientation'] = 'warn'
            else:
                validation.checks['image_orientation'] = 'pass'
            if clip is not None:
                # An explicitly requested clip still has to be impossible to
                # miss: an error diagnostic fails the report, so the export is
                # withheld unless the caller also allows an unresolved result.
                clip['clipped_triangles'] = _clipped_triangles(mesh.triangles, settings, cancel)
                clip.update(_measure_clip(mesh.triangles, bounds, settings, layer_count,
                                          budget=budget, cancel=cancel, progress=progress))
                clip['clipped_to'] = ('the physical LCD panel and machine height; the overflow '
                                      'above is measured against the smaller usable envelope, '
                                      'which additionally reserves printer.edge_clearance_mm')
                validation.metrics['clipped'] = clip
                validation.checks['build_volume_clip'] = 'fail'
                validation.diagnostics.append(Diagnostic(
                    'clipped_geometry',
                    'Geometry outside the build volume was not exported; the printer cannot reach it',
                    severity='error', details=clip))
            from .peel import apply_peel_check
            apply_peel_check(validation, mesh.triangles, bounds, settings,
                             budget=budget, cancel=cancel, progress=progress)
            validation.metrics['grid'] = [stream.grid.width, stream.grid.height]
            validation.metrics['crop_origin_px'] = [stream.grid.column_offset, stream.grid.row_offset]
            try:
                drainage = analyze_drainage(mesh.triangles, bounds, settings, budget=budget,
                                            cancel=cancel, progress=progress)
                validation.metrics['drainage'] = drainage
                validation.checks['drainage_bottlenecks'] = drainage_check(drainage)
                if drainage.get('bottlenecked_components'):
                    validation.diagnostics.append(Diagnostic(
                        'drainage_bottleneck',
                        'Void connects to the exterior only through an orifice below the configured area',
                        details=drainage))
            except VoxelMillError as error:
                validation.checks['drainage_bottlenecks'] = 'not_run'
                validation.diagnostics.append(Diagnostic(
                    'drainage_not_run', str(error), severity='warning', details=error.details))
        source_validation = validation.to_dict()
        if not validation.passed and not allow_unresolved:
            return {'schema_version': 1, 'source': asset, 'output': str(output),
                    'written': False, 'warned': False, 'validation': source_validation,
                    'reason': 'validation did not pass; pass allow_unresolved=True to retain a warned export',
                    'seconds': time.monotonic() - started}

        # Keep the fully written candidate beside the destination.  The final
        # replace happens only after a separate reader/raster pass succeeds.
        # Every compensated layer is recorded, so a shrunk first layer is a
        # reported quantity rather than an invisible difference between the
        # geometry and the exposure.  Dimensional compensation (shrink /
        # tolerance) shares export_mask with elephant-foot so verification
        # recomputes an identical exposure.
        compensation = []
        dimensional_layers = []
        dimensional_z = None
        estimated_print_time = None
        header_print_time_s = int(print_time_s)
        if header_print_time_s == 0:
            estimated_print_time = estimate_print_time_s(settings, layer_count)
            header_print_time_s = int(estimated_print_time['seconds'])
        descriptor, name = tempfile.mkstemp(prefix=f'.{output.name}.slice-', suffix='.goo', dir=output.parent)
        os.close(descriptor)
        stage = Path(name)
        stage.unlink()
        with GooWriter(stage, settings, layer_count,
                       volume_mm3=float(validation.metrics.get('raster_volume_mm3', 0.0)),
                       print_time_s=header_print_time_s, previews=previews, cancel=cancel,
                       progress=progress) as writer:
            with open_stl(source, budget, cancel, progress) as mesh:
                if mesh.asset.sha256 != asset['sha256']:
                    raise VoxelMillError('goo_source_changed', 'Source STL changed after validation; export was aborted')
                stream = MeshLayerStream(mesh.triangles, np.asarray(mesh.asset.bounds, dtype=float), settings,
                                         budget=budget, cancel=cancel, progress=progress)
                if clip is not None:
                    stream.layer_count = layer_count
                for layer in stream:
                    mask, dimensional, record, erased = export_mask(
                        layer.mask, settings, layer.index, grid=stream.grid)
                    if dimensional_z is None:
                        dimensional_z = dimensional.get('shrink_z')
                    if dimensional.get('applied'):
                        if dimensional.get('erased_layer'):
                            raise VoxelMillError(
                                'goo_compensation',
                                'Dimensional compensation removed an entire layer; '
                                'reduce process.shrink_percent_xy or process.tolerance_offset_mm',
                                {'layer': layer.index,
                                 'pixels_changed': dimensional.get('pixels_changed'),
                                 'shrink_xy': dimensional.get('shrink_xy'),
                                 'tolerance': dimensional.get('tolerance')})
                        dimensional_layers.append(dict(dimensional, layer=layer.index))
                    if record['applied']:
                        if record['erased_layer']:
                            raise VoxelMillError(
                                'goo_elephant_foot',
                                'Elephant-foot compensation removed an entire layer; '
                                'reduce process.elephant_foot_compensation_mm',
                                {'layer': layer.index, 'radius_px': record['radius_px'],
                                 'pixels_before': record['pixels_before']})
                        compensation.append(dict(record, layer=layer.index))
                    elif erased:
                        raise VoxelMillError(
                            'goo_compensation',
                            'Compensation removed an entire layer; reduce shrink or tolerance',
                            {'layer': layer.index})
                    writer.add_placed_layer(*_frame_placement(mask, stream.grid, settings['printer']),
                                            layer.z_mm)
            encoded_blob_sha256 = writer.layer_digest.hexdigest()

        # Pass three is intentionally independent of the emitted buffers.
        with GooReader(stage) as reopened:
            _verify_header_settings(reopened.header, settings, layer_count)
            with open_stl(source, budget, cancel, progress) as mesh:
                if mesh.asset.sha256 != asset['sha256']:
                    raise VoxelMillError('goo_source_changed', 'Source STL changed before post-write verification; export was aborted')
                stream = MeshLayerStream(mesh.triangles, np.asarray(mesh.asset.bounds, dtype=float), settings,
                                         budget=budget, cancel=cancel, progress=progress)
                if clip is not None:
                    stream.layer_count = layer_count
                checked = 0
                # Binary occupancy: exact pixel equality. Grayscale AA coverage:
                # an 8-bit decoded value may differ by 1 from the expected
                # coverage because of codec rounding (atol=1).
                pixel_atol = 0 if antialias_levels <= 1 else 1
                for layer in stream:
                    cancel.check()
                    expected_mask, _, _, _ = export_mask(
                        layer.mask, settings, layer.index, grid=stream.grid)
                    differences = _frame_mismatch(reopened, layer.index, expected_mask, stream.grid,
                                                  settings['printer'], pixel_atol)
                    if differences:
                        decoded = reopened.decode(layer.index)
                        expected = _full_frame(expected_mask, stream.grid, settings['printer'])
                        raise VoxelMillError('goo_verify_pixels', 'Decoded GOO pixels differ from the source raster',
                                        {'layer': layer.index, 'different_pixels': differences,
                                         'antialias_levels': antialias_levels, 'atol': pixel_atol,
                                         'expected_sha256': hashlib.sha256(expected).hexdigest(),
                                         'decoded_sha256': hashlib.sha256(decoded).hexdigest()})
                    expected_values = _expected_record(settings, layer.index, layer.z_mm,
                                                       settings['printer']['build_mm'][2])
                    actual_values = reopened.layers[layer.index].values
                    mismatches = {key: {'expected': value, 'actual': actual_values.get(key)}
                                  for key, value in expected_values.items()
                                  if not _finite_equal(actual_values.get(key), value)}
                    if mismatches:
                        raise VoxelMillError('goo_verify_timing', 'Reopened layer timing or motion differs from settings',
                                        {'layer': layer.index, 'mismatches': mismatches})
                    checked += 1
                    progress('goo_verify', checked, layer_count)
            if checked != layer_count:
                raise VoxelMillError('goo_verify_layers', 'Reopened layer count differs from the source raster')
        cancel.check()
        if output.is_symlink():
            raise VoxelMillError('goo_output', 'Output path became a symlink during export; refusing replacement')
        os.replace(stage, output)
        stage = None
        if suffix == '.ctb':
            from .ctb import convert_slices
            convert_slices(output, published, settings, cancel=cancel, progress=progress,
                           print_time_s=header_print_time_s)
            output.unlink(missing_ok=True)
            ctb_goo = None
            output = published
        process = settings['process']
        if dimensional_z is None:
            z_percent = float(process['shrink_percent_z'])
            dimensional_z = {
                'requested': z_percent, 'applied': False,
                'uncalibrated': z_percent != 0.0,
                'reason': None if z_percent == 0.0 else (
                    'Z shrinkage cannot be applied without changing layer count; '
                    'layers left unchanged'),
            }
        payload = {
            'schema_version': 1, 'source': asset, 'output': str(output),
            'format': 'ctb' if suffix == '.ctb' else 'goo', 'written': True,
            'warned': not validation.passed, 'validation': source_validation,
            'layers': layer_count, 'shape_px': [settings['printer']['pixels'][1], settings['printer']['pixels'][0]],
            'layer_blob_sha256': encoded_blob_sha256, 'verification': {
                'framing': 'pass', 'header_settings': 'pass', 'layer_timing_motion': 'pass',
                'decoded_pixels': 'pass', 'layers_compared': layer_count,
                # Binary: every decoded LCD pixel equals the source raster.
                # Grayscale AA: each decoded value is within atol=1 of expected
                # coverage (codec rounding). Topology claims use the source
                # raster thresholded at 128. Re-running analyze_layers on the
                # full panel is not repeated here.
                'pixel_comparison': 'exact' if antialias_levels <= 1 else 'atol_1_grayscale',
                'antialias_levels': antialias_levels,
                'layer_topology': 'equivalent_to_source',
                'layer_topology_reason': (
                    'decoded_pixels passed for every layer, so the source-raster layer '
                    'analysis in this report describes these pixels'
                    + (' after a 128 coverage threshold' if antialias_levels > 1 else ' exactly')),
                'standalone_check': 'voxelmill verify <file.ctb>' if suffix == '.ctb'
                                    else 'voxelmill verify <file.goo>',
            },
            'antialias': {
                'levels': antialias_levels,
                'supports': antialias_supports,
                'supports_unseparated': antialias_supports_unseparated,
            },
            'print_time_s': header_print_time_s, 'seconds': time.monotonic() - started,
            # Schedule estimate when the caller did not override PrintTime.
            # Always labeled uncalibrated: motion units and firmware delays
            # are not established.
            'estimated_print_time': estimated_print_time,
            # The same cured volume the header carries, expressed in the units
            # a user buys resin in.  Weight and cost stay null unless the resin
            # profile supplies a density or a price.
            'resin_usage': resin_usage(settings, validation.metrics.get('raster_volume_mm3')),
            # The volume above is the uncompensated raster measurement, which
            # is what the geometry contains; compensation removes the pixels
            # listed here from the bottom layers of the exposure only.
            'elephant_foot': {
                'compensation_mm': settings['process']['elephant_foot_compensation_mm'],
                'layers_requested': (int(settings['process']['elephant_foot_layers'])
                                     or int(settings['process']['bottom_layers'])),
                'layers_compensated': len(compensation),
                'pixels_removed': sum(entry['pixels_removed'] for entry in compensation),
                'per_layer': compensation,
            },
            # Slice-time dimensional compensation.  Defaults are zero and
            # uncalibrated; nonzero requests are flagged rather than treated as
            # measured resin properties.  Z shrinkage is recorded but not
            # applied — changing it honestly would change the layer count.
            'dimensional_compensation': {
                'shrink_percent_xy': float(process['shrink_percent_xy']),
                'shrink_percent_z': dimensional_z,
                'tolerance_offset_mm': float(process['tolerance_offset_mm']),
                'bottom_tolerance_offset_mm': float(process['bottom_tolerance_offset_mm']),
                'uncalibrated': bool(
                    float(process['shrink_percent_xy']) != 0.0
                    or float(process['shrink_percent_z']) != 0.0
                    or float(process['tolerance_offset_mm']) != 0.0
                    or float(process['bottom_tolerance_offset_mm']) != 0.0),
                'layers_changed': len(dimensional_layers),
                'pixels_changed': sum(entry.get('pixels_changed', 0) for entry in dimensional_layers),
                'per_layer': dimensional_layers,
            },
        }
        from .hooks import run_post_slice_hook
        # Publish the pre-hook report when a path is known so VOXELMILL_REPORT is
        # readable. The CLI may rewrite the same path after hooks are attached.
        if report_path:
            import json
            destination = Path(report_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(payload, indent=2, allow_nan=False, default=str) + '\n')
        hook = run_post_slice_hook(settings, output, report_path, cancel=cancel)
        if hook is not None:
            payload['hooks'] = {'post_slice': hook}
        return payload
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)
        if ctb_goo is not None:
            ctb_goo.unlink(missing_ok=True)
