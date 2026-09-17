"""Headless checks for the automatic orientation candidate controls."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
import manifold3d as m

pytest.importorskip('PySide6')
from PySide6 import QtCore, QtWidgets

from voxelmill.config import resolve_settings
from voxelmill.gui.window import MainWindow
from voxelmill.geometry import manifold_triangles
from voxelmill.mesh import write_stl


@pytest.fixture(scope='session')
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def candidate(rank, angle):
    return {
        'rank': rank, 'rotation_deg': [angle, 0, 90], 'total': float(rank),
        'cheap_composite': float(rank), 'ranking_basis': 'assessed',
        'assessment_status': 'complete', 'full_resolution_bounds': True,
        'score_terms': {'fit': {'value': 1, 'weight': 2, 'contribution': 2, 'counted': True}},
    }


def test_candidate_apply_is_exact_undoable_and_keeps_ranked_list(application, monkeypatch):
    window = MainWindow(resolve_settings(), None, headless=True)
    window._set_orientation_candidates([candidate(1, 12.345), candidate(2, 34.567)], selected_rank=2)
    item = window.orientation_candidates.item(1)
    selected = item.data(QtCore.Qt.ItemDataRole.UserRole)
    selected['rotation_deg'] = [34.567, 0, 90]
    window.orientation_candidates.setCurrentItem(item)
    monkeypatch.setattr(window, 'rebuild', lambda: None)
    assert window._apply_orientation_candidate()
    assert window.document.rotation_deg == selected['rotation_deg']
    assert window.document.undo_label == 'orientation'
    window.undo()
    assert window.document.rotation_deg == [0.0, 0.0, 0.0]
    window.close()


def test_production_auto_search_populates_candidates(application, tmp_path, monkeypatch):
    """The list is populated by the real placement service and survives a choice."""
    source = tmp_path / 'auto.stl'
    write_stl(source, manifold_triangles(m.Manifold.cube((8, 6, 4))))
    settings = resolve_settings(overrides={'resources': {'workers': 1}})
    from voxelmill import geometry
    production_search = geometry.auto_placement

    def bounded_search(*args, **kwargs):
        kwargs.update(directions=4, spins=2, refinements=0, finalists=2,
                      max_verifications=16, assess=False)
        return production_search(*args, **kwargs)

    monkeypatch.setattr(geometry, 'auto_placement', bounded_search)
    window = MainWindow(settings, source, headless=True)
    # Document defaults to explicit zero rotation; deliberately request the
    # automatic workflow after construction, then start that job.
    window.document.set_orientation('auto')
    window.reload()
    timer = QtCore.QElapsedTimer(); timer.start()
    while timer.elapsed() < 30000 and not window.orientation_candidates.count():
        application.processEvents(); window.jobs.wait(50); application.processEvents()
        if window.last_error:
            raise AssertionError(window.last_error)
    assert window.orientation_candidates.count() >= 2
    window.orientation_candidates.setCurrentRow(1)
    selected = window.orientation_candidates.currentItem().data(QtCore.Qt.UserRole)
    assert window._apply_orientation_candidate()
    timer.restart()
    while timer.elapsed() < 30000:
        application.processEvents(); window.jobs.wait(50); application.processEvents()
        if window.last_error:
            raise AssertionError(window.last_error)
        if (window.document.placement is not None and
                window.document.placement.rotation_deg == selected['rotation_deg']):
            break
    assert window.document.placement.rotation_deg == selected['rotation_deg']
    import numpy as np
    np.testing.assert_allclose(window.document.placement.bounds, selected['bounds'], atol=1e-10)
    assert window.orientation_candidates.count() >= 2
    window.undo()
    assert window.document.rotation_deg == 'auto'
    window.close()


def test_candidate_apply_rejects_changed_search_context(application, monkeypatch):
    window = MainWindow(resolve_settings(), None, headless=True)
    window._set_orientation_candidates([candidate(1, 12)])
    window.document.center_offset_mm[0] = 3.0
    monkeypatch.setattr(window, 'rebuild', lambda: None)
    assert not window._apply_orientation_candidate()
    assert window.orientation_candidates.count() == 0
    window.close()
