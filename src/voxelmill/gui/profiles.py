"""The profile library, as the editor sees it.

Everything ``voxelmill profile`` and ``voxelmill resin`` do from the command
line is reachable here, and both sides call the same functions in
``voxelmill.profiles``, so a printer chosen in the editor and a printer chosen
with ``--printer`` cannot resolve to different settings.
"""
from __future__ import annotations

import json

from PySide6 import QtCore, QtWidgets

from ..config import resolve_settings
from ..contracts import VoxelMillError
from .. import profiles as library

NONE_LABEL = '(none)'


class ProfileLibraryDialog(QtWidgets.QDialog):
    """Browse discovered profiles, apply them, compare them and write them."""

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.document = document
        self.applied = None
        self.setWindowTitle('Profile library')
        self.resize(900, 700)
        layout = QtWidgets.QVBoxLayout(self)

        self.search_path = QtWidgets.QLabel()
        self.search_path.setWordWrap(True)
        layout.addWidget(self.search_path)

        selectors = QtWidgets.QFormLayout()
        self.printer = QtWidgets.QComboBox()
        self.resin = QtWidgets.QComboBox()
        selectors.addRow('Printer profile', self.printer)
        selectors.addRow('Resin profile', self.resin)
        layout.addLayout(selectors)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ['kind', 'id', 'name', 'layer', 'path', 'shadows / error'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)

        buttons = QtWidgets.QHBoxLayout()
        for label, slot in (('Apply to editor', self.apply_selection),
                            ('Diff against editor', self.show_diff),
                            ('Provenance', self.show_provenance),
                            ('Save printer profile...', self.save_printer),
                            ('Bind resin to printer...', self.bind_resin),
                            ('Refresh', self.reload)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            button.setObjectName(label.split('.')[0].split(' ')[0].lower())
            buttons.addWidget(button)
            setattr(self, f'button_{button.objectName()}', button)
        layout.addLayout(buttons)

        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 1)

        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.reload()

    # ---- state ---------------------------------------------------------
    def reload(self):
        report = library.library_report()
        self.search_path.setText('Search path (highest first): ' + '  |  '.join(
            f"{entry['layer']}: {entry['directory']}"
            + ('' if entry['exists'] else ' [absent]')
            for entry in report['search_path']))
        entries = report['profiles']
        self.entries = entries
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            note = '; '.join(entry['shadows'])
            if entry['error']:
                note = f"error: {entry['error']}" + (f' | shadows {note}' if note else '')
            for column, value in enumerate((entry['kind'], entry['id'], entry['name'] or '',
                                            entry['layer'], entry['path'], note)):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        for combo, kind in ((self.printer, 'printer'), (self.resin, 'resin')):
            current = combo.currentData()
            combo.clear()
            combo.addItem(NONE_LABEL, None)
            for entry in entries:
                if entry['kind'] == kind and not entry['error']:
                    combo.addItem(f"{entry['id']} — {entry['name'] or entry['path']}"
                                  f" [{entry['layer']}]", entry['path'])
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)

    def selected_paths(self):
        return self.printer.currentData(), self.resin.currentData()

    def selected_row_entry(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) != 1:
            return None
        return self.entries[rows.pop()]

    def _show(self, payload):
        self.output.setPlainText(json.dumps(payload, indent=2, default=str))

    def _fail(self, error):
        self._show({'error': error.to_dict() if isinstance(error, VoxelMillError) else str(error)})

    # ---- actions -------------------------------------------------------
    def resolved_selection(self):
        printer, resin = self.selected_paths()
        return resolve_settings(printer, resin, None)

    def apply_selection(self):
        """Resolve the chosen pair and hand it to the document as one undoable edit."""
        try:
            settings = self.resolved_selection()
        except VoxelMillError as error:
            return self._fail(error)
        self.applied = settings
        self.document.set_settings(settings)
        printer, resin = self.selected_paths()
        self._show({'applied': True, 'printer': printer, 'resin': resin,
                    'printer_name': settings['printer']['name'],
                    'resin_name': settings['resin']['name']})
        return settings

    def show_diff(self):
        try:
            other = self.resolved_selection()
        except VoxelMillError as error:
            return self._fail(error)
        printer, resin = self.selected_paths()
        differences = library.diff_settings(self.document.settings, other)
        self._show({'left': 'editor settings', 'right': {'printer': printer, 'resin': resin},
                    'differences': differences,
                    'identical': not differences})
        return differences

    def show_provenance(self):
        printer, resin = self.selected_paths()
        try:
            self._show({'printer': printer, 'resin': resin,
                        'provenance': library.provenance(printer, resin, None)})
        except VoxelMillError as error:
            self._fail(error)

    def save_printer(self, path=None):
        """Write the editor's current settings as a self-contained ``.ptr``."""
        if not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, 'Save printer profile', '', 'Printer profile (*.ptr)')
        if not path:
            return None
        try:
            payload = library.save_printer_profile(path, self.document.settings)
        except VoxelMillError as error:
            return self._fail(error)
        self._show(payload)
        self.reload()
        return payload

    def bind_resin(self, output=None, target=None, source=None):
        """Copy a resin's process block onto another printer id."""
        entry = self.selected_row_entry()
        reference = entry['path'] if entry and entry['kind'] == 'resin' else self.resin.currentData()
        if not reference:
            return self._fail(VoxelMillError(
                'invalid_profile', 'Select a resin profile in the table or the resin box first'))
        # A resin that already binds several printers has no single obvious
        # source, and the shared function refuses rather than picking one, so
        # the dialog has to ask for it exactly as --from does.
        if source is None:
            try:
                bound = library.resin_document(reference)['bound_printers']
            except VoxelMillError as error:
                return self._fail(error)
            if len(bound) > 1:
                source, accepted = QtWidgets.QInputDialog.getItem(
                    self, 'Bind resin process', 'Copy the process block of which printer?',
                    bound, 0, False)
                if not accepted:
                    return None
        if target is None:
            target, accepted = QtWidgets.QInputDialog.getText(
                self, 'Bind resin process', 'Printer id to bind the copied process to:',
                text=self.document.settings['printer']['id'])
            if not accepted:
                return None
        if not output:
            output, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, 'Save bound resin profile', '', 'Resin profile (*.res)')
        if not output:
            return None
        try:
            payload = library.bind_resin_process(reference, output,
                                                 source_printer=source, target_printer=target)
        except VoxelMillError as error:
            return self._fail(error)
        self._show(payload)
        self.reload()
        return payload


def resin_summary(reference) -> dict:
    """The payload ``voxelmill resin show`` prints, for display in the editor."""
    return library.resin_document(reference)
