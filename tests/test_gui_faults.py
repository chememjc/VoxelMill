"""Headless Faults tab: overlay colors, scene clip/glyphs, and MainWindow wiring."""

import numpy as np
import pytest


pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')

from PySide6 import QtCore  # noqa: E402

from voxelmill.config import resolve_settings  # noqa: E402
from voxelmill.contracts import Diagnostic, ValidationReport  # noqa: E402
from voxelmill.gui.faults import (DEFAULT_FAULT_COLOR, FAULT_COLORS,  # noqa: E402
                                 FaultView, fault_color, fault_overlay)
from voxelmill.gui.viewport import Scene  # noqa: E402
from voxelmill.gui.window import MainWindow  # noqa: E402


def small_settings():
    return resolve_settings(overrides={
        'process': {'layer_height_mm': 0.2},
        'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1], 'build_mm': [100., 80., 165.]},
        'resources': {'workers': 2}})


def test_fault_overlay_assigns_stable_colors_and_default_for_unknown():
    report = ValidationReport()
    report.diagnostics.append(Diagnostic(
        'raster_island', 'island', layer=3, position_mm=[1.0, 2.0, 0.6]))
    report.diagnostics.append(Diagnostic(
        'not_a_real_code', 'mystery', layer=1, position_mm=[0.0, 0.0, 0.2]))
    report.diagnostics.append(Diagnostic(
        'peel_risk', 'peel', severity='warning',
        details={'regions': [{'bounds': [[0, 0, 1], [2, 4, 3]], 'z_layer_span': [5, 6]}]}))
    markers = fault_overlay(report)
    by_code = {}
    for marker in markers:
        by_code.setdefault(marker['code'], []).append(marker)
    assert by_code['raster_island'][0]['color'] == FAULT_COLORS['raster_island']
    assert by_code['not_a_real_code'][0]['color'] == DEFAULT_FAULT_COLOR
    assert fault_color('missing') == DEFAULT_FAULT_COLOR
    peel = by_code['peel_risk']
    assert any(m['position_mm'] is not None and np.allclose(m['position_mm'], [1, 2, 2])
               for m in peel)


def test_fault_overlay_expands_drainage_seeds_with_bounds():
    pitch = 0.5
    bounds = np.asarray([[-1.0, -2.0, 0.0], [10.0, 10.0, 20.0]])
    report = ValidationReport()
    report.diagnostics.append(Diagnostic(
        'drainage_bottleneck', 'neck',
        details={'analysis_pitch_mm': pitch,
                 'bottleneck_examples': [{'seed_zyx': [4, 2, 1], 'core_volume_mm3': 1.0}]}))
    markers = fault_overlay(report, bounds=bounds)
    seeded = [m for m in markers if m['position_mm'] is not None]
    assert seeded
    origin = bounds[0] - 2 * pitch
    # seed_zyx is (z, y, x); world coordinates are (x, y, z).
    expected = [origin[0] + (1 + 0.5) * pitch,
                origin[1] + (2 + 0.5) * pitch,
                origin[2] + (4 + 0.5) * pitch]
    assert any(np.allclose(m['position_mm'], expected) for m in seeded)
    assert all(m['color'] == FAULT_COLORS['drainage_bottleneck'] for m in seeded)


def test_scene_z_clip_and_faults_are_headless_safe():
    scene = Scene()
    cube = np.asarray([
        [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
        [[0, 0, 0], [1, 1, 0], [0, 1, 0]],
        [[0, 0, 2], [1, 0, 2], [1, 1, 2]],
        [[0, 0, 2], [1, 1, 2], [0, 1, 2]],
    ], dtype=np.float32)
    scene.set_mesh('model', cube)
    scene.set_mesh('supports', cube)
    scene.set_z_clip(0.5, 1.5)
    assert scene._z_clip == (0.5, 1.5)
    for role in ('model', 'supports'):
        mapper = scene.actors[role].GetMapper()
        assert mapper.GetNumberOfClippingPlanes() == 2
    scene.set_z_clip(None, None)
    assert scene._z_clip is None
    assert scene.actors['model'].GetMapper().GetNumberOfClippingPlanes() == 0

    scene.set_faults([
        {'position_mm': [0.5, 0.5, 1.0], 'color': FAULT_COLORS['raster_island']},
        {'position_mm': [0.2, 0.2, 0.8], 'color': (1, 2, 3)},
    ])
    actor = scene.actors['faults']
    assert actor.role == 'faults'
    assert actor.GetPickable() == 0
    scene.set_faults([])
    assert 'faults' not in scene.actors


def test_mainwindow_has_faults_tab_and_key(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.tabs.tabText(window.faults_tab_index) == 'Faults'
    assert window.faults.key.objectName() == 'fault_key'
    assert isinstance(window.faults, FaultView)
    assert window.faults_tab_index == window.layers_tab_index + 1
    # Entering and leaving must not require a display. Z clip is shared with
    # the 3D view, so leaving Faults keeps it; only the glyphs go away.
    window.tabs.setCurrentIndex(window.faults_tab_index)
    assert window.scene._z_clip is None
    window.faults.clip_below.setValue(window.faults.clip_below.minimum() + 1.0)
    assert window.scene._z_clip is not None
    window.tabs.setCurrentIndex(window.layers_tab_index)
    assert window.scene._z_clip is not None
    assert 'faults' not in window.scene.actors
    window.z_clip_slider.show_all.click()
    assert window.scene._z_clip is None
    window.close()


def test_faults_layer_slider_is_vertical_and_has_a_zoom_slider(application):
    view = FaultView()
    assert view.slider.orientation() == QtCore.Qt.Vertical
    assert view.zoom_slider.orientation() == QtCore.Qt.Horizontal
    assert view.show_all.objectName() == 'fault_clip_show_all'


def test_faults_show_all_emits_cleared_clip(application):
    view = FaultView()
    view.set_z_extent(0.0, 50.0, reset=True)
    view.clip_below.setValue(5.0)
    view.clip_above.setValue(20.0)
    seen = []
    view.clip_changed.connect(lambda a, b: seen.append((a, b)))
    view.show_all.click()
    assert seen[-1] == (None, None)
    assert view.clip_limits() == (None, None)
