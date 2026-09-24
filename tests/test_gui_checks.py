"""Check-print menu, layer-issue wiring, gizmo preview/deselect and motion mode.

These exercise the pieces window.py and services.py own: the print-checks menu
and its background job, feeding a report's diagnostics into the layer view's
issue navigation, live gizmo preview versus the committed pose, and the
relative/absolute motion mode toggle. The underlying analyses (islands,
drainage, peel, overhangs) are covered by their own modules' tests; here the
point is only that the editor wires them together correctly.
"""
from __future__ import annotations

import json
import os
import sys

import pytest


pytestmark = pytest.mark.gui

sys.path.insert(0, os.path.dirname(__file__))

import manifold3d as m  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

from voxelmill.contracts import CancellationToken, no_progress  # noqa: E402
from voxelmill.geometry import manifold_triangles  # noqa: E402
from voxelmill.gui import services  # noqa: E402
from voxelmill.gui.appprefs import load_preferences  # noqa: E402
from voxelmill.gui.window import MainWindow, PRINT_CHECK_LABELS  # noqa: E402
from test_gui import drain, small_settings, write_stl  # noqa: E402


@pytest.fixture
def isolated_prefs(tmp_path, monkeypatch):
    """Redirect editor preferences to a scratch directory.

    ``_set_motion_mode`` writes through ``save_preferences``; without this a
    test would overwrite the real developer's ``editor.json``.
    """
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    return tmp_path


@pytest.fixture
def cube(tmp_path):
    path = tmp_path / 'cube.stl'
    write_stl(path, manifold_triangles(m.Manifold.cube((10, 10, 10), True).translate((0, 0, 6))))
    return path


@pytest.fixture
def window(application, cube, isolated_prefs):
    editor = MainWindow(small_settings(), cube, headless=True)
    drain(editor, application)
    yield editor
    editor.close()


# ---- Check print menu -----------------------------------------------------

def test_check_print_menu_offers_every_check_plus_all_and_some(window):
    for key, _label in PRINT_CHECK_LABELS:
        assert f'check_print_{key}' in window.actions_map
    assert 'check_print_all' in window.actions_map
    assert 'check_print_some' in window.actions_map


def test_run_print_checks_says_why_it_cannot_run_without_an_assembly(application, isolated_prefs):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.run_print_checks_now({'islands'}, 'islands') is None
    assert 'no assembly yet' in window.statusBar().currentMessage()
    window.close()


def test_run_print_checks_with_a_single_check_name_produces_that_checks_key(window):
    union = window.document.derived.union
    expectations = (
        ({'islands'}, 'raster_connectivity'),
        ({'enclosed_voids'}, 'enclosed_voids'),
        ({'suction_cups'}, 'peel_risk'),
        ({'drainage'}, 'drainage_bottlenecks'),
    )
    for checks, key in expectations:
        result = services.run_print_checks(window.document, union, CancellationToken(),
                                            no_progress, checks=checks)
        assert key in result['checks'], (checks, result['checks'])
        assert result['checks'][key] in ('pass', 'warn', 'fail', 'not_run')
        assert isinstance(result['diagnostics'], list)
        assert all(isinstance(d, dict) for d in result['diagnostics'])
        assert isinstance(result['passed'], bool)


def test_run_print_checks_overhangs_uses_model_triangles_and_routed_contacts(window):
    union = window.document.derived.union
    result = services.run_print_checks(window.document, union, CancellationToken(),
                                        no_progress, checks={'overhangs'})
    assert 'unsupported_overhangs' in result['checks']
    assert result['checks']['unsupported_overhangs'] in ('pass', 'warn', 'not_run')


def test_run_print_checks_overhangs_falls_back_when_the_module_is_unavailable(window, monkeypatch):
    """A contract mismatch (the module hasn't landed) must not crash or hide."""
    monkeypatch.setitem(sys.modules, 'voxelmill.overhangs', None)
    union = window.document.derived.union
    result = services.run_print_checks(window.document, union, CancellationToken(),
                                        no_progress, checks={'overhangs'})
    assert result['checks']['unsupported_overhangs'] == 'not_run'
    assert 'reason' in result['metrics']['unsupported_overhangs']


def test_check_print_all_runs_every_check_in_the_background(window, application):
    assert window.run_print_checks_now(set(services.PRINT_CHECK_NAMES), 'all checks') is not None
    drain(window, application, stages=('print_checks',))
    payload = json.loads(window.diagnostics.toPlainText())
    assert set(payload['validation']['checks']) >= {
        'raster_connectivity', 'peel_risk', 'drainage_bottlenecks', 'unsupported_overhangs'}


def test_check_print_some_dialog_defaults_to_every_check_checked(window):
    states = {}

    def capture():
        dialog = window.findChild(QtWidgets.QDialog, 'check_print_some_dialog')
        for key, _label in PRINT_CHECK_LABELS:
            box = dialog.findChild(QtWidgets.QCheckBox, f'check_print_box_{key}')
            states[key] = box.isChecked()
        dialog.reject()

    QtCore.QTimer.singleShot(0, capture)
    window.check_print_some_dialog()
    assert states and all(states.values())


def test_check_print_some_dialog_runs_exactly_the_checked_set(window, monkeypatch):
    captured = {}
    monkeypatch.setattr(window, 'run_print_checks_now',
                        lambda checks, label: captured.update(checks=set(checks), label=label))

    def choose_only_drainage():
        dialog = window.findChild(QtWidgets.QDialog, 'check_print_some_dialog')
        for key, _label in PRINT_CHECK_LABELS:
            if key != 'drainage':
                dialog.findChild(QtWidgets.QCheckBox, f'check_print_box_{key}').setChecked(False)
        dialog.accept()

    QtCore.QTimer.singleShot(0, choose_only_drainage)
    window.check_print_some_dialog()
    assert captured['checks'] == {'drainage'}


def test_check_print_some_dialog_cancel_runs_nothing(window, monkeypatch):
    called = []
    monkeypatch.setattr(window, 'run_print_checks_now', lambda *a, **k: called.append(a))

    def cancel():
        window.findChild(QtWidgets.QDialog, 'check_print_some_dialog').reject()

    QtCore.QTimer.singleShot(0, cancel)
    window.check_print_some_dialog()
    assert not called


# ---- layer-issue wiring -----------------------------------------------------

def test_show_issues_menu_defaults_to_every_code_visible(window):
    visibility = window.layers.issue_visibility()
    assert visibility
    for code in visibility:
        action = window.actions_map.get(f'show_issue_{code}')
        assert action is not None and action.isChecked()


def test_toggling_a_show_issue_action_reaches_the_layer_view(window):
    action = window.actions_map['show_issue_peel_risk']
    action.setChecked(False)
    assert window.layers.issue_visibility()['peel_risk'] is False


def test_layer_view_visibility_changes_sync_back_to_the_menu_action(window):
    window.layers.set_issue_visibility('drainage_bottleneck', False)
    assert window.actions_map['show_issue_drainage_bottleneck'].isChecked() is False


def test_set_report_feeds_diagnostic_layers_into_the_layer_view(window):
    window.layers.set_range(20)
    report = {'passed': False, 'checks': {}, 'metrics': {}, 'diagnostics': [
        {'code': 'raster_island', 'message': 'x', 'severity': 'error', 'layer': 3},
        {'code': 'raster_island', 'message': 'x', 'severity': 'error', 'layer': 7},
        {'code': 'peel_risk', 'message': 'x', 'severity': 'warning', 'layer': 5},
    ]}
    window._set_report(report)
    assert window.layers.next_issue_layer(-1) == 3
    assert window.layers.next_issue_layer(3) == 5
    assert window.layers.previous_issue_layer(20) == 7


def test_next_and_previous_issue_layer_shortcuts_jump_the_slider(window):
    window.layers.set_range(20)
    window._set_report({'passed': True, 'checks': {}, 'metrics': {}, 'diagnostics': [
        {'code': 'raster_island', 'message': 'x', 'layer': 9},
    ]})
    window.layers.slider.setValue(0)
    window._jump_issue_layer(1)
    assert window.layers.slider.value() == 9
    window._jump_issue_layer(-1)
    # Already at the only issue layer; there is nothing below it to jump to.
    assert window.layers.slider.value() == 9


# ---- gizmo preview and deselect ---------------------------------------------

def test_preview_transform_moves_the_actor_without_touching_the_document(window):
    before = list(window.document.center_offset_mm)
    actor = window.scene.actors['model']
    assert actor.GetUserTransform() is None
    window._on_object_preview_transformed(0, [5.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    transform = actor.GetUserTransform()
    assert transform is not None
    assert transform.GetMatrix().GetElement(0, 3) == pytest.approx(5.0)
    assert window.document.center_offset_mm == before


def test_committing_a_transform_clears_the_live_preview_first(window):
    window.reload = lambda: None
    actor = window.scene.actors['model']
    window._on_object_preview_transformed(0, [5.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    assert actor.GetUserTransform().GetMatrix().GetElement(0, 3) == pytest.approx(5.0)
    window._on_object_transformed(0, [3.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    # No user transform at all: the rebuild _write_pose triggers draws the real
    # pose, so the delta must not still be sitting on the actor on top of it.
    # "No preview" is the absence of a transform, not an identity one.
    assert actor.GetUserTransform() is None
    assert window.document.center_offset_mm[0] == pytest.approx(3.0)


def test_object_deselected_clears_the_list_selection(window):
    window.object_panel.list.setCurrentRow(0)
    window._on_object_deselected()
    assert window.object_panel.list.currentRow() == -1


# ---- relative vs. absolute motion --------------------------------------------

def test_motion_mode_defaults_to_relative(window):
    assert window.motion_mode == 'relative'
    assert window.actions_map['motion_relative'].isChecked()
    assert not window.actions_map['motion_absolute'].isChecked()


def test_motion_mode_round_trips_through_preferences(window, isolated_prefs):
    window._set_motion_mode('absolute')
    assert window.motion_mode == 'absolute'
    assert load_preferences()['motion_mode'] == 'absolute'
    # A window opened afterward picks the persisted choice back up.
    reopened = MainWindow(small_settings(), None, headless=True)
    assert reopened.motion_mode == 'absolute'
    reopened.close()


def test_status_bar_notes_the_motion_mode_change(window):
    window._set_motion_mode('absolute')
    assert 'absolute' in window.statusBar().currentMessage()


def test_relative_mode_gizmo_delta_adds_to_the_current_pose(window):
    window.reload = lambda: None
    window.object_panel.set_snap_angle(0.0)
    window.document.set_orientation([0.0, 0.0, 10.0], [4.0, 0.0], 5.0)
    window._on_object_transformed(0, [0.0, 0.0, 0.0], [0.0, 0.0, 30.0])
    assert window.document.rotation_deg[2] == pytest.approx(40.0)


def test_absolute_mode_gizmo_delta_is_measured_from_import_not_current_pose(window):
    window.reload = lambda: None
    window._set_motion_mode('absolute')
    window.object_panel.set_snap_angle(0.0)
    window.document.set_orientation([0.0, 0.0, 10.0], [4.0, 0.0], 5.0)
    window._on_object_transformed(0, [0.0, 0.0, 0.0], [0.0, 0.0, 30.0])
    # Import rotation is 0, so the result is 0 + 30, not 10 + 30.
    assert window.document.rotation_deg[2] == pytest.approx(30.0)


def test_absolute_mode_displays_and_edits_the_offset_from_import(window):
    window.reload = lambda: None
    window._set_motion_mode('absolute')
    window.document.set_orientation([0.0, 0.0, 10.0], [4.0, 0.0], 5.0)
    window._sync_object_pose(0)
    assert window.object_panel.rotate_rows['Z'].value() == pytest.approx(10.0)
    assert window.object_panel.translate_rows['X'].value() == pytest.approx(4.0)
    # Lift's import default is 5 mm, so an unmoved part displays as 0.
    assert window.object_panel.translate_rows['Z'].value() == pytest.approx(0.0)
    window.object_panel.rotate_rows['Z'].box.setValue(30.0)
    window.apply_object_pose()
    assert window.document.rotation_deg[2] == pytest.approx(30.0)


def test_relative_mode_typed_edits_are_unaffected_by_motion_mode(window):
    """Typing an absolute number has always meant exactly that number."""
    window.reload = lambda: None
    window.object_panel.set_snap_angle(0.0)
    window.object_panel.rotate_rows['Z'].box.setValue(37.5)
    window.apply_object_pose()
    assert window.document.rotation_deg[2] == pytest.approx(37.5)
