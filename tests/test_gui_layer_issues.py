"""Per-issue-type color, visibility filtering, and jump-to-issue navigation."""
from __future__ import annotations


import numpy as np
import pytest


pytestmark = pytest.mark.gui

from PySide6 import QtGui  # noqa: E402

from voxelmill.contracts import Diagnostic  # noqa: E402
from voxelmill.raster import RasterGrid  # noqa: E402
from voxelmill.gui.layerview import (  # noqa: E402
    DEFAULT_ISSUE_COLOR, ISSUE_COLORS, LayerView, issue_color, mask_to_image,
    render_layer_image,
)


def _grid(height, width):
    # dx=dy=1 and a zero origin makes pixel and mm coordinates the same
    # number, so a diagnostic's position_mm can be written directly as a
    # pixel index.
    return RasterGrid(width=width, height=height, x0=0.0, y0=0.0, dx=1.0, dy=1.0)


def _solid(height, width):
    return np.ones((height, width), dtype=np.uint8)


def _pixel(image, x, y):
    color = QtGui.QColor(image.pixel(x, y))
    return (color.red(), color.green(), color.blue())


# ---- per-code marker color ----------------------------------------------

def test_render_layer_image_colors_each_marker_by_its_diagnostic_code(application):
    mask = _solid(20, 20)
    grid = _grid(20, 20)
    diagnostics = [
        Diagnostic('raster_island', 'x', position_mm=[4.5, 4.5]),
        Diagnostic('peel_risk', 'x', position_mm=[14.5, 14.5]),
    ]
    image = render_layer_image(mask, zoom=1, center=(10.0, 10.0), size=(20, 20),
                               diagnostics=diagnostics, grid=grid, marker=1)
    # Image row 0 is the largest Y (mask row height-1), matching the flip
    # used throughout this module.
    assert _pixel(image, 4, 15) == ISSUE_COLORS['raster_island']
    assert _pixel(image, 14, 5) == ISSUE_COLORS['peel_risk']


def test_mask_to_image_colors_each_marker_by_its_diagnostic_code(application):
    mask = _solid(20, 20)
    grid = _grid(20, 20)
    diagnostics = [
        Diagnostic('growth_span', 'x', position_mm=[4.5, 4.5]),
        Diagnostic('drainage_bottleneck', 'x', position_mm=[14.5, 14.5]),
    ]
    image = mask_to_image(mask, diagnostics, grid=grid, marker=1)
    assert _pixel(image, 4, 15) == ISSUE_COLORS['growth_span']
    assert _pixel(image, 14, 5) == ISSUE_COLORS['drainage_bottleneck']


def test_unknown_diagnostic_code_falls_back_to_the_default_color(application):
    mask = _solid(20, 20)
    grid = _grid(20, 20)
    diagnostics = [Diagnostic('some_future_check', 'x', position_mm=[9.5, 9.5])]
    image = render_layer_image(mask, zoom=1, center=(10.0, 10.0), size=(20, 20),
                               diagnostics=diagnostics, grid=grid, marker=1)
    assert _pixel(image, 9, 10) == DEFAULT_ISSUE_COLOR
    assert issue_color('some_future_check') == DEFAULT_ISSUE_COLOR


# ---- per-issue-type visibility -------------------------------------------

def test_every_known_issue_type_starts_visible(application):
    view = LayerView()
    visibility = view.issue_visibility()
    assert visibility and all(visibility.values())
    assert set(ISSUE_COLORS) <= set(visibility)


def _count_marker_pixels(image, color):
    width, height = image.width(), image.height()
    return sum(1 for x in range(width) for y in range(height) if _pixel(image, x, y) == color)


def test_toggled_off_code_produces_no_marker_in_the_rendered_canvas(application):
    view = LayerView()
    view.canvas.resize(240, 240)  # LayerCanvas carries a 240x240 minimum size.
    grid = _grid(20, 20)
    diagnostics = [Diagnostic('raster_island', 'x', position_mm=[9.5, 9.5])]
    payload = {'mask': _solid(20, 20), 'grid': grid, 'index': 0, 'z_mm': 0.0,
              'filled_pixels': 400, 'open_rows': 0}
    view.show_layer(payload, diagnostics)
    image = view.canvas.rendered_image()
    assert _count_marker_pixels(image, ISSUE_COLORS['raster_island']) > 0

    view.set_issue_visibility('raster_island', False)
    view.show_layer(payload, diagnostics)
    image = view.canvas.rendered_image()
    assert _count_marker_pixels(image, ISSUE_COLORS['raster_island']) == 0


def test_the_picker_and_the_toggles_compose(application):
    """Narrowing to one code and hiding a different code both apply."""
    view = LayerView()
    view.set_diagnostic_codes(['raster_island', 'peel_risk'])
    view.picker.setCurrentIndex(view.picker.findData('raster_island'))
    view.set_issue_visibility('peel_risk', False)
    grid = _grid(20, 20)
    diagnostics = [Diagnostic('raster_island', 'x', position_mm=[9.5, 9.5]),
                  Diagnostic('peel_risk', 'x', position_mm=[5.5, 5.5])]
    payload = {'mask': _solid(20, 20), 'grid': grid, 'index': 0, 'z_mm': 0.0,
              'filled_pixels': 400, 'open_rows': 0}
    view.show_layer(payload, diagnostics)
    assert 'markers 1' in view.info.text()


# ---- the color legend -----------------------------------------------------

def test_the_legend_is_hidden_with_no_diagnostics_and_shown_with_some(application):
    view = LayerView()
    grid = _grid(20, 20)
    payload = {'mask': _solid(20, 20), 'grid': grid, 'index': 0, 'z_mm': 0.0,
              'filled_pixels': 400, 'open_rows': 0}
    view.show_layer(payload, [])
    # The widget is never shown in this test, so isVisible() would read False
    # regardless; isHidden() reflects the explicit show()/hide() calls made
    # by _update_legend.
    assert view.legend.isHidden()
    view.show_layer(payload, [Diagnostic('raster_island', 'x', position_mm=[9.5, 9.5])])
    assert not view.legend.isHidden()
    assert 'raster_island' in view.legend.text()


# ---- jump to layers with issues -------------------------------------------

def test_next_and_previous_issue_layer_boundaries(application):
    view = LayerView()
    view.set_range(20)
    view.set_issue_layers({'raster_island': [2, 5, 9], 'peel_risk': [15]})

    # Nothing above the last issue layer.
    assert view.next_issue_layer(15) is None
    assert view.next_issue_layer(19) is None
    # Nothing below the first issue layer.
    assert view.previous_issue_layer(2) is None
    assert view.previous_issue_layer(0) is None
    # An exact match is skipped: the search is strict, not inclusive.
    assert view.next_issue_layer(5) == 9
    assert view.previous_issue_layer(5) == 2
    # Ordinary interior lookups land on the nearest neighbor.
    assert view.next_issue_layer(0) == 2
    assert view.previous_issue_layer(19) == 15


def test_issue_buttons_disable_with_no_issue_layers_and_enable_once_set(application):
    view = LayerView()
    assert not view.prev_issue_button.isEnabled()
    assert not view.next_issue_button.isEnabled()
    view.set_range(10)
    view.set_issue_layers({'raster_island': [3]})
    assert view.prev_issue_button.isEnabled()
    assert view.next_issue_button.isEnabled()


def test_hiding_the_only_issue_type_disables_the_buttons_again(application):
    view = LayerView()
    view.set_range(10)
    view.set_issue_layers({'raster_island': [3]})
    view.set_issue_visibility('raster_island', False)
    assert not view.prev_issue_button.isEnabled()
    assert not view.next_issue_button.isEnabled()
    assert view.next_issue_layer(0) is None


def test_next_issue_button_moves_the_slider_and_emits_the_target_layer(application):
    view = LayerView()
    view.set_range(10)
    view.set_issue_layers({'raster_island': [7]})
    view.slider.setValue(0)
    requested = []
    view.issue_layer_requested.connect(requested.append)
    view.next_issue_button.click()
    assert view.slider.value() == 7
    assert requested == [7]
