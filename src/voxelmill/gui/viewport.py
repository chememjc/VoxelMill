"""VTK 3D view: model, supports, raft, contacts and the build volume.

Display may be decimated; validation never is. When a mesh is too large to
render interactively the viewport shows a strided preview and says so through
:attr:`preview_note`, so nothing on screen is mistaken for the checked geometry.
"""
from __future__ import annotations

import sys

import numpy as np
from PySide6 import QtCore, QtWidgets
import vtkmodules.all as vtk
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.util import numpy_support


def vtk_render_backend():
    """Which VTK/Qt pairing this platform uses for the 3D view."""
    return 'native'


class _VTKInteractor(QVTKRenderWindowInteractor):
    """QVTK widget that must not Render inside a Cocoa CATransaction.

    On Intel macOS 26, ``QVTKRenderWindowInteractor.paintEvent`` calls
    ``Render()`` from ``-[_NSOpenGLViewBackingLayer display]`` during
    ``CATransaction::commit``. That is a synchronous expose: 0.5.0 hung in
    FeatureEdges there, and 0.5.1 still hung in ``vtkCocoaRenderWindow::Start``
    after the annotated cube was removed. ``vtkGenericOpenGLRenderWindow``
    crashed on the same machine. Deferring the VTK render until the event
    loop is idle lets the transaction finish.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paint_queued = False

    def paintEvent(self, ev):
        if sys.platform != 'darwin':
            return QVTKRenderWindowInteractor.paintEvent(self, ev)
        if self._paint_queued:
            return
        self._paint_queued = True
        QtCore.QTimer.singleShot(0, self._flush_deferred_paint)

    def _flush_deferred_paint(self):
        self._paint_queued = False
        try:
            self.Render()
        except Exception:
            pass


def make_vtk_interactor(parent=None):
    return _VTKInteractor(parent)

from .camera import (
    ARROW_TURNS, CameraController, FACE_VIEWS, HOME_VIEW as CAMERA_HOME,
    NAV_STEP_DEG, ROLL_TURNS, named_view_near, roll_view_by, rotate_view_by,
)
from .gizmo import TransformGizmo

LAYER_COLORS = {
    'model': (0.78, 0.79, 0.82),
    'supports': (0.28, 0.58, 0.86),
    'raft': (0.36, 0.40, 0.46),
    'contacts': (0.95, 0.62, 0.22),
}


#: Triangles the printer cannot reach are drawn in this color.  It has to read
#: as a fault at a glance, so it is the same red as the build-volume edges.
OUT_OF_BOUNDS_COLOR = (230, 60, 50)
BLOCKED_PAINT_COLOR = (190, 55, 110)
ENFORCED_PAINT_COLOR = (50, 150, 85)


def out_of_bounds_mask(triangles, settings):
    """Per-triangle: does any vertex lie outside the usable build envelope?

    Vertex-wise rather than centroid-wise, because a triangle straddling the
    edge is partly unprintable and coloring it whole is the honest reading.
    """
    triangles = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
    build = np.asarray(settings['printer']['build_mm'], dtype=float)
    clearance = float(settings['printer'].get('edge_clearance_mm', 2.))
    limit = build[:2] / 2 - clearance
    outside = ((triangles[..., 0] < -limit[0]) | (triangles[..., 0] > limit[0])
               | (triangles[..., 1] < -limit[1]) | (triangles[..., 1] > limit[1])
               | (triangles[..., 2] < 0.0) | (triangles[..., 2] > build[2]))
    return outside.any(axis=1)


def paint_face_colors(triangles, paint, radius, base_color):
    """Per-triangle RGB: enforced, then blocked; caller overlays out-of-bounds."""
    triangles = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
    colors = np.empty((len(triangles), 3), dtype=np.uint8)
    colors[:] = tuple(int(round(255 * c)) for c in base_color)
    if not paint or not len(triangles):
        return colors
    from ..paint import normalize_paint
    from scipy.spatial import cKDTree
    paint = normalize_paint(paint)
    centroids = triangles.mean(axis=1)
    radius = float(radius)
    if paint['enforced']:
        near = cKDTree(np.asarray(paint['enforced'], dtype=float)).query(
            centroids, distance_upper_bound=radius)[0]
        colors[np.isfinite(near)] = ENFORCED_PAINT_COLOR
    if paint['blocked']:
        near = cKDTree(np.asarray(paint['blocked'], dtype=float)).query(
            centroids, distance_upper_bound=radius)[0]
        colors[np.isfinite(near)] = BLOCKED_PAINT_COLOR
    return colors


def polydata_from_triangles(triangles, settings=None, base_color=None, face_colors=None):
    """Triangle soup as VTK geometry, optionally flagged against the envelope.

    Geometry is one three-point cell per triangle with unshared vertices, so
    per-cell colors need no vertex reindexing.  The colors live on the same
    actor rather than a second one on purpose: ``handle_pick`` only accepts a
    support contact on the actor whose ``role`` is ``'model'``, and splitting
    out-of-bounds triangles into their own actor would silently stop supports
    being placed on them.
    """
    triangles = np.ascontiguousarray(triangles, dtype=np.float32).reshape(-1, 3, 3)
    points = triangles.reshape(-1, 3)
    data = vtk.vtkPolyData()
    holder = vtk.vtkPoints()
    holder.SetData(numpy_support.numpy_to_vtk(points, deep=True))
    data.SetPoints(holder)
    count = len(triangles)
    offsets = np.arange(0, 3 * (count + 1), 3, dtype=np.int64)
    connectivity = np.arange(count * 3, dtype=np.int64)
    array = vtk.vtkCellArray()
    array.SetData(numpy_support.numpy_to_vtkIdTypeArray(offsets, deep=True),
                  numpy_support.numpy_to_vtkIdTypeArray(connectivity, deep=True))
    data.SetPolys(array)
    if settings is not None or face_colors is not None:
        outside = out_of_bounds_mask(triangles, settings) if settings is not None else None
        if face_colors is None:
            base = tuple(int(round(255 * c)) for c in (base_color or (0.78, 0.79, 0.82)))
            colors = np.empty((count, 3), dtype=np.uint8)
            colors[:] = base
        else:
            colors = np.asarray(face_colors, dtype=np.uint8).reshape(count, 3).copy()
        if outside is not None:
            colors[outside] = OUT_OF_BOUNDS_COLOR
        scalars = numpy_support.numpy_to_vtk(np.ascontiguousarray(colors), deep=True)
        scalars.SetName('BoundsColors')
        data.GetCellData().SetScalars(scalars)
    return data


#: Navigation cube geometry: which face a pick position lands on. Written as a
#: pure function of the position in the cube's own space so it can be checked
#: without a render window, which is the only part of the cube that has real
#: logic in it.
_CUBE_FACES = {(0, 1): 'Right', (0, -1): 'Left', (1, 1): 'Back',
               (1, -1): 'Front', (2, 1): 'Top', (2, -1): 'Bottom'}

#: Half-extent of the pickable body. Faces sit on this cube; corners are cut
#: back so a click there is an isometric rather than a coin-flip between faces.
CUBE_HALF = 0.5
#: In-plane half-width of each square face. Larger than the corner triangles
#: so "Bottom" still has room, small enough that a corner is a real target.
CUBE_FACE_HALF = 0.28
#: Invisible bounds pad so the orientation marker frames a margin around the
#: cube for the orbit arrows and the two roll buttons.
#:
#: ``ResetCamera`` frames the bounding *sphere* of these bounds, so the cube's
#: silhouette reaches ``0.5 + 0.5 * CUBE_HALF / (CUBE_PAD_HALF * sqrt(3))`` in
#: the marker's normalized viewport -- only 0.69 at the old 0.75, which is why
#: the arrows sat so far out. Barely above ``CUBE_HALF`` puts the cube's edge
#: at 0.76, close under the glyphs and still clear of their touch zones.
CUBE_PAD_HALF = 0.55
#: Longest face label ("Bottom") fills this fraction of the face square.
CUBE_LABEL_FILL = 0.70
CUBE_LABEL_LIFT = 0.01
#: Facet outlines are pushed this far along their own normal. A geometric
#: offset computed once beats any coincident-topology render state: nothing
#: runs during a paint, which is what the Cocoa expose hang taught us.
_OUTLINE_LIFT = 0.004

#: Arrow glyph centres in the marker's normalized viewport. Close to the cube
#: because the hit test follows the glyphs rather than claiming an outer band.
ARROW_CENTRES_UV = {'right': (0.86, 0.50), 'left': (0.14, 0.50),
                    'up': (0.50, 0.86), 'down': (0.50, 0.14)}
#: The two roll buttons share the up arrow's row and flank it.
ROLL_CENTRES_UV = {'left': (0.30, 0.86), 'right': (0.70, 0.86)}
#: Each arrow triangle is grown this much about its centre for touch slack.
ARROW_TOUCH_GROW = 1.6
ROLL_TOUCH_RADIUS = 0.06

_FACE_RGB = (184, 190, 198)
_EDGE_RGB = (138, 146, 156)
_CORNER_RGB = (158, 166, 176)
_ARROW_RGB = (0.18, 0.42, 0.78)

#: Face captions: (label, outward normal, vtk orientation in degrees).
_FACE_CAPTIONS = (
    ('Right',  (1.0, 0.0, 0.0), (90, 90, 0)),
    ('Left',   (-1.0, 0.0, 0.0), (90, -90, 0)),
    ('Back',   (0.0, 1.0, 0.0), (90, 0, 180)),
    ('Front',  (0.0, -1.0, 0.0), (90, 0, 0)),
    ('Top',    (0.0, 0.0, 1.0), (0, 0, 0)),
    ('Bottom', (0.0, 0.0, -1.0), (180, 0, 0)),
)


def _iso_name(position):
    return 'iso_' + ''.join('+' if float(c) > 0 else '-' for c in position)


def _edge_name(position):
    """View name for a pick on one of the twelve 45° bevel facets.

    The axis the bevel is flat along is the smallest coordinate and reads as
    ``0``, so the front-right vertical bevel is ``edge_+-0``.
    """
    flat = int(np.argmin(np.abs(np.asarray(position, dtype=float))))
    return 'edge_' + ''.join(
        '0' if index == flat else ('+' if float(value) > 0 else '-')
        for index, value in enumerate(position))


def _marker_uv(u, v):
    """A finite normalized marker-viewport point, or None."""
    try:
        u, v = float(u), float(v)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(u) and np.isfinite(v)):
        return None
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        return None
    return u, v


def _inside_polygon(point, vertices, grow=1.0):
    """Whether ``point`` is inside a convex polygon grown about its centroid."""
    polygon = np.asarray(vertices, dtype=float).reshape(-1, 2)
    centre = polygon.mean(axis=0)
    polygon = centre + (polygon - centre) * float(grow)
    point = np.asarray(point, dtype=float).reshape(2)
    edges = np.roll(polygon, -1, axis=0) - polygon
    cross = (edges[:, 0] * (point[1] - polygon[:, 1])
             - edges[:, 1] * (point[0] - polygon[:, 0]))
    return bool(np.all(cross >= -1e-12) or np.all(cross <= 1e-12))


def cube_arrow_at(u, v, grow=ARROW_TOUCH_GROW):
    """Orbit arrow under a normalized marker-viewport point, or None.

    Tested against each arrow's own triangle rather than an outer band. A band
    would have to be wider than the cube's silhouette to stay unambiguous, and
    :meth:`Viewport.cube_hit_under` consults the arrows before it picks the
    body, so a band is exactly what used to force the arrows far out from a
    small cube. ``grow`` scales each triangle about its centre for touch
    tolerance without moving the glyph.
    """
    point = _marker_uv(u, v)
    if point is None:
        return None
    for direction in ARROW_TURNS:
        if _inside_polygon(point, _arrow_triangle_uv(direction), grow):
            return direction
    return None


def cube_roll_at(u, v, radius=ROLL_TOUCH_RADIUS):
    """Roll button under a normalized marker-viewport point, or None.

    The two buttons sit on the top row flanking the up arrow, so they are
    tested before the arrows and their zones must not reach ``u = 0.5``.
    """
    point = _marker_uv(u, v)
    if point is None:
        return None
    for turn in ROLL_TURNS:
        centre = np.asarray(ROLL_CENTRES_UV[turn], dtype=float)
        if float(np.hypot(*(np.asarray(point) - centre))) <= float(radius):
            return turn
    return None


def _arrow_from_cube_point(position):
    """3D counterpart of :func:`cube_arrow_at` for a point outside the body."""
    x, y, z = (float(c) for c in position)
    name, value = max(
        (('right', x), ('left', -x), ('up', z), ('down', -z)),
        key=lambda item: item[1])
    second = sorted((abs(x), abs(y), abs(z)))[1]
    if value < CUBE_HALF + 0.08 or second > 0.25:
        return None
    return name


def cube_hit_at(position, tolerance=0.15):
    """Classify a navigation-cube pick.

    A 3-vector is cube-local: ``('face', 'Front')``, ``('edge', 'edge_+-0')``
    for one of the twelve 45° bevels, ``('corner', 'iso_+-+')``,
    ``('arrow', 'right')`` for a point clearly outside the body, or ``None``
    when the hit is too close to the centre. A 2-vector is a normalized
    marker-viewport coordinate and names a roll button or an orbit arrow.
    """
    position = np.asarray(position, dtype=float).reshape(-1)
    if position.shape == (2,):
        roll = cube_roll_at(position[0], position[1])
        if roll is not None:
            return ('roll', roll)
        arrow = cube_arrow_at(position[0], position[1])
        return ('arrow', arrow) if arrow is not None else None
    if position.shape != (3,) or not np.isfinite(position).all():
        return None
    abspos = np.abs(position)
    peak = float(abspos.max())
    if peak < tolerance:
        return None
    if peak >= CUBE_HALF + 0.08:
        arrow = _arrow_from_cube_point(position)
        return ('arrow', arrow) if arrow is not None else None
    ordered = np.sort(abspos)
    # Corner triangle: all three axes are out near the chamfer.
    if ordered[0] >= CUBE_FACE_HALF - 0.02:
        return ('corner', _iso_name(position))
    axis = int(np.argmax(abspos))
    others = [abspos[i] for i in range(3) if i != axis]
    if max(others) <= CUBE_FACE_HALF + 0.02:
        sign = 1 if position[axis] > 0 else -1
        return ('face', _CUBE_FACES[(axis, sign)])
    # Two axes out at the chamfer and one flat: a 45° bevel facet. The cube
    # has drawn these twelve since the start; only the classifier was missing
    # them, so clicking one did nothing while its corners and faces worked.
    if ordered[1] > CUBE_FACE_HALF + 0.02:
        return ('edge', _edge_name(position))
    return None


def cube_face_at(position, tolerance=0.15):
    """Face label for a pick on the navigation cube, or None near its center.

    Kept as the face-only helper so existing tests and the xvfb Front pick
    stay on the six printer faces. Corners, bevel edges, arrows and the roll
    buttons are :func:`cube_hit_at`.
    """
    hit = cube_hit_at(position, tolerance)
    if hit is not None and hit[0] == 'face':
        return hit[1]
    return None


def _lifted_loop(points):
    """A facet's perimeter, pushed out along its own normal.

    The lift is what keeps the outline off the surface it traces. Doing it in
    the geometry, once, avoids any coincident-topology render state: nothing
    the outline needs may run inside a paint.
    """
    loop = [np.asarray(p, dtype=float) for p in points]
    normal = np.cross(loop[1] - loop[0], loop[2] - loop[0])
    length = float(np.linalg.norm(normal))
    if length <= 0:
        return loop
    normal = normal / length
    if float(np.dot(normal, np.mean(loop, axis=0))) < 0:
        normal = -normal
    return [point + normal * _OUTLINE_LIFT for point in loop]


def _emit_triangle(triangles, colors, points, rgb, loops=None):
    tri = [np.asarray(p, dtype=float) for p in points]
    normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
    if float(np.dot(normal, np.mean(tri, axis=0))) < 0:
        tri = [tri[0], tri[2], tri[1]]
    triangles.append(tri)
    colors.append(rgb)
    if loops is not None:
        loops.append(_lifted_loop(tri))


def _emit_quad(triangles, colors, p0, p1, p2, p3, rgb, loops=None):
    # The two inner calls never collect: their shared diagonal is exactly the
    # line that used to be drawn across every face.
    _emit_triangle(triangles, colors, (p0, p1, p2), rgb)
    _emit_triangle(triangles, colors, (p0, p2, p3), rgb)
    if loops is not None:
        loops.append(_lifted_loop((p0, p1, p2, p3)))


def chamfered_cube_triangles(half=CUBE_HALF, face=CUBE_FACE_HALF):
    """Unit-sized cube with truncated corners and edges.

    Six squares, twelve rectangles, eight triangles -- FreeCAD's 26-pick
    NavCube layout -- still bounded by ``[-half, half]`` so picking math stays
    in cube space.

    Returns ``(triangles, colors, loops)``. ``loops`` is one closed perimeter
    per facet, 26 in all, collected here rather than reconstructed later so
    the outline cannot drift from the surface it traces.
    """
    half, face = float(half), float(face)
    triangles, colors, loops = [], [], []
    for axis in range(3):
        for sign in (-1.0, 1.0):
            u_axis, v_axis = (axis + 1) % 3, (axis + 2) % 3

            def pt(su, sv, axis=axis, sign=sign, u_axis=u_axis, v_axis=v_axis):
                point = np.zeros(3)
                point[axis] = sign * half
                point[u_axis] = su * face
                point[v_axis] = sv * face
                return point

            _emit_quad(triangles, colors, pt(-1, -1), pt(1, -1), pt(1, 1), pt(-1, 1),
                       _FACE_RGB, loops)
    for a in range(3):
        for b in range(a + 1, 3):
            e = 3 - a - b
            for sa in (-1.0, 1.0):
                for sb in (-1.0, 1.0):

                    def pt_a(se, a=a, b=b, e=e, sa=sa, sb=sb):
                        point = np.zeros(3)
                        point[a] = sa * half
                        point[b] = sb * face
                        point[e] = se * face
                        return point

                    def pt_b(se, a=a, b=b, e=e, sa=sa, sb=sb):
                        point = np.zeros(3)
                        point[a] = sa * face
                        point[b] = sb * half
                        point[e] = se * face
                        return point

                    _emit_quad(triangles, colors, pt_a(-1), pt_a(1), pt_b(1), pt_b(-1),
                               _EDGE_RGB, loops)
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                _emit_triangle(triangles, colors, (
                    np.array([sx * half, sy * face, sz * face]),
                    np.array([sx * face, sy * half, sz * face]),
                    np.array([sx * face, sy * face, sz * half]),
                ), _CORNER_RGB, loops)
    return (np.asarray(triangles, dtype=np.float32),
            np.asarray(colors, dtype=np.uint8), loops)


def _vector_text_scale(text, face_size, fill=CUBE_LABEL_FILL):
    vec = vtk.vtkVectorText()
    vec.SetText(text)
    vec.Update()
    bounds = vec.GetOutput().GetBounds()
    longest = max(bounds[1] - bounds[0], bounds[3] - bounds[2], 1e-9)
    return fill * float(face_size) / longest


def _centered_label_actor(text, face_center, orientation, scale):
    """``vtkVectorText`` origin is lower-left; place the glyph's AABB centre."""
    vec = vtk.vtkVectorText()
    vec.SetText(text)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(vec.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.SetScale(scale, scale, scale)
    actor.SetOrientation(*orientation)
    actor.GetProperty().SetColor(0.1, 0.1, 0.1)
    actor.SetPosition(0.0, 0.0, 0.0)
    mapper.Update()
    bounds = actor.GetBounds()
    center = np.array((0.5 * (bounds[0] + bounds[1]),
                       0.5 * (bounds[2] + bounds[3]),
                       0.5 * (bounds[4] + bounds[5])))
    actor.SetPosition(*(np.asarray(face_center, dtype=float) - center))
    actor.nav_label = text
    return actor


def _pad_actor():
    """Transparent cube that inflates the marker bounds; never pickable."""
    source = vtk.vtkCubeSource()
    source.SetXLength(2.0 * CUBE_PAD_HALF)
    source.SetYLength(2.0 * CUBE_PAD_HALF)
    source.SetZLength(2.0 * CUBE_PAD_HALF)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(source.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetOpacity(0.0)
    actor.PickableOff()
    actor.nav_role = 'pad'
    return actor


def _perimeter_polydata(loops):
    """Closed polylines for facet perimeters, as static line cells."""
    points, cells = vtk.vtkPoints(), vtk.vtkCellArray()
    index = 0
    for loop in loops:
        count = len(loop)
        cells.InsertNextCell(count + 1)
        for point in loop:
            points.InsertNextPoint(*(float(c) for c in point))
            cells.InsertCellPoint(index)
            index += 1
        cells.InsertCellPoint(index - count)
    data = vtk.vtkPolyData()
    data.SetPoints(points)
    data.SetLines(cells)
    return data


def _arrow_triangle_uv(direction):
    cx, cy = ARROW_CENTRES_UV[direction]
    if direction == 'right':
        return ((cx + 0.035, cy), (cx - 0.028, cy + 0.04), (cx - 0.028, cy - 0.04))
    if direction == 'left':
        return ((cx - 0.035, cy), (cx + 0.028, cy + 0.04), (cx + 0.028, cy - 0.04))
    if direction == 'up':
        return ((cx, cy + 0.035), (cx - 0.04, cy - 0.028), (cx + 0.04, cy - 0.028))
    return ((cx, cy - 0.035), (cx - 0.04, cy + 0.028), (cx + 0.04, cy + 0.028))


def _roll_chevron_uv(turn):
    """Two stacked triangles reading as a 45° in-plane turn.

    A curved arrow is not worth the vertex count in a 2D mapper; a chevron
    pointing the way the view will roll says the same thing.
    """
    cx, cy = ROLL_CENTRES_UV[turn]
    sign = 1.0 if turn == 'right' else -1.0
    return tuple(
        ((cx + sign * (offset + 0.024), cy),
         (cx + sign * (offset - 0.010), cy + 0.026),
         (cx + sign * (offset - 0.010), cy - 0.026))
        for offset in (-0.004, 0.020))


def _screen_triangle_actor(triangles, tag_name, tag_value):
    """One flat 2D actor in normalized-viewport space for the given triangles."""
    points, cells = vtk.vtkPoints(), vtk.vtkCellArray()
    index = 0
    for triangle in triangles:
        cells.InsertNextCell(3)
        for u, v in triangle:
            points.InsertNextPoint(u, v, 0.0)
            cells.InsertCellPoint(index)
            index += 1
    data = vtk.vtkPolyData()
    data.SetPoints(points)
    data.SetPolys(cells)
    mapper = vtk.vtkPolyDataMapper2D()
    coord = vtk.vtkCoordinate()
    coord.SetCoordinateSystemToNormalizedViewport()
    mapper.SetTransformCoordinate(coord)
    mapper.SetInputData(data)
    actor = vtk.vtkActor2D()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(*_ARROW_RGB)
    actor.PickableOff()
    setattr(actor, tag_name, tag_value)
    return actor


def navigation_arrow_actors():
    """Screen-space orbit arrows in normalized marker-viewport coordinates."""
    return [_screen_triangle_actor((_arrow_triangle_uv(direction),),
                                   'nav_arrow', direction)
            for direction in ARROW_TURNS]


def navigation_roll_actors():
    """Screen-space 45° roll buttons flanking the up arrow."""
    return [_screen_triangle_actor(_roll_chevron_uv(turn), 'nav_roll', turn)
            for turn in ROLL_TURNS]


def navigation_cube_prop():
    """Chamfered unit cube with face captions, no ``vtkFeatureEdges``.

    ``vtkAnnotatedCubeActor`` extracts edges on first render; on Cocoa that
    ran inside a synchronous expose and hung the 0.5.0 Intel editor. Static
    polydata plus ``vtkVectorText`` is the same pickable 1×1×1 body
    ``cube_hit_at`` already understands.
    """
    assembly = vtk.vtkAssembly()
    triangles, colors, loops = chamfered_cube_triangles()
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(polydata_from_triangles(triangles, face_colors=colors))
    mapper.SetScalarModeToUseCellData()
    mapper.SetColorModeToDirectScalars()
    body = vtk.vtkActor()
    body.SetMapper(mapper)
    body.GetProperty().SetColor(0.62, 0.66, 0.72)
    # Every mesh edge, the flag this used to set, drew each facet's own
    # triangulation diagonal too, because the body is unshared-vertex triangle
    # soup. Perimeters come from precomputed line cells instead -- still no
    # vtkFeatureEdges, which is the filter that hung the Cocoa expose.
    body.GetProperty().SetEdgeVisibility(False)
    body.nav_role = 'body'
    assembly.AddPart(body)
    outline_mapper = vtk.vtkPolyDataMapper()
    outline_mapper.SetInputData(_perimeter_polydata(loops))
    outline = vtk.vtkActor()
    outline.SetMapper(outline_mapper)
    outline.GetProperty().SetColor(0.16, 0.17, 0.20)
    outline.GetProperty().SetLineWidth(1.2)
    outline.GetProperty().SetLighting(False)
    outline.PickableOff()
    outline.nav_role = 'outline'
    assembly.AddPart(outline)
    assembly.AddPart(_pad_actor())
    scale = _vector_text_scale('Bottom', 2.0 * CUBE_FACE_HALF)
    lift = CUBE_HALF + CUBE_LABEL_LIFT
    for text, normal, orientation in _FACE_CAPTIONS:
        center = np.asarray(normal, dtype=float) * lift
        assembly.AddPart(_centered_label_actor(text, center, orientation, scale))
    return assembly


class Scene:
    """Pure VTK scene: actors, visibility, contacts and picking.

    Kept free of Qt so it can be built and checked without a window or an X
    display, which is also how the headless tests exercise it.
    """
    max_display_triangles = 1_500_000

    def __init__(self):
        self.renderer = vtk.vtkRenderer()
        self.renderer.SetBackground(0.12, 0.13, 0.15)
        self.actors: dict[str, vtk.vtkActor] = {}
        self.preview_note = ''
        self.contact_points = np.empty((0, 3))
        self.picker = vtk.vtkCellPicker()
        self.picker.SetTolerance(0.002)
        self.out_of_bounds: dict[str, int] = {}
        self._plate = None
        #: ``(zmin, zmax)`` clip on model/supports/raft, or None when disabled.
        self._z_clip = None
        self.picked_object = None

    def show_build_volume(self, settings):
        if self._plate is not None:
            self.renderer.RemoveActor(self._plate)
        width, depth, height = settings['printer']['build_mm']
        # Keep the plate orientation visible at a glance.  The front of the
        # printer is the -Y face, so the bottom edge from -X to +X is the one
        # green edge; every other edge is red.
        points = vtk.vtkPoints()
        points.SetData(numpy_support.numpy_to_vtk(
            np.asarray((
                (-width / 2, -depth / 2, 0),
                ( width / 2, -depth / 2, 0),
                ( width / 2,  depth / 2, 0),
                (-width / 2,  depth / 2, 0),
                (-width / 2, -depth / 2, height),
                ( width / 2, -depth / 2, height),
                ( width / 2,  depth / 2, height),
                (-width / 2,  depth / 2, height),
            ), dtype=np.float32), deep=True))
        edges = np.asarray((
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        ), dtype=np.int64)
        lines = vtk.vtkCellArray()
        offsets = np.arange(0, 2 * (len(edges) + 1), 2, dtype=np.int64)
        lines.SetData(numpy_support.numpy_to_vtkIdTypeArray(offsets, deep=True),
                      numpy_support.numpy_to_vtkIdTypeArray(edges.reshape(-1), deep=True))
        data = vtk.vtkPolyData()
        data.SetPoints(points)
        data.SetLines(lines)
        colors = vtk.vtkUnsignedCharArray()
        colors.SetName('EdgeColors')
        colors.SetNumberOfComponents(3)
        colors.SetNumberOfTuples(len(edges))
        for index in range(len(edges)):
            colors.SetTuple3(index, 0, 255, 0) if index == 0 else colors.SetTuple3(index, 255, 0, 0)
        data.GetCellData().SetScalars(colors)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(data)
        mapper.SetScalarModeToUseCellData()
        mapper.SetColorModeToDirectScalars()
        self._plate = vtk.vtkActor()
        self._plate.SetMapper(mapper)
        self._plate.GetProperty().SetLineWidth(2.5)
        self._plate.PickableOff()
        self.renderer.AddActor(self._plate)

    def set_mesh(self, role, triangles, opacity=1.0, settings=None, paint=None, *, key=None,
                 object_index=None):
        """Add or replace one role's geometry.

        Passing ``settings`` colors triangles the printer cannot reach in red
        instead of the role color, so a model that does not fit is visibly
        wrong rather than merely reported as wrong.  ``out_of_bounds`` records
        how many were flagged, on the decimated display geometry.
        """
        key = key or role
        if object_index is None and key == 'model':
            object_index = 0
        self.clear(key)
        self.out_of_bounds[key] = 0
        if triangles is None or not len(triangles):
            return
        triangles = np.asarray(triangles)
        if len(triangles) > self.max_display_triangles:
            stride = int(np.ceil(len(triangles) / self.max_display_triangles))
            triangles = triangles[::stride]
            self.preview_note = (f'{role}: showing 1 in {stride} triangles for display only; '
                                 'validation always uses every triangle')
        color = LAYER_COLORS.get(role, (0.8, 0.8, 0.8))
        face_colors = None
        if role == 'model' and paint:
            radius = float((settings or {}).get('support', {}).get('spacing_mm', 3.0)) / 2
            face_colors = paint_face_colors(triangles, paint, radius, color)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputData(polydata_from_triangles(triangles, settings, color, face_colors))
        if settings is not None or face_colors is not None:
            mapper.SetScalarModeToUseCellData()
            mapper.SetColorModeToDirectScalars()
            if settings is not None:
                self.out_of_bounds[key] = int(np.count_nonzero(
                    out_of_bounds_mask(triangles, settings)))
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetOpacity(opacity)
        actor.role = role
        actor.object_index = object_index
        self.actors[key] = actor
        self.renderer.AddActor(actor)
        self._apply_z_clip_to(mapper)

    def set_z_clip(self, zmin, zmax):
        """Clip model/supports/raft between ``zmin`` and ``zmax`` (mm).

        Passing ``None`` for either bound disables clipping entirely and
        removes any planes already installed. Headless-safe: only mapper state
        changes; no render is required.
        """
        if zmin is None or zmax is None:
            self._z_clip = None
        else:
            lo, hi = float(zmin), float(zmax)
            if lo > hi:
                lo, hi = hi, lo
            self._z_clip = (lo, hi)
        self._apply_z_clip()

    def _role_names(self, role):
        return [name for name in self.actors if name == role or name.startswith(f'{role}:')]

    def _apply_z_clip(self):
        for role in ('model', 'supports', 'raft'):
            for name in self._role_names(role):
                self._apply_z_clip_to(self.actors[name].GetMapper())

    def _apply_z_clip_to(self, mapper):
        if mapper is None:
            return
        mapper.RemoveAllClippingPlanes()
        if self._z_clip is None:
            return
        zmin, zmax = self._z_clip
        below = vtk.vtkPlane()
        below.SetOrigin(0.0, 0.0, zmin)
        below.SetNormal(0.0, 0.0, 1.0)
        above = vtk.vtkPlane()
        above.SetOrigin(0.0, 0.0, zmax)
        above.SetNormal(0.0, 0.0, -1.0)
        mapper.AddClippingPlane(below)
        mapper.AddClippingPlane(above)

    def set_faults(self, points_with_colors, radius=0.55):
        """Non-pickable colored spheres for fault markers (``role='faults'``).

        ``points_with_colors`` is an iterable of dicts with ``position_mm`` and
        ``color`` (0–255 RGB), or ``(position, color)`` pairs. Entries without a
        usable position are skipped. The actor is never pickable so it cannot
        steal support placement from the model.
        """
        self.clear('faults')
        positions = []
        colors = []
        for entry in points_with_colors or ():
            if isinstance(entry, dict):
                position = entry.get('position_mm')
                color = entry.get('color', (128, 128, 128))
            else:
                position, color = entry[0], entry[1]
            if not position:
                continue
            point = np.asarray(position, dtype=float).reshape(3)
            if not np.isfinite(point).all():
                continue
            positions.append(point)
            colors.append([int(c) for c in color[:3]])
        if not positions:
            return
        points = np.ascontiguousarray(positions, dtype=np.float32)
        rgb = np.ascontiguousarray(colors, dtype=np.uint8)
        holder = vtk.vtkPoints()
        holder.SetData(numpy_support.numpy_to_vtk(points, deep=True))
        data = vtk.vtkPolyData()
        data.SetPoints(holder)
        scalars = numpy_support.numpy_to_vtk(rgb, deep=True)
        scalars.SetName('FaultColors')
        data.GetPointData().SetScalars(scalars)
        sphere = vtk.vtkSphereSource()
        sphere.SetRadius(radius)
        sphere.SetThetaResolution(10)
        sphere.SetPhiResolution(10)
        glyph = vtk.vtkGlyph3D()
        glyph.SetInputData(data)
        glyph.SetSourceConnection(sphere.GetOutputPort())
        glyph.SetColorModeToColorByScalar()
        glyph.ScalingOff()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(glyph.GetOutputPort())
        mapper.SetScalarModeToUsePointData()
        mapper.SetColorModeToDirectScalars()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.role = 'faults'
        actor.PickableOff()
        self.actors['faults'] = actor
        self.renderer.AddActor(actor)

    def set_contacts(self, points, radius=0.45):
        self.clear('contacts')
        points = np.asarray(points, dtype=float).reshape(-1, 3)
        self.contact_points = points
        if not len(points):
            return
        holder = vtk.vtkPoints()
        holder.SetData(numpy_support.numpy_to_vtk(np.ascontiguousarray(points, np.float32), deep=True))
        data = vtk.vtkPolyData()
        data.SetPoints(holder)
        sphere = vtk.vtkSphereSource()
        sphere.SetRadius(radius)
        sphere.SetThetaResolution(8)
        sphere.SetPhiResolution(8)
        glyph = vtk.vtkGlyph3D()
        glyph.SetInputData(data)
        glyph.SetSourceConnection(sphere.GetOutputPort())
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(glyph.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*LAYER_COLORS['contacts'])
        actor.role = 'contacts'
        self.actors['contacts'] = actor
        self.renderer.AddActor(actor)

    def clear(self, role=None):
        names = ([name for name in self.actors if name == role or name.startswith(f'{role}:')]
                 if role else list(self.actors))
        for name in names:
            actor = self.actors.pop(name, None)
            self.out_of_bounds.pop(name, None)
            if actor is not None:
                self.renderer.RemoveActor(actor)

    def set_visible(self, role, visible):
        for name in self._role_names(role):
            self.actors[name].SetVisibility(bool(visible))

    def is_visible(self, role):
        actors = [self.actors[name] for name in self._role_names(role)]
        return any(actor.GetVisibility() for actor in actors)

    def nearest_contact(self, position, radius):
        if not len(self.contact_points):
            return None
        distances = np.linalg.norm(self.contact_points - np.asarray(position, dtype=float), axis=1)
        index = int(np.argmin(distances))
        return self.contact_points[index] if distances[index] <= radius else None

    def pick_at(self, x, y):
        """Pick in display coordinates; returns ``(position, role)`` or None."""
        if not self.picker.Pick(x, y, 0, self.renderer):
            return None
        actor = self.picker.GetActor()
        self.picked_object = getattr(actor, 'object_index', None)
        return np.asarray(self.picker.GetPickPosition(), dtype=float), getattr(actor, 'role', '')


class Viewport(QtWidgets.QWidget):
    """Qt wrapper around a :class:`Scene`. Requires a real render window."""
    picked = QtCore.Signal(object, str)
    #: Position and the index of the object the brush landed on. Paint belongs
    #: to a part, so the stroke has to say which part it hit.
    painted = QtCore.Signal(object, int)
    paint_finished = QtCore.Signal()
    view_changed = QtCore.Signal(str)
    object_transformed = QtCore.Signal(int, object, object)
    object_selected = QtCore.Signal(int)
    #: Continuous updates during a gizmo drag -- see ``TransformGizmo.preview``.
    object_preview_transformed = QtCore.Signal(int, object, object)
    #: The gizmo was dismissed by a click on empty space; clear the selection.
    object_deselected = QtCore.Signal()
    #: A gizmo drag was abandoned (Escape, or a right-click mid-drag) rather
    #: than committed -- the window should drop whatever live preview
    #: transform it applied to the actor while the drag was in progress.
    object_transform_cancelled = QtCore.Signal(int)

    #: Transition frames when snapping to a named view. The camera swings
    #: around the model; a jump makes it impossible to tell which way it turned.
    transition_steps = 12
    transition_interval_ms = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.interactor = make_vtk_interactor(self)
        layout.addWidget(self.interactor)
        self.scene = Scene()
        self.interactor.GetRenderWindow().AddRenderer(self.scene.renderer)
        self.camera = CameraController(self.scene.renderer)
        self.cube = None
        self.cube_widget = None
        # A cell picker does not resolve the annotated cube's assembly parts;
        # a prop pick does, and its pick position is what names the face.
        self._cube_picker = vtk.vtkPropPicker()
        self._transition = QtCore.QTimer(self)
        self._transition.setInterval(self.transition_interval_ms)
        self._transition.timeout.connect(self._advance_transition)
        self._pending_poses = []
        self._pending_name = None
        self._pending_final = None
        self.paint_enabled = False
        self._paint_active = False
        self.gizmo = TransformGizmo(self.interactor, self.scene.renderer)
        self.gizmo.preview.connect(self.object_preview_transformed)
        self.gizmo.committed.connect(self.object_transformed)
        self.gizmo.dismissed.connect(self.object_deselected)
        self._gizmo_dragging = False
        # Observer ids for the press/move/release/cancel handlers, filled in
        # by start(); kept so a handler can abort its own event (see
        # _abort_event) once it decides the gizmo consumed it.
        self._click_observer_id = None
        self._move_observer_id = None
        self._release_observer_id = None
        self._right_press_observer_id = None
        self._key_press_observer_id = None

    @property
    def transform_index(self):
        return self.gizmo.attached_index

    def start(self):
        self.interactor.Initialize()
        self.interactor.SetInteractorStyle(vtk.vtkInteractorStyleTrackballCamera())
        # Keep the observer ids: they are how a handler tells VTK "I consumed
        # this one, do not also hand it to the trackball camera style" (see
        # _abort_event). Priority 1.0 only decides who is asked first -- VTK
        # still walks every lower-priority observer afterwards unless the
        # abort flag is set.
        self._click_observer_id = self.interactor.AddObserver(
            'LeftButtonPressEvent', self._on_click, 1.0)
        self._move_observer_id = self.interactor.AddObserver(
            'MouseMoveEvent', self._on_move, 1.0)
        self._release_observer_id = self.interactor.AddObserver(
            'LeftButtonReleaseEvent', self._on_release, 1.0)
        self._right_press_observer_id = self.interactor.AddObserver(
            'RightButtonPressEvent', self._on_right_press, 1.0)
        self._key_press_observer_id = self.interactor.AddObserver(
            'KeyPressEvent', self._on_key_press, 1.0)
        self.camera.home()
        self.render()
        # Add the orientation marker after the first paint. vtkAnnotatedCubeActor
        # ran FeatureEdges inside a Cocoa expose and hung 0.5.0; even the cheap
        # cube is installed from a timer so it cannot nest in CATransaction.
        QtCore.QTimer.singleShot(0, self.add_navigation_cube)

    def _abort_event(self, observer_id):
        """Stop VTK from also dispatching this event to lower-priority
        observers (in practice, ``vtkInteractorStyleTrackballCamera``).

        Only call this once a handler has decided the gizmo actually
        consumed the event -- everywhere else camera navigation, paint
        strokes and contact picking must keep behaving exactly as before.
        """
        if observer_id is None:
            return
        command = self.interactor.GetCommand(observer_id)
        if command is not None:
            command.SetAbortFlag(1)

    def select_transform_object(self, index):
        """Show the translate/rotate gizmo around one model actor."""
        index = int(index)
        actor = self.scene.actors.get('model' if index == 0 else f'model:{index}')
        if actor is None:
            self.gizmo.detach()
            return False
        self.gizmo.attach(actor, index)
        self.render()
        return True

    def deselect_transform_object(self):
        """Hide the gizmo without dispatching ``object_deselected``.

        Distinct from a dismissed click: this is the window telling the
        viewport the selection is already gone, not the viewport discovering
        it from a click on empty space.
        """
        self.gizmo.detach()
        self.render()

    def __getattr__(self, name):
        # Delegate scene vocabulary so callers do not care about the split.
        if name != 'scene' and hasattr(Scene, name):
            return getattr(self.scene, name)
        raise AttributeError(name)

    # ---- navigation cube ------------------------------------------------
    def add_navigation_cube(self):
        """A labeled orientation cube in the top-right corner.

        Faces read Front / Back / Left / Right / Top / Bottom rather than axis
        letters, with Front on -Y so it agrees with the green build-volume
        edge. Built from static polydata rather than ``vtkAnnotatedCubeActor``:
        the latter runs ``vtkFeatureEdges`` on every first paint and hung the
        Intel 0.5.0 editor inside a Cocoa expose. The widget is display-only,
        so clicks are picked here and turned into camera moves.
        """
        if self.cube_widget is not None:
            return self.cube_widget
        cube = navigation_cube_prop()
        widget = vtk.vtkOrientationMarkerWidget()
        widget.SetOrientationMarker(cube)
        widget.SetInteractor(self.interactor)
        widget.SetViewport(0.80, 0.76, 1.0, 1.0)
        widget.SetEnabled(1)
        # Interactive mode lets the user drag the marker around the corner,
        # which is not what a click on it should mean here.
        widget.InteractiveOff()
        renderer = widget.GetRenderer()
        if renderer is not None:
            for actor in navigation_arrow_actors() + navigation_roll_actors():
                # vtkOpenGLRenderer on this VTK build does not wrap AddActor2D.
                renderer.AddViewProp(actor)
        self.cube, self.cube_widget = cube, widget
        self.render()
        return widget

    def cube_hit_under(self, x, y):
        """``cube_hit_at`` result under a display position, or None on a miss.

        The display point is rejected against the marker's own viewport
        rectangle first: a prop pick is asked to search one renderer and will
        happily answer for a point that renderer does not own, which would turn
        every click in the main view into a camera snap. Orbit arrows are
        classified in the marker's normalized viewport so they stay in screen
        space the way FreeCAD's NavCube arrows do.
        """
        if self.cube_widget is None:
            return None
        renderer = self.cube_widget.GetRenderer()
        if renderer is None:
            return None
        width, height = self.interactor.GetRenderWindow().GetSize()
        left, bottom, right, top = renderer.GetViewport()
        if not (left * width <= x <= right * width and bottom * height <= y <= top * height):
            return None
        span_x = (right - left) * width
        span_y = (top - bottom) * height
        if span_x > 0 and span_y > 0:
            u = (x - left * width) / span_x
            v = (y - bottom * height) / span_y
            roll = cube_roll_at(u, v)
            if roll is not None:
                return ('roll', roll)
            arrow = cube_arrow_at(u, v)
            if arrow is not None:
                return ('arrow', arrow)
        if not self._cube_picker.Pick(x, y, 0, renderer):
            return None
        return cube_hit_at(self._cube_picker.GetPickPosition())

    def cube_face_under(self, x, y):
        """Face label under a display position, or None when the cube is missed."""
        hit = self.cube_hit_under(x, y)
        if hit is not None and hit[0] == 'face':
            return hit[1]
        return None

    # ---- camera ---------------------------------------------------------
    def set_view(self, name, animate=True):
        """Snap to a named view, interpolating unless asked not to."""
        self._transition.stop()
        self._pending_final = None
        if not animate:
            self.camera.set_view(name)
            self.render()
            self.view_changed.emit(name)
            return name
        self._pending_poses = list(self.camera.steps_to(name, self.transition_steps))
        self._pending_name = name
        self._transition.start()
        return name

    def rotate_view(self, turn, animate=True, degrees=NAV_STEP_DEG):
        """Orbit in view space, matching a named view when the roll agrees.

        The step is 45° so an arrow click lands on a facet the cube itself
        shows, which is also why the twelve bevel views are named: most steps
        now snap rather than leaving the camera between views.
        """
        camera = self.camera.camera
        focal = np.asarray(camera.GetFocalPoint(), dtype=float)
        direction = np.asarray(camera.GetPosition(), dtype=float) - focal
        up = np.asarray(camera.GetViewUp(), dtype=float)
        new_direction, new_up = rotate_view_by(direction, up, turn, degrees=degrees)
        return self._turn_to(new_direction, new_up, turn, focal, direction, animate)

    def roll_view(self, turn, animate=True, degrees=NAV_STEP_DEG):
        """Roll the view in its own plane, leaving the camera where it stands."""
        camera = self.camera.camera
        focal = np.asarray(camera.GetFocalPoint(), dtype=float)
        direction = np.asarray(camera.GetPosition(), dtype=float) - focal
        up = np.asarray(camera.GetViewUp(), dtype=float)
        new_direction, new_up = roll_view_by(direction, up, turn, degrees=degrees)
        return self._turn_to(new_direction, new_up, turn, focal, direction, animate)

    def _turn_to(self, new_direction, new_up, turn, focal, direction, animate):
        """Animate or jump to an orbited or rolled pose."""
        name = named_view_near(new_direction, new_up)
        if name is not None:
            return self.set_view(name, animate=animate)
        distance = float(np.linalg.norm(direction)) or 1.0
        target = (focal + new_direction * distance, focal, new_up)
        self._transition.stop()
        self._pending_name = None
        if not animate:
            self.camera.apply(target)
            self.camera.current = None
            self._pending_final = None
            self.render()
            self.view_changed.emit(turn)
            return turn
        self._pending_poses = list(self.camera.steps_toward(
            new_direction, new_up, self.transition_steps))
        self._pending_final = target
        self._transition.start()
        return turn

    def _advance_transition(self):
        if not self._pending_poses:
            self._transition.stop()
            name, self._pending_name = self._pending_name, None
            final, self._pending_final = self._pending_final, None
            if name is not None:
                # Land exactly on the named pose and reframe, so a transition
                # can never leave the camera almost-but-not-quite square on.
                self.camera.set_view(name)
                self.render()
                self.view_changed.emit(name)
            elif final is not None:
                self.camera.apply(final)
                self.camera.current = None
                self.render()
                self.view_changed.emit('')
            return
        self.camera.apply(self._pending_poses.pop(0))
        self.render()

    def home(self):
        return self.set_view(CAMERA_HOME)

    def reset_camera(self):
        """Frame the scene without changing the view direction."""
        self.camera.fit()
        self.render()

    def render(self):
        self.interactor.GetRenderWindow().Render()

    def _on_click(self, interactor, event):
        x, y = interactor.GetEventPosition()
        # FakeViewport in the gizmo smoke tests stubs cube_face_under only.
        hit_under = getattr(self, 'cube_hit_under', None)
        if hit_under is not None:
            hit = hit_under(x, y)
        else:
            face = self.cube_face_under(x, y)
            hit = ('face', face) if face is not None else None
        if hit is not None:
            # A click on the cube is a camera command, never a support edit.
            kind, payload = hit
            if kind == 'face':
                self.set_view(FACE_VIEWS[payload])
            elif kind in ('corner', 'edge'):
                self.set_view(payload)
            elif kind == 'arrow':
                self.rotate_view(payload)
            elif kind == 'roll':
                self.roll_view(payload)
            return
        if self.gizmo.attached_index is not None and self.gizmo.begin_drag(x, y):
            # Grabbed a handle: this click drives the gizmo, not a pick/paint,
            # and must not also spin the camera underneath the drag.
            self._gizmo_dragging = True
            self._abort_event(self._click_observer_id)
            return
        hit = self.scene.pick_at(x, y)
        if self.gizmo.attached_index is not None and (hit is None or hit[1] != 'model'):
            # Clicked neither the gizmo nor a model actor: FreeCAD-style, that
            # dismisses the selection rather than leaving stale handles up.
            self.gizmo.dismiss()
        if hit is None:
            return
        if self.paint_enabled and hit[1] == 'model':
            self._paint_active = True
            self.painted.emit(hit[0], int(self.scene.picked_object or 0))
            return
        if hit[1] == 'model' and self.scene.picked_object is not None:
            self.object_selected.emit(self.scene.picked_object)
        self.picked.emit(hit[0], hit[1])

    def _on_move(self, interactor, event):
        x, y = interactor.GetEventPosition()
        if self._gizmo_dragging:
            # The interactor only ever delivers a release while it still owns
            # the mouse; if the pointer left the render window, the window
            # lost focus, or another widget grabbed the mouse, VTK never
            # sends LeftButtonReleaseEvent at all and the flag would stay set
            # forever. The real button state is the ground truth, so treat
            # "still dragging but the button is up" as a release that arrived
            # late: commit at the last known position instead of continuing
            # to track with nothing held down.
            left_down = bool(QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton)
            if not left_down:
                self.gizmo.end_drag(x, y)
                self._gizmo_dragging = False
                self.render()
                self._abort_event(self._move_observer_id)
                return
            self.gizmo.drag(x, y)
            self.render()
            self._abort_event(self._move_observer_id)
            return
        if self.gizmo.attached_index is not None and self.gizmo.hover(x, y):
            self.render()
        if not self._paint_active:
            return
        hit = self.scene.pick_at(x, y)
        if hit is not None and hit[1] == 'model':
            self.painted.emit(hit[0], int(self.scene.picked_object or 0))

    def _on_release(self, interactor, event):
        if self._gizmo_dragging:
            x, y = interactor.GetEventPosition()
            self.gizmo.end_drag(x, y)
            self._gizmo_dragging = False
            self.render()
            self._abort_event(self._release_observer_id)
            return
        if not self._paint_active:
            return
        self._paint_active = False
        self.paint_finished.emit()

    def _on_right_press(self, interactor, event):
        """A right-click mid-drag cancels it instead of committing."""
        if not self._gizmo_dragging:
            return
        index = self.gizmo.attached_index
        self.gizmo.cancel_drag()
        self._gizmo_dragging = False
        self.render()
        self._abort_event(self._right_press_observer_id)
        if index is not None:
            self.object_transform_cancelled.emit(index)

    def _on_key_press(self, interactor, event):
        """Escape mid-drag cancels it instead of committing."""
        if not self._gizmo_dragging or interactor.GetKeySym() != 'Escape':
            return
        index = self.gizmo.attached_index
        self.gizmo.cancel_drag()
        self._gizmo_dragging = False
        self.render()
        self._abort_event(self._key_press_observer_id)
        if index is not None:
            self.object_transform_cancelled.emit(index)
