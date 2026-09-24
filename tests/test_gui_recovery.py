"""Autosave recovery and first-run wizard headless coverage."""
from pathlib import Path

import manifold3d as m
import pytest


pytest.importorskip('PySide6')
pytest.importorskip('vtkmodules')

from PySide6 import QtWidgets

from voxelmill.config import resolve_settings
from voxelmill.geometry import manifold_triangles
from voxelmill.gui.document import Document
from voxelmill.gui.window import MainWindow, autosave_path
from voxelmill.gui.wizard import FirstRunWizard, mark_wizard_done, wizard_done_path, wizard_should_run
from voxelmill.mesh import write_stl


def small_settings():
    return resolve_settings(overrides={
        'process': {'layer_height_mm': 0.2},
        'printer': {'pixels': [1000, 800], 'pixel_pitch_mm': [0.1, 0.1],
                    'build_mm': [100., 80., 165.]},
        'resources': {'workers': 2}})


def test_wizard_skipped_when_headless(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    done = wizard_done_path()
    if done.exists():
        done.unlink()
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    assert wizard_should_run(headless=False, source=None) is True
    assert wizard_should_run(headless=True, source=None) is False
    assert wizard_should_run(headless=False, source='x.stl') is False
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)
    assert wizard_should_run(headless=False, source=None) is False
    window = MainWindow(small_settings(), None, headless=True)
    assert not done.exists()
    window.close()


def test_wizard_finish_writes_done_and_applies(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    done = wizard_done_path()
    if done.exists():
        done.unlink()
    wizard = FirstRunWizard()
    assert wizard.printer.count() >= 1
    assert wizard.resin.count() >= 1
    wizard._finish()
    assert done.exists()
    assert wizard.applied is not None
    assert wizard.applied['printer']['id']


def test_offer_recovery_loads_source(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    source = tmp_path / 'part.stl'
    write_stl(source, manifold_triangles(m.Manifold.cube((6, 5, 4))))
    document = Document(small_settings(), source)
    document.add_contact([0.0, 0.0, 1.0])
    recovery = autosave_path()
    recovery.parent.mkdir(parents=True, exist_ok=True)
    document.save(recovery)
    assert recovery.exists() and recovery.stat().st_size > 0

    window = MainWindow(small_settings(), None, headless=True)
    assert window.document.source is None
    assert window.offer_recovery(recovery) is True
    assert window.document.source is not None
    assert Path(window.document.source).name == 'original.stl' or Path(window.document.source).suffix == '.stl'
    assert window.document.manual_contacts == [[0.0, 0.0, 1.0]]
    window.close()


def test_headless_startup_does_not_prompt_for_recovery(application, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    mark_wizard_done()
    source = tmp_path / 'part.stl'
    write_stl(source, manifold_triangles(m.Manifold.cube((4, 4, 4))))
    document = Document(small_settings(), source)
    path = autosave_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    window = MainWindow(small_settings(), None, headless=True)
    # Headless must not auto-load; only offer_recovery does.
    assert window.document.source is None
    window.close()


def test_non_interactive_startup_never_builds_a_recovery_modal(application, tmp_path, monkeypatch):
    """A leftover autosave must not hang a start nobody is watching.

    The wizard and the FreeCAD prompt already skip on headless / NO_WIZARD /
    no TTY. The recovery prompt did not, so on any machine that had once
    crashed with work open, every non-interactive start -- the packaged
    acceptance smoke, ``gui --screenshot``, a CI run -- stopped forever on a
    modal with no one to click it.

    The modal is asserted never to be *constructed*, rather than letting it
    open and checking the result: an ungated ``exec()`` would hang this test
    exactly as it hung the real startup.
    """
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setenv('VOXELMILL_NO_WIZARD', '1')
    mark_wizard_done()
    source = tmp_path / 'part.stl'
    write_stl(source, manifold_triangles(m.Manifold.cube((4, 4, 4))))
    document = Document(small_settings(), source)
    path = autosave_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    assert path.exists() and path.stat().st_size > 0

    class Refuse:
        def __init__(self, *args, **kwargs):
            raise AssertionError('a non-interactive start must not build a modal')

    window = MainWindow(small_settings(), None)
    monkeypatch.setattr(QtWidgets, 'QMessageBox', Refuse)
    assert window._maybe_offer_recovery() is None
    window.complete_startup()
    # Skipped, not consumed: the next interactive start still offers it.
    assert path.exists() and path.stat().st_size > 0
    assert window.document.source is None
    window.close()
