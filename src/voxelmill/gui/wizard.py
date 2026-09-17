"""First-run printer/resin wizard.

Skipped when ``headless=True``, when a source argument was supplied, or when
``~/.config/voxelmill/wizard-done`` already exists.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from PySide6 import QtWidgets

from .. import profiles as library
from ..config import resolve_settings
from .notifications import config_dir


def wizard_done_path() -> Path:
    return config_dir() / 'wizard-done'


def wizard_should_run(*, headless: bool, source=None) -> bool:
    if headless or source:
        return False
    if os.environ.get('VOXELMILL_NO_WIZARD'):
        return False
    # xvfb/pytest children have no TTY; a blocking dialog would hang the suite.
    if not sys.stdin.isatty():
        return False
    return not wizard_done_path().exists()


def mark_wizard_done() -> Path:
    path = wizard_done_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('1\n')
    return path


class FirstRunWizard(QtWidgets.QDialog):
    """Pick a printer profile and a resin profile, then apply them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('first_run_wizard')
        self.setWindowTitle('VoxelMill setup')
        self.resize(480, 220)
        self.applied = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            'Choose a printer and resin profile to start. '
            'You can change them later from File → Profile library.'))
        form = QtWidgets.QFormLayout()
        self.printer = QtWidgets.QComboBox()
        self.printer.setObjectName('wizard_printer')
        self.resin = QtWidgets.QComboBox()
        self.resin.setObjectName('wizard_resin')
        form.addRow('Printer', self.printer)
        form.addRow('Resin', self.resin)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText('Finish')
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setObjectName('wizard_finish')
        buttons.accepted.connect(self._finish)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load_profiles()

    def _load_profiles(self):
        report = library.library_report()
        self.printer.clear()
        self.resin.clear()
        for entry in report['profiles']:
            if entry.get('error'):
                continue
            label = f"{entry['id']} — {entry.get('name') or entry['path']}"
            if entry['kind'] == 'printer':
                self.printer.addItem(label, entry['path'])
            elif entry['kind'] == 'resin':
                self.resin.addItem(label, entry['path'])
        if self.printer.count() == 0:
            builtin = library.builtin_directory() / 'mars5-ultra.ptr'
            self.printer.addItem('mars5-ultra (builtin)', str(builtin))
        if self.resin.count() == 0:
            builtin = library.builtin_directory() / 'sunlu-abs-like-gray.res'
            self.resin.addItem('sunlu-abs-like-gray (builtin)', str(builtin))

    def _finish(self):
        printer = self.printer.currentData()
        resin = self.resin.currentData()
        self.applied = resolve_settings(printer_path=printer, resin_path=resin)
        mark_wizard_done()
        self.accept()
