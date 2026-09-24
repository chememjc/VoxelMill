"""Offscreen smoke test: the gizmo attaches to, and detaches from, a real
VTK actor without raising.

This does not go through ``Viewport``/``QVTKRenderWindowInteractor`` -- that
opens a genuine native VTK window even under Qt's offscreen platform, which
needs a real X server (see ``test_gui.py``'s ``RENDER_CHILD``). VTK's own
offscreen rendering (``SetOffScreenRendering``) needs no display at all, so it
is the honest way to exercise the actual VTK plumbing (renderer layering,
picking, actor building) headlessly.
"""
from __future__ import annotations



import numpy as np
import pytest

pytest.importorskip('PySide6')
vtk = pytest.importorskip('vtkmodules.all')

from PySide6 import QtCore, QtWidgets

from voxelmill.gui.gizmo import MAX_HANDLE_MM, MIN_HANDLE_MM, TransformGizmo
from voxelmill.gui.viewport import Viewport


@pytest.fixture
def offscreen_scene():
    render_window = vtk.vtkRenderWindow()
    render_window.SetOffScreenRendering(1)
    render_window.SetSize(320, 240)
    interactor = vtk.vtkRenderWindowInteractor()
    interactor.SetRenderWindow(render_window)

    renderer = vtk.vtkRenderer()
    render_window.AddRenderer(renderer)

    sphere = vtk.vtkSphereSource()
    sphere.SetRadius(20.0)
    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(sphere.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    renderer.AddActor(actor)
    renderer.ResetCamera()

    return interactor, renderer, actor


def test_attach_builds_six_handles_sized_from_the_target(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer)
    gizmo.attach(actor, 2)
    assert gizmo.attached_index == 2
    assert len(gizmo._handles) == 6
    assert MIN_HANDLE_MM <= gizmo._handle_length <= MAX_HANDLE_MM
    axes = {handle.gizmo_handle for handle in gizmo._handles}
    assert axes == {('translate', 'x'), ('translate', 'y'), ('translate', 'z'),
                     ('rotate', 'x'), ('rotate', 'y'), ('rotate', 'z')}


def test_attach_uses_its_own_layered_renderer_sharing_the_camera(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer)
    gizmo.attach(actor, 0)
    window = interactor.GetRenderWindow()
    assert window.GetNumberOfLayers() >= 2
    assert gizmo._renderer.GetLayer() == 1
    assert gizmo._renderer.GetActiveCamera() is renderer.GetActiveCamera()


def test_detach_clears_handles_and_forgets_the_index(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer)
    gizmo.attach(actor, 1)
    gizmo.detach()
    assert gizmo.attached_index is None
    assert gizmo._handles == []


def test_dismiss_detaches_and_emits_dismissed(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer)
    gizmo.attach(actor, 0)
    seen = []
    gizmo.dismissed.connect(lambda: seen.append(True))
    gizmo.dismiss()
    assert gizmo.attached_index is None
    assert seen == [True]


def test_a_drag_round_trip_on_a_rendered_frame_previews_then_commits(offscreen_scene):
    """Render once (so picking has something to hit), find a real handle
    on screen, drag it, and confirm both signals carry the same shape of
    delta the window expects: (index, translation_xyz, rotation_xyz_deg)."""
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer, snap=(0.0, 0.0))
    gizmo.attach(actor, 0)
    interactor.GetRenderWindow().Render()

    width, height = interactor.GetRenderWindow().GetSize()
    handle_position = None
    for x in range(0, width, 4):
        for y in range(0, height, 4):
            handle = gizmo.hit_test(x, y)
            if handle is not None:
                handle_position = (x, y)
                break
        if handle_position:
            break
    assert handle_position is not None, 'expected at least one handle to be pickable on screen'

    previews = []
    committed = []
    gizmo.preview.connect(lambda i, t, r: previews.append((i, t, r)))
    gizmo.committed.connect(lambda i, t, r: committed.append((i, t, r)))

    x, y = handle_position
    assert gizmo.begin_drag(x, y)
    gizmo.drag(x + 15, y + 3)
    gizmo.end_drag(x + 15, y + 3)

    assert committed and committed[0][0] == 0
    _, translation, rotation = committed[0]
    assert len(translation) == 3 and len(rotation) == 3


def test_attach_is_safe_to_call_again_after_geometry_changes(offscreen_scene):
    """A reload rebuilds the actor's geometry and re-attaches without a
    detach in between (see window.py's post-reload re-select)."""
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer)
    gizmo.attach(actor, 0)
    actor.SetScale(3.0)
    gizmo.attach(actor, 0)
    assert gizmo.attached_index == 0
    assert len(gizmo._handles) == 6
    assert MIN_HANDLE_MM <= gizmo._handle_length <= MAX_HANDLE_MM


# ---- cancel_drag ------------------------------------------------------

def test_cancel_drag_emits_no_committed_and_leaves_the_gizmo_where_it_started(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer, snap=(0.0, 0.0))
    gizmo.attach(actor, 0)
    interactor.GetRenderWindow().Render()
    start_center = gizmo._center.copy()

    width, height = interactor.GetRenderWindow().GetSize()
    handle_position = None
    for x in range(0, width, 4):
        for y in range(0, height, 4):
            if gizmo.hit_test(x, y) == ('translate', 'x'):
                handle_position = (x, y)
                break
        if handle_position:
            break
    assert handle_position is not None, 'expected the translate-x handle to be pickable'

    committed = []
    gizmo.committed.connect(lambda i, t, r: committed.append((i, t, r)))

    x, y = handle_position
    assert gizmo.begin_drag(x, y)
    gizmo.drag(x + 20, y)  # actually move the handle before abandoning the drag
    gizmo.cancel_drag()

    assert committed == []
    assert gizmo._drag_handle is None
    np.testing.assert_allclose(gizmo._center, start_center)

    # A further move with nothing dragging is a no-op, not a resumed drag.
    gizmo.drag(x + 40, y)
    assert committed == []


# ---- rotation accumulation across a full turn --------------------------

def _drive_ring_drag(gizmo, raw_samples):
    """Feed ``gizmo`` a canned sequence of ring-angle samples (radians, in
    the ``(-pi, pi]`` range ``ring_angle`` itself returns) as if a real
    drag had sampled them one per frame, and return the reported rotation
    (degrees, about whichever axis the fake handle names) after each one.

    Bypasses ``begin_drag``/real picking entirely -- what is under test is
    the accumulation in :meth:`TransformGizmo.drag`, not the ray math, which
    ``test_gui_gizmo_math.py`` already covers on its own.
    """
    gizmo._drag_handle = ('rotate', 'x')
    gizmo._drag_start_value = float(raw_samples[0])
    gizmo._drag_previous_value = float(raw_samples[0])
    gizmo._drag_rotation_total = 0.0
    remaining = iter(raw_samples[1:])
    gizmo._sample = lambda handle, x, y: next(remaining)
    reported = []
    gizmo.preview.connect(lambda i, t, r: reported.append(r[0]))
    for _ in raw_samples[1:]:
        gizmo.drag(0, 0)
    return reported


def test_ring_drag_through_a_full_turn_reports_360_not_a_wrap(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer, snap=(0.0, 0.0))
    gizmo.attach(actor, 0)
    thetas = np.linspace(0.0, 2 * np.pi, 240)
    raw = np.arctan2(np.sin(thetas), np.cos(thetas))  # what ring_angle would report

    reported = _drive_ring_drag(gizmo, raw)

    assert reported[-1] == pytest.approx(360.0, abs=1.0)


def test_ring_drag_through_one_and_a_half_turns_reports_540(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer, snap=(0.0, 0.0))
    gizmo.attach(actor, 0)
    thetas = np.linspace(0.0, 3 * np.pi, 360)
    raw = np.arctan2(np.sin(thetas), np.cos(thetas))

    reported = _drive_ring_drag(gizmo, raw)

    assert reported[-1] == pytest.approx(540.0, abs=1.0)


def test_ring_drag_backwards_a_full_turn_reports_minus_360(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer, snap=(0.0, 0.0))
    gizmo.attach(actor, 0)
    thetas = np.linspace(0.0, -2 * np.pi, 240)
    raw = np.arctan2(np.sin(thetas), np.cos(thetas))

    reported = _drive_ring_drag(gizmo, raw)

    assert reported[-1] == pytest.approx(-360.0, abs=1.0)


def test_ring_drag_single_step_past_the_branch_cut_resolves_the_short_way(offscreen_scene):
    """A step bigger than half a turn between two consecutive frames is
    genuinely ambiguous -- two samples alone cannot distinguish "170 degrees
    forward" from "190 degrees backward". This resolves it the same way
    every frame does, via ``wrap_angle``: the short way around (< 180
    degrees). A caller that wants a huge single-frame jump to read as the
    long way around would need to sample more often; this function does not
    guess at intent from a single ambiguous frame.
    """
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer, snap=(0.0, 0.0))
    gizmo.attach(actor, 0)
    start = np.radians(170.0)
    jump = np.radians(-170.0)  # raw difference is -340 deg; short way is +20 deg

    reported = _drive_ring_drag(gizmo, [start, jump])

    assert reported[-1] == pytest.approx(20.0, abs=1e-6)


# ---- Viewport-level event handling (abort flag, sticky-drag fix) ------

class _NullScene:
    """Stands in for ``Scene`` where a handler only needs ``pick_at`` to
    report "nothing here" without a real renderer."""
    picked_object = None

    def pick_at(self, x, y):
        return None


class _FakeViewport:
    """Just enough of ``Viewport``'s attribute surface for its raw VTK
    event handlers to run standalone.

    Not a real ``Viewport``: building one needs a ``QVTKRenderWindowInteractor``,
    which opens a genuine native window even under Qt's offscreen platform and
    needs a real X server (see this module's docstring). None of the handler
    paths exercised below touch a Qt signal, so a plain object with the right
    attributes -- and the *actual* ``Viewport`` methods bound onto it -- is
    enough to test the real production code headlessly.
    """

    def __init__(self, interactor, gizmo, scene):
        self.interactor = interactor
        self.gizmo = gizmo
        self.scene = scene
        self.cube_widget = None
        self._gizmo_dragging = False
        self._paint_active = False
        self.paint_enabled = False
        self._click_observer_id = None
        self._move_observer_id = None
        self._release_observer_id = None
        self._right_press_observer_id = None
        self._key_press_observer_id = None

    def cube_face_under(self, x, y):
        return None  # no navigation cube wired up for these tests

    def render(self):
        pass


for _name in ('_on_click', '_on_move', '_on_release', '_on_right_press',
              '_on_key_press', '_abort_event'):
    setattr(_FakeViewport, _name, getattr(Viewport, _name))
del _name


def test_abort_flag_is_set_for_a_click_that_grabs_a_handle(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer)
    gizmo.attach(actor, 0)
    interactor.GetRenderWindow().Render()

    width, height = interactor.GetRenderWindow().GetSize()
    handle_position = None
    for x in range(0, width, 4):
        for y in range(0, height, 4):
            if gizmo.hit_test(x, y) is not None:
                handle_position = (x, y)
                break
        if handle_position:
            break
    assert handle_position is not None

    observer_id = interactor.AddObserver('LeftButtonPressEvent', lambda *a: None, 1.0)
    viewport = _FakeViewport(interactor, gizmo, _NullScene())
    viewport._click_observer_id = observer_id

    x, y = handle_position
    interactor.SetEventPosition(x, y)
    viewport._on_click(interactor, None)

    assert viewport._gizmo_dragging is True
    assert interactor.GetCommand(observer_id).GetAbortFlag() == 1


def test_abort_flag_is_not_set_for_a_click_that_hits_nothing(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer)
    # Never attached, so begin_drag short-circuits and there is no handle to
    # grab regardless of where the click lands.
    observer_id = interactor.AddObserver('LeftButtonPressEvent', lambda *a: None, 1.0)
    viewport = _FakeViewport(interactor, gizmo, _NullScene())
    viewport._click_observer_id = observer_id

    interactor.SetEventPosition(1, 1)
    viewport._on_click(interactor, None)

    assert viewport._gizmo_dragging is False
    assert interactor.GetCommand(observer_id).GetAbortFlag() == 0


def test_move_with_no_drag_in_flight_is_a_no_op(offscreen_scene):
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer)
    calls = []
    gizmo.drag = lambda x, y: calls.append((x, y))
    gizmo.end_drag = lambda x, y: calls.append((x, y))
    viewport = _FakeViewport(interactor, gizmo, _NullScene())

    interactor.SetEventPosition(10, 10)
    viewport._on_move(interactor, None)

    assert calls == []
    assert viewport._gizmo_dragging is False


def test_move_while_dragging_but_button_up_ends_the_drag(offscreen_scene):
    """Regression for the "sticky" gizmo: if VTK never delivered the release
    (pointer left the window, focus moved, another widget grabbed the mouse)
    the flag would otherwise stay set and the handle would keep following
    the mouse forever. ``QApplication.mouseButtons()`` reports no button held
    in this headless test environment, which is exactly the "release never
    arrived" case this guards -- so this exercises the real fix, not a stub.
    """
    # Sanity check on the premise: no button is actually held in this
    # offscreen test process, which is exactly the "release never arrived"
    # situation the fix has to cope with.
    assert not (QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton)
    interactor, renderer, actor = offscreen_scene
    gizmo = TransformGizmo(interactor, renderer, snap=(0.0, 0.0))
    gizmo.attach(actor, 0)
    # Fake an in-flight drag without needing a real handle pick.
    gizmo._drag_handle = ('translate', 'x')
    gizmo._drag_start_value = 0.0
    gizmo._drag_previous_value = 0.0

    ended = []
    real_end_drag = gizmo.end_drag
    def _spy_end_drag(x, y):
        ended.append((x, y))
        return real_end_drag(x, y)
    gizmo.end_drag = _spy_end_drag

    viewport = _FakeViewport(interactor, gizmo, _NullScene())
    viewport._gizmo_dragging = True
    viewport._move_observer_id = interactor.AddObserver('MouseMoveEvent', lambda *a: None, 1.0)

    interactor.SetEventPosition(5, 5)
    viewport._on_move(interactor, None)

    assert ended == [(5, 5)]
    assert viewport._gizmo_dragging is False
    assert gizmo._drag_handle is None
