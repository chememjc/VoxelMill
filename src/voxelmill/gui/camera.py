"""Named camera views and the navigation cube's state machine.

Deliberately free of Qt and of any render window: the mapping from a cube face
to a camera pose is arithmetic, and arithmetic can be checked without a display.
Only :class:`~voxelmill.gui.viewport.Viewport` knows about widgets and timers.

Faces are labeled in printer terms rather than axis letters, and **front is
-Y**, which is the same face the green build-volume edge marks. Two independent
cues saying the same thing is the point: a user should not have to remember
which way the machine faces.
"""
from __future__ import annotations

import math

import numpy as np

#: ``name -> (direction from the focal point to the camera, view up)``.
#: The camera looks along the negation of the direction.
VIEWS = {
    'front':  ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    'back':   ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    'left':   ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    'right':  ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    'top':    ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    'bottom': ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
    # Looking at the front-right-top corner, so the green front edge and the
    # plate are both visible on opening. Shallower than ``iso_+-+`` on purpose:
    # home is a working view, the cube corner is a true isometric.
    'iso':    ((1.0, -1.0, 0.8), (0.0, 0.0, 1.0)),
}

# Eight cube-corner isometrics. Signs are the camera-direction octant
# (+X right, +Y back, +Z top), matching the chamfered-cube pick positions.
for _sx, _sy, _sz in (
        (1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1),
        (-1, 1, 1), (-1, 1, -1), (-1, -1, 1), (-1, -1, -1)):
    VIEWS['iso_' + ('+' if _sx > 0 else '-') + ('+' if _sy > 0 else '-')
          + ('+' if _sz > 0 else '-')] = (
        (float(_sx), float(_sy), float(_sz)), (0.0, 0.0, 1.0))
del _sx, _sy, _sz

# Twelve cube-edge views, one per 45 degree bevel facet. Naming mirrors the
# corner isos with ``0`` marking the axis the bevel is flat along, so
# ``edge_+-0`` is the front-right vertical bevel. A plain Z up is safe for all
# twelve: ``orthonormal_up`` only fails when the direction is exactly +/-Z, and
# every edge direction keeps a nonzero horizontal component by construction.
for _a, _b in ((0, 1), (0, 2), (1, 2)):
    for _sa in (1, -1):
        for _sb in (1, -1):
            _chars = ['0', '0', '0']
            _chars[_a] = '+' if _sa > 0 else '-'
            _chars[_b] = '+' if _sb > 0 else '-'
            _direction = [0.0, 0.0, 0.0]
            _direction[_a], _direction[_b] = float(_sa), float(_sb)
            VIEWS['edge_' + ''.join(_chars)] = (tuple(_direction), (0.0, 0.0, 1.0))
del _a, _b, _sa, _sb, _chars, _direction

HOME_VIEW = 'iso'

#: Keyboard shortcuts, mirrored by the View menu. Corner isos are cube picks,
#: not menu items, so they stay out of this map.
SHORTCUTS = {'front': 'Ctrl+1', 'back': 'Ctrl+2', 'left': 'Ctrl+3', 'right': 'Ctrl+4',
             'top': 'Ctrl+5', 'bottom': 'Ctrl+6', 'iso': 'Ctrl+0'}

#: Face labels carried by the navigation cube, and the view each one selects.
#: Picking the +X face means "show me the right side", which is the ``right``
#: view -- the camera moves to +X, it does not look toward +X.
FACE_VIEWS = {'Front': 'front', 'Back': 'back', 'Left': 'left',
              'Right': 'right', 'Top': 'top', 'Bottom': 'bottom'}

#: FreeCAD-style orbit arrows around the cube, in view space.
ARROW_TURNS = ('left', 'right', 'up', 'down')

#: The two in-plane roll buttons flanking the up arrow.
ROLL_TURNS = ('left', 'right')

#: Degrees one arrow or roll click moves. 45 matches the cube's bevel facets,
#: so every click lands on a facet the cube itself shows.
NAV_STEP_DEG = 45.0


def _unit(vector):
    vector = np.asarray(vector, dtype=float)
    length = float(np.linalg.norm(vector))
    if length <= 0 or not math.isfinite(length):
        raise ValueError('a view direction must be a nonzero finite vector')
    return vector / length


def orthonormal_up(direction, up):
    """The component of ``up`` perpendicular to ``direction``, normalized.

    A view up parallel to the view direction leaves the roll undefined and VTK
    silently produces a degenerate view matrix, so the top and bottom views
    carry a Y-based up rather than the Z one every side view uses.
    """
    direction = _unit(direction)
    up = np.asarray(up, dtype=float)
    residual = up - direction * float(np.dot(up, direction))
    if float(np.linalg.norm(residual)) < 1e-9:
        raise ValueError('view up is parallel to the view direction')
    return residual / float(np.linalg.norm(residual))


def _rotate_about(vector, axis, angle):
    """Rodrigues rotation of ``vector`` around unit ``axis`` by ``angle`` rad."""
    axis = _unit(axis)
    cosine, sine = math.cos(angle), math.sin(angle)
    return (vector * cosine
            + np.cross(axis, vector) * sine
            + axis * float(np.dot(axis, vector)) * (1.0 - cosine))


def rotate_view_by(direction, up, turn, degrees=NAV_STEP_DEG):
    """Orbit a camera pose ``degrees`` in view space.

    ``turn`` is one of :data:`ARROW_TURNS` (the four FreeCAD NavCube arrows).
    ``direction`` is camera-minus-focal, the same convention as :data:`VIEWS`.
    Both the direction and the up vector rotate, so a turn from front onto
    top cannot leave the camera with an up parallel to its view direction.
    """
    if turn not in ARROW_TURNS:
        raise ValueError(f'unknown orbit {turn!r}; expected one of {ARROW_TURNS}')
    degrees = float(degrees)
    if not math.isfinite(degrees):
        raise ValueError('an orbit step must be a finite number of degrees')
    direction = _unit(direction)
    up = orthonormal_up(direction, up)
    # VTK view-right: ViewUp × (Position - FocalPoint).
    right = _unit(np.cross(up, direction))
    step = math.radians(degrees)
    if turn == 'right':
        axis, angle = up, step
    elif turn == 'left':
        axis, angle = up, -step
    elif turn == 'up':
        axis, angle = right, -step
    else:
        axis, angle = right, step
    new_direction = _rotate_about(direction, axis, angle)
    new_up = _rotate_about(up, axis, angle)
    return _unit(new_direction), orthonormal_up(new_direction, new_up)


def rotate_view_90(direction, up, turn):
    """Quarter-turn orbit, kept for callers that want face-to-face steps."""
    return rotate_view_by(direction, up, turn, degrees=90.0)


def roll_view_by(direction, up, turn, degrees=NAV_STEP_DEG):
    """Roll a camera pose about its own view axis, leaving the direction alone.

    This is the in-plane screen rotation the two buttons above the cube apply:
    what the viewer sees turns, but the camera does not move around the model.
    A world-Z yaw would only duplicate the left and right orbit arrows.
    """
    if turn not in ROLL_TURNS:
        raise ValueError(f'unknown roll {turn!r}; expected one of {ROLL_TURNS}')
    degrees = float(degrees)
    if not math.isfinite(degrees):
        raise ValueError('a roll step must be a finite number of degrees')
    direction = _unit(direction)
    up = orthonormal_up(direction, up)
    angle = math.radians(degrees) * (1.0 if turn == 'right' else -1.0)
    return direction, orthonormal_up(direction, _rotate_about(up, direction, angle))


def named_view_near(direction, up, max_degrees=12.0):
    """Named view matching both direction and roll, or None.

    Direction-only matching would "unroll" a 90° orbit that left a side face
    with Y up (the honest result of turning right from top) onto the canned
    Z-up ``right`` view. Both vectors have to agree.
    """
    direction = _unit(direction)
    up = orthonormal_up(direction, up)
    limit = math.cos(math.radians(max_degrees))
    best_name, best_score = None, -1.0
    for name, (view_direction, view_up) in VIEWS.items():
        view_direction = _unit(view_direction)
        view_up = orthonormal_up(view_direction, view_up)
        score = (float(np.dot(direction, view_direction))
                 + float(np.dot(up, view_up))) * 0.5
        if score > best_score:
            best_name, best_score = name, score
    if best_score >= limit:
        return best_name
    return None


class CameraController:
    """Named views, fit and home for one VTK renderer.

    ``set_view`` orients the camera and then lets the renderer fit the scene
    along that direction, so a view is always framed on what is actually
    loaded rather than on a distance guessed in advance. ``reset_camera`` on
    the viewport becomes one caller of this rather than the only camera code in
    the program.
    """

    def __init__(self, renderer):
        self.renderer = renderer
        self.current = None

    # ---- queries --------------------------------------------------------
    @property
    def camera(self):
        return self.renderer.GetActiveCamera()

    def focal_point(self):
        """Center of the visible scene, or the origin when nothing is loaded."""
        bounds = self.renderer.ComputeVisiblePropBounds()
        if bounds[0] > bounds[1]:
            return np.zeros(3)
        bounds = np.asarray(bounds, dtype=float).reshape(3, 2)
        return bounds.mean(axis=1)

    def pose(self, name, distance=None):
        """``(position, focal_point, view_up)`` for a named view, unapplied."""
        if name not in VIEWS:
            raise KeyError(f'unknown view {name!r}; expected one of {sorted(VIEWS)}')
        direction, up = VIEWS[name]
        direction = _unit(direction)
        focal = self.focal_point()
        if distance is None:
            distance = float(self.camera.GetDistance()) or 1.0
        return focal + direction * distance, focal, orthonormal_up(direction, up)

    # ---- commands -------------------------------------------------------
    def set_view(self, name):
        """Point the camera along a named view and frame the scene on it."""
        position, focal, up = self.pose(name)
        camera = self.camera
        camera.SetFocalPoint(*focal)
        camera.SetPosition(*position)
        camera.SetViewUp(*up)
        camera.SetViewAngle(30.0)
        # ResetCamera moves the camera along its current view vector, so the
        # direction set above survives and only the framing changes.
        self.fit()
        self.current = name
        return name

    def home(self):
        return self.set_view(HOME_VIEW)

    def _move_to(self, new_direction, new_up, focal, direction):
        """Apply an orbited or rolled pose, snapping to a named view if one fits."""
        name = named_view_near(new_direction, new_up)
        if name is not None:
            return self.set_view(name)
        distance = float(np.linalg.norm(direction)) or 1.0
        self.apply((focal + new_direction * distance, focal, new_up))
        self.current = None
        return None

    def _pose_now(self):
        camera = self.camera
        focal = np.asarray(camera.GetFocalPoint(), dtype=float)
        direction = np.asarray(camera.GetPosition(), dtype=float) - focal
        return focal, direction, np.asarray(camera.GetViewUp(), dtype=float)

    def rotate_by(self, turn, degrees=NAV_STEP_DEG):
        """Orbit the current camera; snap to a named view when the roll matches."""
        focal, direction, up = self._pose_now()
        return self._move_to(*rotate_view_by(direction, up, turn, degrees=degrees),
                             focal, direction)

    def rotate_90(self, turn):
        """Quarter-turn orbit of the current camera."""
        return self.rotate_by(turn, degrees=90.0)

    def roll(self, turn, degrees=NAV_STEP_DEG):
        """Roll the current camera in view plane; snap when the pose matches a view."""
        focal, direction, up = self._pose_now()
        return self._move_to(*roll_view_by(direction, up, turn, degrees=degrees),
                             focal, direction)

    def fit(self):
        self.renderer.ResetCamera()
        self.renderer.ResetCameraClippingRange()

    # ---- transitions ----------------------------------------------------
    def steps_toward(self, direction, up, steps=12):
        """Intermediate poses toward an explicit camera direction and up."""
        steps = max(1, int(steps))
        focal = self.focal_point()
        distance = float(self.camera.GetDistance()) or 1.0
        start_dir = _unit(np.asarray(self.camera.GetPosition(), dtype=float) - focal)
        start_up = np.asarray(self.camera.GetViewUp(), dtype=float)
        end_dir = _unit(direction)
        try:
            end_up = orthonormal_up(end_dir, up)
        except ValueError:
            end_up = np.asarray(up, dtype=float)
        poses = []
        for step in range(1, steps + 1):
            fraction = step / steps
            heading = (1 - fraction) * start_dir + fraction * end_dir
            if float(np.linalg.norm(heading)) < 1e-9:
                heading = end_dir           # exactly opposite views
            else:
                heading = _unit(heading)
            rolled = (1 - fraction) * start_up + fraction * end_up
            try:
                rolled = orthonormal_up(heading, rolled)
            except ValueError:
                rolled = end_up
            poses.append((focal + heading * distance, focal, rolled))
        return poses

    def steps_to(self, name, steps=12):
        """Intermediate ``(position, focal, up)`` poses toward a named view.

        The direction is interpolated on the sphere and renormalized each step,
        so the camera swings around the model rather than cutting through it.
        A single step is a jump, which is what a headless caller wants.
        """
        if name not in VIEWS:
            raise KeyError(f'unknown view {name!r}; expected one of {sorted(VIEWS)}')
        direction, up = VIEWS[name]
        return self.steps_toward(direction, up, steps)

    def apply(self, pose):
        position, focal, up = pose
        camera = self.camera
        camera.SetFocalPoint(*focal)
        camera.SetPosition(*position)
        camera.SetViewUp(*up)
        self.renderer.ResetCameraClippingRange()
