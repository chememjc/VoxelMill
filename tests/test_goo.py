"""GOO framing, bounded RLE and end-to-end export checks."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from voxelmill import _native
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.goo import GooReader, GooWriter, slice_stl
from voxelmill.mesh import write_stl


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'right_temporal_bone_mars5_oriented_1_202609061627_04h20m_77ml_uvtools-good.goo'


def small_settings(*, mirror_x=False, mirror_y=False):
    """A physical-consistent small panel keeps exporter tests fast and exact."""
    return resolve_settings(
        ROOT / 'profiles/mars5-ultra.ptr', ROOT / 'profiles/sunlu-abs-like-gray.res',
        {'printer': {'build_mm': [3.2, 2.4, 10.0], 'pixels': [32, 24],
                     'pixel_pitch_mm': [0.1, 0.1], 'edge_clearance_mm': 0.0,
                     'image_mirror_x': mirror_x, 'image_mirror_y': mirror_y},
         'process': {'layer_height_mm': 0.1, 'bottom_layers': 1, 'transition_layers': 2,
                     'bottom_exposure_s': 8.0, 'normal_exposure_s': 2.0}})


def cube_triangles():
    points = np.array([
        [-0.4, -0.4, 0.0], [0.4, -0.4, 0.0], [0.4, 0.4, 0.0], [-0.4, 0.4, 0.0],
        [-0.4, -0.4, 0.4], [0.4, -0.4, 0.4], [0.4, 0.4, 0.4], [-0.4, 0.4, 0.4],
    ], dtype=np.float32)
    faces = ((0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
             (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
             (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7))
    return points[np.asarray(faces)]


def test_native_codec_round_trips_grays_and_rejects_bad_framing():
    image = np.array([[0, 255, 255, 7, 7, 10, 10, 3],
                      [3, 3, 0, 1, 2, 3, 4, 5]], dtype=np.uint8)
    blob = _native.goo_encode_layer(image)
    assert np.array_equal(_native.goo_decode_layer(np.frombuffer(blob, dtype=np.uint8), 8, 2), image)
    for damaged in (b'\x54\x0f\xf0', blob[:-1] + bytes([blob[-1] ^ 1]), b'\x55\x00\xff'):
        with pytest.raises(ValueError):
            _native.goo_decode_layer(np.frombuffer(damaged, dtype=np.uint8), 8, 2)
    with pytest.raises(ValueError, match='decoder limit'):
        _native.goo_decode_layer(np.frombuffer(b'\x55\x0f\xf0', dtype=np.uint8), 10001, 10001)
    with pytest.raises(ValueError, match='contiguous'):
        _native.goo_decode_layer(np.frombuffer(blob + blob, dtype=np.uint8)[::2], 8, 2)


def test_writer_reader_round_trip_and_reader_rejects_tampering(tmp_path):
    settings = small_settings()
    path = tmp_path / 'manual.goo'
    frame0 = np.zeros((24, 32), dtype=np.uint8)
    frame0[3:9, 4:14] = 255
    frame1 = frame0.copy()
    frame1[4, 5] = 127
    with GooWriter(path, settings, 2, previews={'small': np.zeros((116, 116, 3), np.uint8),
                                                'big': np.zeros((290, 290, 3), np.uint8)}) as writer:
        writer.add_layer(frame0, 0.1)
        writer.add_layer(frame1, 0.2)
    with GooReader(path) as reader:
        assert reader.header['per_layer_settings'] == 1
        assert reader.header['layer_count'] == 2
        assert reader.layers[0].values['exposure_time'] == pytest.approx(8.0)
        assert reader.layers[1].values['exposure_time'] == pytest.approx(6.0)
        assert np.array_equal(reader.decode(0), frame0)
        assert np.array_equal(reader.decode(1), frame1)
        offset = reader.layers[0].offset + 70 + reader.layers[0].data_length
    corrupt = bytearray(path.read_bytes())
    corrupt[offset] = 0
    broken = tmp_path / 'bad-delimiter.goo'
    broken.write_bytes(corrupt)
    with pytest.raises(VoxelMillError, match='trailing delimiter'):
        GooReader(broken)


def test_slice_stl_is_atomic_and_exact_after_mirroring(tmp_path):
    settings = small_settings(mirror_x=True, mirror_y=True)
    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    output = tmp_path / 'cube.goo'
    result = slice_stl(source, output, settings)
    assert result['written'] and not result['warned']
    assert result['verification']['decoded_pixels'] == 'pass'
    assert result['validation']['checks']['drainage_bottlenecks'] == 'pass'
    with GooReader(output) as reader:
        assert reader.header['mirror_x'] == 1 and reader.header['mirror_y'] == 1
        assert len(reader.layers) == result['layers'] == 5
        assert reader.decode(1).max() == 255

    output.write_bytes(b'previous-good-output')
    missing_motion = deepcopy(settings)
    missing_motion['printer']['motion'] = {}
    with pytest.raises(VoxelMillError, match='must supply every machine motion'):
        slice_stl(source, output, missing_motion)
    assert output.read_bytes() == b'previous-good-output'
    with pytest.raises(VoxelMillError, match='same file'):
        slice_stl(source, source, settings)

    changed_output = tmp_path / 'changed-source.goo'
    changed = False

    def mutate_source(stage, _done, _total):
        nonlocal changed
        if stage == 'goo' and not changed:
            altered = cube_triangles().copy()
            altered[..., 2] *= np.float32(0.9)
            write_stl(source, altered)
            changed = True

    with pytest.raises(VoxelMillError, match='Source STL changed'):
        slice_stl(source, changed_output, settings, progress=mutate_source)
    assert changed and not changed_output.exists()


@pytest.mark.samples
def test_immutable_reference_has_valid_chain_and_codec_samples():
    if not REFERENCE.exists():
        pytest.skip('immutable reference GOO is not available')
    probe = subprocess.run([sys.executable, str(ROOT / 'scripts/goo_probe.py'), '--all-layers', str(REFERENCE)],
                           cwd=ROOT, capture_output=True, text=True, check=False, timeout=60)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    with GooReader(REFERENCE) as reader:
        assert reader.header['layer_count'] == 1832
        assert reader.shape == (4320, 8520)
        for index in (0, 1, 5, 9, 500, 1000, 1831):
            image = reader.decode(index)
            assert _native.goo_encode_layer(image) == reader.blob(index)


# --- standalone GOO verification (plan2 Tier 0.6) -------------------------

def _write_goo(path, settings, frames, *, previews=None):
    """A GOO built straight from frames, so a test can author its topology."""
    previews = previews or {'small': np.zeros((116, 116, 3), np.uint8),
                            'big': np.zeros((290, 290, 3), np.uint8)}
    height = settings['process']['layer_height_mm']
    with GooWriter(path, settings, len(frames), previews=previews) as writer:
        for index, frame in enumerate(frames):
            writer.add_layer(np.ascontiguousarray(frame, dtype=np.uint8), (index + 1) * height)
    return path


def _solid_frames(settings, layers=4):
    width, height = settings['printer']['pixels']
    frames = []
    for _ in range(layers):
        frame = np.zeros((height, width), dtype=np.uint8)
        frame[8:16, 8:24] = 255
        frames.append(frame)
    return frames


def test_verify_goo_passes_a_clean_file_and_uses_the_files_own_geometry(tmp_path):
    from voxelmill.goo import verify_goo
    settings = small_settings()
    path = _write_goo(tmp_path / 'clean.goo', settings, _solid_frames(settings))
    payload = verify_goo(path, settings)
    assert payload['report']['passed'], payload['report']['checks']
    assert payload['layers'] == 4
    assert payload['settings_mismatches'] == {}
    assert payload['settings_from_file']['printer.pixels'] == [32, 24]
    assert payload['report']['metrics']['goo']['shape_px'] == [24, 32]
    # The claim boundary is part of the product, not a comment.
    assert any('any particular source mesh' in line for line in payload['does_not_establish'])


def test_verify_goo_finds_a_floating_island(tmp_path):
    from voxelmill.goo import verify_goo
    settings = small_settings()
    frames = _solid_frames(settings)
    frames[2][2:5, 26:30] = 255  # unattached to anything in layer 1
    path = _write_goo(tmp_path / 'island.goo', settings, frames)
    payload = verify_goo(path, settings)
    assert not payload['report']['passed']
    assert payload['report']['checks']['raster_connectivity'] == 'fail'
    codes = {d['code'] for d in payload['report']['diagnostics']}
    assert 'raster_island' in codes


def test_verify_records_caller_thresholds_that_change_the_verdict(tmp_path):
    from voxelmill.goo import verify_goo
    settings = small_settings()
    path = _write_goo(tmp_path / 'thresholds.goo', settings, _solid_frames(settings))
    before = deepcopy(settings)
    ordinary = verify_goo(path, settings)
    strict = deepcopy(settings)
    strict['support']['min_overlap_pixels'] = 129  # block overlap is 8 * 16
    strict['support']['max_span_mm'] = 0.75
    strict['repair']['min_void_volume_mm3'] = 0.02
    payload = verify_goo(path, strict, track_voids=False)
    assert ordinary['report']['checks']['overlap'] == 'pass'
    assert payload['report']['checks']['overlap'] == 'fail'
    assert payload['settings_from_caller'] == {
        'support.min_overlap_pixels': 129,
        'support.max_span_mm': 0.75,
        'repair.min_void_volume_mm3': 0.02,
    }
    assert payload['analysis_options'] == {'track_voids': False}
    assert payload['report']['checks']['enclosed_voids'] == 'not_run'
    assert payload['settings_from_file'] == ordinary['settings_from_file']
    assert payload['settings_mismatches'] == {}
    assert set(payload['settings_from_caller']).isdisjoint(payload['settings_from_file'])
    assert settings == before


def test_verify_goo_finds_an_enclosed_void(tmp_path):
    from voxelmill.goo import verify_goo
    settings = small_settings()
    width, height = settings['printer']['pixels']

    def block(hollow):
        frame = np.zeros((height, width), dtype=np.uint8)
        frame[6:18, 6:26] = 255
        if hollow:
            frame[10:14, 12:20] = 0
        return frame

    frames = [block(False), block(True), block(True), block(False)]
    path = _write_goo(tmp_path / 'void.goo', settings, frames)
    payload = verify_goo(path, settings)
    assert not payload['report']['passed']
    assert payload['report']['checks']['enclosed_voids'] == 'fail'
    assert payload['report']['metrics']['enclosed_voids']['count'] >= 1


def test_verify_goo_unmirrors_before_analysis(tmp_path):
    """Topology survives a flip; a diagnostic's coordinates do not."""
    from voxelmill.goo import verify_goo
    plain, mirrored = small_settings(), small_settings(mirror_x=True)
    frames = _solid_frames(plain)
    frames[2][2:5, 26:30] = 255
    straight = verify_goo(_write_goo(tmp_path / 'a.goo', plain, frames), plain)
    flipped_frames = [f[:, ::-1].copy() for f in frames]
    flipped = verify_goo(_write_goo(tmp_path / 'b.goo', mirrored, flipped_frames), mirrored)

    def island(payload):
        return next(d for d in payload['report']['diagnostics'] if d['code'] == 'raster_island')

    assert island(straight)['position_mm'] == pytest.approx(island(flipped)['position_mm'])


def test_verify_goo_reports_a_profile_that_does_not_describe_the_file(tmp_path):
    from voxelmill.goo import verify_goo
    settings = small_settings()
    path = _write_goo(tmp_path / 'clean.goo', settings, _solid_frames(settings))
    other = deepcopy(settings)
    other['process']['layer_height_mm'] = 0.05
    payload = verify_goo(path, other)
    assert 'process.layer_height_mm' in payload['settings_mismatches']
    assert payload['settings_from_file']['process.layer_height_mm'] == pytest.approx(0.1)
    # The file's own pitch is what was analyzed, not the profile's.
    assert payload['report']['metrics']['layer_height_mm'] == pytest.approx(0.1)
    assert any(d['code'] == 'goo_settings_differ' for d in payload['report']['diagnostics'])


def test_verify_cli_exit_codes(tmp_path, monkeypatch):
    from voxelmill.cli import main
    settings = small_settings()
    clean = _write_goo(tmp_path / 'clean.goo', settings, _solid_frames(settings))
    frames = _solid_frames(settings)
    frames[2][2:5, 26:30] = 255
    broken = _write_goo(tmp_path / 'island.goo', settings, frames)
    common = ['--printer', str(ROOT / 'profiles/mars5-ultra.ptr'),
              '--resin', str(ROOT / 'profiles/sunlu-abs-like-gray.res')]
    assert main(['verify', str(clean), *common, '--report', str(tmp_path / 'ok.json')]) == 0
    assert main(['verify', str(broken), *common, '--report', str(tmp_path / 'bad.json')]) == 2
    import json as _json
    assert _json.loads((tmp_path / 'bad.json').read_text())['command'] == 'verify'


# --- build volume as a clipping window (plan2 Tier 0.4) -------------------

def clip_settings(**overrides):
    """The small panel again, with clipping switched on unless asked otherwise."""
    settings = small_settings()
    settings['assembly']['clip_to_build_volume'] = True
    for section, values in overrides.items():
        settings[section].update(values)
    return settings


def box_triangles(size, center=(0.0, 0.0, 0.0)):
    import manifold3d as m
    from voxelmill.geometry import manifold_triangles
    solid = m.Manifold.cube(size, True).translate(center)
    return manifold_triangles(solid)


def test_envelope_overflow_measures_each_axis_independently():
    from voxelmill.geometry import envelope_fits, envelope_overflow_mm
    settings = small_settings()          # 3.2 x 2.4 x 10 mm, no edge clearance
    inside = np.array([[-1.0, -1.0, 0.0], [1.0, 1.0, 5.0]])
    assert envelope_fits(inside, settings)
    assert envelope_overflow_mm(inside, settings) == [0.0, 0.0, 0.0]
    outside = np.array([[-2.6, -1.0, 0.0], [1.0, 1.4, 12.0]])
    assert envelope_overflow_mm(outside, settings) == pytest.approx([1.0, 0.2, 2.0])
    # The larger of the two single-side excursions, not their sum.
    both = np.array([[-2.6, -1.2, 0.0], [2.1, 1.2, 1.0]])
    assert envelope_overflow_mm(both, settings)[0] == pytest.approx(1.0)


def test_slice_refuses_an_oversized_part_by_default_and_says_by_how_much(tmp_path):
    source = tmp_path / 'big.stl'
    write_stl(source, box_triangles((8.0, 1.0, 1.0), (0.0, 0.0, 0.5)))
    with pytest.raises(VoxelMillError) as caught:
        slice_stl(source, tmp_path / 'out.goo', small_settings())
    assert caught.value.code == 'goo_envelope'
    assert caught.value.details['overflow_mm'][0] == pytest.approx(2.4)
    assert 'clip_to_build_volume' in caught.value.details['enable_with']


def test_clipping_exports_and_records_exactly_what_was_removed(tmp_path):
    source = tmp_path / 'big.stl'
    write_stl(source, box_triangles((8.0, 1.0, 1.0), (0.0, 0.0, 0.5)))
    output = tmp_path / 'clipped.goo'
    # The clip diagnostic is severity error, so the export is withheld unless
    # the caller also accepts an unresolved result.  That ordering is the point.
    refused = slice_stl(source, output, clip_settings())
    assert refused['written'] is False
    assert not output.exists()
    codes = {d['code'] for d in refused['validation']['diagnostics']}
    assert 'clipped_geometry' in codes

    report = slice_stl(source, output, clip_settings(), allow_unresolved=True)
    assert report['written'] and report['warned'] and output.exists()
    clip = report['validation']['metrics']['clipped']
    assert clip['overflow_mm'][0] == pytest.approx(2.4)
    assert clip['clipped_triangles'] > 0
    assert clip['clipped_pixels'] == clip['source_pixels'] - clip['retained_pixels'] > 0
    assert clip['clipped_pixels_above_machine_z'] == 0
    # Nothing was scaled: the retained pixels are the ones on the panel.
    with GooReader(output) as reader:
        decoded = sum(int(np.count_nonzero(reader.decode(i))) for i in range(len(reader.layers)))
    assert decoded == clip['retained_pixels']


def test_clipping_drops_layers_above_the_machine_height(tmp_path):
    source = tmp_path / 'tall.stl'
    write_stl(source, box_triangles((1.0, 1.0, 20.0), (0.0, 0.0, 10.0)))
    output = tmp_path / 'tall.goo'
    report = slice_stl(source, output, clip_settings(), allow_unresolved=True)
    clip = report['validation']['metrics']['clipped']
    assert report['layers'] == 100                      # 10 mm at 0.1 mm
    assert clip['source_layers'] == 200
    assert clip['clipped_layers'] == 100
    assert clip['clipped_pixels_above_machine_z'] > 0


def test_a_straddling_mesh_matches_the_same_mesh_moved_fully_inside(tmp_path):
    """Clipping removes pixels; it never moves or resamples the ones it keeps."""
    settings = clip_settings()
    inside = tmp_path / 'inside.stl'
    write_stl(inside, box_triangles((1.0, 1.0, 0.4), (0.0, 0.0, 0.2)))
    straddling = tmp_path / 'straddling.stl'
    # The same box shifted so half of it hangs off the -X edge of a 3.2 mm panel.
    write_stl(straddling, box_triangles((1.0, 1.0, 0.4), (-1.6, 0.0, 0.2)))
    assert slice_stl(inside, tmp_path / 'a.goo', settings, allow_unresolved=True)['written']
    assert slice_stl(straddling, tmp_path / 'b.goo', settings, allow_unresolved=True)['written']
    with GooReader(tmp_path / 'a.goo') as centered, GooReader(tmp_path / 'b.goo') as cut:
        assert len(centered.layers) == len(cut.layers)
        compared = 0
        for index in range(len(centered.layers)):
            whole, clipped = centered.decode(index), cut.decode(index)
            if not whole.any():
                continue
            # The centered box occupies columns 11..20; the straddling box keeps
            # its own right half at columns 0..4.  Those are the same five
            # columns of the same geometry, so the pixels must be identical.
            assert np.array_equal(clipped[:, 0:5], whole[:, 11:16])
            assert not clipped[:, 5:].any()
            assert int(np.count_nonzero(clipped)) * 2 == int(np.count_nonzero(whole))
            compared += 1
        assert compared >= 4


def test_an_unverified_lcd_orientation_is_reported_on_every_export(tmp_path):
    """A mirrored threaded part is scrap, so the uncertainty ships with the file."""
    from voxelmill.goo import REFERENCE_MIRRORS, orientation_warning
    settings = small_settings()
    assert settings['printer']['image_mirror_verified'] is False
    warning = orientation_warning(settings)
    assert warning is not None and warning.severity == 'warning'
    assert warning.details['reference_files_disagree'] == REFERENCE_MIRRORS

    source = tmp_path / 'cube.stl'
    write_stl(source, cube_triangles())
    report = slice_stl(source, tmp_path / 'out.goo', settings)
    codes = {d['code'] for d in report['validation']['diagnostics']}
    assert 'goo_orientation_unverified' in codes
    assert report['validation']['checks']['image_orientation'] == 'warn'
    # A warning must not block an export it cannot judge.
    assert report['written'] and not report['warned']

    verified = deepcopy(settings)
    verified['printer']['image_mirror_verified'] = True
    assert orientation_warning(verified) is None
    report = slice_stl(source, tmp_path / 'verified.goo', verified)
    codes = {d['code'] for d in report['validation']['diagnostics']}
    assert 'goo_orientation_unverified' not in codes
    assert report['validation']['checks']['image_orientation'] == 'pass'


def test_verify_crops_to_the_exposed_pixels_without_changing_the_answer(tmp_path):
    """The crop is an optimization, so it has to be provably invisible."""
    from voxelmill.goo import occupied_crop, verify_goo
    settings = small_settings()
    frames = _solid_frames(settings)
    frames[2][2:5, 26:30] = 255                       # an island, off to one side
    block = np.zeros((24, 32), np.uint8)
    block[6:18, 6:26] = 255
    hollow = block.copy()
    hollow[10:14, 12:20] = 0
    frames = [block, hollow, hollow, frames[2], block]
    path = _write_goo(tmp_path / 'mixed.goo', settings, frames)

    with GooReader(path) as reader:
        crop = occupied_crop(reader)
    assert crop is not None
    r0, r1, c0, c1 = crop
    assert (r1 - r0) < 24 and (c1 - c0) < 32          # it really did crop
    # Every exposed pixel is inside, with a border to spare on each side.
    for frame in frames:
        rows, cols = np.nonzero(frame)
        assert rows.min() > r0 and rows.max() < r1 - 1
        assert cols.min() > c0 and cols.max() < c1 - 1

    cropped = verify_goo(path, settings)
    whole = verify_goo(path, settings, full_panel=True)
    assert cropped['report']['checks'] == whole['report']['checks']
    assert cropped['report']['passed'] == whole['report']['passed']
    for field in ('exposed_pixels', 'raster_volume_mm3', 'island_components',
                  'growth_violation_pixels', 'layer_count', 'nonempty_layers'):
        assert cropped['report']['metrics'][field] == whole['report']['metrics'][field], field
    assert (cropped['report']['metrics']['enclosed_voids']['count']
            == whole['report']['metrics']['enclosed_voids']['count'])

    def positions(payload):
        return sorted((d['code'], d['layer'], tuple(d['position_mm'] or ()))
                      for d in payload['report']['diagnostics'])

    # Coordinates are in plate millimeters either way; the crop moves the
    # window's origin, not the lattice.
    assert positions(cropped) == positions(whole)
    assert cropped['report']['metrics']['goo']['shape_px'] == [24, 32]
    assert cropped['report']['metrics']['goo']['analysis_window_px'] == [c1 - c0, r1 - r0]
    assert cropped['report']['metrics']['goo']['decode_passes'] == 2
    assert whole['report']['metrics']['goo']['decode_passes'] == 1


def test_verify_falls_back_to_the_panel_for_a_file_that_exposes_nothing(tmp_path):
    from voxelmill.goo import occupied_crop, verify_goo
    settings = small_settings()
    blank = [np.zeros((24, 32), np.uint8) for _ in range(3)]
    path = _write_goo(tmp_path / 'blank.goo', settings, blank)
    with GooReader(path) as reader:
        assert occupied_crop(reader) is None
    payload = verify_goo(path, settings)
    assert payload['report']['metrics']['goo']['analysis_window_px'] == [32, 24]
    assert not payload['report']['passed']
    assert any(d['code'] == 'empty_raster' for d in payload['report']['diagnostics'])


def test_float32_header_rounding_is_not_a_settings_mismatch(tmp_path):
    """Every header float is float32; a round trip must not read as a mismatch.

    The Mars 5 Ultra's own 77.76 mm build depth comes back as 77.76000213623047,
    which is 2.1e-6 away and was reported against the profile that wrote it.
    A field that cries wolf on every export is a field people learn to ignore.
    """
    from voxelmill.goo import verify_goo
    settings = resolve_settings(ROOT / 'profiles/mars5-ultra.ptr',
                                ROOT / 'profiles/sunlu-abs-like-gray.res',
                                {'printer': {'build_mm': [3.2, 2.4, 10.0], 'pixels': [32, 24],
                                             'pixel_pitch_mm': [0.1, 0.1], 'edge_clearance_mm': 0.0},
                                 'process': {'layer_height_mm': 0.1, 'bottom_layers': 1,
                                             'transition_layers': 2}})
    path = _write_goo(tmp_path / 'exact.goo', settings, _solid_frames(settings))
    assert verify_goo(path, settings)['settings_mismatches'] == {}

    # A real difference is still reported: the tolerance is float32 quantisation,
    # not a license to ignore a different machine.
    other = deepcopy(settings)
    other['printer']['build_mm'] = [3.2, 2.4, 12.0]
    assert 'printer.build_mm' in verify_goo(path, other)['settings_mismatches']


def _placed_frame(rng, kind):
    w, h = int(rng.integers(1, 60)), int(rng.integers(1, 40))
    fw, fh = int(rng.integers(w, w + 30)), int(rng.integers(h, h + 30))
    r, c = int(rng.integers(0, fh - h + 1)), int(rng.integers(0, fw - w + 1))
    if kind == 0:
        crop = (rng.random((h, w)) < 0.5).astype(np.uint8) * 255   # binary exposure
    elif kind == 1:
        crop = rng.integers(0, 256, (h, w), dtype=np.uint8)          # grayscale, delta chunks
    else:
        crop = np.zeros((h, w), dtype=np.uint8)                     # a dark layer
    frame = np.zeros((fh, fw), dtype=np.uint8)
    frame[r:r + h, c:c + w] = crop
    return crop, r, c, fw, fh, frame


def test_placed_encoding_is_byte_identical_to_the_full_frame():
    rng = np.random.default_rng(3)
    for trial in range(200):
        crop, r, c, fw, fh, frame = _placed_frame(rng, trial % 3)
        assert _native.goo_encode_placed(crop, r, c, fw, fh) == _native.goo_encode_layer(frame)


def test_placed_verification_counts_every_corrupted_pixel():
    """It decodes the file's own chunks: a pixel wrong anywhere on the panel counts."""
    rng = np.random.default_rng(4)
    for trial in range(200):
        crop, r, c, fw, fh, frame = _placed_frame(rng, trial % 3)
        blob = np.frombuffer(_native.goo_encode_layer(frame), dtype=np.uint8)
        assert _native.goo_verify_placed(blob, crop, r, c, fw, fh) == 0
        corrupt = frame.copy()
        where = rng.integers(0, fw * fh, int(rng.integers(1, 20)))
        corrupt.reshape(-1)[where] = rng.integers(0, 256, len(where), dtype=np.uint8)
        blob = np.frombuffer(_native.goo_encode_layer(corrupt), dtype=np.uint8)
        assert (_native.goo_verify_placed(blob, crop, r, c, fw, fh)
                == int(np.count_nonzero(corrupt != frame)))
        difference = np.abs(corrupt.astype(np.int16) - frame.astype(np.int16))
        for atol in (1, 7):
            assert (_native.goo_verify_placed(blob, crop, r, c, fw, fh, atol)
                    == int(np.count_nonzero(difference > atol)))


def test_placed_verification_rejects_a_damaged_blob():
    crop = np.full((3, 4), 255, dtype=np.uint8)
    blob = bytearray(_native.goo_encode_placed(crop, 2, 2, 10, 8))
    blob[1] ^= 0x01                                       # breaks the checksum
    with pytest.raises(ValueError, match='checksum'):
        _native.goo_verify_placed(np.frombuffer(bytes(blob), dtype=np.uint8), crop, 2, 2, 10, 8)
