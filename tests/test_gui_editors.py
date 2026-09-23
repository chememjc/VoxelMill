"""Dedicated editors use the production settings, routing, and file formats."""
import json
import os
import time

import numpy as np
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')
from PySide6 import QtWidgets
from voxelmill.config import resolve_settings
from voxelmill.cli import main
from voxelmill.gui.document import Document
from voxelmill.gui.editors import ConfigurationEditor, load_component, SECTIONS
from voxelmill.support_example import support_example


@pytest.fixture(scope='session')
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_example(dialog, app):
    dialog.refresh_preview()
    deadline = time.monotonic() + 15
    while dialog.example is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert dialog.example is not None, dialog.status.text()
    return dialog.example


@pytest.mark.parametrize('kind', ['printer', 'resin', 'support'])
def test_editors_expose_every_owned_key_and_apply_undoably(kind, app):
    document = Document()
    before = document.settings.copy()
    dialog = ConfigurationEditor(document, kind, headless=True)
    expected = {(section, key) for section in SECTIONS[kind] for key in document.settings[section]}
    assert set(dialog.fields) == expected
    for section, key in expected:
        assert f'{section}.{key}' in dialog.fields[section, key].toolTip()
    target = {'printer': ('printer', 'name'), 'resin': ('resin', 'name'),
              'support': ('support', 'spacing_mm')}[kind]
    dialog.fields[target].setText('4' if kind == 'support' else 'Custom')
    assert document.settings == before
    assert dialog.apply() is not None
    assert document.settings != before
    document.undo()
    assert document.settings == before
    dialog.reject()


def test_invalid_edits_cannot_apply_or_replace_saved_files(app, tmp_path):
    document = Document()
    dialog = ConfigurationEditor(document, 'support', headless=True)
    original = document.settings.copy()
    output = tmp_path / 'existing.json'
    output.write_text('keep me')
    dialog.fields['support', 'part_to_part_avoidance'].setText('2')
    assert dialog.apply() is None
    assert dialog.save_file(str(output)) is None
    assert output.read_text() == 'keep me'
    assert document.settings == original
    dialog.reject()


@pytest.mark.parametrize('kind,suffix', [('printer', '.ptr'), ('resin', '.res'), ('support', '.json')])
def test_editor_files_reload_in_cli_and_other_projects(kind, suffix, app, tmp_path, capsys):
    settings = resolve_settings(overrides={'resin': {'density_g_cm3': 1.15},
        'process': {'normal_exposure_s': 4.2},
        'support': {'allow_part_to_part': False, 'brace_diameter_mm': .8,
                    'brace_spacing_mm': 17.0, 'brace_max_length_mm': 28.0}})
    dialog = ConfigurationEditor(Document(settings), kind, headless=True)
    output = tmp_path / ('saved' + suffix)
    assert dialog.save_file(str(output)) is not None, dialog.status.text()
    flag = {'printer': '--printer', 'resin': '--resin', 'support': '--support-preset'}[kind]
    assert main(['profile', flag, str(output)]) == 0
    resolved = json.loads(capsys.readouterr().out)['settings']
    loaded = load_component(kind, str(output), resolve_settings())
    sections = SECTIONS[kind] + (('support',) if kind == 'resin' else ())
    for section in sections:
        assert loaded[section] == resolved[section] == settings[section]
    other = ConfigurationEditor(Document(), kind, headless=True)
    other.load_file(str(output))
    for section in sections:
        assert other.settings()[section] == settings[section]
    other.reject()
    dialog.reject()


def test_example_matches_cli_geometry_and_changes_when_tip_changes(app, tmp_path):
    dialog = ConfigurationEditor(Document(), 'support', headless=True)
    first = wait_example(dialog, app)
    direct = support_example(dialog.settings(), dialog.height.value())
    assert first['metrics']['routing'] == direct['metrics']['routing']
    np.testing.assert_array_equal(first['triangles']['supports'], direct['triangles']['supports'])
    dialog.fields['support', 'contact_diameter_mm'].setText('0.8')
    assert dialog.example is None  # obsolete geometry cannot be exported as current
    second = wait_example(dialog, app)
    assert not np.array_equal(first['triangles']['supports'], second['triangles']['supports'])
    assert 'supports' in dialog.scene.actors
    output = tmp_path / 'example.stl'
    assert dialog.export_example(str(output)) == str(output)
    assert output.stat().st_size > 84
    dialog.reject()


def test_invalid_example_dimensions_leave_valid_project_settings_editable(app):
    dialog = ConfigurationEditor(Document(), 'support', headless=True)
    dialog.fields['support', 'spacing_mm'].setText('50')
    dialog.refresh_preview()
    deadline = time.monotonic() + 15
    while dialog.jobs.tokens and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert dialog.example is None
    assert '1 to 30' in dialog.status.text()
    assert dialog.apply()['support']['spacing_mm'] == 50
    dialog.reject()


def test_menu_applies_editor_settings_and_refreshes_controls(app, monkeypatch):
    from voxelmill.gui.window import MainWindow
    window = MainWindow(headless=True)
    assert {'printer_editor', 'resin_editor', 'support_editor'} <= set(window.actions_map)
    def edit(self):
        self.fields['support', 'spacing_mm'].setText('4.25')
        self.apply()
        self.reject()
    monkeypatch.setattr(ConfigurationEditor, 'exec', edit)
    window.support_editor_dialog()
    assert window.document.settings['support']['spacing_mm'] == 4.25
    window.close()


RENDER_EDITOR = r'''
import json, time
import numpy as np
from PySide6 import QtWidgets
import vtkmodules.all as vtk
from vtkmodules.util import numpy_support
from voxelmill.gui.document import Document
from voxelmill.gui.editors import ConfigurationEditor
app = QtWidgets.QApplication([])
dialog = ConfigurationEditor(Document(), 'support')
dialog.show()
def frame():
    dialog.refresh_preview()
    deadline = time.monotonic() + 20
    while dialog.example is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert dialog.example is not None, dialog.status.text()
    for _ in range(5):
        app.processEvents()
    dialog.viewport.render()
    shot = vtk.vtkWindowToImageFilter()
    shot.SetInput(dialog.viewport.interactor.GetRenderWindow())
    shot.Update()
    return numpy_support.vtk_to_numpy(shot.GetOutput().GetPointData().GetScalars()).copy()
first = frame()
dialog.fields['support', 'pillar_diameter_mm'].setText('2.0')
second = frame()
lit = int((np.abs(second[:, :3].astype(int) - [31,33,38]).sum(axis=1) > 30).sum())
changed = int((first != second).any(axis=1).sum())
assert lit > 1000, lit
assert changed > 100, changed
# Exercise the visible controls and both requested illustrative cases in VTK.
for key, value in [('brace_destination', 'supports'), ('brace_pattern', 'x')]:
    field = dialog.fields['support', key]
    field.setCurrentIndex(field.findData(value))
dialog.fields['support', 'brace_branches_per_node'].setText('3')
dialog.fields['support', 'brace_spacing_mm'].setText('8')
frame()
assert dialog.example['metrics']['brace_pattern'] == 'x'
assert dialog.example['metrics']['braces'] > 0

def save_frame(name):
    from pathlib import Path
    image = vtk.vtkWindowToImageFilter()
    image.SetInput(dialog.viewport.interactor.GetRenderWindow())
    image.Update()
    writer = vtk.vtkPNGWriter()
    writer.SetFileName(str(Path(__file__).with_name(name)))
    writer.SetInputConnection(image.GetOutputPort())
    writer.Write()

save_frame('x-bracing.png')
dialog.fields['support', 'pillar_diameter_mm'].setText('1.2')
dialog.fields['support', 'auto_bracing'].setChecked(False)
dialog.fields['support', 'tree_supports'].setChecked(True)
frame()
assert dialog.example['metrics']['tree']['trunks'] > 0
save_frame('tree-junctions.png')
dialog.model_anchor_demo.click()
frame()
assert dialog.example['metrics']['routing']['model_anchor'] == 4
save_frame('part-to-part.png')
dialog.reject()
printer = ConfigurationEditor(Document(), 'printer')
printer.show()
printer.refresh_preview()
for _ in range(5):
    app.processEvents()
assert printer.scene._plate
printer.reject()
print(json.dumps({'surface_pixels': lit, 'changed_pixels': changed}))
'''


@pytest.mark.gui
def test_support_editor_renders_parameter_changes_in_real_vtk(tmp_path):
    import shutil
    import subprocess
    import sys
    if not shutil.which('xvfb-run'):
        pytest.skip('xvfb-run required for real VTK rendering')
    script = tmp_path / 'render_support_editor.py'
    script.write_text(RENDER_EDITOR)
    result = subprocess.run(['xvfb-run', '-a', sys.executable, str(script)],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'xcb', 'XDG_CACHE_HOME': str(tmp_path / 'cache')},
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence['surface_pixels'] > 1000
    assert evidence['changed_pixels'] > 100


def test_apply_invalidates_parent_jobs_before_the_dialog_closes(app, monkeypatch):
    from voxelmill.gui.window import MainWindow
    window = MainWindow(headless=True)
    generation = window.jobs.generation
    def edit(self):
        self.fields['support', 'spacing_mm'].setText('4.25')
        self.apply()
        # Still inside the modal exec call; waiting for it to return is too late.
        assert window.jobs.generation > generation
        self.reject()
    monkeypatch.setattr(ConfigurationEditor, 'exec', edit)
    window.support_editor_dialog()
    window.close()


def test_bracing_tab_and_model_gap_round_trip(app, tmp_path):
    document = Document()
    dialog = ConfigurationEditor(document, 'support', headless=True)
    assert dialog.tabs.tabText(0) == 'Bracing'
    for key, value in {'brace_spacing_mm': '8', 'brace_max_distance_mm': '12',
                       'brace_branches_per_node': '3', 'brace_angle_deg': '60',
                       'brace_min_height_mm': '4', 'brace_azimuth_deg': '30'}.items():
        dialog.fields['support', key].setText(value)
    for key, value in [('brace_destination', 'supports'), ('brace_pattern', 'x')]:
        field = dialog.fields['support', key]
        field.setCurrentIndex(field.findData(value))
    path = tmp_path / 'bracing.json'
    dialog.save_file(str(path))
    restored = load_component('support', path, resolve_settings())
    assert restored['support'] == dialog.settings()['support']
    dialog.model_anchor_demo.click()
    example = wait_example(dialog, app)
    assert example['layout'] == 'part-to-part'
    assert example['metrics']['routing']['model_anchor'] == 4
    assert not document.settings['support']['allow_part_to_part']
    dialog.apply()
    assert document.settings['support']['allow_part_to_part']
    document.undo()
    assert not document.settings['support']['allow_part_to_part']
    dialog.reject()


def test_support_tabs_are_grouped_and_free_of_qt_mnemonics(app):
    """Thin pillars are their own tab, and no title hides a letter.

    In middle mode the `small_pillar_*` keys apply to every pillar, not only
    to part-to-part routes, so filing them under the anchor tab misdescribed
    them. And Qt reads '&' in a tab title as a mnemonic marker, which turned
    "tips & bases" into "tips _bases" on screen.
    """
    dialog = ConfigurationEditor(Document(), 'support', headless=True)
    titles = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
    assert titles == ['Bracing', 'Pillars, tips and bases',
                      'Part-to-part anchors', 'Thin pillars']
    for title in titles:
        assert '&' not in title, title
    anchors = titles.index('Part-to-part anchors')
    thin = titles.index('Thin pillars')

    def keys_on(index):
        page = dialog.tabs.widget(index).widget()
        found = set()
        for (_section, key), field in dialog.fields.items():
            if field.parentWidget() is page:
                found.add(key)
        return found

    assert {'allow_part_to_part', 'part_to_part_avoidance'} <= keys_on(anchors)
    assert all(key.startswith('model_anchor_') or key.startswith('part_to_part_')
               or key == 'allow_part_to_part' for key in keys_on(anchors))
    assert all(key.startswith('small_pillar_') for key in keys_on(thin))
    assert 'small_pillar_diameter_mm' in keys_on(thin)
    dialog.reject()
