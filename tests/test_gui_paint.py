import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError
from voxelmill.gui.document import Document
from voxelmill.gui.window import MainWindow
from voxelmill.paint import normalize_paint


def test_paint_undo_persist_and_load(tmp_path):
    """Paint is one record per plate object, in that object's own frame."""
    document = Document(resolve_settings())
    document.paint_marks('blocked', [[1.0, 2.0, 3.0]])
    document.paint_marks('enforced', [[4.0, 5.0, 6.0]])
    assert document.paint[0]['blocked'] == [[1.0, 2.0, 3.0]]
    document.undo()
    assert document.paint[0]['enforced'] == []
    document.redo()
    path = tmp_path / 'paint.voxmil'
    document.save(path)
    loaded = Document.load(path)
    assert loaded.paint == [normalize_paint({'blocked': [[1, 2, 3]], 'enforced': [[4, 5, 6]]})]


def test_each_part_owns_its_own_paint(tmp_path):
    source = tmp_path / 'other.stl'
    source.write_bytes(b'fixture')
    document = Document(resolve_settings())
    document.add_extra_model({'path': source})
    document.paint_marks('blocked', [[1.0, 2.0, 3.0]], 0)
    document.paint_marks('blocked', [[7.0, 8.0, 9.0]], 1)
    assert document.paint[0]['blocked'] == [[1.0, 2.0, 3.0]]
    assert document.paint[1]['blocked'] == [[7.0, 8.0, 9.0]]
    # Clearing one part leaves the other alone.
    document.clear_paint('blocked', object_index=1)
    assert document.paint[0]['blocked'] == [[1.0, 2.0, 3.0]]
    assert document.paint[1]['blocked'] == []


def test_painting_an_object_that_is_not_on_the_plate_is_refused():
    document = Document(resolve_settings())
    with pytest.raises(VoxelMillError) as error:
        document.paint_marks('blocked', [[1.0, 2.0, 3.0]], 3)
    assert error.value.code == 'invalid_paint'


def test_adding_a_part_gives_it_an_empty_paint_record(tmp_path):
    source = tmp_path / 'other.stl'
    source.write_bytes(b'fixture')
    document = Document(resolve_settings())
    document.paint_marks('blocked', [[1.0, 2.0, 3.0]])
    document.add_extra_model({'path': source})
    records = document.normalized_paint()
    assert len(records) == 2
    assert records[1] == {'blocked': [], 'enforced': []}


def test_a_plate_coordinate_paint_table_is_refused_rather_than_reinterpreted():
    """A schema-1 mark cannot be attributed to a part after the fact."""
    from voxelmill.paint import normalize_object_paint
    with pytest.raises(VoxelMillError) as error:
        normalize_object_paint({'blocked': [[1.0, 2.0, 3.0]], 'enforced': []})
    assert error.value.code == 'invalid_paint'


def test_paint_mode_widget_and_clear_buttons():
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(resolve_settings(), None, headless=True)
    assert [window.paint_mode.itemText(i) for i in range(window.paint_mode.count())] == [
        'off', 'block', 'enforce']
    window.document.paint_marks('blocked', [[1.0, 2.0, 3.0]])
    window._clear_paint('blocked')
    assert window.document.paint[0]['blocked'] == []
    window.close()


def test_clearing_paint_only_clears_the_selected_part(tmp_path):
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = tmp_path / 'other.stl'
    source.write_bytes(b'fixture')
    window = MainWindow(resolve_settings(), None, headless=True)
    window.reload = lambda: None
    window.document.add_extra_model({'path': source})
    window._sync_widgets_from_document()
    window.document.paint_marks('blocked', [[1.0, 2.0, 3.0]], 0)
    window.document.paint_marks('blocked', [[7.0, 8.0, 9.0]], 1)
    window.object_panel.list.setCurrentRow(1)
    window._clear_paint('blocked')
    assert window.document.paint[0]['blocked'] == [[1.0, 2.0, 3.0]]
    assert window.document.paint[1]['blocked'] == []
    window.close()


def test_add_model_action_exists():
    from PySide6 import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(resolve_settings(), None, headless=True)
    assert 'add_model' in window.actions_map
    window.close()
