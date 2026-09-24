import json
import time

import pytest

pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')
from PySide6 import QtCore, QtWidgets
from voxelmill.cli import build_parser, _settings, main
from voxelmill.contracts import VoxelMillError
from voxelmill.gui.document import Document
from voxelmill.gui.operations import OperationDialog, form_actions
from voxelmill.gui.window import MainWindow


@pytest.fixture(scope='session')
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_operation_form_exposes_all_arguments(app):
    dialog = OperationDialog(Document())
    for name, parser in dialog.parsers.items():
        dialog.operation.setCurrentText(name)
        assert set(dialog.fields) == {a.dest for a in form_actions(parser)}
        for action in form_actions(parser):
            if action.nargs == 0:
                widget = dialog.fields[action.dest]
                available = {widget.itemData(i) for i in range(widget.count())}
                assert set(action.option_strings) <= available
    dialog.close()


def test_operation_arguments_keep_editor_pose_and_explicit_overrides(app, tmp_path):
    document = Document(source=tmp_path / 'part with spaces.stl')
    document.set_orientation([10, 20, 30], [2, 3], 0)
    document.add_contact([1, 2, 3])
    document.removed_contacts = [[4, 5, 6]]
    dialog = OperationDialog(document)
    dialog.fields['support_spacing_mm'].setText('7')
    dialog.fields['max_passes'].setText('3')
    dialog.fields['components'].setCurrentIndex(1)
    dialog.fields['project'].setText(str(tmp_path / 'part project.voxmil'))
    args = build_parser().parse_args(dialog.arguments())
    assert args.input == str(document.source)
    assert args.rotate == ['10.0', '20.0', '30.0']
    assert args.model_lift_mm == 0
    assert args.max_passes == 3 and args.components
    assert _settings(args)['support']['spacing_mm'] == 7
    assert json.loads(open(args.contacts).read()) == document.manual_contacts
    assert json.loads(open(args.removed_contacts).read()) == document.removed_contacts
    dialog.fields['support_preset'].setText('heavy')
    assert _settings(build_parser().parse_args(dialog.arguments()))['support']['pillar_diameter_mm'] == 1.6
    dialog.close()


def test_operation_profiles_override_editor_settings_and_fields_validate(app):
    dialog = OperationDialog(Document(), command='verify')
    with pytest.raises(VoxelMillError, match='required'):
        dialog.arguments()
    dialog.fields['input'].setText('file.goo')
    dialog.fields['printer_profile'].setText('machine.ptr')
    dialog.fields['full_panel'].setCurrentIndex(1)
    args = build_parser().parse_args(dialog.arguments())
    assert args.printer_profile == 'machine.ptr' and args.full_panel
    assert not args.set
    dialog.close()


def test_operation_runs_cli_in_child_and_displays_report(app):
    dialog = OperationDialog(Document(), command='profile')
    dialog.fields['support_spacing_mm'].setText('4.25')
    dialog.start()
    deadline = time.monotonic() + 20
    while dialog.last_exit_code is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert dialog.last_exit_code == 0, dialog.status.text() + dialog.output.toPlainText()
    report = json.loads(dialog.output.toPlainText())
    assert report['settings']['support']['spacing_mm'] == 4.25
    assert dialog.process.state() == QtCore.QProcess.NotRunning
    dialog.close()


def test_cli_and_editor_apply_identical_portable_presets(app, tmp_path, capsys):
    path = tmp_path / 'custom.json'
    assert main(['preset', 'save', '--output', str(path), '--name', 'custom',
                 '--support-preset', 'heavy', '--support-spacing-mm', '2.5']) == 0
    capsys.readouterr()
    assert main(['profile', '--support-preset', str(path)]) == 0
    expected = json.loads(capsys.readouterr().out)['settings']
    window = MainWindow(headless=True)
    window.apply_support_preset(str(path))
    assert window.document.settings == expected
    window.undo()
    assert window.document.settings['support']['spacing_mm'] == 3
    window.close()


def test_cached_layer_selection_rejects_late_uncached_result(app):
    import numpy as np
    from voxelmill.raster import RasterGrid
    window = MainWindow(headless=True)
    def payload(index):
        return {'index': index, 'grid': RasterGrid(2, 2, 0, 0, 1, 1),
                'mask': np.ones((2, 2), dtype=np.uint8), 'filled_pixels': 4,
                'z_mm': index * .05, 'open_rows': 0}
    cached = payload(0)
    window.document.derived.layer_cache.put(('union', 0), cached)
    window.request_layer(0)
    assert window.layers.info.text().startswith('layer 0 ')
    window._finish_layer(payload(10))
    assert window.layers.info.text().startswith('layer 0 ')
    window.close()


def test_editor_export_cannot_replace_source(app, tmp_path):
    source = tmp_path / 'original.stl'
    source.write_bytes(b'original')
    window = MainWindow(headless=True)
    window.document.source = source
    window.export(source)
    assert window.last_error['code'] == 'source_overwrite'
    assert source.read_bytes() == b'original'
    assert window._pending_export is None
    window.close()
