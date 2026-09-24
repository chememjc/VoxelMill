"""GUI parity for the independent model-anchor support controls."""
import time


import pytest
pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')
from PySide6 import QtWidgets

from voxelmill.config import (MODEL_ANCHOR_SHAPES, SMALL_PILLAR_MODES,
                              SMALL_PILLAR_SHAPES, resolve_settings)
from voxelmill.gui.document import Document
from voxelmill.gui.editors import ConfigurationEditor, ENUMS


@pytest.fixture(scope='session')
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_preview(dialog, app):
    dialog.refresh_preview()
    deadline = time.monotonic() + 20
    while dialog.example is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert dialog.example is not None, dialog.status.text()
    return dialog.example


def test_model_anchor_enum_and_help_are_exposed(app):
    editor = ConfigurationEditor(Document(), 'support', headless=True)
    field = editor.fields['support', 'model_anchor_shape']
    assert ENUMS[('support', 'model_anchor_shape')] == MODEL_ANCHOR_SHAPES
    assert tuple(field.itemText(i) for i in range(field.count())) == MODEL_ANCHOR_SHAPES
    for key in ('model_anchor_shape', 'model_anchor_length_mm',
                'model_anchor_diameter_mm', 'model_anchor_penetration_mm'):
        tip = editor.fields['support', key].toolTip()
        assert f'support.{key}' in tip
        assert tip.split('\n', 1)[1]
    editor.reject()


def test_small_pillar_enums_and_help_are_exposed(app):
    editor = ConfigurationEditor(Document(), 'support', headless=True)
    for key, choices in (('small_pillar_mode', SMALL_PILLAR_MODES),
                         ('small_pillar_shape', SMALL_PILLAR_SHAPES)):
        field = editor.fields['support', key]
        assert tuple(field.itemText(i) for i in range(field.count())) == choices
        assert field.toolTip().split('\n', 1)[1]
    for key in ('small_pillar_upper_depth_mm', 'small_pillar_lower_depth_mm'):
        assert editor.fields['support', key].toolTip().split('\n', 1)[1]
    editor.reject()


def test_anchor_fields_round_trip_through_portable_support_save(app, tmp_path):
    settings = resolve_settings()
    settings['support'].update({
        'model_anchor_shape': 'cylinder',
        'model_anchor_length_mm': 1.25,
        'model_anchor_diameter_mm': .55,
        'model_anchor_penetration_mm': .20,
        'small_pillar_mode': 'model',
        'small_pillar_shape': 'cone',
        'small_pillar_diameter_mm': .4,
        'small_pillar_max_length_mm': 30.0,
        'small_pillar_upper_depth_mm': .25,
        'small_pillar_lower_depth_mm': .25,
    })
    editor = ConfigurationEditor(Document(settings), 'support', headless=True)
    path = tmp_path / 'anchors.json'
    assert editor.save_file(str(path)) is not None
    other = ConfigurationEditor(Document(), 'support', headless=True)
    other.load_file(str(path))
    assert other.settings()['support'] == settings['support']
    editor.reject()
    other.reject()


def test_anchor_settings_change_the_headless_example_geometry(app):
    settings = resolve_settings(overrides={'support': {
        'part_to_part_avoidance': 0.0,
        'allow_part_to_part': True,
        'model_anchor_length_mm': 1.0,
        'model_anchor_diameter_mm': .55,
        'model_anchor_penetration_mm': .2,
    }})
    editor = ConfigurationEditor(Document(settings), 'support', headless=True)
    first = wait_preview(editor, app)
    assert first['metrics']['routing']['model_anchor'] > 0
    first_triangles = first['triangles']['supports'].copy()

    editor.fields['support', 'model_anchor_shape'].setCurrentText('cylinder')
    second = wait_preview(editor, app)
    assert second['metrics']['routing']['model_anchor'] == first['metrics']['routing']['model_anchor']
    assert not (second['triangles']['supports'].shape == first_triangles.shape and
                (second['triangles']['supports'] == first_triangles).all())
    editor.reject()


def test_small_model_pillar_settings_reach_the_preview(app):
    settings = resolve_settings(overrides={'support': {
        'part_to_part_avoidance': 0.0,
        'allow_part_to_part': True,
        'small_pillar_mode': 'model',
        'small_pillar_shape': 'cone',
        'small_pillar_diameter_mm': .4,
        'small_pillar_max_length_mm': 30.0,
        'small_pillar_upper_depth_mm': .25,
        'small_pillar_lower_depth_mm': .25,
    }})
    editor = ConfigurationEditor(Document(settings), 'support', headless=True)
    example = wait_preview(editor, app)
    assert example['metrics']['routing']['model_anchor'] > 0
    assert example['metrics']['small_pillar']['mode'] == 'model'
    assert example['metrics']['small_pillar']['shape'] == 'cone'
    assert example['metrics']['small_pillar']['upper_depth_mm'] == pytest.approx(.25)
    editor.reject()
