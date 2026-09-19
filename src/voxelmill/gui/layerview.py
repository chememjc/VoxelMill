"""Layer scrubber: one printer-resolution slice with its diagnostics.

The same widget shows a slice of the in-memory assembly and a decoded layer of
an already-written ``.goo``, because the two payloads are deliberately the same
shape.  What differs is stated on screen rather than assumed by the reader.

Zooming renders only the visible crop, so the cost of a paint is bounded by the
viewport rather than by the zoom factor: a 32x view of an 8520x4320 frame
touches the same number of pixels as a 1x view.  Above 1:1 the zoom ladder is
integer so a printer pixel stays an exact square block; below it the ladder is
1/n so decimation stays a whole-block maximum.  Fractional scaling would blur
the one thing this view exists to show.
"""
from __future__ import annotations

import bisect
import math
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..validation import block_any

#: A full Mars 5 Ultra frame is 8520x4320 = 36.8 Mpx, and an RGB buffer plus
#: its detached copy for one of those is roughly 220 MB.  Preview images are
#: decimated below this before any buffer is allocated.
MAX_PREVIEW_PIXELS = 4_000_000

#: Screen pixels per printer pixel.  Every step at or above 1 is an integer and
#: every step below it is 1/n, so a printer pixel is always a whole block of
#: screen pixels or a whole block of printer pixels is one screen pixel.
ZOOM_STEPS = (1 / 16, 1 / 12, 1 / 8, 1 / 6, 1 / 4, 1 / 3, 1 / 2,
              1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 24, 32)

#: At or above this many screen pixels per printer pixel each pixel is drawn as
#: its own square with a one-pixel black gutter, so individual exposed pixels
#: can be counted.  Five is the smallest scale where the lit part of the cell
#: (4x4) is still clearly larger than its border.
PIXEL_GRID_MIN_SCALE = 5

#: Occupancy gray and the color of the pixel-grid gutter.
OCCUPIED_VALUE = 220
GRID_VALUE = 0

#: Diagnostic code -> marker RGB, so an issue type is recognizable at a
#: glance instead of every marker reading as one undifferentiated red dot.
#: Drawn from the Okabe-Ito colorblind-safe palette (its black is skipped:
#: black is the unoccupied background here and would vanish into it). Codes
#: are the exact strings ``Diagnostic(...)`` calls emit in validation.py,
#: peel.py and pipeline.py.
ISSUE_COLORS = {
    'raster_island': (213, 94, 0),          # vermillion -- broken layer connectivity
    'enclosed_voids': (0, 114, 178),        # blue -- material trapped in the raster
    'transient_trap': (204, 121, 167),      # reddish purple -- trap open only mid-build
    'growth_span': (240, 228, 66),          # yellow -- new material outruns support
    'unsupported_overhang': (230, 159, 0),  # orange -- overhang needs support review
    'peel_risk': (0, 158, 115),             # bluish green -- release/orientation risk
    'drainage_bottleneck': (86, 180, 233),  # sky blue -- void drains too slowly
}
#: Marker color for a code not yet in :data:`ISSUE_COLORS`, e.g. a check
#: added to the pipeline before this view learned its code.
DEFAULT_ISSUE_COLOR = (235, 70, 60)


def issue_color(code):
    """RGB marker color for a diagnostic code, falling back to the default."""
    return ISSUE_COLORS.get(code, DEFAULT_ISSUE_COLOR)


def diagnostic_code(diagnostic):
    """A diagnostic's code, whether it is a dataclass or its dict form."""
    return diagnostic.get('code') if isinstance(diagnostic, dict) else getattr(diagnostic, 'code', None)


def preview_decimation(height, width, max_pixels=MAX_PREVIEW_PIXELS):
    """Integer block factor that brings an image under ``max_pixels``."""
    if max_pixels <= 0 or height * width <= max_pixels:
        return 1
    return int(math.ceil(math.sqrt(height * width / max_pixels)))


def zoom_step(zoom, delta):
    """Move ``delta`` notches along :data:`ZOOM_STEPS` from the nearest step."""
    current = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - zoom))
    return ZOOM_STEPS[max(0, min(len(ZOOM_STEPS) - 1, current + int(delta)))]


def fit_zoom(mask_shape, size):
    """Largest ladder step that shows the whole frame inside ``size``."""
    height, width = mask_shape
    view_width, view_height = size
    if height <= 0 or width <= 0 or view_width <= 0 or view_height <= 0:
        return ZOOM_STEPS[0]
    limit = min(view_width / width, view_height / height)
    fitting = [step for step in ZOOM_STEPS if step <= limit]
    return fitting[-1] if fitting else ZOOM_STEPS[0]


def visible_crop(mask_shape, zoom, center, size):
    """Printer-pixel rectangle and screen offset a viewport shows.

    ``center`` is in image coordinates, where row 0 is the top of the screen
    and therefore the largest Y.  Returns ``(col0, row0, cols, rows, ox, oy)``:
    the crop origin may be negative and the crop may run past the frame, which
    is how the background around a zoomed-out frame stays background instead of
    wrapping around to the far edge.
    """
    view_width, view_height = int(size[0]), int(size[1])
    if zoom >= 1:
        factor = int(round(zoom))
        cols = int(math.ceil(view_width / factor)) + 1
        rows = int(math.ceil(view_height / factor)) + 1
    else:
        decimation = int(round(1 / zoom))
        cols = view_width * decimation
        rows = view_height * decimation
    left = float(center[0]) - cols / 2.0
    top = float(center[1]) - rows / 2.0
    col0, row0 = int(math.floor(left)), int(math.floor(top))
    if zoom >= 1:
        factor = int(round(zoom))
        ox = int(round((left - col0) * factor))
        oy = int(round((top - row0) * factor))
    else:
        # A whole printer pixel is under one screen pixel here, so there is no
        # sub-cell offset to honour.
        ox = oy = 0
    return col0, row0, cols, rows, ox, oy


def _crop_occupancy(occupied, col0, row0, cols, rows):
    """Occupancy for an image-space rectangle, zero-padded outside the frame."""
    height, width = occupied.shape
    crop = np.zeros((rows, cols), dtype=bool)
    first_row, last_row = max(0, row0), min(height, row0 + rows)
    first_col, last_col = max(0, col0), min(width, col0 + cols)
    if last_row > first_row and last_col > first_col:
        # Image row r is mask row height-1-r, so the mask slice is taken from
        # the far end and reversed rather than flipping the whole frame.
        block = occupied[height - last_row:height - first_row, first_col:last_col][::-1]
        crop[first_row - row0:last_row - row0, first_col - col0:last_col - col0] = block
    return crop


def render_layer_image(mask, *, zoom=1.0, center=None, size=(640, 640), diagnostics=(),
                       grid=None, marker=6, grid_min_scale=PIXEL_GRID_MIN_SCALE,
                       color_for=None):
    """Render one viewport of a layer at ``zoom`` screen pixels per printer pixel.

    Only the visible crop is materialised.  At or above ``grid_min_scale`` the
    gutter between printer pixels is drawn black, so a solid region reads as a
    grid of separate exposed pixels rather than one white field.
    ``color_for`` maps a diagnostic code to an RGB triple; the default is
    :func:`issue_color`.  The Faults tab passes its own palette so the 2D
    preview matches the 3D glyphs.
    """
    mask = np.asarray(mask)
    if mask.ndim != 2:
        raise ValueError('a layer mask must be two dimensional')
    color_fn = color_for or issue_color
    occupied = mask != 0
    height, width = occupied.shape
    view_width, view_height = max(1, int(size[0])), max(1, int(size[1]))
    if center is None:
        center = (width / 2.0, height / 2.0)
    col0, row0, cols, rows, ox, oy = visible_crop(occupied.shape, zoom, center, (view_width, view_height))
    crop = _crop_occupancy(occupied, col0, row0, cols, rows)

    gutter = None
    if zoom >= 1:
        factor = int(round(zoom))
        scaled = np.repeat(np.repeat(crop, factor, axis=0), factor, axis=1)
        if factor >= grid_min_scale:
            gutter = np.zeros(scaled.shape, dtype=bool)
            gutter[::factor, :] = True
            gutter[:, ::factor] = True
    else:
        decimation = int(round(1 / zoom))
        scaled = block_any(crop, decimation)

    rgb = np.zeros((scaled.shape[0], scaled.shape[1], 3), dtype=np.uint8)
    rgb[scaled] = OCCUPIED_VALUE
    if gutter is not None:
        rgb[gutter] = GRID_VALUE

    view = np.zeros((view_height, view_width, 3), dtype=np.uint8)
    window = rgb[oy:oy + view_height, ox:ox + view_width]
    view[:window.shape[0], :window.shape[1]] = window

    if grid is not None:
        for diagnostic in diagnostics:
            position = (diagnostic.get('position_mm') if isinstance(diagnostic, dict)
                        else getattr(diagnostic, 'position_mm', None))
            if not position:
                continue
            column = (position[0] - grid.x0) / grid.dx - 0.5
            row = (position[1] - grid.y0) / grid.dy - 0.5
            x = int(round((column - col0) * zoom)) - ox
            y = int(round((height - 1 - row - row0) * zoom)) - oy
            lo_y, hi_y = max(0, y - marker), min(view_height, y + marker + 1)
            lo_x, hi_x = max(0, x - marker), min(view_width, x + marker + 1)
            if hi_y > lo_y and hi_x > lo_x:
                view[lo_y:hi_y, lo_x:hi_x] = color_fn(diagnostic_code(diagnostic))

    view = np.ascontiguousarray(view)
    return QtGui.QImage(view.data, view_width, view_height,
                        3 * view_width, QtGui.QImage.Format_RGB888).copy()


def mask_to_image(mask, diagnostics=(), grid=None, marker=6, max_pixels=MAX_PREVIEW_PIXELS):
    """Gray occupancy with diagnostic markers colored by issue type.

    ``mask`` is normalized to a boolean first.  A geometry raster holds 0/1
    occupancy while a decoded GOO frame holds 0/255, and ``255 * 220`` wraps in
    uint8 to 228 -- a plausible-looking gray that silently misrenders every
    decoded layer.  Normalizing is what keeps the two sources comparable.

    Decimation is block maximum rather than striding, so a support tip a few
    pixels wide survives the preview instead of vanishing from it.  The factor
    is returned to the caller through the image's own size; markers are placed
    in decimated coordinates so they still land on what they point at.
    """
    mask = np.asarray(mask)
    if mask.ndim != 2:
        raise ValueError('a layer mask must be two dimensional')
    occupied = mask != 0
    factor = preview_decimation(*occupied.shape, max_pixels=max_pixels)
    if factor > 1:
        occupied = block_any(occupied, factor)
    height, width = occupied.shape
    rgb = np.empty((height, width, 3), dtype=np.uint8)
    # Rows are flipped so positive Y points up on screen, as in the raster docs.
    gray = np.flipud(occupied).astype(np.uint8) * np.uint8(OCCUPIED_VALUE)
    rgb[..., 0] = rgb[..., 1] = rgb[..., 2] = gray
    if grid is not None:
        for diagnostic in diagnostics:
            if isinstance(diagnostic, dict):
                position = diagnostic.get('position_mm')
            else:
                position = getattr(diagnostic, 'position_mm', None)
            if not position:
                continue
            column = int(round((position[0] - grid.x0) / grid.dx - 0.5)) // factor
            row = int(round((position[1] - grid.y0) / grid.dy - 0.5)) // factor
            if not (0 <= column < width and 0 <= row < height):
                continue
            flipped = height - 1 - row
            lo_y, hi_y = max(0, flipped - marker), min(height, flipped + marker + 1)
            lo_x, hi_x = max(0, column - marker), min(width, column + marker + 1)
            rgb[lo_y:hi_y, lo_x:hi_x] = issue_color(diagnostic_code(diagnostic))
    return QtGui.QImage(rgb.data, width, height, 3 * width, QtGui.QImage.Format_RGB888).copy()


class LayerCanvas(QtWidgets.QWidget):
    """Zoomable, pannable view of one layer mask.

    The mask is held at printer resolution and never pre-scaled, because the
    whole point of zooming is to see the pixels the printer will actually
    expose.  Painting renders only what fits on screen.
    """
    layer_delta = QtCore.Signal(int)
    zoom_changed = QtCore.Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setAutoFillBackground(True)
        self.setFocusPolicy(QtCore.Qt.WheelFocus)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self._mask = None
        self._grid = None
        self._diagnostics = ()
        self._color_for = None
        self._zoom = 1.0
        self._fit = True
        self._center = None
        self._drag_origin = None
        self._drag_center = None

    # ---- state ----------------------------------------------------------
    @property
    def zoom(self):
        return self._zoom

    @property
    def center(self):
        return self._center

    @property
    def fitted(self):
        return self._fit

    def set_color_for(self, color_for):
        """Override the diagnostic-code → RGB mapping used when painting markers."""
        self._color_for = color_for
        self.update()

    def set_layer(self, mask, grid=None, diagnostics=()):
        mask = np.asarray(mask) if mask is not None else None
        changed_shape = mask is None or self._mask is None or mask.shape != self._mask.shape
        self._mask, self._grid, self._diagnostics = mask, grid, tuple(diagnostics)
        if changed_shape:
            # A different frame size invalidates a pan expressed in its pixels.
            self._center = None
            self._fit = True
        self.update()

    def set_zoom(self, zoom, *, anchor=None):
        """Set the zoom, keeping the printer pixel under ``anchor`` in place."""
        if self._mask is None:
            return
        zoom = float(zoom)
        old, center = self._zoom, self._effective_center()
        if anchor is not None and old > 0:
            view = QtCore.QPointF(self.width() / 2.0, self.height() / 2.0)
            held = (center[0] + (anchor.x() - view.x()) / old,
                    center[1] + (anchor.y() - view.y()) / old)
            center = (held[0] - (anchor.x() - view.x()) / zoom,
                      held[1] - (anchor.y() - view.y()) / zoom)
        self._zoom, self._fit, self._center = zoom, False, center
        self.zoom_changed.emit(zoom)
        self.update()

    def fit(self):
        self._fit, self._center = True, None
        if self._mask is not None:
            self._zoom = fit_zoom(self._mask.shape, (self.width(), self.height()))
            self.zoom_changed.emit(self._zoom)
        self.update()

    def _effective_zoom(self):
        if self._mask is None:
            return self._zoom
        if self._fit:
            self._zoom = fit_zoom(self._mask.shape, (self.width(), self.height()))
        return self._zoom

    def _effective_center(self):
        if self._center is not None:
            return self._center
        if self._mask is None:
            return (0.0, 0.0)
        height, width = self._mask.shape
        return (width / 2.0, height / 2.0)

    # ---- interaction ----------------------------------------------------
    def wheelEvent(self, event):
        """Ctrl+wheel zooms about the cursor; a plain wheel changes layer."""
        steps = event.angleDelta().y()
        if not steps:
            return super().wheelEvent(event)
        notches = 1 if steps > 0 else -1
        if event.modifiers() & QtCore.Qt.ControlModifier:
            self.set_zoom(zoom_step(self._effective_zoom(), notches),
                          anchor=event.position())
        else:
            self.layer_delta.emit(notches)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._mask is not None:
            self._drag_origin = event.position()
            self._drag_center = self._effective_center()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_origin is None:
            return super().mouseMoveEvent(event)
        zoom = self._effective_zoom()
        dx = (event.position().x() - self._drag_origin.x()) / zoom
        dy = (event.position().y() - self._drag_origin.y()) / zoom
        self._center = (self._drag_center[0] - dx, self._drag_center[1] - dy)
        self._fit = False
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_origin is not None:
            self._drag_origin = self._drag_center = None
            self.setCursor(QtCore.Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ---- painting -------------------------------------------------------
    def rendered_image(self):
        if self._mask is None:
            return None
        return render_layer_image(
            self._mask, zoom=self._effective_zoom(), center=self._effective_center(),
            size=(max(1, self.width()), max(1, self.height())),
            diagnostics=self._diagnostics, grid=self._grid,
            color_for=self._color_for)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(17, 17, 17))
        image = self.rendered_image()
        if image is not None:
            painter.drawImage(0, 0, image)
        painter.end()


class IssueLayerStrip(QtWidgets.QWidget):
    """Thin strip beside the layer slider marking which layers carry an issue.

    Qt's slider groove has no per-position color hook, and restyling the
    whole slider to get one would cost far more than painting a handful of
    rectangles here.  Cost is O(number of issue layers), not O(layer count).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(8)
        self._layers_by_code = {}
        self._visibility = {}
        self._maximum = 0

    def set_issue_layers(self, layers_by_code, visibility, maximum):
        """Redraw for a new set of issue layers, visibility mask, and slider range."""
        self._layers_by_code = layers_by_code
        self._visibility = visibility
        self._maximum = max(0, int(maximum))
        codes = sorted({code for code, _ in self._visible_entries()})
        self.setToolTip(', '.join(codes))
        self.update()

    def _visible_entries(self):
        for code, layers in self._layers_by_code.items():
            if not self._visibility.get(code, True):
                continue
            for layer in layers:
                yield code, layer

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(40, 40, 40))
        if self._maximum > 0:
            height = self.height()
            for code, layer in self._visible_entries():
                # Layer 0 is the plate, drawn at the bottom to match the
                # slider's own bottom-to-top travel.
                fraction = max(0.0, min(1.0, layer / self._maximum))
                y = int(round((1 - fraction) * (height - 2)))
                painter.fillRect(0, y, self.width(), 2, QtGui.QColor(*issue_color(code)))
        painter.end()


class LayerView(QtWidgets.QWidget):
    """Scrubber over one layer source at a time.

    Two sources produce the same payload shape: the assembled union sliced on
    demand, and a decoded ``.goo``.  Keeping them behind one selector is what
    makes "what did the file actually get" answerable in the editor, rather
    than only in a report.

    The layer slider is vertical and runs bottom-to-top so its travel matches
    the print: layer 0 is the plate.  The horizontal slider under the view is
    zoom, which is also what Ctrl+wheel drives.
    """
    layer_requested = QtCore.Signal(int)
    source_changed = QtCore.Signal(str)
    issue_visibility_changed = QtCore.Signal(str, bool)
    issue_layer_requested = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        self.canvas = LayerCanvas()
        self.canvas.setObjectName('layer_canvas')
        self.label = QtWidgets.QLabel('No layer rasterized yet')
        self.label.setAlignment(QtCore.Qt.AlignCenter)
        self.label.setStyleSheet('background:#111;color:#bbb;')
        self.label.setMinimumSize(240, 240)
        # The placeholder and the canvas occupy the same cell; the canvas
        # replaces it once a layer exists rather than sitting empty beside it.
        self.stack = QtWidgets.QStackedLayout()
        self.stack.addWidget(self.label)
        self.stack.addWidget(self.canvas)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Vertical)
        self.slider.setObjectName('layer_slider')
        self.slider.setEnabled(False)
        self.slider.setInvertedAppearance(False)
        self.slider.setToolTip('Layer. The wheel over the view moves one layer; '
                               'Ctrl+wheel zooms.')
        self.issue_strip = IssueLayerStrip()
        self.issue_strip.setObjectName('layer_issue_strip')
        self.info = QtWidgets.QLabel('')
        self.info.setWordWrap(True)
        self.legend = QtWidgets.QLabel('')
        self.legend.setObjectName('layer_legend')
        self.legend.setTextFormat(QtCore.Qt.RichText)
        self.legend.hide()
        self.picker = QtWidgets.QComboBox()
        self.picker.addItem('All diagnostics', '')
        self.source = QtWidgets.QComboBox()
        self.source.setObjectName('layer_source')
        self.source.addItem('Current assembly', 'union')
        self.source.setToolTip(
            'Current assembly slices the in-memory union at the configured layer height. '
            'An opened GOO shows the decoded pixels the printer will expose.')
        self.thumbnail = QtWidgets.QLabel()
        self.thumbnail.setObjectName('goo_preview')
        self.thumbnail.setAlignment(QtCore.Qt.AlignCenter)
        self.thumbnail.setFixedHeight(0)

        self.zoom_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.zoom_slider.setObjectName('layer_zoom')
        self.zoom_slider.setRange(0, len(ZOOM_STEPS) - 1)
        self.zoom_slider.setValue(ZOOM_STEPS.index(1))
        self.zoom_slider.setToolTip(
            'Screen pixels per printer pixel. At 5x and above each printer pixel is '
            'drawn as its own square with a black gutter, so exposed pixels can be counted.')
        self.zoom_label = QtWidgets.QLabel('')
        self.zoom_label.setObjectName('layer_zoom_label')
        self.zoom_label.setMinimumWidth(64)
        self.fit_button = QtWidgets.QPushButton('Fit')
        self.fit_button.setObjectName('layer_fit')
        self.fit_button.setToolTip('Show the whole frame and resume fitting on resize.')
        self.prev_issue_button = QtWidgets.QPushButton('Prev issue')
        self.prev_issue_button.setObjectName('layer_prev_issue')
        self.prev_issue_button.setToolTip('Jump to the nearest layer below with a visible issue.')
        self.next_issue_button = QtWidgets.QPushButton('Next issue')
        self.next_issue_button.setObjectName('layer_next_issue')
        self.next_issue_button.setToolTip('Jump to the nearest layer above with a visible issue.')

        source_row = QtWidgets.QHBoxLayout()
        source_row.addWidget(QtWidgets.QLabel('Source:'))
        source_row.addWidget(self.source, 1)
        source_row.addWidget(self.thumbnail)
        body = QtWidgets.QHBoxLayout()
        body.addLayout(self.stack, 1)
        body.addWidget(self.slider)
        body.addWidget(self.issue_strip)
        zoom_row = QtWidgets.QHBoxLayout()
        zoom_row.addWidget(QtWidgets.QLabel('Zoom:'))
        zoom_row.addWidget(self.zoom_slider, 1)
        zoom_row.addWidget(self.zoom_label)
        zoom_row.addWidget(self.fit_button)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('Show:'))
        row.addWidget(self.picker, 1)
        row.addWidget(self.prev_issue_button)
        row.addWidget(self.next_issue_button)
        layout.addLayout(source_row)
        layout.addLayout(body, 1)
        layout.addWidget(self.legend)
        layout.addLayout(zoom_row)
        layout.addLayout(row)
        layout.addWidget(self.info)

        self.slider.valueChanged.connect(self.layer_requested)
        self.picker.currentIndexChanged.connect(lambda _: self.layer_requested.emit(self.slider.value()))
        self.source.currentIndexChanged.connect(
            lambda _: self.source_changed.emit(self.selected_source))
        self.canvas.layer_delta.connect(self.step_layer)
        self.canvas.zoom_changed.connect(self._on_canvas_zoom)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        self.fit_button.clicked.connect(self.fit)
        self.prev_issue_button.clicked.connect(
            lambda: self._go_to_issue_layer(self.previous_issue_layer(self.slider.value())))
        self.next_issue_button.clicked.connect(
            lambda: self._go_to_issue_layer(self.next_issue_layer(self.slider.value())))
        self._image = None
        self._syncing_zoom = False
        self._issue_visibility = dict.fromkeys(ISSUE_COLORS, True)
        self._issue_layers = {}
        self._visible_issue_layers = []
        self._update_zoom_label(self.canvas.zoom)
        self._refresh_issue_layers()

    # ---- zoom -----------------------------------------------------------
    def step_layer(self, delta):
        """Move the layer slider, clamped, without wrapping at either end."""
        if not self.slider.isEnabled():
            return
        self.slider.setValue(max(self.slider.minimum(),
                                 min(self.slider.maximum(), self.slider.value() + int(delta))))

    def fit(self):
        self.canvas.fit()

    def _on_zoom_slider(self, index):
        if self._syncing_zoom:
            return
        self.canvas.set_zoom(ZOOM_STEPS[int(index)])

    def _on_canvas_zoom(self, zoom):
        self._syncing_zoom = True
        nearest = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - zoom))
        self.zoom_slider.setValue(nearest)
        self._syncing_zoom = False
        self._update_zoom_label(zoom)

    def _update_zoom_label(self, zoom):
        text = f'{zoom:g}x' if zoom >= 1 else f'1:{int(round(1 / zoom))}'
        grid = '  grid' if zoom >= PIXEL_GRID_MIN_SCALE else ''
        self.zoom_label.setText(f'{text}{grid}')

    # ---- source selection ----------------------------------------------
    @property
    def selected_source(self):
        return self.source.currentData() or 'union'

    def set_goo_source(self, path, layer_count, preview=None):
        """Offer an opened file as a layer source and select it."""
        self.source.blockSignals(True)
        index = self.source.findData('goo')
        kind = 'CTB' if Path(path).suffix.lower() == '.ctb' else 'GOO'
        title = f'{kind}: {Path(path).name} ({layer_count} layers)'
        if index < 0:
            self.source.addItem(title, 'goo')
            index = self.source.findData('goo')
        else:
            self.source.setItemText(index, title)
        self.source.setCurrentIndex(index)
        self.source.blockSignals(False)
        self.set_preview(preview)
        self.source_changed.emit('goo')

    def clear_goo_source(self):
        index = self.source.findData('goo')
        if index >= 0:
            self.source.blockSignals(True)
            self.source.removeItem(index)
            self.source.setCurrentIndex(0)
            self.source.blockSignals(False)
        self.set_preview(None)

    def set_preview(self, rgb):
        """Show the file's own embedded preview thumbnail, when it has one."""
        if rgb is None:
            self.thumbnail.clear()
            self.thumbnail.setFixedHeight(0)
            return
        rgb = np.ascontiguousarray(np.asarray(rgb, dtype=np.uint8))
        height, width = rgb.shape[:2]
        image = QtGui.QImage(rgb.data, width, height, 3 * width, QtGui.QImage.Format_RGB888).copy()
        self.thumbnail.setFixedHeight(72)
        self.thumbnail.setPixmap(QtGui.QPixmap.fromImage(image).scaledToHeight(
            72, QtCore.Qt.SmoothTransformation))

    def set_range(self, layers):
        self.slider.setEnabled(layers > 0)
        self.slider.setRange(0, max(0, layers - 1))
        self._refresh_issue_layers()

    def set_diagnostic_codes(self, codes):
        current = self.picker.currentData()
        self.picker.blockSignals(True)
        self.picker.clear()
        self.picker.addItem('All diagnostics', '')
        for code in sorted(codes):
            self.picker.addItem(code, code)
            # A code the pipeline knows but this view didn't (a check added
            # elsewhere) still starts visible rather than silently hidden.
            self._issue_visibility.setdefault(code, True)
        index = self.picker.findData(current)
        self.picker.setCurrentIndex(max(0, index))
        self.picker.blockSignals(False)

    @property
    def selected_code(self):
        return self.picker.currentData() or ''

    # ---- per-issue-type visibility ---------------------------------------
    def set_issue_visibility(self, code, visible):
        """Show or hide every marker of one diagnostic code."""
        self._issue_visibility[code] = bool(visible)
        self.issue_visibility_changed.emit(code, bool(visible))
        self._refresh_issue_layers()

    def issue_visibility(self):
        """Current code -> visible mapping; every known type starts visible."""
        return dict(self._issue_visibility)

    # ---- jump to layers with issues ---------------------------------------
    def set_issue_layers(self, layers_by_code):
        """Record where each diagnostic code occurs, for Prev/Next and the strip.

        ``layers_by_code`` maps a code to a sorted list of layer indices, as
        built by the caller from a validation report.
        """
        self._issue_layers = {code: list(layers) for code, layers in layers_by_code.items()}
        self._refresh_issue_layers()

    def _refresh_issue_layers(self):
        visibility = self.issue_visibility()
        self._visible_issue_layers = sorted({
            layer for code, layers in self._issue_layers.items()
            if visibility.get(code, True) for layer in layers})
        has_issues = bool(self._visible_issue_layers)
        self.prev_issue_button.setEnabled(has_issues)
        self.next_issue_button.setEnabled(has_issues)
        self.issue_strip.set_issue_layers(self._issue_layers, visibility, self.slider.maximum())

    def next_issue_layer(self, from_index):
        """Nearest layer strictly above ``from_index`` with a visible issue, or ``None``."""
        layers = self._visible_issue_layers
        position = bisect.bisect_right(layers, from_index)
        return layers[position] if position < len(layers) else None

    def previous_issue_layer(self, from_index):
        """Nearest layer strictly below ``from_index`` with a visible issue, or ``None``."""
        layers = self._visible_issue_layers
        position = bisect.bisect_left(layers, from_index)
        return layers[position - 1] if position > 0 else None

    def _go_to_issue_layer(self, index):
        if index is None:
            return
        self.slider.setValue(index)
        self.issue_layer_requested.emit(index)

    def _update_legend(self, diagnostics):
        """Compact swatch list for the codes actually drawn on this layer."""
        codes = sorted({diagnostic_code(d) for d in diagnostics if diagnostic_code(d)})
        if not codes:
            self.legend.hide()
            self.legend.clear()
            return
        swatches = []
        for code in codes:
            r, g, b = issue_color(code)
            swatches.append(f'<span style="color:rgb({r},{g},{b})">&#9632;</span> {code}')
        self.legend.setText('&nbsp;&nbsp;'.join(swatches))
        self.legend.show()

    def show_layer(self, payload, diagnostics=()):
        visibility = self.issue_visibility()
        chosen = [d for d in diagnostics
                  if (not self.selected_code or diagnostic_code(d) == self.selected_code)
                  and visibility.get(diagnostic_code(d), True)]
        self.canvas.set_layer(payload['mask'], payload['grid'], chosen)
        self.stack.setCurrentWidget(self.canvas)
        self._image = self.canvas.rendered_image()
        self._on_canvas_zoom(self.canvas.zoom)
        self._update_legend(chosen)
        origin = payload.get('source', 'union')
        kind = payload.get('format', origin)
        shape = payload['mask'].shape
        note = ('decoded CTB pixels' if kind == 'ctb'
                else 'decoded GOO pixels' if origin == 'goo'
                else 'assembly sliced at the configured layer height')
        self.info.setText(
            f"layer {payload['index']}  z={payload['z_mm']:.3f} mm  "
            f"{payload['filled_pixels']} px  open rows {payload['open_rows']}  "
            f"markers {len(chosen)}  frame {shape[1]}x{shape[0]}  [{note}]")
