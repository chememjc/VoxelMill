import pytest
import numpy as np

from voxelmill.config import resolve_settings
from voxelmill.gui.document import Document
from voxelmill.gui.window import MainWindow


def test_contact_parameters_batch_undo_move_remove_and_clear():
    document = Document(resolve_settings())
    first, second = [1., 2., 3.], [4., 5., 6.]
    document.add_contact(first); document.add_contact(second)
    document.set_contact_parameters([first, second], {'tip_length_mm': 2.5})
    assert all(row['parameters']['tip_length_mm'] == 2.5 for row in document.contact_parameters)
    document.move_contact(first, [7., 8., 9.])
    assert document.contact_parameters[0]['position_mm'] == [7., 8., 9.]
    document.remove_contact([7., 8., 9.])
    assert len(document.contact_parameters) == 1
    document.undo()
    assert len(document.contact_parameters) == 2
    document.clear_contact_parameters([[4., 5., 6.]])
    assert len(document.contact_parameters) == 1
    document.undo()
    assert len(document.contact_parameters) == 2


def test_contact_parameters_persist_in_manifest_and_load(tmp_path):
    document = Document(resolve_settings())
    point = [1., 2., 3.]
    document.add_contact(point)
    document.set_contact_parameters([point], {'tip_length_mm': 2.5})
    path = tmp_path / 'contact.voxmil'
    document.save(path)
    loaded = Document.load(path)
    assert loaded.contact_parameters == document.contact_parameters


def test_contact_widget_batch_only_submits_touched_fields():
    app = __import__('PySide6').QtWidgets.QApplication.instance() or __import__('PySide6').QtWidgets.QApplication([])
    window = MainWindow(resolve_settings(), None, headless=True)
    points = [[1., 2., 3.], [4., 5., 6.]]
    window._set_contact_list(points)
    window.contact_list.item(0).setSelected(True)
    window.contact_list.setCurrentRow(0)
    window.contact_parameter_controls['tip_length_mm'].setValue(2.5)
    assert window._contact_parameter_values() == {'tip_length_mm': 2.5}
    window.close()


def test_contact_widget_accepts_numpy_routing_contacts_and_loads_effective_values():
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(resolve_settings(), None, headless=True)
    window._set_contact_list(np.asarray([[1., 2., 3.], [4., 5., 6.]]))
    window.contact_list.setCurrentRow(0)
    assert window.contact_parameter_controls['tip_length_mm'].value() == 2.0
    window.close()


def test_contact_widget_exposes_every_personal_field():
    from voxelmill.contact_parameters import PERSONAL_FIELDS
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(resolve_settings(), None, headless=True)
    assert set(window.contact_parameter_controls) == PERSONAL_FIELDS
    window.close()


def test_contact_widget_copy_paste_and_reset():
    from voxelmill.contact_parameters import contact_key, parameters_for_contact
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(resolve_settings(), None, headless=True)
    first, second = [1., 2., 3.], [4., 5., 6.]
    window._set_contact_list([first, second])
    window.contact_list.item(0).setSelected(True)
    window.contact_list.setCurrentRow(0)
    window.contact_parameter_controls['tip_shape'].setCurrentText('cylinder')
    window.contact_parameter_controls['break_point_diameter_mm'].setValue(0.6)
    assert window._apply_contact_parameters() is True
    assert parameters_for_contact(window.document.contact_parameters, first)['tip_shape'] == 'cylinder'
    assert window._copy_first_contact_parameters() is True
    window.contact_list.clearSelection()
    window.contact_list.item(1).setSelected(True)
    window.contact_list.setCurrentRow(1)
    assert window._paste_contact_parameters() is True
    pasted = parameters_for_contact(window.document.contact_parameters, second)
    assert pasted['tip_shape'] == 'cylinder'
    assert pasted['break_point_diameter_mm'] == pytest.approx(0.6)
    assert window._reset_contact_parameters() is True
    assert parameters_for_contact(window.document.contact_parameters, second) == {}
    assert contact_key(first) in {
        contact_key(row['position_mm']) for row in window.document.contact_parameters}
    window.close()
