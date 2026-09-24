"""Classic unencrypted CTB v3 framing, conversion and CLI coverage."""
from pathlib import Path
import struct

import numpy as np
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.ctb import CtbReader, CtbWriter, decode_rle, encode_rle, verify_ctb, convert_slices
from voxelmill.goo import GooReader, GooWriter

ROOT = Path(__file__).resolve().parents[1]


def settings():
    return resolve_settings(
        ROOT / 'profiles/mars5-ultra.ptr', ROOT / 'profiles/sunlu-abs-like-gray.res',
        {'printer': {'build_mm': [3.2, 2.4, 10.0], 'pixels': [32, 24],
                     'pixel_pitch_mm': [0.1, 0.1], 'edge_clearance_mm': 0.0,
                     'image_mirror_x': False, 'image_mirror_y': False},
         'process': {'layer_height_mm': 0.1, 'bottom_layers': 1,
                     'transition_layers': 0, 'bottom_exposure_s': 8.0,
                     'normal_exposure_s': 2.0}})


def frames():
    result = []
    for index in range(3):
        image = np.zeros((24, 32), np.uint8)
        image[8:16, 7:25] = 255
        if index == 1: image[10:12, 10:14] = 127
        result.append(image)
    return result


def write_ctb(path, config=None):
    config = config or settings()
    with CtbWriter(path, config, 3) as writer:
        for index, image in enumerate(frames()): writer.add_layer(image, (index + 1) * .1)
    return path


def test_ctb_rle_round_trip_and_bounded_failures():
    image = np.array([[0, 0, 255, 255, 255, 7, 7, 10],
                      [3, 3, 0, 1, 2, 3, 4, 5]], np.uint8)
    decoded = decode_rle(encode_rle(np.ascontiguousarray(image)), 8, 2)
    # CTB stores 7-bit grayscale. A source value of 1 shifts to color 0 and
    # round-trips as empty; every other nonzero color comes back as odd.
    color = image >> 1
    expected = np.where(color == 0, 0, (color << 1) | 1).astype(np.uint8)
    assert np.array_equal(decoded, expected)
    for blob in (b'\x80', b'\x80\x01', b'\x80\xf0', b'\x80\x7f'):
        with pytest.raises(VoxelMillError): decode_rle(blob, 2, 1)


def test_ctb_writer_reader_and_deep_verifier(tmp_path):
    path = write_ctb(tmp_path / 'clean.ctb')
    with CtbReader(path) as reader:
        assert reader.header['version'] == 3
        assert reader.header['encryption_key'] == 0
        assert reader.shape == (24, 32)
        assert reader.layers[0].exposure_s == pytest.approx(8)
        assert np.array_equal(reader.decode(0), frames()[0])
    payload = verify_ctb(path, settings(), track_voids=False)
    assert payload['report']['checks']['raster_connectivity'] == 'pass'
    assert payload['report']['checks']['enclosed_voids'] == 'not_run'
    tracked = verify_ctb(path, settings(), track_voids=True)
    assert tracked['report']['passed']
    assert tracked['report']['metrics']['ctb']['version'] == 3


def test_reader_rejects_versions_encryption_and_bad_offsets(tmp_path):
    path = write_ctb(tmp_path / 'base.ctb')
    original = bytearray(path.read_bytes())
    for name, offset, value, code in (('v4', 4, 4, 'I'), ('encrypted', 100, 7, 'I'),
                                      ('offset', 64, len(original), 'I')):
        damaged = original.copy(); struct.pack_into('<' + code, damaged, offset, value)
        candidate = tmp_path / f'{name}.ctb'; candidate.write_bytes(damaged)
        with pytest.raises(VoxelMillError): CtbReader(candidate)


def test_writer_is_atomic_on_failure(tmp_path):
    target = tmp_path / 'keep.ctb'; target.write_bytes(b'previous')
    with pytest.raises(VoxelMillError, match='layer count'):
        with CtbWriter(target, settings(), 2) as writer:
            writer.add_layer(frames()[0], .1)
    assert target.read_bytes() == b'previous'


def test_goo_ctb_conversion_preserves_pixels_and_refuses_alias(tmp_path):
    config = settings(); goo = tmp_path / 'source.goo'
    with GooWriter(goo, config, 3) as writer:
        for index, image in enumerate(frames()): writer.add_layer(image, (index + 1) * .1)
    ctb = tmp_path / 'converted.ctb'
    report = convert_slices(goo, ctb, config)
    assert report['verification']['report']['passed']
    with GooReader(goo) as left, CtbReader(ctb) as right:
        for index in range(3): assert np.array_equal(left.decode(index), right.decode(index))
    with pytest.raises(VoxelMillError, match='different files'): convert_slices(goo, goo, config)


def test_slice_stl_writes_verified_ctb(tmp_path):
    from voxelmill.goo import slice_stl
    from voxelmill.mesh import write_stl
    points = np.array([
        [-0.4, -0.4, 0.0], [0.4, -0.4, 0.0], [0.4, 0.4, 0.0], [-0.4, 0.4, 0.0],
        [-0.4, -0.4, 0.4], [0.4, -0.4, 0.4], [0.4, 0.4, 0.4], [-0.4, 0.4, 0.4],
    ], dtype=np.float32)
    faces = ((0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
             (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
             (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7))
    source = tmp_path / 'cube.stl'
    write_stl(source, points[np.asarray(faces)])
    output = tmp_path / 'cube.ctb'
    result = slice_stl(source, output, settings())
    assert result['written'] and result['format'] == 'ctb'
    assert output.is_file()
    with CtbReader(output) as reader:
        assert reader.header['version'] == 3
        assert len(reader.layers) == result['layers']
        assert reader.decode(0).max() == 255


def test_cli_exposes_ctb_commands_and_acceleration_flags():
    from voxelmill.cli import build_parser, _overrides
    parser = build_parser()
    args = parser.parse_args(['verify', 'part.ctb', '--acceleration', 'cuda', '--cuda-device', '2'])
    assert _overrides(args)['resources'] == {'acceleration': 'cuda', 'cuda_device': 2}
    assert parser.parse_args(['ctb-info', 'part.ctb']).func.__name__ == 'cmd_info'
    assert parser.parse_args(['convert', 'a.goo', 'b.ctb']).func.__name__ == 'cmd_convert'
