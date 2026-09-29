"""Opening, importing, adding and dropping several files onto one plate (VM-098).

STEP import used to open its tessellated STL as a new document, discarding
the plate it was imported into; Open STL took one file; Add model with
nothing open stored parts in a document with no source, which never drew.
Every route now goes through ``MainWindow._open_or_add``.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.gui

sys.path.insert(0, os.path.dirname(__file__))

import manifold3d as m  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

from voxelmill.geometry import manifold_triangles  # noqa: E402
from voxelmill.gui.window import MainWindow  # noqa: E402
from test_gui import drain, small_settings, write_stl  # noqa: E402

SHAPES = Path(__file__).resolve().parents[1] / 'fixtures' / 'shapes'


@pytest.fixture
def cube(tmp_path):
    path = tmp_path / 'cube.stl'
    write_stl(path, manifold_triangles(m.Manifold.cube((10, 10, 10), True).translate((0, 0, 6))))
    return path


@pytest.fixture
def empty(application):
    editor = MainWindow(small_settings(), None, headless=True)
    yield editor
    editor.jobs.cancel_all()
    editor.jobs.wait(5000)
    editor.close()


@pytest.fixture
def loaded(application, cube):
    editor = MainWindow(small_settings(), cube, headless=True)
    drain(editor, application)
    yield editor
    editor.jobs.cancel_all()
    editor.jobs.wait(5000)
    editor.close()


@pytest.fixture
def fake_step(tmp_path, monkeypatch):
    """A .step file whose 'tessellation' copies a known STL, so FreeCAD is not needed."""
    step = tmp_path / 'part.step'
    step.write_text('ISO-10303-21; not really\n')
    calls = []

    def tessellate(self, path):
        calls.append(str(path))
        out = tmp_path / f'tessellated-{len(calls)}.stl'
        shutil.copyfile(SHAPES / 'cylinder.stl', out)
        return out

    monkeypatch.setattr(MainWindow, '_tessellate_step_to_temp', tessellate)
    return step, calls


def _choose(monkeypatch, paths):
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getOpenFileNames',
                        staticmethod(lambda *a, **k: ([str(p) for p in paths], '')))


def _part_actors(window):
    return sorted(key for key in window.scene.actors if key == 'model' or key.startswith('model:'))


def _offsets(window):
    return [tuple(window.document.center_offset_mm)] + [
        tuple(spec['center_offset']) for spec in window.document.extra_models]


def test_importing_step_onto_a_loaded_plate_adds_a_part(loaded, application, cube, fake_step):
    step, calls = fake_step
    assert loaded.import_step(step) is not None
    assert calls == [str(step)]
    # The plate it was imported into is still there, with its primary.
    assert loaded.document.source == cube
    assert len(loaded.document.extra_models) == 1
    drain(loaded, application)
    assert _part_actors(loaded) == ['model', 'model:1']
    assert len(set(_offsets(loaded))) == 2


def test_the_import_step_dialog_takes_several_files(loaded, application, cube, fake_step,
                                                    monkeypatch):
    step, calls = fake_step
    second = step.with_name('other.stp')
    second.write_text('ISO-10303-21; not really\n')
    _choose(monkeypatch, [step, second])
    assert loaded.import_step_dialog()
    assert len(calls) == 2
    assert loaded.document.source == cube
    assert len(loaded.document.extra_models) == 2


def test_importing_step_into_an_empty_editor_opens_it(empty, application, fake_step):
    step, _calls = fake_step
    assert empty.import_step(step) is not None
    drain(empty, application)
    assert empty.document.source is not None
    assert _part_actors(empty) == ['model']


def test_opening_several_stls_lays_them_all_out_on_one_plate(empty, application, monkeypatch):
    files = [SHAPES / 'cube.stl', SHAPES / 'cylinder.stl', SHAPES / 'cone.stl']
    _choose(monkeypatch, files)
    assert empty.open_stl_dialog()
    # drain raises on any job error, which is how models_intersect would show.
    drain(empty, application)
    assert empty.last_error is None
    assert str(empty.document.source) == str(files[0])
    assert [spec['path'] for spec in empty.document.extra_models] == [str(p) for p in files[1:]]
    assert _part_actors(empty) == ['model', 'model:1', 'model:2']
    assert len(set(_offsets(empty))) == 3


def test_open_stl_still_replaces_a_loaded_plate(loaded, application, monkeypatch):
    _choose(monkeypatch, [SHAPES / 'cone.stl'])
    assert loaded.open_stl_dialog()
    assert str(loaded.document.source) == str(SHAPES / 'cone.stl')
    assert loaded.document.extra_models == []


def test_adding_models_with_nothing_open_draws_them(empty, application, monkeypatch):
    files = [SHAPES / 'cylinder.stl', SHAPES / 'cube.stl']
    _choose(monkeypatch, files)
    assert empty.add_extra_model_dialog()
    drain(empty, application)
    assert str(empty.document.source) == str(files[0])
    assert len(empty.document.extra_models) == 1
    assert _part_actors(empty) == ['model', 'model:1']
    assert empty.document.derived.model_triangles is not None


def test_a_second_drop_while_the_first_is_placing_joins_the_plate(empty, application):
    first = QtCore.QMimeData()
    first.setUrls([QtCore.QUrl.fromLocalFile(str(SHAPES / 'cube.stl'))])
    empty.dropEvent(_drop(first))
    second = QtCore.QMimeData()
    second.setUrls([QtCore.QUrl.fromLocalFile(str(SHAPES / 'cone.stl'))])
    empty.dropEvent(_drop(second))
    drain(empty, application)
    assert [spec['path'] for spec in empty.document.extra_models] == [str(SHAPES / 'cone.stl')]
    assert _part_actors(empty) == ['model', 'model:1']


def test_step_is_dropped_only_when_freecad_can_tessellate_it(loaded, fake_step, monkeypatch):
    step, calls = fake_step
    mime = QtCore.QMimeData()
    mime.setUrls([QtCore.QUrl.fromLocalFile(str(step))])
    monkeypatch.setattr('voxelmill.importers.find_freecad', lambda preferred=None: None)
    loaded._refresh_drop_suffixes()
    assert loaded._dropped_models(mime) == []
    monkeypatch.setattr('voxelmill.importers.find_freecad',
                        lambda preferred=None: Path('/opt/freecad'))
    loaded._refresh_drop_suffixes()
    assert loaded.object_panel._dropped_models(mime, loaded.object_panel.drop_suffixes) == [str(step)]
    loaded.reload = lambda: None
    loaded.dropEvent(_drop(mime))
    assert calls == [str(step)]
    assert len(loaded.document.extra_models) == 1


def _drop(mime):
    from PySide6 import QtGui
    return QtGui.QDropEvent(QtCore.QPointF(10, 10), QtCore.Qt.CopyAction, mime,
                            QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
