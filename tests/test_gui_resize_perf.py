"""Dock-separator drags: coalesced macOS VTK renders and a cached layer image.

The macOS path is forced on so it runs here. No VTK render happens: the
offscreen Qt platform hands VTK a window id X rejects (see gotchas.md), so the
scene render is replaced by a counter.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

pytestmark = pytest.mark.gui

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')

from PySide6 import QtWidgets  # noqa: E402

from voxelmill.gui import layerview  # noqa: E402
from voxelmill.gui.editors import ConfigurationEditor, opaque_splitter_resize  # noqa: E402
from voxelmill.gui.document import Document  # noqa: E402
from voxelmill.gui.layerview import LayerCanvas  # noqa: E402
from voxelmill.gui.viewport import _VTKInteractor, defers_paint_renders  # noqa: E402


def _spin(app, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)


def _counting_interactor(defer_renders):
    widget = _VTKInteractor(defer_renders=defer_renders)
    renders = []
    widget._scene_ready = lambda: True
    widget._render_scene = lambda: renders.append(time.monotonic())
    return widget, renders


def test_only_darwin_defers_paint_renders():
    assert defers_paint_renders('darwin')
    assert not defers_paint_renders('linux')
    assert not defers_paint_renders('win32')


def test_a_resize_burst_renders_a_few_times_and_once_after_it_ends(application):
    widget, renders = _counting_interactor(defer_renders=True)
    widget.resize(400, 300)
    widget.show()
    _spin(application, 0.2)
    renders.clear()
    for step in range(50):
        widget.resize(400 + 3 * step, 300)
        application.processEvents()
    assert renders == [], 'a paint must never render synchronously on the macOS path'
    ended = time.monotonic()
    _spin(application, 0.3)
    assert 1 <= len(renders) <= 3
    assert renders[-1] >= ended
    widget.close()


def test_an_idle_deferred_widget_does_not_keep_repainting(application):
    # QVTK's own Render() is update(); flushing through it looped forever.
    widget, renders = _counting_interactor(defer_renders=True)
    paints = []
    original = widget.paintEvent

    def counted(event):
        paints.append(1)
        return original(event)

    widget.paintEvent = counted
    widget.resize(300, 300)
    widget.show()
    _spin(application, 0.2)
    paints.clear()
    renders.clear()
    _spin(application, 0.3)
    assert len(paints) <= 1 and len(renders) <= 1
    widget.close()


def test_a_deferred_render_waits_for_the_interactor_to_start(application):
    widget = _VTKInteractor(defer_renders=True)
    renders = []
    widget._render_scene = lambda: renders.append(1)
    widget.resize(300, 300)
    widget.show()
    _spin(application, 0.2)
    assert renders == []
    widget.close()


def _mask(size=200):
    mask = np.zeros((size, size), dtype=np.uint8)
    mask[20:120, 30:150] = 1
    return mask


@pytest.fixture
def counted_layer_renders(monkeypatch):
    calls = []
    real = layerview.render_layer_image

    def counting(*args, **kwargs):
        calls.append(kwargs.get('zoom'))
        return real(*args, **kwargs)

    monkeypatch.setattr(layerview, 'render_layer_image', counting)
    return calls


def test_layer_canvas_reuses_its_image_until_an_input_changes(application,
                                                              counted_layer_renders):
    canvas = LayerCanvas()
    canvas.resize(300, 300)
    mask = _mask()
    canvas.set_layer(mask)
    first = canvas.rendered_image()
    canvas.grab()
    canvas.grab()
    assert canvas.rendered_image() is first
    assert len(counted_layer_renders) == 1

    canvas.set_zoom(3)
    assert canvas.rendered_image() is not first
    assert len(counted_layer_renders) == 2

    canvas.set_layer(mask.copy())
    canvas.rendered_image()
    assert len(counted_layer_renders) == 3

    canvas.set_layer(canvas._mask, diagnostics=({'code': 'raster_island'},))
    canvas.rendered_image()
    assert len(counted_layer_renders) == 4

    canvas._center = (10.0, 10.0)
    canvas.rendered_image()
    assert len(counted_layer_renders) == 5

    canvas.resize(360, 320)
    canvas.rendered_image()
    assert len(counted_layer_renders) == 6

    canvas.set_color_for(lambda code: (1, 2, 3))
    canvas.rendered_image()
    assert len(counted_layer_renders) == 7
    canvas.rendered_image()
    assert len(counted_layer_renders) == 7


def test_a_resized_canvas_repaints_its_last_image_until_the_size_settles(
        application, counted_layer_renders):
    canvas = LayerCanvas()
    canvas.resize(300, 300)
    canvas.set_layer(_mask(64))
    canvas.set_zoom(2)
    canvas.grab()
    assert len(counted_layer_renders) == 1
    for step in range(10):
        canvas.resize(300 + 4 * step, 300)
        canvas.grab()
    assert len(counted_layer_renders) == 1
    _spin(application, 3 * LayerCanvas.resize_settle_ms / 1000.0)
    assert len(counted_layer_renders) == 2
    assert canvas.rendered_image().width() == canvas.width()
    canvas.grab()
    _spin(application, 3 * LayerCanvas.resize_settle_ms / 1000.0)
    assert len(counted_layer_renders) == 2
    # A size that changes the fitted zoom is not a stale-image case.
    canvas.fit()
    canvas.rendered_image()
    before = len(counted_layer_renders)
    canvas.resize(900, 900)
    canvas.grab()
    assert len(counted_layer_renders) == before + 1


def test_cached_layer_image_matches_a_fresh_render(application):
    canvas = LayerCanvas()
    canvas.resize(300, 300)
    canvas.set_layer(_mask())
    canvas.set_zoom(2)
    cached = canvas.rendered_image()
    fresh = layerview.render_layer_image(
        canvas._mask, zoom=canvas.zoom, center=canvas.center,
        size=(canvas.width(), canvas.height()))
    assert cached == fresh


def test_only_macos_settings_splitters_resize_on_release(application):
    assert opaque_splitter_resize('linux')
    assert opaque_splitter_resize('win32')
    assert not opaque_splitter_resize('darwin')
    dialog = ConfigurationEditor(Document(), 'printer', headless=True)
    assert isinstance(dialog.splitter, QtWidgets.QSplitter)
    assert dialog.splitter.opaqueResize() == opaque_splitter_resize()
    dialog.reject()
