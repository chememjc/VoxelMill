"""A FreeCAD-style translate/rotate manipulator built from plain VTK actors.

``vtkBoxWidget``'s wireframe cube with corner handles does not read as "grab
this axis and drag" -- users have to discover which corner scales, which edge
rotates, and which face translates, and the handles are easy to miss on a
small part. This replaces it with the manipulator shape people already know
from FreeCAD/Fusion: three colored arrows for translation along an axis, and
three colored rings for rotation about an axis. It is built entirely from
``vtkPolyDataMapper``/``vtkActor`` plumbing so hit-testing and drag math are
ours to control (and to unit test) rather than living inside a VTK widget.

The interaction math (turning a screen-space pick ray into a translation
distance or a rotation angle) is split into free functions at module scope so
it can be exercised with plain numpy, no render window required. The
:class:`TransformGizmo` class wraps that math with the VTK bookkeeping:
building the handle actors, sizing them to the target, picking them, and
emitting Qt signals so a window can move the real part live during a drag and
commit the final delta on release.
"""
from __future__ import annotations

import numpy as np
from PySide6 import QtCore
import vtkmodules.all as vtk

#: Unit direction and display color (0-1 RGB) for each principal axis. The
#: translation arrow points along the direction; the rotation ring's plane is
#: normal to it (so the X ring, which rotates about X, lies in the YZ plane).
AXES = {
    'x': (np.array([1.0, 0.0, 0.0]), (0.85, 0.20, 0.20)),
    'y': (np.array([0.0, 1.0, 0.0]), (0.25, 0.75, 0.25)),
    'z': (np.array([0.0, 0.0, 1.0]), (0.25, 0.45, 0.90)),
}
_AXIS_INDEX = {'x': 0, 'y': 1, 'z': 2}

#: Handle length floor/ceiling in mm, so the gizmo stays grabbable on a 5 mm
#: part and does not swallow the screen on a 150 mm one.
MIN_HANDLE_MM = 8.0
MAX_HANDLE_MM = 60.0
#: Fraction of the target's half-diagonal used as the raw (pre-clamp) handle
#: length, chosen so the arrows clear the part instead of poking through it.
_HANDLE_FRACTION = 0.6
#: Hovered handle stays at full brightness; everything else dims by this much
#: so the highlighted axis is unambiguous at a glance.
_DIM_FACTOR = 0.45
_EPS = 1e-9


def axis_translation(ray_origin, ray_direction, axis_point, axis_direction):
    """Parameter ``s`` such that ``axis_point + s * axis_direction`` is the
    point on the axis line closest to the pick ray (classic closest-point of
    two skew lines, e.g. Ericson, *Real-Time Collision Detection* 5.1.9).

    ``s`` is a signed distance along the (unit) axis direction, which is
    exactly the translation a drag should apply: project the mouse ray onto
    the one line the handle is allowed to move along, ignore everything else.
    Returns ``None`` when the ray runs parallel to the axis, where "closest
    point" is undefined (every point is equally close).
    """
    ray_origin = np.asarray(ray_origin, dtype=float).reshape(3)
    ray_direction = np.asarray(ray_direction, dtype=float).reshape(3)
    axis_point = np.asarray(axis_point, dtype=float).reshape(3)
    axis_direction = np.asarray(axis_direction, dtype=float).reshape(3)
    rd_norm = np.linalg.norm(ray_direction)
    ad_norm = np.linalg.norm(axis_direction)
    if rd_norm < _EPS or ad_norm < _EPS:
        return None
    d1 = axis_direction / ad_norm
    d2 = ray_direction / rd_norm
    r = axis_point - ray_origin
    b = float(np.dot(d1, d2))
    f = float(np.dot(d2, r))
    c = float(np.dot(d1, r))
    denom = 1.0 - b * b
    if abs(denom) < _EPS:
        return None
    s = (b * f - c) / denom
    if not np.isfinite(s):
        return None
    return float(s)


def _arbitrary_perpendicular(normal):
    """A unit vector perpendicular to ``normal`` (which must already be unit).

    Only the *choice* of in-plane zero-angle reference matters here, not
    which one it is -- as long as the same ``normal`` always yields the same
    reference, two calls to :func:`ring_angle` for the same ring measure
    angle from a consistent baseline and their difference is a real swept
    angle.
    """
    hint = np.array([1.0, 0.0, 0.0])
    if abs(np.dot(hint, normal)) > 0.9:
        hint = np.array([0.0, 1.0, 0.0])
    u = hint - np.dot(hint, normal) * normal
    return u / np.linalg.norm(u)


def ring_angle(ray_origin, ray_direction, center, normal):
    """Angle (radians) of the pick ray's crossing of the ring's plane.

    The angle is measured in-plane from an arbitrary but fixed reference
    direction (see :func:`_arbitrary_perpendicular`); it is not itself the
    "swept angle" of a drag, but the difference between two calls -- one at
    drag start, one now -- is, which is how :class:`TransformGizmo` uses it.

    Returns ``None`` when the ray is (nearly) parallel to the ring's plane,
    when the plane lies behind the ray's origin, or when the ray crosses
    exactly on the rotation axis (no in-plane direction to measure).
    """
    ray_origin = np.asarray(ray_origin, dtype=float).reshape(3)
    ray_direction = np.asarray(ray_direction, dtype=float).reshape(3)
    center = np.asarray(center, dtype=float).reshape(3)
    normal = np.asarray(normal, dtype=float).reshape(3)
    d_norm = np.linalg.norm(ray_direction)
    n_norm = np.linalg.norm(normal)
    if d_norm < _EPS or n_norm < _EPS:
        return None
    direction = ray_direction / d_norm
    normal = normal / n_norm
    denom = float(np.dot(normal, direction))
    if abs(denom) < 1e-6:
        return None
    t = float(np.dot(normal, center - ray_origin) / denom)
    if not np.isfinite(t) or t < 0:
        return None
    point = ray_origin + t * direction
    in_plane = point - center
    u = _arbitrary_perpendicular(normal)
    v = np.cross(normal, u)
    x = float(np.dot(in_plane, u))
    y = float(np.dot(in_plane, v))
    if abs(x) < _EPS and abs(y) < _EPS:
        return None
    return float(np.arctan2(y, x))


def wrap_angle(radians):
    """Wrap an angle (radians) into ``(-pi, pi]`` so a swept delta is signed
    and never off by a full turn just because it crossed the branch cut."""
    return float((radians + np.pi) % (2 * np.pi) - np.pi)


def quantize(value, step):
    """Round ``value`` to the nearest multiple of ``step``; ``step <= 0`` (or
    falsy) means "no snap", so the value passes through unchanged."""
    if not step:
        return float(value)
    return float(round(value / step) * step)


class TransformGizmo(QtCore.QObject):
    """Translate/rotate handles anchored to one model actor.

    Not a VTK widget: the handles are ordinary actors this class owns and
    picks itself, so the geometry, sizing and drag math are all plain,
    testable code instead of being buried in ``vtkBoxWidget``'s C++.
    """

    #: Continuous updates during a drag: ``(index, translation_xyz, rotation_xyz_deg)``
    #: as the delta from where the drag started. Connect this to move the
    #: live VTK actor without waiting for (or rebuilding for) a commit.
    preview = QtCore.Signal(int, object, object)
    #: Final delta, emitted once on release, same shape as ``preview``.
    committed = QtCore.Signal(int, object, object)
    #: The user clicked somewhere that is neither the gizmo nor a model actor.
    dismissed = QtCore.Signal()

    def __init__(self, interactor, renderer, *, snap=None):
        super().__init__()
        self._interactor = interactor
        self._scene_renderer = renderer
        # The handles live in their own renderer, layered on top of the model
        # renderer and sharing its camera. VTK gives every renderer a fresh
        # depth buffer by default (vtkRenderer.PreserveDepthBuffer defaults
        # to False), so props in a higher layer draw over a lower layer's
        # geometry regardless of which is nearer the camera -- exactly what
        # "the gizmo must never bury itself inside the part" needs, without
        # touching the model's own depth testing at all.
        self._renderer = vtk.vtkRenderer()
        self._renderer.SetLayer(1)
        self._renderer.SetActiveCamera(renderer.GetActiveCamera())
        # This renderer never drives the camera itself -- the base layer's
        # trackball interactor style already does that for the shared camera.
        self._renderer.InteractiveOff()
        window = interactor.GetRenderWindow()
        window.SetNumberOfLayers(max(2, window.GetNumberOfLayers()))
        window.AddRenderer(self._renderer)
        # A plain prop picker restricted to this renderer: it only ever sees
        # the handle actors added below, so picking the gizmo can never pick
        # the model (which lives in a different renderer entirely).
        self._picker = vtk.vtkPropPicker()

        self._handles: list = []
        self._hover_actor = None
        self._target = None
        self._center = None
        self._handle_length = MIN_HANDLE_MM
        self._attached_index = None
        self._drag_handle = None
        self._drag_start_value = None
        #: Previous frame's raw sample and the running swept angle (radians),
        #: used only for rotation -- see :meth:`drag` for why the total is
        #: accumulated incrementally instead of derived from the raw value.
        self._drag_previous_value = None
        self._drag_rotation_total = 0.0
        self._drag_delta = (np.zeros(3), np.zeros(3))

        translate_mm, rotate_deg = snap if snap is not None else (0.0, 0.0)
        self._snap_translate = float(translate_mm or 0.0)
        self._snap_rotate = float(rotate_deg or 0.0)

    # ---- public API -------------------------------------------------
    @property
    def attached_index(self):
        return self._attached_index

    def set_snap(self, translate_mm, rotate_deg):
        self._snap_translate = float(translate_mm or 0.0)
        self._snap_rotate = float(rotate_deg or 0.0)

    def attach(self, actor, index):
        """Show the gizmo around ``actor``'s current bounding box.

        Safe to call again on an actor that already has the gizmo showing
        (e.g. after a reload rebuilds the geometry): it just rebuilds the
        handles at the new bounds rather than requiring a detach first.
        """
        self._clear_actors()
        self._reset_drag()
        bounds = actor.GetBounds()
        if bounds is None or len(bounds) != 6 or bounds[1] < bounds[0]:
            self._target = None
            self._attached_index = None
            return
        bounds = np.asarray(bounds, dtype=float)
        center = np.array([(bounds[0] + bounds[1]) / 2.0,
                            (bounds[2] + bounds[3]) / 2.0,
                            (bounds[4] + bounds[5]) / 2.0])
        size = np.array([bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4]])
        half_diagonal = float(np.linalg.norm(size)) / 2.0
        length = _HANDLE_FRACTION * half_diagonal
        self._handle_length = float(np.clip(length, MIN_HANDLE_MM, MAX_HANDLE_MM))
        self._target = actor
        self._center = center
        self._attached_index = int(index)
        self._build_actors()
        self._reposition(self._center)

    def detach(self):
        """Hide the handles and forget the target. Emits nothing: callers
        that want the "user clicked away" notification use :meth:`dismiss`."""
        self._clear_actors()
        self._reset_drag()
        self._target = None
        self._center = None
        self._attached_index = None

    def dismiss(self):
        """Detach because the user clicked empty space, not because a drag
        finished -- the window needs to know to clear its own selection."""
        self.detach()
        self.dismissed.emit()

    # ---- picking / hover ---------------------------------------------
    def hit_test(self, x, y):
        """``('translate' | 'rotate', axis)`` under the given display point,
        or ``None``. ``axis`` is one of ``'x'``, ``'y'``, ``'z'``."""
        actor = self._pick_actor(x, y)
        if actor is None:
            return None
        return actor.gizmo_handle

    def hover(self, x, y):
        """Brighten the handle under the cursor, dim the rest. Returns
        whether the point landed on a handle at all."""
        if not self._handles:
            return False
        actor = self._pick_actor(x, y)
        if actor is self._hover_actor:
            return actor is not None
        self._hover_actor = actor
        for handle in self._handles:
            color = handle.gizmo_base_color
            bright = handle is actor
            shown = color if bright else tuple(c * _DIM_FACTOR for c in color)
            handle.GetProperty().SetColor(*shown)
            handle.GetProperty().SetAmbient(0.55 if bright else 0.2)
        return actor is not None

    # ---- dragging -------------------------------------------------------
    def begin_drag(self, x, y):
        """Start a drag if ``(x, y)`` is on a handle. Returns whether it did."""
        if self._attached_index is None:
            return False
        handle = self.hit_test(x, y)
        if handle is None:
            return False
        self._drag_handle = handle
        self._drag_start_value = self._sample(handle, x, y)
        self._drag_previous_value = self._drag_start_value
        self._drag_rotation_total = 0.0
        self._drag_delta = (np.zeros(3), np.zeros(3))
        return True

    def drag(self, x, y):
        """Update the live preview for the drag in progress; no-op if none."""
        if self._drag_handle is None:
            return
        value = self._sample(self._drag_handle, x, y)
        if value is None:
            return  # a degenerate ray this frame; hold the last preview
        if self._drag_start_value is None:
            # The first frame of the drag happened to be degenerate (e.g. the
            # ray was exactly parallel to the axis); take the first usable
            # sample as the reference instead of reporting a bogus delta.
            self._drag_start_value = value
            self._drag_previous_value = value
            return
        kind, axis = self._drag_handle
        translation = np.zeros(3)
        rotation = np.zeros(3)
        if kind == 'translate':
            delta = quantize(value - self._drag_start_value, self._snap_translate)
            translation = AXES[axis][0] * delta
            self._reposition(self._center + translation)
        else:
            # wrap_angle(total - start) can never leave (-180, 180], so a
            # drag past half a turn would snap backwards if the total were
            # derived from the raw start/now values. Instead accumulate each
            # frame's small step (wrapped, so it is safe across the branch
            # cut) into a running total that itself has no range limit: a
            # full turn reads as 360, two turns as 720, a drag back to the
            # start as 0. Only the accumulated total is snapped -- snapping
            # each increment would let rounding error compound over a long
            # drag.
            step = wrap_angle(value - self._drag_previous_value)
            self._drag_rotation_total += step
            self._drag_previous_value = value
            delta_deg = quantize(np.degrees(self._drag_rotation_total), self._snap_rotate)
            rotation[_AXIS_INDEX[axis]] = delta_deg
        self._drag_delta = (translation, rotation)
        self.preview.emit(self._attached_index, list(translation), list(rotation))

    def end_drag(self, x, y):
        """Finish the drag in progress (if any) and emit the final delta."""
        if self._drag_handle is None:
            return
        self.drag(x, y)
        translation, rotation = self._drag_delta
        kind, _axis = self._drag_handle
        index = self._attached_index
        self._reset_drag()
        if kind == 'translate' and self._center is not None:
            # The object really did move; keep the gizmo anchored to it so
            # the next drag starts from where the part now is rather than
            # snapping the handles back to the pre-drag position.
            self._center = self._center + translation
        if self._center is not None:
            self._reposition(self._center)
        self.committed.emit(index, list(translation), list(rotation))

    def cancel_drag(self):
        """Abandon the drag in progress, if any: no ``committed`` signal,
        and the handles go back to where this drag started.

        Only the translate handles actually move during a drag (see
        :meth:`drag`), so restoring ``self._center`` undoes that; a rotate
        drag never repositioned anything to begin with. The live preview
        drawn on the actual model actor is the caller's to undo -- see
        ``Viewport.object_transform_cancelled``.
        """
        if self._drag_handle is None:
            return
        self._reset_drag()
        if self._center is not None:
            self._reposition(self._center)

    def _reset_drag(self):
        self._drag_handle = None
        self._drag_start_value = None
        self._drag_previous_value = None
        self._drag_rotation_total = 0.0
        self._drag_delta = (np.zeros(3), np.zeros(3))

    def _sample(self, handle, x, y):
        kind, axis = handle
        ray_origin, ray_direction = self._pick_ray(x, y)
        if kind == 'translate':
            return axis_translation(ray_origin, ray_direction, self._center, AXES[axis][0])
        return ring_angle(ray_origin, ray_direction, self._center, AXES[axis][0])

    # ---- VTK plumbing -----------------------------------------------------
    def _pick_ray(self, x, y):
        """World-space ray under display point ``(x, y)``, near point and
        direction, via the standard display-to-world unprojection (pick the
        near and far clip points and subtract)."""
        renderer = self._renderer
        renderer.SetDisplayPoint(x, y, 0.0)
        renderer.DisplayToWorld()
        near = np.array(renderer.GetWorldPoint())
        renderer.SetDisplayPoint(x, y, 1.0)
        renderer.DisplayToWorld()
        far = np.array(renderer.GetWorldPoint())
        if abs(near[3]) > _EPS:
            near = near / near[3]
        if abs(far[3]) > _EPS:
            far = far / far[3]
        origin = near[:3]
        direction = far[:3] - near[:3]
        return origin, direction

    def _pick_actor(self, x, y):
        if not self._handles:
            return None
        if not self._picker.Pick(x, y, 0, self._renderer):
            return None
        actor = self._picker.GetActor()
        if getattr(actor, 'gizmo_handle', None) is None:
            return None
        return actor

    def _clear_actors(self):
        for actor in self._handles:
            self._renderer.RemoveActor(actor)
        self._handles = []
        self._hover_actor = None

    def _reposition(self, center):
        for actor in self._handles:
            actor.SetPosition(float(center[0]), float(center[1]), float(center[2]))

    def _build_actors(self):
        length = self._handle_length
        for axis, (_direction, color) in AXES.items():
            self._handles.append(self._make_arrow(axis, length, color))
        for axis, (_direction, color) in AXES.items():
            self._handles.append(self._make_ring(axis, length, color))
        for actor in self._handles:
            self._renderer.AddActor(actor)

    def _make_arrow(self, axis, length, color):
        """Cylinder shaft + cone head along the target axis. ``vtkArrowSource``
        builds exactly that shape, unit length along +X; orienting and scaling
        it is cheaper and more robust than assembling cylinder+cone by hand."""
        source = vtk.vtkArrowSource()
        source.SetTipLength(0.35)
        source.SetTipRadius(0.12)
        source.SetShaftRadius(0.045)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(source.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetScale(length)
        # A single-axis rotation is unambiguous regardless of VTK's XYZ
        # orientation-composition order, so each arrow only ever needs one.
        if axis == 'y':
            actor.SetOrientation(0, 0, 90)   # +X shaft -> +Y
        elif axis == 'z':
            actor.SetOrientation(0, -90, 0)  # +X shaft -> +Z
        self._style(actor, ('translate', axis), color)
        return actor

    def _make_ring(self, axis, length, color):
        """A torus in the plane normal to ``axis`` -- the X ring (rotates
        about X) lies in the YZ plane, and so on."""
        torus = vtk.vtkParametricTorus()
        torus.SetRingRadius(1.0)
        torus.SetCrossSectionRadius(0.045)
        source = vtk.vtkParametricFunctionSource()
        source.SetParametricFunction(torus)
        source.SetUResolution(48)
        source.SetVResolution(12)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(source.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.SetScale(length)
        # The torus is built flat in the XY plane (hole along Z); tip it onto
        # its side for the X and Y rings, one axis of rotation each.
        if axis == 'x':
            actor.SetOrientation(0, 90, 0)
        elif axis == 'y':
            actor.SetOrientation(90, 0, 0)
        self._style(actor, ('rotate', axis), color)
        return actor

    @staticmethod
    def _style(actor, handle, color):
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetAmbient(0.2)
        actor.PickableOn()
        actor.gizmo_handle = handle
        actor.gizmo_base_color = color
