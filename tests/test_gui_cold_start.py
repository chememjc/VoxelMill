"""Fresh-process coverage: pre-importing native modules hides Qt worker bugs."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')

CHILD = r'''
import faulthandler, json, sys, time
faulthandler.enable()
from PySide6 import QtWidgets
from voxelmill.config import resolve_settings
from voxelmill.gui.window import MainWindow
# No _native/manifold3d import on the main thread: startup really loads them
# inside the Qt worker, whose Python thread state expires between jobs.
assert 'voxelmill._native' not in sys.modules
app = QtWidgets.QApplication([])
settings = resolve_settings()
original = len(sys.argv) > 3 and sys.argv[3] == 'original'
if original:
    # Exercise every original triangle in the viewport and native layer path,
    # without claiming an expensive corrected/support-routed print acceptance.
    settings['repair']['aggressiveness'] = 'none'
    settings['support']['automatic'] = False
window = MainWindow(settings, sys.argv[1], headless=sys.argv[2] == 'headless')
window.show()
if window.viewport:
    window.viewport.start()
def settle(predicate):
    deadline = time.monotonic() + (300 if original else 120)
    while not predicate():
        app.processEvents()
        if window.last_error:
            raise AssertionError(window.last_error)
        if time.monotonic() > deadline:
            raise AssertionError('job timed out: ' + window.statusBar().currentMessage())
        time.sleep(.005)
settle(lambda: window.document.derived.union is not None)
if not original:
    # Nothing is attached on import, so the plate layer is empty until the
    # attachments that reach it have been routed.
    window.compute_attachments()
    settle(lambda: window.document.derived.union is not None
           and window.document.derived.plan is not None
           and window.document.derived.plan.metrics['contacts_routed'] > 0)
window.tabs.setCurrentIndex(window.layers_tab_index)
settle(lambda: window.layers._image is not None)
for index in [min(100, window.layers.slider.maximum()), window.layers.slider.maximum(), 0]:
    window.layers.slider.setValue(index)
    settle(lambda: window.layers.info.text().startswith(f'layer {index} '))
assert not window.layers._image.isNull()
if not original:
    assert window.document.derived.layer_cache.get(('union', 0))['filled_pixels'] > 0
else:
    from voxelmill.mesh import open_stl
    with open_stl(sys.argv[1]) as source:
        assert len(window.placed) == source.asset.triangle_count
    assert window.document.derived.union.num_tri() == len(window.placed)
if window.viewport:
    import vtkmodules.all as vtk
    from vtkmodules.util import numpy_support
    import numpy as np
    window.viewport.render()
    shot = vtk.vtkWindowToImageFilter()
    shot.SetInput(window.viewport.interactor.GetRenderWindow())
    shot.Update()
    pixels = numpy_support.vtk_to_numpy(shot.GetOutput().GetPointData().GetScalars())
    assert np.count_nonzero(np.abs(pixels[:, :3].astype(int) - [31, 33, 38]).sum(axis=1) > 30) > 1000
print(json.dumps({'layer': window.layers.info.text(), 'triangles': window.document.derived.union.num_tri()}))
window.jobs.wait(5000)
window.close()
'''


@pytest.mark.gui
@pytest.mark.parametrize('render', [False, True])
def test_cold_editor_loads_supports_then_scrubs_layers(tmp_path, render):
    if render and not shutil.which('xvfb-run'):
        pytest.skip('xvfb-run needed for real VTK rendering')
    # Construct the fixture in the parent, never warm the child's native import.
    import manifold3d as m
    from voxelmill.geometry import manifold_triangles
    from voxelmill.mesh import write_stl
    source = tmp_path / 'cube.stl'
    write_stl(source, manifold_triangles(m.Manifold.cube((8, 6, 4))))
    run_child(tmp_path, source, render)


@pytest.mark.gui
@pytest.mark.samples
@pytest.mark.skipif(os.environ.get('VOXELMILL_SAMPLES') != '1', reason='opt-in original STL')
def test_latch_preview_to_layers(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'inputstl/Latch_fat_finger.stl'
    if not source.exists() or not shutil.which('xvfb-run'):
        pytest.skip('latch sample and xvfb-run required')
    run_child(tmp_path, source, True)


@pytest.mark.gui
@pytest.mark.samples
@pytest.mark.skipif(os.environ.get('VOXELMILL_SAMPLES') != '1', reason='opt-in original STL')
@pytest.mark.parametrize('filename', ['right_temporal_bone_mars5_oriented.stl',
                                      'skull_slab_q00_mars5_oriented.stl'])
def test_full_resolution_original_viewport_and_layer_scrubbing(tmp_path, filename):
    source = Path(__file__).resolve().parents[1] / 'inputstl' / filename
    if not source.exists() or not shutil.which('xvfb-run'):
        pytest.skip('original sample and xvfb-run required')
    run_child(tmp_path, source, True, original=True)


def run_child(tmp_path, source, render, original=False):
    script = tmp_path / 'cold_editor.py'
    script.write_text(CHILD)
    command = [sys.executable, str(script), str(source), 'render' if render else 'headless']
    if original:
        command.append('original')
    if render:
        command = ['xvfb-run', '-a', *command]
    result = subprocess.run(command, env={**os.environ, 'QT_QPA_PLATFORM': 'xcb' if render else 'offscreen',
                                         'XDG_CACHE_HOME': str(tmp_path / 'cache')},
                            text=True, capture_output=True, timeout=360 if original else 180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"layer": "layer 0 ' in result.stdout
