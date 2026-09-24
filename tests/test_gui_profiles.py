"""The editor's profile library reaches the same code the CLI does."""
from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip('PySide6')
from PySide6 import QtWidgets

from voxelmill import profiles as library
from voxelmill.config import resolve_settings
from voxelmill.gui.document import Document
from voxelmill.gui.profiles import ProfileLibraryDialog

ROOT = Path(__file__).resolve().parents[1]
PRINTER = ROOT / 'profiles/mars5-ultra.ptr'
RESIN = ROOT / 'profiles/sunlu-abs-like-gray.res'


@pytest.fixture(scope='session')
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    top = tmp_path / 'library'
    top.mkdir()
    monkeypatch.setenv(library.PATH_VARIABLE, str(top))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'xdg'))
    return top


def test_dialog_lists_every_discovered_profile_and_names_shadowed_files(app, isolated):
    (isolated / 'mars5-ultra.ptr').write_bytes(PRINTER.read_bytes())
    dialog = ProfileLibraryDialog(Document())
    rows = {dialog.table.item(r, 1).text(): r for r in range(dialog.table.rowCount())}
    assert {'mars5-ultra', 'sunlu-abs-like-gray'} <= set(rows)
    row = rows['mars5-ultra']
    assert dialog.table.item(row, 3).text() == f'{library.PATH_VARIABLE}[0]'
    assert 'mars5-ultra.ptr' in dialog.table.item(row, 5).text()
    assert 'Search path' in dialog.search_path.text()
    dialog.close()


def test_applying_a_profile_pair_resolves_exactly_as_the_cli_does(app, isolated):
    document = Document()
    dialog = ProfileLibraryDialog(document)
    dialog.printer.setCurrentIndex(dialog.printer.findData(str(
        library.resolve_profile_path('mars5-ultra', 'printer'))))
    dialog.resin.setCurrentIndex(dialog.resin.findData(str(
        library.resolve_profile_path('sunlu-abs-like-gray', 'resin'))))
    applied = dialog.apply_selection()
    assert applied == resolve_settings(PRINTER, RESIN, None)
    assert document.settings == applied
    assert dialog.applied is applied
    dialog.close()


def test_diff_and_provenance_use_the_shared_library_functions(app, isolated):
    document = Document()
    document.set_settings(resolve_settings(PRINTER, RESIN, {'support': {'spacing_mm': 4.5}}))
    dialog = ProfileLibraryDialog(document)
    dialog.printer.setCurrentIndex(dialog.printer.findData(str(
        library.resolve_profile_path('mars5-ultra', 'printer'))))
    differences = dialog.show_diff()
    assert differences['support']['spacing_mm'] == {'left': 4.5, 'right': 3.0}
    dialog.show_provenance()
    assert '"provenance"' in dialog.output.toPlainText()
    dialog.close()


def test_saving_and_binding_from_the_dialog_write_the_same_files_the_cli_writes(
        app, isolated, tmp_path):
    document = Document()
    document.set_settings(resolve_settings(PRINTER, RESIN, {'support': {'spacing_mm': 4.5}}))
    dialog = ProfileLibraryDialog(document)
    saved = dialog.save_printer(str(tmp_path / 'from-editor.ptr'))
    assert saved['round_trip'] == 'identical'
    assert resolve_settings(tmp_path / 'from-editor.ptr', None, None)['support']['spacing_mm'] == 4.5

    dialog.resin.setCurrentIndex(dialog.resin.findData(str(
        library.resolve_profile_path('sunlu-abs-like-gray', 'resin'))))
    bound = dialog.bind_resin(output=str(tmp_path / 'from-editor.res'), target='saturn4-ultra')
    assert bound['bound_printers'] == ['mars5-ultra', 'saturn4-ultra']
    dialog.close()


def test_a_failed_action_reports_the_structured_error_instead_of_raising(app, isolated,
                                                                        tmp_path):
    dialog = ProfileLibraryDialog(Document())
    dialog.resin.setCurrentIndex(0)
    dialog.table.clearSelection()
    assert dialog.bind_resin(output=str(tmp_path / 'x.res'), target='other') is None
    assert 'Select a resin profile' in dialog.output.toPlainText()
    dialog.close()


@pytest.mark.gui
def test_the_file_menu_opens_the_library_and_applies_its_result(app, monkeypatch, isolated):
    from voxelmill.gui.window import MainWindow
    window = MainWindow(resolve_settings(PRINTER, RESIN, None), None, headless=True)
    assert 'profile_library' in window.actions_map

    applied = {}

    def fake_exec(self):
        self.printer.setCurrentIndex(self.printer.findData(str(
            library.resolve_profile_path('mars5-ultra', 'printer'))))
        applied['settings'] = self.apply_selection()
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(ProfileLibraryDialog, 'exec', fake_exec)
    dialog = window.profile_library_dialog()
    assert dialog.applied is applied['settings']
    assert window.document.settings == applied['settings']
    window.close()


def test_binding_a_multi_printer_resin_asks_which_process_to_copy(app, isolated,
                                                                  tmp_path, monkeypatch):
    """The shared function refuses an ambiguous source, so the dialog must ask.

    Without this the button fails with the CLI's ``--from is required`` message
    and offers no way to supply it.
    """
    # Put it in the library so the dialog offers it; it shadows the built-in
    # copy of the same identifier, which is the point of the search path.
    two = isolated / 'sunlu-abs-like-gray.res'
    library.bind_resin_process(RESIN, two, target_printer='saturn4-ultra')
    asked = {}

    def choose(_parent, _title, _label, items, _current, _editable):
        asked['items'] = list(items)
        return items[1], True

    monkeypatch.setattr(QtWidgets.QInputDialog, 'getItem', staticmethod(choose))
    dialog = ProfileLibraryDialog(Document())
    dialog.resin.setCurrentIndex(dialog.resin.findData(str(two)))
    payload = dialog.bind_resin(output=str(tmp_path / 'three.res'), target='mars5-clone')
    assert asked['items'] == ['mars5-ultra', 'saturn4-ultra']
    assert payload['source_printer'] == 'saturn4-ultra'
    assert payload['bound_printers'] == ['mars5-clone', 'mars5-ultra', 'saturn4-ultra']
    dialog.close()


# ---- A7: dirty-state save/discard -----------------------------------------

def _dirtied(document=None):
    """A document with one undoable edit, so ``.dirty`` is true."""
    document = document or Document()
    document.set_settings(resolve_settings(PRINTER, RESIN, {'support': {'spacing_mm': 4.5}}))
    return document


def test_a_clean_document_shows_no_dirty_indicator_and_never_builds_a_prompt(app, isolated,
                                                                            monkeypatch):
    class Refuse:
        def __init__(self, *args, **kwargs):
            raise AssertionError('a clean document must not build a confirm modal')

    dialog = ProfileLibraryDialog(Document())
    assert not dialog.isWindowModified()
    assert dialog.dirty_label.isHidden()
    monkeypatch.setattr(QtWidgets, 'QMessageBox', Refuse)
    dialog.printer.setCurrentIndex(dialog.printer.findData(str(
        library.resolve_profile_path('mars5-ultra', 'printer'))))
    applied = dialog.apply_selection()
    assert applied is not None
    dialog.close()


def test_dialog_shows_a_dirty_indicator_once_the_document_has_unsaved_edits(app, isolated):
    dialog = ProfileLibraryDialog(_dirtied())
    assert dialog.isWindowModified()
    assert not dialog.dirty_label.isHidden()
    dialog.close()


def test_apply_on_a_dirty_document_with_no_save_path_offers_apply_or_cancel(app, isolated,
                                                                            monkeypatch):
    """The dialog has no ``parent`` here, so it has no ``save_project`` to call.

    Save/Discard/Cancel would be misleading with nothing to save to, so the
    prompt narrows to Apply (discard the edits) / Cancel instead.
    """
    document = _dirtied()
    before = deepcopy(document.settings)
    dialog = ProfileLibraryDialog(document)
    dialog.printer.setCurrentIndex(dialog.printer.findData(str(
        library.resolve_profile_path('mars5-ultra', 'printer'))))

    seen = {}

    def fake_exec(self):
        seen['buttons'] = self.standardButtons()
        return QtWidgets.QMessageBox.Cancel

    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', fake_exec)
    assert dialog.apply_selection() is None
    assert document.settings == before
    assert dialog.applied is None
    assert not (seen['buttons'] & QtWidgets.QMessageBox.Save)

    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', lambda self: QtWidgets.QMessageBox.Apply)
    applied = dialog.apply_selection()
    assert applied is not None
    assert document.settings == applied
    dialog.close()


def test_apply_on_a_dirty_document_with_a_save_path_offers_save_discard_cancel(app, isolated,
                                                                              monkeypatch):
    class FakeMainWindow(QtWidgets.QWidget):
        def __init__(self):
            super().__init__()
            self.save_calls = 0

        def save_project(self):
            self.save_calls += 1
            return True

    parent = FakeMainWindow()
    document = _dirtied()
    before = deepcopy(document.settings)
    dialog = ProfileLibraryDialog(document, parent)
    dialog.printer.setCurrentIndex(dialog.printer.findData(str(
        library.resolve_profile_path('mars5-ultra', 'printer'))))

    # Cancel: nothing saved, nothing applied.
    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', lambda self: QtWidgets.QMessageBox.Cancel)
    assert dialog.apply_selection() is None
    assert parent.save_calls == 0
    assert document.settings == before

    # Save: goes through the parent's save_project before applying.
    monkeypatch.setattr(QtWidgets.QMessageBox, 'exec', lambda self: QtWidgets.QMessageBox.Save)
    applied = dialog.apply_selection()
    assert parent.save_calls == 1
    assert applied is not None
    assert document.settings == applied
    dialog.close()
    parent.deleteLater()


def test_save_printer_and_bind_resin_ask_before_overwriting_an_existing_file(app, isolated,
                                                                            tmp_path, monkeypatch):
    document = Document()
    document.set_settings(resolve_settings(PRINTER, RESIN, None))
    dialog = ProfileLibraryDialog(document)

    existing = tmp_path / 'existing.ptr'
    existing.write_text('not a profile yet')
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.No))
    assert dialog.save_printer(str(existing)) is None
    assert existing.read_text() == 'not a profile yet'

    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    saved = dialog.save_printer(str(existing))
    assert saved['round_trip'] == 'identical'
    assert existing.read_text() != 'not a profile yet'

    dialog.resin.setCurrentIndex(dialog.resin.findData(str(
        library.resolve_profile_path('sunlu-abs-like-gray', 'resin'))))
    bound_output = tmp_path / 'bound.res'
    bound_output.write_text('placeholder')
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.No))
    assert dialog.bind_resin(output=str(bound_output), target='saturn4-ultra') is None
    assert bound_output.read_text() == 'placeholder'

    monkeypatch.setattr(QtWidgets.QMessageBox, 'question',
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    bound = dialog.bind_resin(output=str(bound_output), target='saturn4-ultra')
    assert bound['bound_printers'] == ['mars5-ultra', 'saturn4-ultra']
    dialog.close()
