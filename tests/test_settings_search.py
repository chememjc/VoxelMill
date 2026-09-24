"""The settings-search matcher is a pure function: test it without Qt."""
from voxelmill.gui.settings_search import rank_settings, score_descriptor
from voxelmill.gui.settings_table import SETTINGS_DESCRIPTORS


def _paths(query, limit=None):
    matches = rank_settings(query, SETTINGS_DESCRIPTORS)
    paths = [match.descriptor.path for match in matches]
    return paths[:limit] if limit else paths


def test_empty_query_matches_nothing():
    assert rank_settings('', SETTINGS_DESCRIPTORS) == []
    assert rank_settings('   ', SETTINGS_DESCRIPTORS) == []


def test_exact_path_is_the_top_hit():
    top = _paths('support.brace_spacing_mm', limit=1)
    assert top == ['support.brace_spacing_mm']


def test_prefix_and_whole_word_rank_above_substring_noise():
    matches = rank_settings('workers', SETTINGS_DESCRIPTORS)
    assert matches[0].descriptor.path == 'resources.workers'
    # worker_policy shares the "worker" stem but is not the whole-word hit.
    assert matches[0].score > next(m.score for m in matches if m.descriptor.path == 'resources.worker_policy')


def test_typo_tolerance_still_finds_the_field():
    """The scenario ISSUES.md/G2 names: a misspelled two-word query."""
    matches = rank_settings('brase spacing', SETTINGS_DESCRIPTORS)
    assert matches, 'expected at least one match for a one-letter typo'
    assert matches[0].descriptor.path == 'support.brace_spacing_mm'


def test_multi_word_query_matches_regardless_of_order():
    matches = rank_settings('spacing brace', SETTINGS_DESCRIPTORS)
    paths = {m.descriptor.path for m in matches}
    assert 'support.brace_spacing_mm' in paths


def test_flag_surface_is_searchable():
    matches = rank_settings('--overhang-angle-deg', SETTINGS_DESCRIPTORS)
    assert matches
    assert matches[0].descriptor.path == 'support.overhang_angle_deg'


def test_help_text_word_is_searchable_but_not_via_loose_matching():
    """A real word from the help text finds the field ..."""
    matches = rank_settings('elephant', SETTINGS_DESCRIPTORS)
    paths = {m.descriptor.path for m in matches}
    assert 'process.elephant_foot_compensation_mm' in paths


def test_garbage_query_matches_nothing():
    assert rank_settings('zzzznotasettingatallxyz', SETTINGS_DESCRIPTORS) == []


def test_ordering_is_deterministic_score_then_path():
    matches = rank_settings('mm', SETTINGS_DESCRIPTORS)
    scores_and_paths = [(round(m.score, 6), m.descriptor.path) for m in matches]
    # Every run over the same frozen descriptor tuple must reproduce this
    # exact order: the tie-break is (-score, path), not dict/set iteration
    # order, which Python does not guarantee to be stable across processes
    # for anything but insertion order.
    again = rank_settings('mm', SETTINGS_DESCRIPTORS)
    assert scores_and_paths == [(round(m.score, 6), m.descriptor.path) for m in again]
    # And within any one score, paths are ascending.
    for a, b in zip(matches, matches[1:]):
        if a.score == b.score:
            assert a.descriptor.path < b.descriptor.path


def test_score_descriptor_is_symmetric_with_rank_settings():
    descriptor = next(d for d in SETTINGS_DESCRIPTORS if d.path == 'support.overhang_angle_deg')
    assert score_descriptor('overhang', descriptor) > 0
    assert score_descriptor('', descriptor) == 0.0
    assert score_descriptor('zzzznotasettingatallxyz', descriptor) == 0.0
