"""Layer-view zoom, panning, the pixel grid, and wheel behavior."""
from __future__ import annotations


import numpy as np
import pytest


pytestmark = pytest.mark.gui

from PySide6 import QtCore, QtGui  # noqa: E402

from voxelmill.gui.layerview import (  # noqa: E402
    GRID_VALUE, OCCUPIED_VALUE, PIXEL_GRID_MIN_SCALE, ZOOM_STEPS, LayerCanvas,
    LayerView, fit_zoom, pixel_under_cursor, render_layer_image, visible_crop,
    zoom_step,
)
from voxelmill.raster import RasterGrid  # noqa: E402


def _solid(height, width):
    return np.ones((height, width), dtype=np.uint8)


# ---- zoom ladder --------------------------------------------------------

def test_every_zoom_step_is_an_integer_scale_or_an_integer_reciprocal():
    """Fractional scaling would blur the pixels this view exists to show."""
    for step in ZOOM_STEPS:
        if step >= 1:
            assert step == int(step)
        else:
            assert abs(1 / step - round(1 / step)) < 1e-9


def test_zoom_step_moves_one_notch_and_clamps_at_both_ends():
    assert zoom_step(1, 1) == 2
    assert zoom_step(1, -1) == 1 / 2
    assert zoom_step(ZOOM_STEPS[-1], 5) == ZOOM_STEPS[-1]
    assert zoom_step(ZOOM_STEPS[0], -5) == ZOOM_STEPS[0]


def test_zoom_step_starts_from_the_nearest_ladder_value():
    """A fitted zoom is a ladder value, but a resize can land between two."""
    assert zoom_step(2.1, 1) == 3


def test_fit_zoom_never_returns_a_step_that_overflows_the_viewport():
    for shape, size in (((100, 100), (400, 400)), ((4320, 8520), (800, 600)),
                        ((10, 10), (33, 21)), ((4320, 8520), (100, 100))):
        zoom = fit_zoom(shape, size)
        assert zoom in ZOOM_STEPS
        if zoom != ZOOM_STEPS[0]:
            assert shape[1] * zoom <= size[0] + 1e-9
            assert shape[0] * zoom <= size[1] + 1e-9


# ---- rendering ----------------------------------------------------------

def test_render_returns_exactly_the_requested_viewport_size(application):
    for zoom in (1 / 8, 1 / 2, 1, 3, 8):
        image = render_layer_image(_solid(64, 64), zoom=zoom, size=(120, 90))
        assert (image.width(), image.height()) == (120, 90)


def test_render_cost_does_not_grow_with_zoom(application):
    """The whole point of cropping first: a 32x view is not a 32x buffer."""
    mask = np.zeros((4320, 8520), dtype=np.uint8)
    mask[2000:2100, 4000:4100] = 1
    image = render_layer_image(mask, zoom=32, center=(4050.0, 2260.0), size=(200, 200))
    assert (image.width(), image.height()) == (200, 200)


def test_occupied_pixels_are_gray_and_empty_pixels_are_background(application):
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[4, 4] = 1
    image = render_layer_image(mask, zoom=1, center=(4.0, 4.0), size=(9, 9))
    # Image row 0 is the largest Y, so mask row 4 of 8 lands above center.
    filled = [(x, y) for y in range(9) for x in range(9)
              if QtGui.QColor(image.pixel(x, y)).red() == OCCUPIED_VALUE]
    assert len(filled) == 1


def test_a_zoomed_out_frame_is_surrounded_by_background_not_by_wrapped_pixels(application):
    mask = _solid(16, 16)
    image = render_layer_image(mask, zoom=1, center=(8.0, 8.0), size=(64, 64))
    # The frame is 16x16 inside a 64x64 viewport, so the corners are outside it.
    assert QtGui.QColor(image.pixel(1, 1)).red() == 0
    assert QtGui.QColor(image.pixel(62, 62)).red() == 0


# ---- the pixel grid -----------------------------------------------------

def test_no_pixel_grid_below_the_threshold(application):
    image = render_layer_image(_solid(32, 32), zoom=PIXEL_GRID_MIN_SCALE - 1,
                               center=(16.0, 16.0), size=(40, 40))
    reds = {QtGui.QColor(image.pixel(x, y)).red() for x in range(40) for y in range(40)}
    assert reds == {OCCUPIED_VALUE}


def test_a_solid_region_becomes_separated_pixels_at_the_grid_threshold(application):
    """Five screen pixels per printer pixel is a 4x4 lit cell in a 1px gutter."""
    factor = PIXEL_GRID_MIN_SCALE
    image = render_layer_image(_solid(32, 32), zoom=factor, center=(16.0, 16.0),
                               size=(factor * 4, factor * 4), grid_min_scale=factor)
    rows = [[QtGui.QColor(image.pixel(x, y)).red() for x in range(factor * 4)]
            for y in range(factor * 4)]
    # Exactly one gutter line every `factor` pixels, on both axes.
    gutter_rows = [y for y, row in enumerate(rows) if all(v == GRID_VALUE for v in row)]
    assert len(gutter_rows) == 4
    assert all(b - a == factor for a, b in zip(gutter_rows, gutter_rows[1:]))
    # A lit cell is a solid (factor-1) square between two gutters.
    y = gutter_rows[0] + 1
    run = [x for x in range(factor * 4) if rows[y][x] == OCCUPIED_VALUE]
    assert len(run) == (factor - 1) * 4


def test_the_grid_does_not_light_empty_pixels(application):
    """An empty frame stays black: the gutter is drawn, not the cells."""
    mask = np.zeros((32, 32), dtype=np.uint8)
    image = render_layer_image(mask, zoom=8, center=(16.0, 16.0), size=(64, 64))
    reds = {QtGui.QColor(image.pixel(x, y)).red() for x in range(64) for y in range(64)}
    assert reds == {0}


# ---- panning ------------------------------------------------------------

def test_visible_crop_follows_the_center():
    left = visible_crop((100, 100), 1, (10.0, 50.0), (20, 20))
    right = visible_crop((100, 100), 1, (60.0, 50.0), (20, 20))
    assert right[0] - left[0] == 50


def test_panning_changes_what_is_drawn(application):
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[:, :8] = 1
    near = render_layer_image(mask, zoom=1, center=(4.0, 32.0), size=(16, 16))
    far = render_layer_image(mask, zoom=1, center=(56.0, 32.0), size=(16, 16))
    lit = lambda img: sum(QtGui.QColor(img.pixel(x, y)).red() > 0
                          for x in range(16) for y in range(16))
    assert lit(near) > 0 and lit(far) == 0


# ---- the widget ---------------------------------------------------------

def test_the_layer_slider_is_vertical_and_the_zoom_slider_is_horizontal(application):
    view = LayerView()
    assert view.slider.orientation() == QtCore.Qt.Vertical
    assert view.zoom_slider.orientation() == QtCore.Qt.Horizontal


def test_a_plain_wheel_moves_one_layer_and_clamps_at_the_ends(application):
    view = LayerView()
    view.set_range(5)
    view.slider.setValue(2)
    moved = []
    view.layer_requested.connect(moved.append)
    view.step_layer(1)
    assert view.slider.value() == 3
    view.step_layer(-3)
    assert view.slider.value() == 0
    view.step_layer(-1)
    assert view.slider.value() == 0
    assert moved == [3, 0]


def test_a_wheel_over_the_canvas_asks_for_a_layer_but_ctrl_wheel_zooms(application):
    canvas = LayerCanvas()
    canvas.resize(100, 100)
    canvas.set_layer(_solid(64, 64))
    canvas.set_zoom(2)
    deltas, zooms = [], []
    canvas.layer_delta.connect(deltas.append)
    canvas.zoom_changed.connect(zooms.append)

    def wheel(modifier):
        return QtGui.QWheelEvent(
            QtCore.QPointF(50, 50), QtCore.QPointF(50, 50), QtCore.QPoint(0, 0),
            QtCore.QPoint(0, 120), QtCore.Qt.NoButton, modifier,
            QtCore.Qt.NoScrollPhase, False)

    canvas.wheelEvent(wheel(QtCore.Qt.NoModifier))
    assert deltas == [1] and zooms == []
    canvas.wheelEvent(wheel(QtCore.Qt.ControlModifier))
    assert deltas == [1] and zooms == [3]


def test_ctrl_wheel_keeps_the_pixel_under_the_cursor_in_place(application):
    canvas = LayerCanvas()
    canvas.resize(400, 400)
    canvas.set_layer(_solid(400, 400))
    canvas.set_zoom(2)
    # The widget carries a minimum size, so the viewport center is read back
    # rather than assumed from the requested size.
    mid_x, mid_y = canvas.width() / 2.0, canvas.height() / 2.0
    anchor = QtCore.QPointF(mid_x + 50.0, mid_y - 40.0)
    before = canvas.center
    held = (before[0] + (anchor.x() - mid_x) / 2, before[1] + (anchor.y() - mid_y) / 2)
    canvas.set_zoom(4, anchor=anchor)
    after = canvas.center
    still = (after[0] + (anchor.x() - mid_x) / 4, after[1] + (anchor.y() - mid_y) / 4)
    assert still == pytest.approx(held)


def test_the_zoom_slider_and_the_canvas_stay_in_step(application):
    view = LayerView()
    view.canvas.resize(200, 200)
    view.canvas.set_layer(_solid(64, 64))
    view.zoom_slider.setValue(ZOOM_STEPS.index(8))
    assert view.canvas.zoom == 8
    assert '8x' in view.zoom_label.text() and 'grid' in view.zoom_label.text()
    view.canvas.set_zoom(1 / 4)
    assert view.zoom_slider.value() == ZOOM_STEPS.index(1 / 4)
    assert '1:4' in view.zoom_label.text()


def test_fit_shows_the_whole_frame_again_after_zooming(application):
    view = LayerView()
    view.canvas.resize(200, 200)
    view.canvas.set_layer(_solid(64, 64))
    view.canvas.set_zoom(16)
    assert not view.canvas.fitted
    view.fit()
    assert view.canvas.fitted
    assert view.canvas.zoom == fit_zoom((64, 64), (200, 200))


def test_a_new_frame_size_drops_a_pan_expressed_in_the_old_pixels(application):
    canvas = LayerCanvas()
    canvas.resize(100, 100)
    canvas.set_layer(_solid(64, 64))
    canvas.set_zoom(4)
    canvas._center = (10.0, 10.0)
    canvas.set_layer(_solid(4320, 8520))
    assert canvas.fitted and canvas._center is None


# ---- G12: pixel inspection ------------------------------------------------

def _move_event(x, y):
    """A real ``QMouseEvent`` -- ``mouseMoveEvent`` forwards it to ``super()``,
    which needs an actual event, not a duck-typed stand-in."""
    point = QtCore.QPointF(x, y)
    return QtGui.QMouseEvent(QtCore.QEvent.MouseMove, point, point,
                             QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier)


def test_pixel_under_cursor_inverts_the_exact_math_that_places_a_marker():
    """render_layer_image places a marker at this screen point for a given
    printer pixel; pixel_under_cursor must recover that pixel from the point."""
    mask_shape = (64, 64)
    height, _width = mask_shape
    zoom, center, size = 3, (20.0, 40.0), (140, 130)
    col0, row0, _cols, _rows, ox, oy = visible_crop(mask_shape, zoom, center, size)
    column, row = 17, 25
    x = int(round((column - col0) * zoom)) - ox
    y = int(round((height - 1 - row - row0) * zoom)) - oy
    assert pixel_under_cursor(mask_shape, zoom, center, size, x, y) == (column, row)


def test_pixel_under_cursor_maps_a_decimated_screen_pixel_to_its_block_origin():
    """Below 1:1 several printer pixels share one screen pixel (whole-block
    decimation), so the inverse cannot recover an arbitrary original pixel --
    only the block's own origin, by the same ``x * decimation + col0`` a
    forward decimated crop uses (``ox``/``oy`` are always 0 below 1:1)."""
    mask_shape = (256, 256)
    height = mask_shape[0]
    zoom, center, size = 1 / 4, (128.0, 128.0), (100, 100)
    col0, row0, _cols, _rows, ox, oy = visible_crop(mask_shape, zoom, center, size)
    assert ox == 0 and oy == 0
    decimation = round(1 / zoom)
    x, y = 30, 20
    expected = (x * decimation + col0, height - 1 - (y * decimation + row0))
    assert pixel_under_cursor(mask_shape, zoom, center, size, x, y) == expected


def test_pixel_under_cursor_is_none_outside_the_frame():
    assert pixel_under_cursor((64, 64), 1, (32.0, 32.0), (200, 200), -50, -50) is None
    assert pixel_under_cursor((64, 64), 1, (32.0, 32.0), (200, 200), 199, 199) is None


def test_hover_pixel_reports_value_mm_position_and_a_nearby_diagnostic(application):
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[3, 4] = 200  # a greyscale-style value, not just occupancy
    grid = RasterGrid(10, 10, x0=0.0, y0=0.0, dx=0.05, dy=0.05)
    diagnostics = [{'code': 'peel_risk', 'position_mm': grid.xy(3, 4)}]
    canvas = LayerCanvas()
    canvas.resize(100, 100)
    canvas.set_layer(mask, grid, diagnostics)
    canvas.set_zoom(1)

    zoom, center = canvas.zoom, canvas._effective_center()
    size = (canvas.width(), canvas.height())
    col0, row0, _cols, _rows, ox, oy = visible_crop(mask.shape, zoom, center, size)
    height = mask.shape[0]
    x = int(round((4 - col0) * zoom)) - ox
    y = int(round((height - 1 - 3 - row0) * zoom)) - oy

    reading = canvas.hover_pixel(QtCore.QPointF(x, y))
    assert reading == {
        'column': 4, 'row': 3, 'value': 200,
        'x_mm': pytest.approx(grid.xy(3, 4)[0]),
        'y_mm': pytest.approx(grid.xy(3, 4)[1]),
        'codes': ('peel_risk',),
    }


def test_hover_pixel_shows_pixels_only_with_no_grid(application):
    """No grid (an assembly slice with no printer metadata) still reports the
    pixel and its value -- just no millimeter position."""
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[2, 2] = 1
    canvas = LayerCanvas()
    canvas.resize(100, 100)
    canvas.set_layer(mask)
    canvas.set_zoom(1)
    reading = canvas.hover_pixel(QtCore.QPointF(canvas.width() / 2, canvas.height() / 2))
    assert reading is not None
    assert reading['x_mm'] is None and reading['y_mm'] is None
    assert reading['codes'] == ()


def test_hover_pixel_is_none_with_no_layer_or_outside_the_frame(application):
    canvas = LayerCanvas()
    canvas.resize(100, 100)
    assert canvas.hover_pixel(QtCore.QPointF(10, 10)) is None
    canvas.set_layer(_solid(10, 10))
    canvas.set_zoom(1)
    assert canvas.hover_pixel(QtCore.QPointF(-5, -5)) is None


def test_mouse_move_emits_a_reading_and_leaving_the_canvas_clears_it(application):
    canvas = LayerCanvas()
    canvas.resize(100, 100)
    canvas.set_layer(_solid(20, 20))
    canvas.set_zoom(2)
    readings = []
    canvas.pixel_hovered.connect(readings.append)

    canvas.mouseMoveEvent(_move_event(canvas.width() / 2, canvas.height() / 2))
    assert len(readings) == 1
    assert readings[-1] is not None
    assert readings[-1]['value'] == 1

    canvas.leaveEvent(QtCore.QEvent(QtCore.QEvent.Leave))
    assert readings[-1] is None


def test_dragging_does_not_emit_a_pixel_reading(application):
    """A drag repositions the pan; it must not also spam the readout."""
    canvas = LayerCanvas()
    canvas.resize(100, 100)
    canvas.set_layer(_solid(20, 20))
    canvas._drag_origin = QtCore.QPointF(10, 10)
    canvas._drag_center = canvas._effective_center()
    readings = []
    canvas.pixel_hovered.connect(readings.append)
    canvas.mouseMoveEvent(_move_event(20, 20))
    assert readings == []


def test_the_layer_view_readout_label_tracks_the_canvas_hover(application):
    view = LayerView()
    view.canvas.resize(100, 100)
    view.canvas.set_layer(_solid(20, 20))
    view.canvas.set_zoom(2)
    forwarded = []
    view.pixel_hovered.connect(forwarded.append)

    view.canvas.mouseMoveEvent(_move_event(view.canvas.width() / 2, view.canvas.height() / 2))
    assert 'pixel (' in view.pixel_readout.text()
    assert 'no pixel pitch known' in view.pixel_readout.text()
    assert len(forwarded) == 1 and forwarded[0] is not None

    view.canvas.leaveEvent(QtCore.QEvent(QtCore.QEvent.Leave))
    assert view.pixel_readout.text() == ''
    assert forwarded[-1] is None
