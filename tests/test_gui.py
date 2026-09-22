"""Headless editor tests.

The viewport splits into a Qt-free :class:`Scene` and a widget precisely so the
whole editor except the render window can be exercised without a display.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
import manifold3d as m

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')

from PySide6 import QtCore, QtWidgets  # noqa: E402

from voxelmill.config import resolve_settings  # noqa: E402
from voxelmill.contracts import VoxelMillError  # noqa: E402
from voxelmill.geometry import manifold_triangles  # noqa: E402
from voxelmill.gui.document import Document  # noqa: E402
from voxelmill.gui.jobs import JobRunner  # noqa: E402
from voxelmill.gui.layerview import ISSUE_COLORS, mask_to_image  # noqa: E402
from voxelmill.gui.viewport import Scene, polydata_from_triangles  # noqa: E402
from voxelmill.gui.window import MainWindow, with_suffix_if_missing  # noqa: E402
from voxelmill.mesh import write_stl  # noqa: E402
from voxelmill.raster import RasterGrid  # noqa: E402
from voxelmill.contracts import Diagnostic  # noqa: E402


@pytest.fixture(scope='session')
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'sphere.stl'
    write_stl(path, manifold_triangles(m.Manifold.sphere(6, 48)))
    return path


def small_settings():
    return resolve_settings(overrides={
        'process': {'layer_height_mm': 0.2},
        'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1], 'build_mm': [100., 80., 165.]},
        'resources': {'workers': 2}})


def drain(window, application, stages=('place', 'model', 'supports', 'union'), limit=180000):
    seen = []
    window.stage_changed.connect(seen.append)
    deadline = QtCore.QElapsedTimer()
    deadline.start()
    while deadline.elapsed() < limit:
        application.processEvents()
        if window.last_error:
            raise AssertionError(f'job failed: {window.last_error}')
        if all(stage in seen for stage in stages):
            return seen
        window.jobs.wait(200)
        application.processEvents()
        if not window.jobs.busy and all(stage in seen for stage in stages):
            return seen
    raise AssertionError(f'stages {stages} not reached; saw {seen}')



def settle(window, application, predicate, limit=60000):
    """Pump the event loop until ``predicate`` holds.

    Some results land before a caller can connect to ``stage_changed``, so
    waiting on the observable state is the only race-free way to assert it.
    """
    deadline = QtCore.QElapsedTimer()
    deadline.start()
    while deadline.elapsed() < limit:
        application.processEvents()
        if window.last_error:
            raise AssertionError(f'job failed: {window.last_error}')
        if predicate():
            return True
        window.jobs.wait(100)
        application.processEvents()
    raise AssertionError('condition was not reached')


def test_document_undo_redo_covers_every_edit():
    document = Document()
    document.set_orientation([10, 20, 30], [1, 2], 6.0)
    document.add_contact([1, 2, 3])
    document.add_contact([4, 5, 6])
    document.remove_contact([1, 2, 3])
    document.move_contact([4, 5, 6], [7, 8, 9])
    assert document.manual_contacts == [[7.0, 8.0, 9.0]]
    for _ in range(4):
        document.undo()
    assert document.manual_contacts == [] and document.rotation_deg == [10, 20, 30]
    document.undo()
    assert document.rotation_deg == [0.0, 0.0, 0.0]
    for _ in range(5):
        document.redo()
    assert document.manual_contacts == [[7.0, 8.0, 9.0]]
    assert document.rotation_deg == [10, 20, 30]
    with pytest.raises(VoxelMillError):
        document.add_contact([1, 2, float('nan')])


def test_document_preserves_auto_orientation_in_projects_and_undo():
    document = Document()
    document.set_orientation('auto', [1, 2], 6.0)
    assert document.rotation_deg == 'auto'
    assert document.manifest()['rotation_deg'] == 'auto'
    document.undo()
    assert document.rotation_deg == [0.0, 0.0, 0.0]
    document.redo()
    assert document.rotation_deg == 'auto'


def test_deleting_an_automatic_contact_is_recorded_as_a_suppression():
    document = Document()
    document.remove_contact([1, 1, 1])
    assert document.removed_contacts == [[1.0, 1.0, 1.0]]
    document.undo()
    assert document.removed_contacts == []


def test_job_runner_drops_stale_results(application):
    runner = JobRunner()
    done, stale = [], []
    runner.completed.connect(lambda r: done.append(r.value))
    runner.stale.connect(lambda r: stale.append(r.name))
    gate = QtCore.QSemaphore(0)
    runner.submit('slow', lambda token, progress: (gate.acquire(), 'old')[1])
    runner.invalidate()
    gate.release()
    runner.wait(10000)
    application.processEvents()
    assert stale == ['slow'] and done == []
    runner.submit('fresh', lambda token, progress: 'new')
    runner.wait(10000)
    application.processEvents()
    assert done == ['new']


def test_job_runner_reports_cancellation_and_errors(application):
    runner = JobRunner()
    results = []
    runner.completed.connect(results.append)

    def cancellable(token, progress):
        token.cancel()
        token.check()
    runner.submit('c', cancellable)
    runner.wait(10000)
    application.processEvents()
    assert results[-1].canceled

    runner.submit('e', lambda token, progress: (_ for _ in ()).throw(VoxelMillError('boom', 'no')))
    runner.wait(10000)
    application.processEvents()
    assert results[-1].error['code'] == 'boom'

    runner.submit('x', lambda token, progress: 1 / 0)
    runner.wait(10000)
    application.processEvents()
    assert results[-1].error['code'] == 'internal_error'
    assert 'ZeroDivisionError' in results[-1].traceback


def test_scene_decimates_only_for_display():
    scene = Scene()
    triangles = manifold_triangles(m.Manifold.cube((2, 2, 2)))
    scene.set_mesh('model', triangles)
    assert scene.actors['model'].GetMapper().GetInput().GetNumberOfCells() == len(triangles)
    assert scene.preview_note == ''
    big = np.repeat(triangles, 200000, axis=0)
    scene.set_mesh('supports', big)
    assert scene.actors['supports'].GetMapper().GetInput().GetNumberOfCells() < len(big)
    assert 'validation always uses every triangle' in scene.preview_note


def test_scene_visibility_and_contact_hit_testing():
    scene = Scene()
    scene.set_mesh('model', manifold_triangles(m.Manifold.cube((2, 2, 2))))
    scene.set_contacts([[0, 0, 2], [5, 5, 2]])
    assert scene.is_visible('model')
    scene.set_visible('model', False)
    assert not scene.is_visible('model')
    np.testing.assert_allclose(scene.nearest_contact([0.2, 0.1, 2.0], 0.5), [0, 0, 2])
    assert scene.nearest_contact([2.5, 2.5, 2.0], 0.5) is None
    scene.clear()
    assert scene.actors == {}


def test_layer_image_marks_selected_diagnostics():
    mask = np.zeros((8, 8), np.uint8)
    mask[2:6, 2:6] = 1
    grid = RasterGrid(8, 8, 0, 0, 1, 1)
    plain = mask_to_image(mask, [], grid)
    marked = mask_to_image(mask, [Diagnostic('raster_island', 'x', position_mm=[3.5, 3.5, 0])],
                           grid, marker=1)
    assert plain.pixelColor(3, 4).red() == 220
    # Markers are colored by diagnostic code now, so the expectation comes
    # from the palette rather than from a copy of one of its entries.
    island = ISSUE_COLORS['raster_island']
    assert (marked.pixelColor(3, 4).red(), marked.pixelColor(3, 4).green(),
            marked.pixelColor(3, 4).blue()) == island
    # Diagnostics without a position are legitimate report entries, not image
    # markers.  Rendering them must not assume a mapping-style object.
    mask_to_image(mask, [Diagnostic('plate_fit', 'ok')], grid)


@pytest.mark.gui
def test_editor_builds_supports_and_exports_only_after_validation(application, source, tmp_path):
    window = MainWindow(small_settings(), source, headless=True)
    drain(window, application)
    # Nothing is attached on import: a part is positioned first, then attached.
    assert window.document.derived.plan.metrics['contacts_routed'] == 0
    assert window.attachment_state == 'none'
    assert 'supports' not in window.scene.actors

    window.compute_attachments()
    drain(window, application, stages=('attachments',))
    assert window.attachment_state == 'routed'
    assert window.document.derived.plan.metrics['contacts_routed'] > 0
    assert window.document.derived.union.solid.status() == m.Error.NoError
    assert 'model' in window.scene.actors and 'supports' in window.scene.actors
    assert len(window.scene.contact_points) > 0

    # Layer scrubbing rasterizes at printer pitch, on a worker thread.
    window.request_layer(30)
    drain(window, application, stages=('layer',))
    assert 'z=' in window.layers.info.text()

    target = tmp_path / 'out.stl'
    window.export(target)
    drain(window, application, stages=('validate',))
    assert window.document.derived.validation.passed
    assert target.exists()
    assert json.loads(window.diagnostics.toPlainText())['passed']


@pytest.mark.gui
def test_editor_refuses_a_failed_export_until_warnings_are_armed(application, tmp_path):
    source = tmp_path / 'hollow.stl'
    write_stl(source, manifold_triangles(m.Manifold.cube((12, 12, 12), True)
                                         - m.Manifold.cube((6, 6, 6), True)))
    settings = small_settings()
    settings['repair']['seal_voids'] = False
    window = MainWindow(settings, source, headless=True)
    drain(window, application)
    target = tmp_path / 'blocked.stl'
    window.export(target)
    drain(window, application, stages=('validate',))
    assert not window.document.derived.validation.passed
    assert not target.exists()
    assert 'Allow warned export' in window.statusBar().currentMessage()

    window.actions_map['warned'].setChecked(True)
    window.export(target)
    drain(window, application, stages=('validate',))
    assert target.exists()
    assert 'warned' in window.statusBar().currentMessage()


@pytest.mark.gui
def test_editor_never_overwrites_existing_export_before_validation(application, tmp_path):
    source = tmp_path / 'hollow.stl'
    write_stl(source, manifold_triangles(m.Manifold.cube((12, 12, 12), True)
                                         - m.Manifold.cube((6, 6, 6), True)))
    settings = small_settings()
    settings['repair']['seal_voids'] = False
    window = MainWindow(settings, source, headless=True)
    drain(window, application)
    target = tmp_path / 'existing.stl'
    sentinel = b'preserve this previous export'
    target.write_bytes(sentinel)
    window.export(target)
    drain(window, application, stages=('validate',))
    assert not window.document.derived.validation.passed
    assert target.read_bytes() == sentinel


@pytest.mark.gui
def test_setup_exposes_manual_auto_toggles_and_complete_settings(application, source):
    window = MainWindow(small_settings(), source, headless=True)
    window.rotate_auto.setChecked(True)
    window.support_auto.setChecked(False)
    window.apply_settings()
    assert window.document.rotation_deg == 'auto'
    assert window.document.settings['support']['automatic'] is False
    assert not all(box.isEnabled() for box in window.rotation)
    raw = json.loads(window.settings_json.toPlainText())
    assert raw['process']['layer_height_mm'] == pytest.approx(0.2)
    assert 'motion' in raw['printer']
    drain(window, application)
    assert window.document.derived.plan.metrics['contacts_requested'] == 0


# Run in a child process under a real X server. VTK opens a genuine X window,
# and the offscreen Qt platform hands it a window id the server rejects; Xlib
# answers a BadWindow error by calling exit(), which would take the whole test
# session down with it rather than failing one test.
RENDER_CHILD = """
import json, sys
import numpy as np
from PySide6 import QtWidgets
import vtkmodules.all as vtk
from vtkmodules.util import numpy_support
import manifold3d as m
from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles
from voxelmill.gui.window import MainWindow

target = sys.argv[1]
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
settings = resolve_settings(overrides={
    'process': {'layer_height_mm': 0.2},
    'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1],
                'build_mm': [100., 80., 165.]},
    'resources': {'workers': 2}})
window = MainWindow(settings, headless=False)
window.show()
window.viewport.start()
window.scene.set_mesh('model', manifold_triangles(m.Manifold.sphere(12, 64)))
window.viewport.reset_camera()
for _ in range(8):
    app.processEvents()
window.viewport.add_navigation_cube()
window.viewport.render()

# The navigation cube only exists once there is a real interactor, and its
# face picking runs against the marker's own renderer, which headless tests
# cannot build.  Snap to a named view and pick the center of the marker
# viewport: with the camera on the front face, that has to read as Front.
window.set_view('front')
window.viewport.set_view('front', animate=False)
window.viewport.render()
size = window.viewport.interactor.GetRenderWindow().GetSize()
face = window.viewport.cube_face_under(int(0.90 * size[0]), int(0.88 * size[1]))
cube_enabled = bool(window.viewport.cube_widget and window.viewport.cube_widget.GetEnabled())

grab = window.grab()
saved = grab.save(target)
shot = vtk.vtkWindowToImageFilter()
shot.SetInput(window.viewport.interactor.GetRenderWindow())
shot.Update()
buffer = shot.GetOutput()
width, height, _ = buffer.GetDimensions()
pixels = numpy_support.vtk_to_numpy(
    buffer.GetPointData().GetScalars()).reshape(height, width, -1)
background = np.array([31, 33, 38])  # Scene's 0.12, 0.13, 0.15 background
lit = int((np.abs(pixels[:, :, :3].astype(int) - background).sum(axis=2) > 30).sum())
print(json.dumps({'qt': [grab.width(), grab.height()], 'saved': bool(saved),
                  'render': [width, height], 'surface_pixels': lit,
                  'cube_enabled': cube_enabled, 'cube_face': face}))
window.close()
"""


@pytest.mark.gui
def test_window_renders_a_real_vtk_surface(tmp_path):
    """Exercise Qt + VTK together; headless Scene tests cannot catch this."""
    if shutil.which('xvfb-run') is None:
        pytest.skip('a real X server (xvfb-run) is needed to open a VTK window')
    script = tmp_path / 'render_child.py'
    script.write_text(RENDER_CHILD)
    screenshot = tmp_path / 'editor.png'
    finished = subprocess.run(
        ['xvfb-run', '-a', '--server-args=-screen 0 1280x1024x24',
         sys.executable, str(script), str(screenshot)],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, 'QT_QPA_PLATFORM': 'xcb', 'XDG_CACHE_HOME': str(tmp_path / 'cache')})
    assert finished.returncode == 0, (
        f'render child died: stdout={finished.stdout[-2000:]} stderr={finished.stderr[-2000:]}')
    report = json.loads(finished.stdout.strip().splitlines()[-1])

    assert report['qt'][0] > 100 and report['qt'][1] > 100
    assert report['saved'] and screenshot.stat().st_size > 1000
    # Qt cannot grab VTK's native child window, so the screenshot's viewport is
    # blank whether or not anything rendered. The render window's own buffer is
    # the only evidence that the geometry reached the GPU.
    assert report['render'][0] > 100 and report['render'][1] > 100
    assert report['surface_pixels'] > 1000, 'the render window drew no surface'
    # The face mapping is unit-tested headlessly; this is the only place the
    # pick actually runs against the orientation marker's own renderer.
    assert report['cube_enabled']
    assert report['cube_face'] == 'Front', report['cube_face']


@pytest.mark.gui
def test_manual_contact_edits_rebuild_the_supports(application, source):
    window = MainWindow(small_settings(), source, headless=True)
    drain(window, application)
    before = window.document.derived.plan.metrics['contacts_requested']
    point = np.array([0.0, 0.0, 5.0])
    assert window.handle_pick(point, 'model', QtCore.Qt.ShiftModifier)
    drain(window, application, stages=('supports', 'union'))
    assert window.document.manual_contacts == [[0.0, 0.0, 5.0]]
    assert window.document.derived.plan.metrics['contacts_requested'] >= before
    existing = window.scene.contact_points[0]
    assert window.handle_pick(existing, 'contacts', QtCore.Qt.ControlModifier)
    drain(window, application, stages=('supports',))
    assert window.document.removed_contacts or window.document.manual_contacts == []
    window.undo()
    drain(window, application, stages=('supports',))
    assert window.document.undo_label == 'add support'


@pytest.mark.gui
def test_project_round_trip_keeps_edits_and_saved_validation(application, source, tmp_path):
    window = MainWindow(small_settings(), source, headless=True)
    drain(window, application)
    window.compute_attachments()
    drain(window, application, stages=('attachments',))
    window.document.add_contact([0.0, 0.0, 5.0])
    window.export(tmp_path / 'out.stl')
    drain(window, application, stages=('validate',))
    saved_validation = window.document.saved_validation
    assert saved_validation is not None
    project = tmp_path / 'part.voxmil'
    window.document.save(project)
    assert not window.document.dirty

    reopened = MainWindow(small_settings(), None, headless=True)
    reopened.open_project(project, extract_dir=tmp_path / 'extracted')
    assert reopened.document.manual_contacts == [[0.0, 0.0, 5.0]]
    assert reopened.document.saved_validation == saved_validation
    # A reopened project reports history, never a fresh guarantee.
    assert 'revalidate before exporting' in reopened.diagnostics.toPlainText()
    assert reopened.document.derived.validation is None
    drain(reopened, application)
    assert reopened.document.derived.union is not None


# --- GOO inspection in the editor (plan2 Tier 0.5) -----------------------

def _tiny_goo(path):
    """A small but physically consistent GOO the editor can open."""
    from voxelmill.goo import GooWriter
    settings = resolve_settings(overrides={
        'printer': {'build_mm': [3.2, 2.4, 10.0], 'pixels': [32, 24],
                    'pixel_pitch_mm': [0.1, 0.1], 'edge_clearance_mm': 0.0},
        'process': {'layer_height_mm': 0.1, 'bottom_layers': 1, 'transition_layers': 2}})
    frames = []
    for _ in range(4):
        frame = np.zeros((24, 32), np.uint8)
        frame[8:16, 8:24] = 255
        frames.append(frame)
    with GooWriter(path, settings, len(frames),
                   previews={'small': np.full((116, 116, 3), 40, np.uint8),
                             'big': np.full((290, 290, 3), 40, np.uint8)}) as writer:
        for index, frame in enumerate(frames):
            writer.add_layer(frame, (index + 1) * 0.1)
    return path, settings


def test_mask_to_image_does_not_wrap_a_255_valued_goo_frame():
    """255 * 220 wraps to 228 in uint8 and looks plausible by accident."""
    occupancy = np.zeros((8, 8), np.uint8)
    occupancy[2:6, 2:6] = 1
    decoded = occupancy * np.uint8(255)
    assert mask_to_image(occupancy).pixelColor(3, 4).red() == 220
    assert mask_to_image(decoded).pixelColor(3, 4).red() == 220


def test_mask_to_image_decimates_a_full_panel_before_allocating():
    from voxelmill.gui.layerview import preview_decimation
    assert preview_decimation(4320, 8520) == 4
    mask = np.zeros((512, 512), np.uint8)
    mask[100:104, 100:104] = 255      # a four-pixel feature must survive
    image = mask_to_image(mask, max_pixels=16384)
    assert image.width() == 128 and image.height() == 128
    assert image.pixelColor(25, 128 - 1 - 25).red() == 220


@pytest.mark.gui
def test_editor_opens_a_goo_and_scrubs_its_decoded_layers(application, tmp_path):
    path, _ = _tiny_goo(tmp_path / 'tiny.goo')
    window = MainWindow(small_settings(), None, headless=True)
    window.open_goo(path)
    drain(window, application, stages=('open_goo',))
    assert window.goo_source['layer_count'] == 4
    assert window.layers.selected_source == 'goo'
    assert window.layers.slider.maximum() == 3

    # Opening selects the file and asks for layer 0, so the tab is never blank.
    settle(window, application, lambda: 'layer 0' in window.layers.info.text())
    assert 'decoded GOO pixels' in window.layers.info.text()

    window.request_layer(2)
    settle(window, application, lambda: 'layer 2' in window.layers.info.text())
    assert 'z=0.300' in window.layers.info.text()

    # The second visit is served from the cache, with no job at all.
    assert len(window.document.derived.layer_cache) >= 1
    submitted = []
    original = window.jobs.submit
    window.jobs.submit = lambda name, fn: submitted.append(name)
    window.request_layer(2)
    window.jobs.submit = original
    assert submitted == []

    window.close_goo()
    assert window.goo_source is None
    assert window.layers.selected_source == 'union'


@pytest.mark.gui
def test_editor_opens_a_ctb_and_scrubs_its_decoded_layers(application, tmp_path):
    from voxelmill.ctb import CtbWriter
    settings = resolve_settings(overrides={
        'printer': {'build_mm': [3.2, 2.4, 10.0], 'pixels': [32, 24],
                    'pixel_pitch_mm': [0.1, 0.1], 'edge_clearance_mm': 0.0},
        'process': {'layer_height_mm': 0.1, 'bottom_layers': 1, 'transition_layers': 0,
                    'bottom_exposure_s': 8.0, 'normal_exposure_s': 2.0}})
    path = tmp_path / 'tiny.ctb'
    with CtbWriter(path, settings, 3) as writer:
        for index in range(3):
            frame = np.zeros((24, 32), np.uint8)
            frame[8:16, 8:24] = 255
            writer.add_layer(frame, (index + 1) * 0.1)
    window = MainWindow(settings, None, headless=True)
    window.open_goo(path)
    drain(window, application, stages=('open_goo',))
    assert window.goo_source['format'] == 'ctb'
    assert window.goo_source['layer_count'] == 3
    settle(window, application, lambda: 'layer 0' in window.layers.info.text())
    assert 'decoded CTB pixels' in window.layers.info.text()
    window.request_layer(1)
    settle(window, application, lambda: 'layer 1' in window.layers.info.text())
    window.close()


@pytest.mark.gui
def test_editor_says_why_a_layer_is_unavailable_instead_of_going_quiet(application):
    window = MainWindow(small_settings(), None, headless=True)
    window.request_layer(0)
    assert 'open an STL' in window.statusBar().currentMessage()
    window.layers.source.addItem('GOO: none', 'goo')
    window.layers.source.setCurrentIndex(window.layers.source.findData('goo'))
    window.request_layer(0)
    assert 'no slice file is open' in window.statusBar().currentMessage()


@pytest.mark.gui
def test_editor_deep_verifies_a_goo_with_the_same_code_as_the_cli(application, tmp_path):
    path, settings = _tiny_goo(tmp_path / 'tiny.goo')
    window = MainWindow(settings, None, headless=True)
    window.verify_goo(path)
    drain(window, application, stages=('verify',))
    payload = json.loads(window.diagnostics.toPlainText())
    assert payload['command'] == 'verify'
    assert payload['report']['passed']
    assert 'passed' in window.statusBar().currentMessage()

    from voxelmill.goo import verify_goo
    assert payload['report']['checks'] == verify_goo(path, settings)['report']['checks']


# --- a model that does not fit still opens (plan2 Tier 0.4) --------------

def oversized_settings():
    """A plate far smaller than the sphere fixture, so nothing fits."""
    return resolve_settings(overrides={
        'process': {'layer_height_mm': 0.2},
        'printer': {'pixels': [100, 80], 'pixel_pitch_mm': [0.1, 0.1],
                    'build_mm': [10., 8., 40.]},
        'resources': {'workers': 2}})


def test_out_of_bounds_triangles_get_their_own_cell_color():
    from voxelmill.gui.viewport import OUT_OF_BOUNDS_COLOR, out_of_bounds_mask
    settings = oversized_settings()
    inside = manifold_triangles(m.Manifold.cube((4, 4, 4), True).translate((0, 0, 4)))
    outside = manifold_triangles(m.Manifold.cube((4, 4, 4), True).translate((30, 0, 4)))
    triangles = np.concatenate([inside, outside])
    mask = out_of_bounds_mask(triangles, settings)
    assert mask.sum() == len(outside) and not mask[:len(inside)].any()

    data = polydata_from_triangles(triangles, settings)
    scalars = data.GetCellData().GetScalars()
    assert scalars.GetNumberOfTuples() == len(triangles)
    reds = sum(1 for i in range(scalars.GetNumberOfTuples())
               if tuple(int(v) for v in scalars.GetTuple3(i)) == OUT_OF_BOUNDS_COLOR)
    assert reds == len(outside)

    # No settings means no scalar array at all, which is the pre-existing path.
    assert polydata_from_triangles(triangles).GetCellData().GetScalars() is None


@pytest.mark.gui
def test_editor_opens_a_model_that_cannot_fit_and_says_by_how_much(application, source):
    window = MainWindow(oversized_settings(), source, headless=True)
    drain(window, application, stages=('place', 'model'))
    assert window.placement_fits is False
    assert max(window.placement_overflow_mm) > 0
    # The point of the change: it is on screen rather than an error message.
    assert 'model' in window.scene.actors
    assert window.scene.out_of_bounds['model'] > 0
    assert 'display-only count:' in window.statusBar().currentMessage()
    assert window.last_error is None


@pytest.mark.gui
def test_clip_setting_round_trips_through_the_setup_tab(application, source):
    window = MainWindow(small_settings(), source, headless=True)
    assert window.clip_to_build.isChecked() is False
    window.clip_to_build.setChecked(True)
    window.apply_settings()
    assert window.document.settings['assembly']['clip_to_build_volume'] is True
    assert json.loads(window.settings_json.toPlainText())['assembly']['clip_to_build_volume'] is True


# --- navigation cube and camera control (plan2 Tier 0.3) -----------------

def _plate_scene(settings=None):
    scene = Scene()
    scene.show_build_volume(settings or small_settings())
    return scene


def test_named_views_agree_with_the_green_plate_edge():
    """Front is -Y, the same face show_build_volume paints green."""
    from voxelmill.gui.camera import CameraController, VIEWS
    settings = small_settings()
    scene = _plate_scene(settings)
    camera = CameraController(scene.renderer)
    center = np.asarray([0.0, 0.0, settings['printer']['build_mm'][2] / 2])

    camera.set_view('front')
    assert np.allclose(camera.camera.GetFocalPoint(), center)
    assert np.allclose(camera.camera.GetViewUp(), [0, 0, 1])
    offset = np.asarray(camera.camera.GetPosition()) - center
    assert offset[1] < 0 and np.allclose(offset[[0, 2]], 0)

    # Every face maps to a distinct camera direction.
    directions = {}
    for name in VIEWS:
        camera.set_view(name)
        offset = np.asarray(camera.camera.GetPosition()) - np.asarray(camera.camera.GetFocalPoint())
        directions[name] = tuple(np.round(offset / np.linalg.norm(offset), 6))
    assert len(set(directions.values())) == len(VIEWS)
    assert directions['front'] == tuple(-np.asarray(directions['back']))
    assert directions['left'] == tuple(-np.asarray(directions['right']))
    assert directions['top'] == tuple(-np.asarray(directions['bottom']))
    # A view up parallel to the view direction would be degenerate.
    for name in VIEWS:
        camera.set_view(name)
        up = np.asarray(camera.camera.GetViewUp())
        # directions[] is rounded to six places, so this is exact to that.
        assert abs(np.dot(up, np.asarray(directions[name]))) < 1e-6


def test_every_cube_face_maps_to_its_own_view():
    from voxelmill.gui.camera import FACE_VIEWS, VIEWS
    from voxelmill.gui.viewport import cube_face_at
    assert set(FACE_VIEWS) == {'Front', 'Back', 'Left', 'Right', 'Top', 'Bottom'}
    assert len(set(FACE_VIEWS.values())) == 6
    assert set(FACE_VIEWS.values()) <= set(VIEWS)
    for position, face in ((0.5, 0.1, 0.1), 'Right'), ((-0.5, 0, 0), 'Left'), \
                          ((0, 0.5, 0), 'Back'), ((0, -0.5, 0), 'Front'), \
                          ((0, 0, 0.5), 'Top'), ((0, 0, -0.5), 'Bottom'):
        assert cube_face_at(position) == face
    # Near the center the face is ambiguous, so nothing is guessed.
    assert cube_face_at((0.01, 0.01, 0.01)) is None
    assert cube_face_at((np.nan, 0, 0)) is None


def test_view_transition_lands_exactly_on_the_named_pose():
    from voxelmill.gui.camera import CameraController
    camera = CameraController(_plate_scene().renderer)
    camera.set_view('front')
    poses = camera.steps_to('right', steps=8)
    assert len(poses) == 8
    for pose in poses:
        camera.apply(pose)
    direct = CameraController(_plate_scene().renderer)
    direct.set_view('right')
    interpolated = np.asarray(camera.camera.GetPosition()) - np.asarray(camera.camera.GetFocalPoint())
    expected = np.asarray(direct.camera.GetPosition()) - np.asarray(direct.camera.GetFocalPoint())
    assert np.allclose(interpolated / np.linalg.norm(interpolated),
                       expected / np.linalg.norm(expected))
    # Opposite views have no defined interpolation plane; it must still arrive.
    camera.set_view('front')
    for pose in camera.steps_to('back', steps=4):
        camera.apply(pose)
    offset = np.asarray(camera.camera.GetPosition()) - np.asarray(camera.camera.GetFocalPoint())
    assert offset[1] > 0


@pytest.mark.gui
def test_window_exposes_every_view_as_an_action(application, source):
    from voxelmill.gui.camera import SHORTCUTS, VIEWS
    window = MainWindow(small_settings(), None, headless=True)
    for name in SHORTCUTS:
        assert f'view_{name}' in window.actions_map
        assert window.actions_map[f'view_{name}'].shortcut().toString() == SHORTCUTS[name].replace('Ctrl', 'Ctrl')
        assert name in VIEWS
    assert window.set_view('front') == 'front'
    assert window.camera.current == 'front'
    with pytest.raises(VoxelMillError, match='Unknown view'):
        window.set_view('sideways')
    window.fit_view()


# --- whole-file checks shared with the CLI -------------------------------

@pytest.mark.gui
def test_editor_inspects_and_validates_a_file_exactly_as_the_cli_does(application, source):
    from voxelmill.pipeline import inspect_stl, validate_stl
    settings = small_settings()
    window = MainWindow(settings, None, headless=True)

    window.inspect_stl_file(source)
    drain(window, application, stages=('inspect',))
    payload = json.loads(window.diagnostics.toPlainText())
    assert payload['command'] == 'inspect'
    direct = inspect_stl(source, settings)
    timing = {'inspection_seconds'}      # wall clock differs between two runs
    assert ({k: v for k, v in payload['meshes'][0].items() if k not in timing}
            == {k: v for k, v in direct.items() if k not in timing})
    assert 'triangles' in window.statusBar().currentMessage()

    window.validate_stl_file(source)
    drain(window, application, stages=('validate_file',))
    payload = json.loads(window.diagnostics.toPlainText())
    assert payload['command'] == 'validate'
    expected = validate_stl(source, settings)
    assert payload['report']['checks'] == expected.to_dict()['checks']
    assert payload['report']['passed'] == expected.passed
    assert 'validation' in window.statusBar().currentMessage()


def test_every_cli_subcommand_has_a_place_in_the_editor():
    """CLI and GUI parity is a stated requirement, so it gets an assertion."""
    from voxelmill.cli import build_parser
    parser = build_parser()
    actions = [a for a in parser._actions if hasattr(a, 'choices') and a.choices
               and 'prepare' in (a.choices or {})]
    commands = set(actions[0].choices)
    # gui is the editor itself.  profile keeps its resolved-settings JSON box
    # and also reaches the library through the same dialog resin uses.
    editor_actions = {
        'inspect': 'inspect_stl', 'validate': 'validate_stl', 'prepare': 'export',
        'slice': 'export_goo', 'goo-info': 'open_goo', 'verify': 'verify_goo',
        'ctb-info': 'open_goo', 'convert': 'operation_dialog',
        'preset': 'apply_support_preset', 'resin': 'profile_library_dialog',
        'profile': 'profile_library_dialog', 'islands': 'check_islands',
        'measure': 'measure_stl', 'batch': 'operation_dialog',
        'support-example': 'support_editor_dialog',
        'import-step': 'operation_dialog',
        'boolean': 'operation_dialog', 'trim': 'operation_dialog',
        'cap': 'operation_dialog', 'hollow': 'operation_dialog',
        'thickness': 'operation_dialog', 'calibrate': 'operation_dialog',
        'monitor': 'printer_monitor_dialog',
        # Generated text; the editor reaches them through Run operation, which
        # is itself generated from this parser.
        'completion': 'operation_dialog', 'manpage': 'operation_dialog',
        'report-html': 'operation_dialog',
    }
    assert commands - set(editor_actions) == {'gui'}
    # The stronger statement: Run operation is generated from this same parser,
    # so every non-gui command is reachable there whatever else it also has.
    from voxelmill.gui.operations import operation_parsers
    assert set(operation_parsers()) == commands - {'gui'}
    window_actions = set(MainWindow.__dict__) | {
        'open', 'open_project', 'save_project', 'export', 'export_goo', 'export_ctb',
        'cancel', 'open_goo', 'close_goo', 'verify_goo', 'inspect_stl', 'validate_stl'}
    for command, action in editor_actions.items():
        assert action in window_actions, f'{command} has no editor equivalent'


@pytest.mark.gui
def test_setup_shows_the_same_exposure_schedule_as_the_profile_command(application):
    from voxelmill.config import layer_exposure
    settings = small_settings()
    window = MainWindow(settings, None, headless=True)
    process = settings['process']
    expected = [layer_exposure(settings, i)
                for i in range(process['bottom_layers'] + process['transition_layers'] + 2)]
    shown = window.exposure_schedule.text()
    for value in expected:
        assert f'{value:g}' in shown
    # The ramp is visible, not just the two endpoints.
    assert len(shown.split()) == len(expected) + 1


def test_island_badge_starts_unchecked_and_says_what_it_does_not_cover(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.island_badge.text() == 'islands: not checked'
    assert window.island_summary is None
    window._set_island_badge({'island_count': 0, 'layers': 40, 'min_overlap_pixels': 1,
                              'not_examined': ['drainage_bottlenecks', 'support_routes']})
    assert window.island_badge.text() == 'islands: no islands'
    assert 'drainage_bottlenecks' in window.island_badge.toolTip()
    window._set_island_badge({'island_count': 3, 'layers': 40, 'min_overlap_pixels': 1,
                              'not_examined': []})
    assert window.island_badge.text() == 'islands: 3 islands'
    window.close()


@pytest.mark.gui
def test_added_object_pose_controls_are_undoable_and_quit_is_last(application, tmp_path):
    window = MainWindow(small_settings(), None, headless=True)
    source = tmp_path / 'second.stl'
    source.write_bytes(b'fixture')
    window.document.add_extra_model({'path': source})
    window._sync_widgets_from_document()
    assert window.object_panel.list.count() == 2
    window.object_panel.list.setCurrentRow(1)
    window.object_panel.rotate_rows['Z'].set_value(35)
    window.object_panel.translate_rows['X'].set_value(12)
    window.object_panel.translate_rows['Z'].set_value(7)
    # Avoid starting a geometry job from this state-only control regression.
    window.reload = lambda: None
    assert window.apply_object_pose()
    assert window.document.extra_models[0]['rotate'] == [0.0, 0.0, 35.0]
    assert window.document.extra_models[0]['center_offset'] == [12.0, 0.0]
    assert window.document.extra_models[0]['lift_mm'] == 7.0
    assert window.document.undo_label == 'move model'
    assert window.actions_map['quit'].text() == 'Quit'
    assert window.file_menu.actions()[-1] is window.actions_map['quit']
    window.object_panel.list.setCurrentRow(1)
    window.object_overrides.setText('{"pillar_diameter_mm": 1.6}')
    assert window.apply_object_overrides()
    assert window.document.extra_models[0]['overrides']['support']['pillar_diameter_mm'] == 1.6
    window.close()


@pytest.mark.gui
def test_startup_prompts_are_deferred_until_complete_startup(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    called = []
    monkeypatch.setattr(MainWindow, '_maybe_run_wizard', lambda self, source: called.append('wizard'))
    monkeypatch.setattr(MainWindow, '_maybe_prompt_freecad', lambda self: called.append('freecad'))
    monkeypatch.setattr(MainWindow, '_maybe_offer_recovery', lambda self: called.append('recovery'))
    window = MainWindow(small_settings(), None, headless=True)
    assert called == []
    window.complete_startup()
    assert called == ['wizard', 'freecad', 'recovery']
    window.complete_startup()
    assert called == ['wizard', 'freecad', 'recovery']
    window.close()


@pytest.mark.gui
def test_accept_freecad_path_resolves_macos_app_bundle(application, tmp_path, monkeypatch):
    from voxelmill.gui import appprefs
    monkeypatch.setattr(appprefs, 'preferences_path', lambda: tmp_path / 'editor.json')
    bundle = tmp_path / 'FreeCAD.app'
    cmd = bundle / 'Contents' / 'MacOS' / 'FreeCADCmd'
    cmd.parent.mkdir(parents=True)
    cmd.write_text('#!/bin/sh\n')
    cmd.chmod(0o755)
    window = MainWindow(small_settings(), None, headless=True)
    resolved = window._accept_freecad_path(str(bundle))
    assert resolved == str(cmd.resolve())
    assert window.editor_preferences['freecad_path'] == str(bundle)
    assert appprefs.load_preferences()['freecad_path'] == str(bundle)
    window.close()


@pytest.mark.gui
def test_run_shows_the_window_before_vtk_start_and_startup_prompts():
    import inspect
    from voxelmill.gui.window import run
    source = inspect.getsource(run)
    assert source.index('window.show()') < source.index('viewport.start()')
    assert source.index('window.show()') < source.index('complete_startup')


@pytest.mark.gui
def test_import_step_action_tracks_freecad(application, tmp_path, monkeypatch):
    from voxelmill.gui import appprefs
    monkeypatch.setattr(appprefs, 'preferences_path', lambda: tmp_path / 'editor.json')
    monkeypatch.setattr('voxelmill.importers.find_freecad', lambda preferred=None: None)
    window = MainWindow(small_settings(), None, headless=True)
    assert 'import_step' in window.actions_map
    assert window.actions_map['import_step'].text() == 'Import STEP...'
    assert not window.actions_map['import_step'].isEnabled()
    window.close()

    fake = tmp_path / 'fakefreecad'
    fake.write_text('#!/bin/sh\n')
    fake.chmod(0o755)
    monkeypatch.setattr('voxelmill.importers.find_freecad', lambda preferred=None: fake)
    window = MainWindow(small_settings(), None, headless=True)
    assert window.actions_map['import_step'].isEnabled()
    window.editor_preferences['freecad_path'] = str(fake)
    window._editor_preferences_applied({'freecad_path': str(fake)})
    assert window.actions_map['import_step'].isEnabled()
    window.close()


@pytest.mark.gui
def test_open_stl_draws_the_primary_under_the_model_actor(application, source):
    """A single STL with no added parts used to crash in ``_finish_place``.

    ``load_and_place`` returned ``extra_models=None``, and the handler called
    ``.get`` on that None before the mesh could be drawn.
    """
    window = MainWindow(small_settings(), None, headless=True)
    window.open_stl(source)
    drain(window, application, stages=('place', 'model'))
    assert window.last_error is None
    assert 'model' in window.scene.actors
    assert window.scene.actors['model'].role == 'model'
    assert window.scene.actors['model'].object_index == 0
    window.close()


@pytest.mark.gui
def test_added_models_are_drawn_as_separate_actors(application, source, tmp_path):
    second = tmp_path / 'other.stl'
    write_stl(second, manifold_triangles(m.Manifold.cube((4, 4, 4))))
    window = MainWindow(small_settings(), source, headless=True)
    window.document.add_extra_model({
        'path': second, 'center_offset': (20.0, 0.0), 'lift_mm': 5.0})
    window.reload()
    drain(window, application, stages=('place', 'model'))
    assert window.last_error is None
    assert 'model' in window.scene.actors
    assert 'model:1' in window.scene.actors
    assert window.scene.actors['model:1'].object_index == 1
    assert window.object_panel.list.count() == 2
    window.close()


def test_an_edit_marks_the_island_badge_stale_rather_than_leaving_it_current(application):
    window = MainWindow(small_settings(), None, headless=True)
    window._set_island_badge({'island_count': 0, 'layers': 40, 'min_overlap_pixels': 1,
                              'not_examined': []})
    assert not window.island_stale
    # reload() is the entry every settings edit funnels through.
    window.reload()
    assert window.island_stale and '(stale)' in window.island_badge.text()
    assert 'changed since this scan' in window.island_badge.toolTip()
    window.close()


def test_check_islands_says_why_it_cannot_run_without_an_assembly(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.check_islands() is None
    assert 'no assembly yet' in window.statusBar().currentMessage()
    window.close()


@pytest.mark.gui
def test_the_editor_island_scan_matches_the_islands_command(application, source, tmp_path):
    """One assembly, two paths into the same analysis, one answer."""
    from voxelmill.contracts import CancellationToken, no_progress
    from voxelmill.gui import services
    from voxelmill.pipeline import scan_islands
    window = MainWindow(small_settings(), source, headless=True)
    drain(window, application)
    settle(window, application, lambda: window.document.derived.union is not None)
    union = window.document.derived.union
    from_editor = services.scan_islands(window.document, union,
                                        CancellationToken(), no_progress)
    exported = tmp_path / 'assembly.stl'
    write_stl(exported, union.triangle_arrays)
    from_file = scan_islands(exported, window.document.settings)
    assert from_editor['island_count'] == from_file['island_count']
    assert from_editor['check'] == from_file['check']
    assert from_editor['layers'] == from_file['layers']
    window.close()


@pytest.mark.gui
def test_the_badge_is_earned_again_after_every_rebuild(application, source):
    window = MainWindow(small_settings(), source, headless=True)
    assert window.auto_island_check
    drain(window, application)
    settle(window, application, lambda: window.island_summary is not None)
    assert not window.island_stale
    assert window.island_badge.text().startswith('islands: ')
    window.rebuild()
    assert window.island_stale
    settle(window, application, lambda: not window.island_stale)
    window.close()


def test_scale_and_mirror_round_trip_through_the_setup_tab(application, tmp_path):
    window = MainWindow(small_settings(), None, headless=True)
    window.scale[0].setValue(1.25)
    window.mirror[2].setChecked(True)
    window.apply_settings()
    assert window.document.scale_factors == [1.25, 1.0, 1.0]
    assert window.document.mirror_axes == [False, False, True]
    # Applying the Setup tab is three commands — settings, transform, then
    # orientation — so reverting the transform takes two undos, not one.
    # Undo syncs the widgets itself; no explicit refresh is needed here.
    assert window.document.undo_label == 'orientation'
    window.undo()
    assert window.document.undo_label == 'transform'
    window.undo()
    assert window.document.scale_factors == [1.0, 1.0, 1.0]
    assert window.scale[0].value() == pytest.approx(1.0)
    assert not window.mirror[2].isChecked()

    # They survive a project round trip, and an archive without them loads as
    # the identity rather than failing.
    window.document.set_transform([1.5, 1.5, 1.5], [True, False, False])
    project = tmp_path / 'scaled.voxmil'
    window.document.source = tmp_path / 'model.stl'
    write_stl(window.document.source, manifold_triangles(m.Manifold.cube((2, 2, 2))))
    window.document.save(project)
    reloaded = Document.load(project, extract_dir=str(tmp_path / 'extract'))
    assert reloaded.scale_factors == [1.5, 1.5, 1.5]
    assert reloaded.mirror_axes == [True, False, False]
    window.close()


def test_an_out_of_range_scale_is_reported_and_leaves_the_document_alone(application):
    window = MainWindow(small_settings(), None, headless=True)
    window.scale[0].setRange(0.0, 1000.0)  # bypass the widget clamp, not the validator
    window.scale[0].setValue(500.0)
    window.apply_settings()
    assert window.last_error['code'] == 'invalid_scale'
    assert window.document.scale_factors == [1.0, 1.0, 1.0]
    window.close()


def test_the_measurement_row_says_when_there_is_nothing_to_measure(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.measurement.text() == 'no placement yet'
    window.close()


@pytest.mark.gui
def test_the_editor_measurement_matches_the_measure_command(application, source):
    """One pose, two paths into the same function, one set of numbers."""
    from voxelmill.pipeline import measure_stl
    window = MainWindow(small_settings(), source, headless=True)
    window.document.set_transform([1.2, 1.2, 1.2], [False, True, False])
    window.reload()
    drain(window, application, stages=('place',))
    settle(window, application, lambda: window.document.placement is not None)
    placed = np.asarray(window.document.placement.bounds, dtype=float)
    shown = window.measurement.text()
    for value in (placed[1] - placed[0]):
        assert f'{value:.3f}' in shown
    reference = measure_stl(source, window.document.settings, rotate=window.document.rotation_deg,
                            center_offset=window.document.center_offset_mm,
                            lift_mm=window.document.model_lift_mm,
                            scale=window.document.scale_factors,
                            mirror=window.document.mirror_axes)
    np.testing.assert_allclose(reference['placed_size_mm'], placed[1] - placed[0], atol=1e-9)
    assert 'mirrored on Y' in reference['transform_note']
    window.close()


def test_every_compact_setting_names_its_config_key_and_its_cli_flag(application):
    """A GUI user reading a tooltip must be able to find the same value in the CLI."""
    from voxelmill.cli import build_parser
    from voxelmill.config import DEFAULTS
    window = MainWindow(small_settings(), None, headless=True)
    flags = {flag for action in build_parser()._actions for flag in action.option_strings}
    from voxelmill.gui.operations import operation_parsers
    for parser in operation_parsers().values():
        flags |= {flag for action in parser._actions for flag in action.option_strings}
    for name, (section, key, flag) in MainWindow.SETTING_KEYS.items():
        # The key has to exist, or the tooltip documents something unreachable.
        assert key in DEFAULTS[section], f'{section}.{key}'
        tip = getattr(window, name).toolTip()
        assert f'{section}.{key}' in tip, name
        assert f'--set {section}.{key}=VALUE' in tip, name
        if flag is not None:
            assert flag in flags, f'{name} names a flag that does not exist: {flag}'
            assert flag in tip
    window.close()


def test_a_changed_setting_is_marked_and_can_be_reverted(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.markers['spacing'].text() == ''
    assert not window.reverts['spacing'].isEnabled()
    baseline = window.document.settings['support']['spacing_mm']

    window.spacing.setValue(baseline + 2.0)
    window.apply_settings()
    assert window.document.settings['support']['spacing_mm'] == baseline + 2.0
    assert window.markers['spacing'].text() == '●'
    assert window.reverts['spacing'].isEnabled()
    # The marker says what it was and what it is, not merely that it changed.
    tip = window.markers['spacing'].toolTip()
    assert repr(baseline) in tip and repr(baseline + 2.0) in tip
    # Nothing else is marked.
    assert window.markers['overhang'].text() == ''

    window.revert_setting('spacing')
    assert window.document.settings['support']['spacing_mm'] == baseline
    assert window.markers['spacing'].text() == ''
    assert not window.reverts['spacing'].isEnabled()
    assert window.document.undo_label == 'settings'


def test_the_baseline_follows_the_profile_the_user_chose(application, monkeypatch, tmp_path):
    """After applying a profile, modified means changed from that profile."""
    from voxelmill.config import resolve_settings
    from voxelmill.gui.profiles import ProfileLibraryDialog
    monkeypatch.setenv('VOXELMILL_PROFILE_PATH', str(tmp_path))
    started = resolve_settings(overrides={'support': {'spacing_mm': 4.0}})
    window = MainWindow(started, None, headless=True)
    assert window.markers['spacing'].text() == ''

    applied = resolve_settings()  # spacing back at the default 3.0
    monkeypatch.setattr(ProfileLibraryDialog, 'exec',
                        lambda self: (self.__setattr__('applied', applied),
                                      self.document.set_settings(applied))[0])
    window.profile_library_dialog()
    assert window.document.baseline_settings['support']['spacing_mm'] == 3.0
    assert window.markers['spacing'].text() == ''
    window.close()


def test_reverting_a_setting_absent_from_the_baseline_reports_rather_than_guesses():
    document = Document()
    document.baseline_settings['support'].pop('spacing_mm')
    with pytest.raises(VoxelMillError, match='no value to revert to'):
        document.revert_setting('support', 'spacing_mm')


def test_undo_and_redo_refresh_the_setup_controls_and_their_markers(application):
    """A document change nothing reflects is a control showing a stale value.

    Undo only refreshed the undo labels and restarted the pipeline, so an
    undone settings edit left every Setup field, and every modified dot,
    showing the value that had just been reverted.
    """
    window = MainWindow(small_settings(), None, headless=True)
    baseline = window.document.settings['support']['spacing_mm']
    window.spacing.setValue(baseline + 1.5)
    window.apply_settings()
    assert window.markers['spacing'].text() == '●'

    # Setup applies settings, transform and orientation, so the settings edit
    # is the third undo back.
    for _ in range(3):
        window.undo()
    assert window.document.settings['support']['spacing_mm'] == baseline
    assert window.spacing.value() == pytest.approx(baseline)
    assert window.markers['spacing'].text() == ''

    for _ in range(3):
        window.redo()
    assert window.spacing.value() == pytest.approx(baseline + 1.5)
    assert window.markers['spacing'].text() == '●'
    window.close()


def test_with_suffix_if_missing_only_appends_when_the_user_typed_none():
    assert with_suffix_if_missing('part', '.voxmil') == 'part.voxmil'
    assert with_suffix_if_missing('part.stl', '.voxmil') == 'part.stl'
    assert with_suffix_if_missing('/tmp/dir/name', '.goo') == '/tmp/dir/name.goo'
    assert with_suffix_if_missing('/tmp/dir/name.ctb', '.goo') == '/tmp/dir/name.ctb'


def test_finish_islands_fills_the_report_dock_and_offers_the_island_code(application):
    """Check islands used to leave the Report tab blank even on a failure."""
    window = MainWindow(small_settings(), None, headless=True)
    diagnostic = asdict(Diagnostic('raster_island', 'floating material', layer=3,
                                   position_mm=[1.0, 2.0, 3.0]))
    summary = {
        'check': 'fail', 'island_count': 1, 'island_components': 1,
        'layers': 40, 'nonempty_layers': 12, 'min_overlap_pixels': 1,
        'layer_height_mm': 0.1,
        'islands': [{'layer': 3, 'position_mm': [1.0, 2.0, 3.0],
                    'message': 'floating material', 'details': {}}],
        'truncated': False, 'other_checks': {'overlap': 'pass'},
        'closed_surface': 'pass', 'open_rows': 0,
        'not_examined': ['drainage_bottlenecks', 'support_routes', 'plate_fit', 'union_raster_parity'],
        'diagnostics': [diagnostic],
    }
    window._finish_islands(summary)
    assert window.diagnostic_list.topLevelItemCount() > 0
    payload = window.diagnostics.toPlainText()
    assert payload.strip()
    assert 'island scan only' in payload
    assert 'raster_island' in [window.layers.picker.itemData(i)
                               for i in range(window.layers.picker.count())]
    window.close()


def _shown_title(window):
    """Expand Qt's ``[*]`` placeholder the way a window manager would."""
    title = window.windowTitle()
    if '[*]' in title:
        return title.replace('[*]', '*' if window.isWindowModified() else '')
    return title


def test_actions_map_exposes_the_new_file_and_parts_keys(application):
    window = MainWindow(small_settings(), None, headless=True)
    for key in ('new_project', 'save_project_as', 'allow_part_to_part', 'reset_all_lifts'):
        assert key in window.actions_map
    assert window.file_menu.actions()[-1] is window.actions_map['quit']
    assert window.actions_map['quit'].text() == 'Quit'
    assert window.actions_map['save_project'].text() == 'Save project...'
    window.close()


def test_save_project_with_a_path_does_not_open_a_dialog(application, tmp_path, monkeypatch):
    window = MainWindow(small_settings(), None, headless=True)
    path = tmp_path / 'named.voxmil'
    window.document.add_contact([1.0, 2.0, 3.0])
    window.project_path = str(path)
    called = []
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName',
                        lambda *args, **kwargs: called.append(True) or ('', ''))
    assert window.save_project() is True
    assert called == []
    assert path.exists()
    assert not window.document.dirty
    assert window.actions_map['save_project'].text() == 'Save project'
    assert _shown_title(window) == f'{path.name} — VoxelMill'
    window.close()


def test_save_project_as_remembers_the_path(application, tmp_path, monkeypatch):
    window = MainWindow(small_settings(), None, headless=True)
    target = tmp_path / 'as.voxmil'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName',
                        lambda *args, **kwargs: (str(target), 'VoxelMill (*.voxmil)'))
    window.document.add_contact([1.0, 2.0, 3.0])
    assert window.save_project_as() is True
    assert window.project_path == str(target.resolve())
    assert not window.document.dirty
    assert window.actions_map['save_project'].text() == 'Save project'
    called = []
    window.document.add_contact([4.0, 5.0, 6.0])
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName',
                        lambda *args, **kwargs: called.append(True) or ('', ''))
    assert window.save_project() is True
    assert called == []
    window.close()


def test_new_project_keeps_settings_and_clears_the_document(application, tmp_path):
    window = MainWindow(small_settings(), None, headless=True)
    settings = deepcopy(window.document.settings)
    settings['support']['spacing_mm'] = 9.0
    window.document.set_settings(settings)
    window.document.add_contact([1.0, 2.0, 3.0])
    window.project_path = str(tmp_path / 'old.voxmil')
    window._refresh_window_title()
    assert window.document.dirty
    assert window.new_project() is True
    assert window.document.settings['support']['spacing_mm'] == pytest.approx(9.0)
    assert window.document.source is None
    assert window.document.manual_contacts == []
    assert window.document.extra_models == []
    assert window.document.undo_label is None
    assert window.project_path is None
    assert _shown_title(window) == 'VoxelMill'
    assert not window.document.dirty
    assert not window.isWindowModified()
    window.close()


def test_dirty_document_marks_the_window_title(application, tmp_path):
    window = MainWindow(small_settings(), None, headless=True)
    assert _shown_title(window) == 'VoxelMill'
    assert not window.isWindowModified()
    window.document.add_contact([1.0, 2.0, 3.0])
    window._refresh_undo()
    assert window.isWindowModified()
    assert _shown_title(window) == 'VoxelMill*'
    window.project_path = str(tmp_path / 'foo.voxmil')
    window._refresh_window_title()
    assert _shown_title(window) == 'foo.voxmil — VoxelMill*'
    window.close()


def test_set_plate_floor_preserves_relative_gaps_and_undoes(tmp_path):
    document = Document()
    document.set_orientation([0.0, 0.0, 0.0], [0.0, 0.0], 5.0)
    document.add_extra_model({'path': tmp_path / 'extra.stl', 'lift_mm': 12.0})
    document.set_plate_floor(20)
    assert document.model_lift_mm == pytest.approx(20.0)
    assert document.extra_models[0]['lift_mm'] == pytest.approx(27.0)
    document.undo()
    assert document.model_lift_mm == pytest.approx(5.0)
    assert document.extra_models[0]['lift_mm'] == pytest.approx(12.0)
    document.reset_all_lifts(20)
    assert document.model_lift_mm == pytest.approx(20.0)
    assert document.extra_models[0]['lift_mm'] == pytest.approx(20.0)


def test_setup_lift_commits_the_plate_floor_immediately(application, tmp_path):
    window = MainWindow(small_settings(), None, headless=True)
    window.reload = lambda: None
    window.document.set_orientation([0.0, 0.0, 0.0], [0.0, 0.0], 5.0)
    window.document.add_extra_model({'path': tmp_path / 'extra.stl', 'lift_mm': 12.0})
    window._sync_widgets_from_document()
    assert window.lift.value() == pytest.approx(5.0)
    window.lift.setValue(20)
    assert window.document.model_lift_mm == pytest.approx(20.0)
    assert window.document.extra_models[0]['lift_mm'] == pytest.approx(27.0)
    assert window.lift.value() == pytest.approx(20.0)
    window.apply_settings()
    assert window.document.model_lift_mm == pytest.approx(20.0)
    assert window.document.extra_models[0]['lift_mm'] == pytest.approx(27.0)
    window.close()


def test_apply_settings_does_not_flatten_extra_part_lifts(application, tmp_path):
    window = MainWindow(small_settings(), None, headless=True)
    window.reload = lambda: None
    window.document.set_orientation([0.0, 0.0, 0.0], [0.0, 0.0], 5.0)
    window.document.add_extra_model({'path': tmp_path / 'extra.stl', 'lift_mm': 12.0})
    window._sync_widgets_from_document()
    window.apply_settings()
    assert window.document.model_lift_mm == pytest.approx(5.0)
    assert window.document.extra_models[0]['lift_mm'] == pytest.approx(12.0)
    window.close()


def test_allow_part_to_part_checkbox_toggles_the_setting(application):
    window = MainWindow(small_settings(), None, headless=True)
    assert window.document.settings['support']['allow_part_to_part'] is False
    window.actions_map['allow_part_to_part'].setChecked(False)
    assert window.document.settings['support']['allow_part_to_part'] is False
    window.actions_map['allow_part_to_part'].setChecked(True)
    assert window.document.settings['support']['allow_part_to_part'] is True
    window.close()


# Same child-process reasoning as RENDER_CHILD: a real X server, because VTK
# opens a genuine X window. This one exercises capture_window, whose whole
# point is that Qt's own grab cannot see inside that native child.
CAPTURE_CHILD = """
import json, sys
import numpy as np
from PySide6 import QtWidgets, QtCore
import manifold3d as m
from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles
from voxelmill.gui.window import MainWindow, capture_window

target = sys.argv[1]
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
settings = resolve_settings(overrides={
    'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1],
                'build_mm': [100., 80., 165.]},
    'resources': {'workers': 2}})
window = MainWindow(settings, headless=False)
window.show()
window.viewport.start()
window.scene.set_mesh('model', manifold_triangles(m.Manifold.sphere(12, 64)))
window.viewport.add_navigation_cube()
window.viewport.reset_camera()
for _ in range(8):
    app.processEvents()
window.viewport.render()

path = capture_window(window, target)
interactor = window.viewport.interactor
origin = interactor.mapTo(window, QtCore.QPoint(0, 0))
print(json.dumps({'path': str(path),
                  'viewport': [origin.x(), origin.y(),
                               interactor.width(), interactor.height()],
                  'window': [window.width(), window.height()]}))
window.close()
"""


@pytest.mark.gui
def test_screenshot_captures_the_3d_view_not_a_blank_hole(tmp_path):
    """``--screenshot`` has to show the render window, or it is not evidence.

    ``QWidget.grab`` renders the Qt tree only, so the native VTK child comes
    out blank; ``QScreen.grabWindow`` would catch both but needs macOS Screen
    Recording permission, which an SSH session cannot be granted. So the
    capture composites the VTK framebuffer in, and the thing worth asserting
    is that the viewport rectangle of the PNG is not empty.
    """
    if shutil.which('xvfb-run') is None:
        pytest.skip('a real X server (xvfb-run) is needed to open a VTK window')
    script = tmp_path / 'capture_child.py'
    script.write_text(CAPTURE_CHILD)
    shot = tmp_path / 'editor.png'
    finished = subprocess.run(
        ['xvfb-run', '-a', '--server-args=-screen 0 1280x1024x24',
         sys.executable, str(script), str(shot)],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, 'QT_QPA_PLATFORM': 'xcb',
             'VOXELMILL_NO_WIZARD': '1', 'XDG_CACHE_HOME': str(tmp_path / 'cache')})
    assert finished.returncode == 0, (
        f'capture child died: stdout={finished.stdout[-2000:]} stderr={finished.stderr[-2000:]}')
    report = json.loads(finished.stdout.strip().splitlines()[-1])
    assert shot.stat().st_size > 1000

    from PySide6 import QtGui
    image = QtGui.QImage(str(shot))
    assert not image.isNull()
    left, top, width, height = report['viewport']
    assert width > 100 and height > 100
    # Sample a grid inside the viewport rectangle. A blank hole is one flat
    # colour; a rendered scene is not.
    seen = set()
    for row in range(top + 10, top + height - 10, max(1, height // 12)):
        for column in range(left + 10, left + width - 10, max(1, width // 12)):
            if 0 <= row < image.height() and 0 <= column < image.width():
                seen.add(image.pixel(column, row))
    assert len(seen) > 3, f'the viewport area is flat: {seen}'
