"""Bit-identical checks for the native row-RLE kernels against dense NumPy/scipy."""
import numpy as np
import pytest
from scipy import ndimage as ndi
from voxelmill import _native
from voxelmill.validation import CROSS, block_any, _border_flags, _island_extent


def dense_extract(mask, want):
    """Row-wise runs where (mask != 0) == want; half-open [start, end)."""
    target = bool(want)
    h, w = mask.shape
    starts, ends, offsets = [], [], [0]
    for r in range(h):
        row = mask[r]
        c = 0
        while c < w:
            while c < w and bool(row[c] != 0) != target:
                c += 1
            if c >= w:
                break
            begin = c
            while c < w and bool(row[c] != 0) == target:
                c += 1
            starts.append(begin)
            ends.append(c)
        offsets.append(len(starts))
    return (np.asarray(starts, np.int32), np.asarray(ends, np.int32),
            np.asarray(offsets, np.int32))


def dense_pairs(prev_labels, labels, chunk_rows=64):
    """VoidForest.merge's (before, after) keys: 64-row chunks, within-chunk unique."""
    total = int(labels.max())
    span = total + 1
    out = []
    for row in range(0, labels.shape[0], chunk_rows):
        before = prev_labels[row:row + chunk_rows].ravel()
        after = labels[row:row + chunk_rows].ravel()
        both = (before > 0) & (after > 0)
        if both.any():
            keyed = before[both].astype(np.int64) * span + after[both]
            for key in np.unique(keyed):
                out.append((int(key // span), int(key % span)))
    if not out:
        return np.empty((0, 2), dtype=np.int32)
    return np.asarray(out, dtype=np.int32)


def dense_overlap(labels, count, previous):
    """Chunked bincount at validation.py:399-402."""
    overlap = np.zeros(count + 1, dtype=np.int64)
    for row in range(0, labels.shape[0], 64):
        overlap += np.bincount(labels[row:row + 64][previous[row:row + 64]],
                               minlength=count + 1)
    return overlap


def as_runs(mask, want=1):
    return _native.extract_runs(np.ascontiguousarray(mask), want)


def assert_runs_equal(got, expected):
    gs, ge, go = got
    es, ee, eo = expected
    np.testing.assert_array_equal(gs, es)
    np.testing.assert_array_equal(ge, ee)
    np.testing.assert_array_equal(go, eo)


def random_masks(rng, n=220):
    """Boolean fields at various densities plus a few panel-shaped cases."""
    cases = []
    for _ in range(n):
        h = int(rng.integers(1, 48))
        w = int(rng.integers(1, 64))
        p = float(rng.choice([0.0, 0.05, 0.15, 0.35, 0.5, 0.85, 0.95, 1.0]))
        cases.append(rng.random((h, w)) < p)
    # Real-shaped panels (~200x300), sparse and mid density.
    for p in (0.02, 0.12, 0.4):
        cases.append(rng.random((200, 300)) < p)
        cases.append(rng.random((180, 240)) < p)
    # Structured edge cases.
    cases.append(np.zeros((1, 1), bool))
    cases.append(np.ones((1, 1), bool))
    cases.append(np.zeros((7, 11), bool))
    cases.append(np.ones((7, 11), bool))
    single = np.zeros((9, 13), bool)
    single[4, 6] = True
    cases.append(single)
    checker = np.indices((16, 20)).sum(axis=0) % 2 == 0
    cases.append(checker)
    stripe = np.zeros((100, 40), bool)
    stripe[:, ::3] = True
    cases.append(stripe)
    return cases


# --- extraction / complement / difference ---------------------------------


def test_extract_runs_matches_row_scan_on_random_fields():
    rng = np.random.default_rng(17)
    for mask in random_masks(rng):
        for want in (0, 1):
            assert_runs_equal(as_runs(mask, want), dense_extract(mask, want))


def test_complement_runs_matches_extract_want_zero():
    rng = np.random.default_rng(23)
    for mask in random_masks(rng, n=80):
        h, w = mask.shape
        s, e, o = as_runs(mask, 1)
        assert_runs_equal(_native.complement_runs(s, e, o, w), as_runs(mask, 0))
        assert_runs_equal(_native.complement_runs(s, e, o, w), dense_extract(mask, 0))


def test_run_difference_matches_masked_extract():
    rng = np.random.default_rng(29)
    for _ in range(80):
        h, w = int(rng.integers(2, 40)), int(rng.integers(2, 50))
        a = rng.random((h, w)) < 0.4
        b = rng.random((h, w)) < 0.4
        expected = dense_extract(a & ~b, 1)
        sa, ea, oa = as_runs(a, 1)
        sb, eb, ob = as_runs(b, 1)
        assert_runs_equal(_native.run_difference(sa, ea, oa, sb, eb, ob), expected)


def test_extract_rejects_noncontiguous_and_bad_want():
    mask = np.ones((4, 6), dtype=bool)
    view = mask[:, ::2]
    assert not view.flags['C_CONTIGUOUS']
    with pytest.raises(ValueError, match='C-contiguous|contiguous'):
        _native.extract_runs(view, 1)
    with pytest.raises(ValueError, match='want'):
        _native.extract_runs(mask, 2)
    with pytest.raises(ValueError, match='want'):
        _native.extract_runs(mask, -1)


def test_extract_empty_full_single_checkerboard():
    empty = np.zeros((5, 8), bool)
    full = np.ones((5, 8), bool)
    single = np.zeros((5, 8), bool)
    single[2, 3] = True
    checker = np.indices((12, 14)).sum(axis=0) % 2 == 0
    for mask in (empty, full, single, checker):
        for want in (0, 1):
            assert_runs_equal(as_runs(mask, want), dense_extract(mask, want))
    # Checkerboard is dense enough that mean px/run can sit under the floor.
    s, _, _ = as_runs(checker, 1)
    assert checker.size / max(1, len(s)) < _native.RUN_DENSITY_FLOOR


# --- CCL / scatter / counts / border / extent / area ----------------------


def test_run_ccl_matches_scipy_label_after_scatter():
    rng = np.random.default_rng(31)
    for mask in random_masks(rng, n=120):
        expected, count = ndi.label(mask, CROSS)
        s, e, o = as_runs(mask, 1)
        labels, n = _native.run_ccl(s, e, o, mask.shape[0])
        assert n == count
        if count == 0:
            assert len(labels) == 0
            continue
        scattered = _native.run_scatter(s, e, o, labels, mask.shape[1])
        np.testing.assert_array_equal(scattered, expected)


def test_run_counts_matches_bincount_including_bin_zero():
    rng = np.random.default_rng(37)
    for mask in random_masks(rng, n=100):
        dense, count = ndi.label(mask, CROSS)
        s, e, o = as_runs(mask, 1)
        labels, n = _native.run_ccl(s, e, o, mask.shape[0])
        assert n == count
        panel = mask.shape[0] * mask.shape[1]
        got = _native.run_counts(s, e, labels, n, panel)
        expected = np.bincount(dense.ravel(), minlength=count + 1)
        np.testing.assert_array_equal(got, expected)


def test_run_border_matches_dense_border_scatter():
    rng = np.random.default_rng(41)
    for mask in random_masks(rng, n=100):
        dense, count = ndi.label(mask, CROSS)
        s, e, o = as_runs(mask, 1)
        labels, n = _native.run_ccl(s, e, o, mask.shape[0])
        got = _native.run_border(s, e, o, labels, n, mask.shape[1])
        expected = _border_flags(dense, count + 1)
        np.testing.assert_array_equal(got, expected)


def test_run_area_matches_mask_sum():
    rng = np.random.default_rng(43)
    for mask in random_masks(rng, n=80):
        s, e, _ = as_runs(mask, 1)
        assert _native.run_area(s, e) == int(mask.sum())


def test_run_extent_matches_island_extent():
    rng = np.random.default_rng(47)
    for mask in random_masks(rng, n=60):
        dense, count = ndi.label(mask, CROSS)
        if count == 0:
            continue
        s, e, o = as_runs(mask, 1)
        labels, n = _native.run_ccl(s, e, o, mask.shape[0])
        for component in range(1, n + 1):
            row, col, pixels = _native.run_extent(s, e, o, labels, n, component)
            er, ec, ep = _island_extent(dense, component)
            assert (row, col, pixels) == (er, ec, ep)


# --- overlap / pairs / block_any ------------------------------------------


def test_run_overlap_matches_chunked_bincount():
    rng = np.random.default_rng(53)
    for _ in range(80):
        h, w = int(rng.integers(2, 90)), int(rng.integers(2, 70))
        cur = rng.random((h, w)) < 0.35
        prev = rng.random((h, w)) < 0.35
        dense, count = ndi.label(cur, CROSS)
        expected = dense_overlap(dense, count, prev)
        s, e, o = as_runs(cur, 1)
        labels, n = _native.run_ccl(s, e, o, h)
        ps, pe, po = as_runs(prev, 1)
        got = _native.run_overlap(s, e, o, labels, n, ps, pe, po)
        np.testing.assert_array_equal(got, expected)


def test_run_pairs_matches_voidforest_merge_keys():
    rng = np.random.default_rng(59)
    # Tall panels so cross-chunk duplicates appear.
    shapes = [(200, 61), (130, 40), (96, 30), (64, 20), (40, 25)]
    for h, w in shapes:
        for _ in range(12):
            cur = rng.random((h, w)) < 0.55
            prev = rng.random((h, w)) < 0.55
            # Force a few full-height corridors so the same pair hits many chunks.
            cur[:, 3:6] = False
            prev[:, 3:6] = False
            cur_dense, _ = ndi.label(~cur, CROSS)   # void labels, like VoidForest
            prev_dense, _ = ndi.label(~prev, CROSS)
            expected = dense_pairs(prev_dense, cur_dense, 64)
            cs, ce, co = as_runs(~cur, 1)
            ps, pe, po = as_runs(~prev, 1)
            cl, cn = _native.run_ccl(cs, ce, co, h)
            pl, pn = _native.run_ccl(ps, pe, po, h)
            # Scatter must agree with scipy before pair order is meaningful.
            if cn:
                np.testing.assert_array_equal(
                    _native.run_scatter(cs, ce, co, cl, w), cur_dense)
            if pn:
                np.testing.assert_array_equal(
                    _native.run_scatter(ps, pe, po, pl, w), prev_dense)
            got = _native.run_pairs(cs, ce, co, cl, ps, pe, po, pl, 64)
            np.testing.assert_array_equal(got, expected)


def test_run_pairs_preserves_cross_chunk_duplicates():
    """Same (before, after) in several 64-row chunks must be emitted once per chunk."""
    h, w = 200, 40
    cur = np.ones((h, w), bool)
    prev = np.ones((h, w), bool)
    cur[:, 10:13] = False
    prev[:, 10:13] = False
    cur_dense, _ = ndi.label(~cur, CROSS)
    prev_dense, _ = ndi.label(~prev, CROSS)
    expected = dense_pairs(prev_dense, cur_dense, 64)
    assert len(expected) > len({tuple(p) for p in expected}), \
        'fixture must produce cross-chunk duplicate pairs'
    cs, ce, co = as_runs(~cur, 1)
    ps, pe, po = as_runs(~prev, 1)
    cl, _ = _native.run_ccl(cs, ce, co, h)
    pl, _ = _native.run_ccl(ps, pe, po, h)
    got = _native.run_pairs(cs, ce, co, cl, ps, pe, po, pl, 64)
    np.testing.assert_array_equal(got, expected)


def test_run_block_any_matches_validation():
    rng = np.random.default_rng(61)
    for mask in random_masks(rng, n=60):
        s, e, o = as_runs(mask, 1)
        for factor in (1, 2, 3, 8):
            if factor == 1:
                # Native always materialises; block_any returns the input when factor<=1.
                got = _native.run_block_any(s, e, o, mask.shape[1], factor)
                expected = block_any(np.asarray(mask, bool), factor)
                # factor==1: shapes equal; values equal to occupancy.
                np.testing.assert_array_equal(got, expected)
            else:
                got = _native.run_block_any(s, e, o, mask.shape[1], factor)
                expected = block_any(np.asarray(mask, bool), factor)
                np.testing.assert_array_equal(got, expected)


def test_constants_exported():
    assert _native.RUN_TABLE_CAP == 1 << 24
    assert _native.RUN_DENSITY_FLOOR == 6
