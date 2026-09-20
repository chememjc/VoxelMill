"""Shared editor widgets: focused-only wheel, dual-handle Z clip.

Spin boxes and combo boxes that change on a hover-wheel are a constant
source of accidental edits while scrolling a tall Setup page. The filter
installed on the application ignores a wheel over those widgets until the
user has clicked them (they have keyboard focus), and hands the event to
the enclosing scroll area so the page still moves.

The Z clip slider is the same two-handle vertical bar Chitubox and
PrusaSlicer put on the 3D view: the bottom handle is Zmin, the top handle
is Zmax, and Show all restores the full interval (no clip planes).
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class FocusedWheelFilter(QtCore.QObject):
    """Ignore hover-wheel on spin boxes and combo boxes until they are focused."""

    _TYPES = (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)

    @classmethod
    def _target(cls, obj):
        """Nearest spin/combo ancestor, including ``obj`` itself.

        On Cocoa the wheel often lands on the inner ``QLineEdit``, so matching
        only ``isinstance(obj, _TYPES)`` lets hover-wheels change values.
        """
        widget = obj if isinstance(obj, QtWidgets.QWidget) else None
        while widget is not None:
            if isinstance(widget, cls._TYPES):
                return widget
            widget = widget.parentWidget()
        return None

    def eventFilter(self, obj, event):
        # Compare as ints: PySide6 EnumMeta can raise from a VTK/xvfb child
        # when QEvent.Type is touched after the event is already dispatched.
        try:
            etype = int(event.type())
            if etype == int(QtCore.QEvent.Type.Show) and isinstance(obj, self._TYPES):
                # WheelFocus is Qt's default and is exactly the hover-changes-value
                # behaviour this filter exists to stop.
                if obj.focusPolicy() == QtCore.Qt.WheelFocus:
                    obj.setFocusPolicy(QtCore.Qt.StrongFocus)
                line_edit = getattr(obj, 'lineEdit', None)
                if callable(line_edit):
                    edit = line_edit()
                    if edit is not None:
                        edit.setFocusPolicy(QtCore.Qt.StrongFocus)
                return False
            if etype != int(QtCore.QEvent.Type.Wheel):
                return False
            target = self._target(obj)
            if target is None or target.hasFocus():
                return False
            parent = target.parentWidget()
            while parent is not None:
                if isinstance(parent, QtWidgets.QAbstractScrollArea):
                    viewport = parent.viewport()
                    if viewport is not None and viewport is not obj:
                        QtWidgets.QApplication.sendEvent(viewport, event)
                    return True
                parent = parent.parentWidget()
            event.ignore()
            return True
        except Exception:
            return False


_WHEEL_FILTER = None


def install_focused_wheel_filter(app):
    """Install the hover-wheel guard once on ``app``. Safe to call repeatedly."""
    global _WHEEL_FILTER
    if app is None:
        return None
    if _WHEEL_FILTER is not None:
        return _WHEEL_FILTER
    _WHEEL_FILTER = FocusedWheelFilter(app)
    app.installEventFilter(_WHEEL_FILTER)
    return _WHEEL_FILTER


class ZClipSlider(QtWidgets.QWidget):
    """Vertical range slider: bottom handle is Zmin, top handle is Zmax.

    Emitting ``(None, None)`` means "show everything" — the scene should drop
    its clip planes rather than clip at the exact bounds, which is cheaper
    and visually identical. ``Show all`` (the button, a double-click, or
    dragging both handles to the ends) is how that state is reached.
    """

    clip_changed = QtCore.Signal(object, object)
    show_all_requested = QtCore.Signal()

    _HANDLE = 14
    _GROOVE = 8
    _GAP_FRAC = 0.002

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('view_z_clip')
        self.setMinimumWidth(44)
        self.setMaximumWidth(52)
        self.setMinimumHeight(160)
        self.setToolTip('Z clip. Drag the top and bottom handles; Show all '
                        'clears the clip. Same idea as Chitubox / PrusaSlicer.')
        self._zmin_bound = 0.0
        self._zmax_bound = 1.0
        self._lo = 0.0
        self._hi = 1.0
        self._drag = None
        self._drag_offset = 0.0
        self._silent = False

        self.show_all = QtWidgets.QPushButton('All')
        self.show_all.setObjectName('view_z_clip_show_all')
        self.show_all.setFixedHeight(22)
        self.show_all.setToolTip('Clear Z clipping and show the whole model.')
        self.show_all.clicked.connect(self._on_show_all)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        layout.addWidget(self.show_all)
        layout.addStretch(1)

    def set_extent(self, zmin, zmax):
        """World-Z range of the scene. Out-of-range handles are clamped."""
        zmin, zmax = float(zmin), float(zmax)
        if zmax < zmin:
            zmin, zmax = zmax, zmin
        if zmax - zmin < 1e-6:
            zmax = zmin + 1.0
        full = self.is_full()
        self._zmin_bound, self._zmax_bound = zmin, zmax
        if full:
            self._lo, self._hi = zmin, zmax
        else:
            self._lo = max(zmin, min(zmax, self._lo))
            self._hi = max(zmin, min(zmax, self._hi))
            if self._lo > self._hi:
                self._lo, self._hi = self._hi, self._lo
        self.update()

    def set_clip(self, zmin, zmax):
        """Move the handles. ``None, None`` restores the full interval."""
        if zmin is None or zmax is None:
            self._lo, self._hi = self._zmin_bound, self._zmax_bound
        else:
            lo, hi = float(zmin), float(zmax)
            if lo > hi:
                lo, hi = hi, lo
            self._lo = max(self._zmin_bound, min(self._zmax_bound, lo))
            self._hi = max(self._zmin_bound, min(self._zmax_bound, hi))
        self.update()

    def clip_limits(self):
        if self.is_full():
            return None, None
        return float(self._lo), float(self._hi)

    def is_full(self):
        eps = max(1e-6, (self._zmax_bound - self._zmin_bound) * 1e-4)
        return (self._lo <= self._zmin_bound + eps
                and self._hi >= self._zmax_bound - eps)

    def extent(self):
        return float(self._zmin_bound), float(self._zmax_bound)

    def _on_show_all(self):
        self._lo, self._hi = self._zmin_bound, self._zmax_bound
        self.update()
        self.show_all_requested.emit()
        self._emit()

    def _emit(self):
        if self._silent:
            return
        self.clip_changed.emit(*self.clip_limits())

    def _groove_rect(self):
        top = self.show_all.geometry().bottom() + 10
        bottom = self.height() - 8
        x = (self.width() - self._GROOVE) // 2
        return QtCore.QRect(x, top, self._GROOVE, max(20, bottom - top))

    def _z_to_y(self, z):
        groove = self._groove_rect()
        span = self._zmax_bound - self._zmin_bound
        if span <= 0:
            return groove.bottom()
        t = (self._zmax_bound - z) / span
        return groove.top() + t * groove.height()

    def _y_to_z(self, y):
        groove = self._groove_rect()
        if groove.height() <= 0:
            return self._zmin_bound
        t = (y - groove.top()) / groove.height()
        t = max(0.0, min(1.0, t))
        return self._zmax_bound - t * (self._zmax_bound - self._zmin_bound)

    def _handle_rect(self, z):
        y = int(round(self._z_to_y(z)))
        w = self._HANDLE
        return QtCore.QRect(self.width() // 2 - w // 2, y - w // 2, w, w)

    def _min_gap(self):
        return max(1e-4, (self._zmax_bound - self._zmin_bound) * self._GAP_FRAC)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(event)
        lo_rect = self._handle_rect(self._lo)
        hi_rect = self._handle_rect(self._hi)
        pos = event.position().toPoint()
        if hi_rect.adjusted(-2, -2, 2, 2).contains(pos):
            self._drag = 'hi'
            self._drag_offset = pos.y() - hi_rect.center().y()
        elif lo_rect.adjusted(-2, -2, 2, 2).contains(pos):
            self._drag = 'lo'
            self._drag_offset = pos.y() - lo_rect.center().y()
        else:
            groove = self._groove_rect()
            y = pos.y()
            if groove.adjusted(-6, 0, 6, 0).contains(pos):
                lo_y, hi_y = self._z_to_y(self._lo), self._z_to_y(self._hi)
                if hi_y <= y <= lo_y:
                    self._drag = 'span'
                    self._drag_offset = y
                    self._drag_lo = self._lo
                    self._drag_hi = self._hi
                elif y > lo_y:
                    self._drag = 'lo'
                    self._drag_offset = 0
                    self._lo = self._y_to_z(y)
                    self._emit()
                    self.update()
                else:
                    self._drag = 'hi'
                    self._drag_offset = 0
                    self._hi = self._y_to_z(y)
                    self._emit()
                    self.update()
            else:
                return super().mousePressEvent(event)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return super().mouseMoveEvent(event)
        y = event.position().y() - (self._drag_offset if self._drag != 'span' else 0)
        gap = self._min_gap()
        if self._drag == 'lo':
            self._lo = max(self._zmin_bound, min(self._hi - gap, self._y_to_z(y)))
        elif self._drag == 'hi':
            self._hi = min(self._zmax_bound, max(self._lo + gap, self._y_to_z(y)))
        elif self._drag == 'span':
            delta = self._y_to_z(event.position().y()) - self._y_to_z(self._drag_offset)
            # y-down vs z-up: dragging the bar down should lower both handles.
            # _y_to_z already inverts, so a positive screen-delta is a negative
            # z-delta when the cursor moves down. Using the z difference of the
            # two y positions gives the right sign.
            lo = self._drag_lo + delta
            hi = self._drag_hi + delta
            span = self._drag_hi - self._drag_lo
            if lo < self._zmin_bound:
                lo = self._zmin_bound
                hi = lo + span
            if hi > self._zmax_bound:
                hi = self._zmax_bound
                lo = hi - span
            self._lo, self._hi = lo, hi
        self.update()
        self._emit()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._on_show_all()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        groove = self._groove_rect()
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(50, 52, 56))
        painter.drawRoundedRect(groove, 3, 3)
        y_hi = int(round(self._z_to_y(self._hi)))
        y_lo = int(round(self._z_to_y(self._lo)))
        active = QtCore.QRect(groove.x(), y_hi, groove.width(), max(2, y_lo - y_hi))
        # A full-range fill would read as "clipped" rather than "showing all".
        painter.setBrush(QtGui.QColor(70, 140, 210) if not self.is_full()
                         else QtGui.QColor(70, 75, 82))
        painter.drawRoundedRect(active, 3, 3)
        for z, color in ((self._lo, QtGui.QColor(230, 230, 235)),
                         (self._hi, QtGui.QColor(230, 230, 235))):
            rect = self._handle_rect(z)
            painter.setBrush(color)
            painter.setPen(QtGui.QPen(QtGui.QColor(30, 30, 32), 1))
            painter.drawEllipse(rect)
        painter.setPen(QtGui.QColor(180, 180, 185))
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)
        painter.drawText(0, groove.top() - 2, self.width(), 12,
                         QtCore.Qt.AlignHCenter, f'{self._hi:.1f}')
        painter.drawText(0, groove.bottom() + 1, self.width(), 12,
                         QtCore.Qt.AlignHCenter, f'{self._lo:.1f}')
        painter.end()
