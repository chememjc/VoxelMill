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
    # plate are both visible on opening.
    'iso':    ((1.0, -1.0, 0.8), (0.0, 0.0, 1.0)),
}

HOME_VIEW = 'iso'

#: Keyboard shortcuts, mirrored by the View menu.
SHORTCUTS = {'front': 'Ctrl+1', 'back': 'Ctrl+2', 'left': 'Ctrl+3', 'right': 'Ctrl+4',
             'top': 'Ctrl+5', 'bottom': 'Ctrl+6', 'iso': 'Ctrl+0'}

#: Face labels carried by the annotated cube, and the view each one selects.
#: Picking the +X face means "show me the right side", which is the ``right``
#: view -- the camera moves to +X, it does not look toward +X.
FACE_VIEWS = {'Front': 'front', 'Back': 'back', 'Left': 'left',
              'Right': 'right', 'Top': 'top', 'Bottom': 'bottom'}


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

    def fit(self):
        self.renderer.ResetCamera()
        self.renderer.ResetCameraClippingRange()

    # ---- transitions ----------------------------------------------------
    def steps_to(self, name, steps=12):
        """Intermediate ``(position, focal, up)`` poses toward a named view.

        The direction is interpolated on the sphere and renormalized each step,
        so the camera swings around the model rather than cutting through it.
        A single step is a jump, which is what a headless caller wants.
        """
        steps = max(1, int(steps))
        focal = self.focal_point()
        distance = float(self.camera.GetDistance()) or 1.0
        start_dir = _unit(np.asarray(self.camera.GetPosition(), dtype=float) - focal)
        start_up = np.asarray(self.camera.GetViewUp(), dtype=float)
        end_position, _focal, end_up = self.pose(name, distance)
        end_dir = _unit(end_position - focal)
        poses = []
        for step in range(1, steps + 1):
            fraction = step / steps
            direction = (1 - fraction) * start_dir + fraction * end_dir
            if float(np.linalg.norm(direction)) < 1e-9:
                direction = end_dir           # exactly opposite views
            else:
                direction = _unit(direction)
            up = (1 - fraction) * start_up + fraction * end_up
            try:
                up = orthonormal_up(direction, up)
            except ValueError:
                up = end_up
            poses.append((focal + direction * distance, focal, up))
        return poses

    def apply(self, pose):
        position, focal, up = pose
        camera = self.camera
        camera.SetFocalPoint(*focal)
        camera.SetPosition(*position)
        camera.SetViewUp(*up)
        self.renderer.ResetCameraClippingRange()
