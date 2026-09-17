"""Focused contract tests for ranked automatic-orientation candidates."""

import json

import numpy as np
import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError, Placement
from voxelmill.cli import main
from voxelmill.geometry import _rank_finalists, select_orientation_candidate
from voxelmill.mesh import write_stl
from voxelmill.pipeline import prepare
from voxelmill.project import load_project


def _placement(angle, cheap=1.0):
    return Placement(
        matrix=np.eye(4).tolist(), rotation_deg=[float(angle), 2.0, 3.0],
        center_offset_mm=[0.0, 0.0], model_lift_mm=5.0,
        bounds=[[-1.0, -1.0, 0.0], [1.0, 1.0, 2.0]],
        search={
            'score': [float(cheap), 0.0, 0.0, 0.0],
            'score_terms': {'cheap': {'value': cheap, 'weight': 1.0,
                                      'contribution': cheap, 'counted': True}},
        })


def _settings():
    return resolve_settings(overrides={
        'printer': {'build_mm': [100.0, 100.0, 100.0],
                    'pixels': [100, 100], 'pixel_pitch_mm': [1.0, 1.0]},
        'process': {'layer_height_mm': 0.1},
    })


def test_ranked_candidates_record_weighted_contributions_and_total(monkeypatch):
    first, second = _placement(10, 2.0), _placement(20, 1.0)
    reports = [
        {'trapped_resin_mm3': 1.0, 'enclosed_cavity_mm3': 2.0,
         'occupancy_closed': True, 'support_accessible_fraction': .75,
         'center_of_mass_overhang': .5},
        {'trapped_resin_mm3': 0.0, 'enclosed_cavity_mm3': 0.0,
         'occupancy_closed': True, 'support_accessible_fraction': 1.0,
         'center_of_mass_overhang': 0.0},
    ]
    monkeypatch.setattr('voxelmill.geometry._attach_assessment',
                        lambda *args, **kwargs: reports.pop(0))
    best = _rank_finalists(np.zeros((1, 3, 3)), [first, second], _settings(),
                           assess=True, cancel=None, progress=None)
    record = best.search['ranked_candidates'][0]
    terms = record['score_terms']
    counted = [term['contribution'] for term in terms.values()
               if term.get('counted') and term.get('contribution') is not None]
    assert record['total'] == pytest.approx(sum(counted))
    assert record['total'] == pytest.approx(record['cheap_composite'] +
                                             sum(counted[1:]))
    assert all(term['contribution'] == pytest.approx(term['value'] * term['weight'])
               for term in terms.values() if term.get('counted'))


def test_rank_two_selection_preserves_exact_matrix_and_angles():
    best = _placement(1)
    candidate = _placement(37.123456)
    best.search = {'ranked_candidates': [
        {'rank': 1, 'rotation_deg': [1.0, 2.0, 3.0], 'matrix': np.eye(4).tolist(),
         'bounds': best.bounds, 'center_offset_mm': [0.0, 0.0], 'model_lift_mm': 5.0,
         'total': 1.0, 'cheap_composite': 1.0, 'score_terms': {},
         'assessment': {}, 'search': {'score': [1.0]}},
        {'rank': 2, 'rotation_deg': candidate.rotation_deg, 'matrix': candidate.matrix,
         'bounds': candidate.bounds, 'center_offset_mm': [0.0, 0.0], 'model_lift_mm': 5.0,
         'total': 2.0, 'cheap_composite': 2.0, 'score_terms': {},
         'assessment': {}, 'search': {'score': [2.0]}},
    ], 'selected_rank': 1, 'finalists_considered': 2,
                   'finalist_totals': [1.0, 2.0], 'weight_calibration': 'test',
                   'ranking_note': 'test'}
    selected = select_orientation_candidate(best, 2)
    assert selected.rotation_deg == candidate.rotation_deg
    assert selected.matrix == candidate.matrix
    assert selected.search['selected_rank'] == 2


@pytest.mark.parametrize('rank', [0, -1, 3, True, 1.0])
def test_selection_rejects_invalid_or_unavailable_rank(rank):
    best = _placement(1)
    best.search = {'ranked_candidates': [{'search': {}, 'rotation_deg': [0, 0, 0],
                                         'matrix': np.eye(4).tolist(), 'bounds': best.bounds,
                                         'center_offset_mm': [0, 0], 'model_lift_mm': 5,
                                         'total': 1, 'cheap_composite': 1,
                                         'score_terms': {}, 'assessment': {}}],
                   'finalists_considered': 1, 'finalist_totals': [1],
                   'weight_calibration': '', 'ranking_note': ''}
    with pytest.raises(VoxelMillError, match='Candidate rank'):
        select_orientation_candidate(best, rank)


def test_missing_assessment_ranks_after_assessed_and_singleton_is_scored(monkeypatch):
    missing, assessed = _placement(1, 0.1), _placement(2, 9.0)
    reports = [None, {'trapped_resin_mm3': 0, 'enclosed_cavity_mm3': 0,
                      'occupancy_closed': True, 'support_accessible_fraction': 1,
                      'center_of_mass_overhang': 0}]
    monkeypatch.setattr('voxelmill.geometry._attach_assessment',
                        lambda *args, **kwargs: reports.pop(0))
    best = _rank_finalists(np.zeros((1, 3, 3)), [missing, assessed], _settings(),
                           assess=True, cancel=None, progress=None)
    assert [r['assessment_status'] for r in best.search['ranked_candidates']] == ['complete', 'not_run']
    assert best.search['ranked_candidates'][1]['total'] is None

    only = _placement(3, 4.0)
    monkeypatch.setattr('voxelmill.geometry._attach_assessment',
                        lambda *args, **kwargs: {'trapped_resin_mm3': 0,
                                                  'enclosed_cavity_mm3': 0,
                                                  'occupancy_closed': True,
                                                  'support_accessible_fraction': 1,
                                                  'center_of_mass_overhang': 0})
    singleton = _rank_finalists(np.zeros((1, 3, 3)), [only], _settings(),
                                assess=True, cancel=None, progress=None)
    assert singleton.search['ranked_candidates'][0]['assessment_status'] == 'complete'
    assert singleton.search['ranked_candidates'][0]['total'] is not None


def test_pipeline_persists_selected_auto_pose_as_explicit_rotation(tmp_path, capsys):
    source = tmp_path / 'tetra.stl'
    write_stl(source, np.array([
        [[0, 0, 0], [8, 0, 0], [0, 8, 0]],
        [[0, 0, 0], [0, 8, 0], [0, 0, 8]],
        [[0, 0, 0], [0, 0, 8], [8, 0, 0]],
        [[8, 0, 0], [0, 0, 8], [0, 8, 0]],
    ], dtype=np.float32))
    project = tmp_path / 'selected.voxmil'
    report = prepare(source, _settings(), rotate='auto', candidates=2,
                     candidate_rank=2, project=project, drainage=False,
                     track_voids=False)
    state = load_project(project)
    assert report['stages']['orientation_selection']['persist_as_explicit_pose'] is True
    assert state['rotation_deg'] == report['placement']['rotation_deg']
    assert state['rotation_deg'] != 'auto'

    cli_report = tmp_path / 'reopened.json'
    assert main(['prepare', str(project), '--report', str(cli_report), '--no-drainage',
                 '--allow-unresolved']) == 2
    reopened = json.loads(cli_report.read_text())
    assert reopened['placement']['rotation_deg'] == state['rotation_deg']

    chosen_report = tmp_path / 'cli-chosen.json'
    assert main(['prepare', str(source), '--rotate', 'auto', '--candidates', '2',
                 '--candidate-rank', '2', '--report', str(chosen_report),
                 '--no-drainage', '--allow-unresolved', '--no-auto-supports',
                 '--set', 'printer.pixels=[100,100]',
                 '--set', 'printer.pixel_pitch_mm=[1,1]',
                 '--set', 'printer.build_mm=[100,100,100]']) in (0, 2)
    chosen = json.loads(chosen_report.read_text())
    assert chosen['placement']['search']['selected_rank'] == 2
    assert 'Orientation candidates' in capsys.readouterr().err


def test_production_candidate_bounds_and_terms_are_reproducible():
    import manifold3d as m
    from voxelmill.geometry import auto_placement, manifold_triangles, iter_transformed_triangles
    triangles = manifold_triangles(m.Manifold.cube((8, 6, 4)))
    pose = auto_placement(triangles, _settings(), finalists=2)
    records = pose.search['ranked_candidates']
    assert len(records) == 2
    for record in records:
        moved = np.concatenate(list(iter_transformed_triangles(triangles, record['matrix'])))
        np.testing.assert_allclose(record['bounds'],
                                   [moved.min(axis=(0, 1)), moved.max(axis=(0, 1))])
        assert record['total'] == pytest.approx(sum(
            t['contribution'] for t in record['score_terms'].values() if t['counted']))
    assert records[0]['matrix'] != records[1]['matrix']
    json.dumps(pose.search, allow_nan=False)
