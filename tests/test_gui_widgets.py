"""Focused-only wheel on spin boxes, dual-handle Z clip, Darwin VTK backend."""
from __future__ import annotations

import os
import sys
from dataclasses import asdict

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytestmark = pytest.mark.gui

sys.path.insert(0, os.path.dirname(__file__))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from voxelmill.contracts import Diagnostic  # noqa: E402
from voxelmill.gui.viewport import vtk_render_backend  # noqa: E402
from voxelmill.gui.widgets import (  # noqa: E402
    ZClipSlider, install_focused_wheel_filter,
)
from voxelmill.gui.window import MainWindow  # noqa: E402
from test_gui import small_settings  # noqa: E402


@pytest.fixture(scope='module')
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _wheel(widget, delta=120):
    pos = QtCore.QPointF(widget.width() / 2, widget.height() / 2)
    return QtGui.QWheelEvent(
        pos, widget.mapToGlobal(pos.toPoint()), QtCore.QPoint(0, 0),
        QtCore.QPoint(0, delta), QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
        QtCore.Qt.NoScrollPhase, False)


def test_spinbox_wheel_is_ignored_until_the_field_is_focused(application):
    install_focused_wheel_filter(application)
    box = QtWidgets.QDoubleSpinBox()
    box.setRange(0, 100)
    box.setValue(5.0)
    box.setSingleStep(1.0)
    box.show()
    application.processEvents()
    box.clearFocus()
    application.processEvents()
    assert not box.hasFocus()
    start = box.value()
    QtWidgets.QApplication.sendEvent(box, _wheel(box))
    application.processEvents()
    assert box.value() == start
    box.setFocus(QtCore.Qt.MouseFocusReason)
    application.processEvents()
    if not box.hasFocus():
        pytest.skip('offscreen platform did not grant focus')
    QtWidgets.QApplication.sendEvent(box, _wheel(box))
    application.processEvents()
    assert box.value() != start
    box.close()


def test_z_clip_slider_emits_none_when_showing_all(application):
    slider = ZClipSlider()
    slider.set_extent(0.0, 100.0)
    seen = []
    slider.clip_changed.connect(lambda a, b: seen.append((a, b)))
    slider.set_clip(10.0, 40.0)
    assert slider.clip_limits() == (10.0, 40.0)
    slider.show_all.click()
    assert seen[-1] == (None, None)
    assert slider.is_full()
    assert slider.clip_limits() == (None, None)


def test_vtk_render_backend_is_native_off_darwin():
    import sys
    if sys.platform == 'darwin':
        assert vtk_render_backend() == 'generic_opengl'
    else:
        assert vtk_render_backend() == 'native'


def test_auto_island_scan_does_not_raise_the_report_tab(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.tabs.currentIndex() == 0
    diagnostic = asdict(Diagnostic('raster_island', 'floating material', layer=3,
                                   position_mm=[1.0, 2.0, 3.0]))
    summary = {
        'check': 'fail', 'island_count': 1, 'island_components': 1,
        'layers': 40, 'nonempty_layers': 12, 'min_overlap_pixels': 1,
        'layer_height_mm': 0.1,
        'islands': [{'layer': 3, 'position_mm': [1.0, 2.0, 3.0],
                    'message': 'floating material', 'details': {}}],
        'truncated': False, 'other_checks': {'overlap': 'pass'},
        'closed_surface': 'pass', 'open_rows': 0,
        'not_examined': ['drainage_bottlenecks', 'support_routes', 'plate_fit',
                         'union_raster_parity'],
        'diagnostics': [diagnostic],
    }
    window._island_check_show_report = False
    window._finish_islands(summary)
    assert window.tabs.currentIndex() == 0
    window._island_check_show_report = True
    window._finish_islands(summary)
    assert window.tabs.currentIndex() == window.report_tab_index
    window.close()


def test_activating_a_diagnostic_opens_the_layers_tab(application):
    window = MainWindow(small_settings(), None, headless=True)
    window.layers.set_range(10)
    window._set_report({
        'passed': False,
        'diagnostics': [{'code': 'raster_island', 'message': 'x', 'layer': 3,
                         'severity': 'error'}],
        'checks': {},
        'metrics': {},
    })
    item = window.diagnostic_list.topLevelItem(0)
    window._select_diagnostic(item, 0)
    assert window.tabs.currentIndex() == window.layers_tab_index
    assert window.layers.slider.value() == 3
    window.close()


def test_main_window_has_a_vertical_z_clip_slider(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.z_clip_slider.objectName() == 'view_z_clip'
    assert window.z_clip_slider.show_all.objectName() == 'view_z_clip_show_all'
    window.close()
