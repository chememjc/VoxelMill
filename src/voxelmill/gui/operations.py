"""Complete operation forms generated from the public CLI argument contract.

Run in an isolated Python process, with an argument vector (never a shell), so
CLI resource limits and long correction passes cannot freeze the editor.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import sys
import tempfile

from PySide6 import QtCore, QtWidgets

from ..cli import build_parser, _overrides
from ..contracts import VoxelMillError


def operation_parsers():
    parser = build_parser()
    subcommands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    def error(message):
        raise VoxelMillError('invalid_option', message)
    commands = {name: command for name, command in subcommands.choices.items() if name != 'gui'}
    for command in commands.values():
        command.error = error
    return commands


def form_actions(parser):
    return [a for a in parser._actions if a.dest != 'help']


class OperationDialog(QtWidgets.QDialog):
    """Every noninteractive command and option, including full prepare passes."""

    def __init__(self, document, parent=None, command='prepare'):
        super().__init__(parent)
        self.document = document
        self.setWindowTitle('Run operation')
        self.resize(850, 850)
        self.parsers = operation_parsers()
        self.process = QtCore.QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self._scratch = None
        self._output = bytearray()
        self._truncated = False
        self.last_exit_code = None
        self.fields = {}
        layout = QtWidgets.QVBoxLayout(self)
        self.operation = QtWidgets.QComboBox()
        for name in self.parsers:
            self.operation.addItem(name)
        layout.addWidget(self.operation)
        self.editor_settings = QtWidgets.QCheckBox('Use current editor settings and support edits')
        self.editor_settings.setChecked(True)
        self.editor_settings.setToolTip(
            'Without a printer/resin profile override, start from the editor settings. '
            'Prepare also starts from its pose and contact edits. Explicit fields override these defaults.')
        layout.addWidget(self.editor_settings)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 3)
        self.status = QtWidgets.QLabel('Choose an operation, review its options, then Run.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 2)
        buttons = QtWidgets.QHBoxLayout()
        self.run_button = QtWidgets.QPushButton('Run')
        self.run_button.clicked.connect(self.start)
        self.cancel_button = QtWidgets.QPushButton('Cancel operation')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.save_button = QtWidgets.QPushButton('Save displayed report…')
        self.save_button.clicked.connect(self.save_report)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.operation.currentTextChanged.connect(self._build_form)
        self.operation.setCurrentText(command)
        self._build_form(command)

    def _build_form(self, name):
        page = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(page)
        self.fields = {}
        for action in form_actions(self.parsers[name]):
            if action.dest in self.fields:
                for flag in action.option_strings:
                    self.fields[action.dest].addItem(flag, flag)
                continue
            if action.nargs == 0:
                widget = QtWidgets.QComboBox()
                widget.addItem('Default', None)
                for flag in action.option_strings:
                    widget.addItem(flag, flag)
            else:
                widget = QtWidgets.QLineEdit()
                if action.choices:
                    widget.setPlaceholderText(' / '.join(map(str, action.choices)))
                elif action.default is not None and action.default != argparse.SUPPRESS:
                    widget.setPlaceholderText(f'Default: {action.default}')
                if action.nargs in ('+', '*') or isinstance(action.nargs, int):
                    widget.setToolTip('Separate values with spaces; quote paths containing spaces.')
                if isinstance(action, argparse._AppendAction):
                    widget.setPlaceholderText('JSON array of strings, e.g. ["support.spacing_mm=2"]')
                if not action.option_strings and action.dest in ('input', 'inputs') and self.document.source:
                    value = str(self.document.source)
                    widget.setText(shlex.quote(value) if action.nargs in ('+', '*') else value)
            help_text = action.help or action.dest.replace('_', ' ')
            widget.setToolTip(help_text + '\n' + widget.toolTip())
            self.fields[action.dest] = widget
            label = action.option_strings[0] if action.option_strings else action.dest
            if action.required or (not action.option_strings and action.nargs != '?'):
                label += ' *'
            form.addRow(label, widget)
        self.scroll.setWidget(page)

    def arguments(self):
        name = self.operation.currentText()
        args = [name]
        actions = form_actions(self.parsers[name])
        profiles = False
        if self.editor_settings.isChecked():
            profiles = any(self.fields.get(k) and self.fields[k].text().strip()
                           for k in ('printer_profile', 'resin_profile'))
            if name == 'prepare':
                if not self.fields['rotate'].text().strip():
                    rotation = self.document.rotation_deg
                    args.extend(['--rotate', *(['auto'] if rotation == 'auto' else map(str, rotation))])
                if not self.fields['center_offset'].text().strip():
                    args.extend(['--center-offset', *map(str, self.document.center_offset_mm)])
                if not self.fields['model_lift_mm'].text().strip():
                    args.extend(['--model-lift-mm', str(self.document.model_lift_mm)])
                for key, contacts in [('contacts', self.document.manual_contacts),
                                      ('removed_contacts', self.document.removed_contacts)]:
                    if contacts and key in self.fields and not self.fields[key].text().strip():
                        if self._scratch is None:
                            self._scratch = tempfile.TemporaryDirectory(prefix='voxelmill-operation-')
                        path = Path(self._scratch.name) / f'{key}.json'
                        path.write_text(json.dumps(contacts))
                        args.extend(['--' + key.replace('_', '-'), str(path)])
        seen = set()
        for action in actions:
            if action.dest in seen:
                continue
            seen.add(action.dest)
            widget = self.fields[action.dest]
            if action.nargs == 0:
                if widget.currentData():
                    args.append(widget.currentData())
                continue
            value = widget.text().strip()
            if not value:
                continue
            flag = action.option_strings[:1]
            if isinstance(action, argparse._AppendAction):
                try:
                    values = json.loads(value)
                    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                        raise ValueError('expected a JSON array of strings')
                except ValueError as error:
                    raise VoxelMillError('invalid_option', f'{action.dest}: {error}') from error
                for item in values:
                    args.extend([*flag, item])
            else:
                multiple = action.nargs in ('+', '*') or isinstance(action.nargs, int)
                args.extend([*flag, *(shlex.split(value) if multiple else [value])])
        parsed = self.parsers[name].parse_args(args[1:])
        if self.editor_settings.isChecked() and 'set' in self.fields and not profiles:
            explicit = _overrides(parsed) or {}
            skip = {section: set(values) for section, values in explicit.items()}
            preset_field = self.fields.get('support_preset')
            preset = preset_field.text().strip() if preset_field is not None else ''
            if preset:
                from ..config import _support_overlay_from_preset
                skip.setdefault('support', set()).update(
                    _support_overlay_from_preset(preset, {}))
            for section, values in self.document.settings.items():
                if isinstance(values, dict):
                    for key, value in values.items():
                        if key not in skip.get(section, ()):
                            args.extend(['--set', f'{section}.{key}={json.dumps(value)}'])
        return args

    def start(self):
        if self.process.state() != QtCore.QProcess.NotRunning:
            return
        try:
            args = self.arguments()
        except (VoxelMillError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.output.clear()
        self._output.clear()
        self._truncated = False
        self.last_exit_code = None
        self.status.setText('Running ' + self.operation.currentText() + '…')
        self._set_running(True)
        self.process.start(sys.executable, ['-m', 'voxelmill.cli', *args])

    def _set_running(self, running):
        self.run_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.operation.setEnabled(not running)
        self.editor_settings.setEnabled(not running)
        self.scroll.setEnabled(not running)
        self.save_button.setEnabled(not running)

    def _read_stdout(self):
        block = bytes(self.process.readAllStandardOutput())
        available = max(0, 16 * 1024**2 - len(self._output))
        self._output.extend(block[:available])
        self._truncated |= len(block) > available

    def _read_stderr(self):
        block = bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace')
        if block.strip():
            # Progress can be frequent; bound its visible history.
            self.output.document().setMaximumBlockCount(1000)
            self.output.appendPlainText(block.strip())

    def _finished(self, code, status):
        self._read_stdout()
        self._read_stderr()
        self.last_exit_code = code
        self._set_running(False)
        text = self._output.decode('utf-8', errors='replace')
        if text:
            self.output.document().setMaximumBlockCount(0)
            self.output.setPlainText(text)
        message = ('Completed' if code == 0 and status == QtCore.QProcess.NormalExit
                   else 'Validation failed' if code == 2 else f'Operation stopped (exit {code})')
        if self._truncated:
            message += '; display limited to 16 MiB — use --report for the complete report'
        self.status.setText(message)
        self._cleanup()

    def _error(self, error):
        if error == QtCore.QProcess.FailedToStart:
            self._set_running(False)
            self.status.setText(self.process.errorString())
            self._cleanup()

    def cancel(self):
        if self.process.state() == QtCore.QProcess.Running:
            try:
                os.kill(self.process.processId(), signal.SIGINT)
            except ProcessLookupError:
                return  # It exited between the state check and signal.
            self.status.setText('Canceling; waiting for operation cleanup…')

    def _cleanup(self):
        if self._scratch is not None:
            self._scratch.cleanup()
            self._scratch = None

    def save_report(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, 'Save report', '', 'JSON (*.json);;Text (*.txt)')
        if path:
            try:
                Path(path).write_text(self.output.toPlainText() + '\n')
            except OSError as error:
                self.status.setText(str(error))

    def reject(self):
        if self.process.state() != QtCore.QProcess.NotRunning:
            self.cancel()
            return
        self._cleanup()
        super().reject()

    def closeEvent(self, event):
        if self.process.state() != QtCore.QProcess.NotRunning:
            self.cancel()
            event.ignore()
        else:
            self._cleanup()
            event.accept()
