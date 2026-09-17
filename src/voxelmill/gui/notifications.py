"""Status-bar notification queue with per-category suppression.

Warnings and errors land here instead of modal dialogs. Suppressed categories
persist under ``~/.config/voxelmill/notifications.json`` (or ``$XDG_CONFIG_HOME``).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6 import QtCore, QtWidgets


def config_dir() -> Path:
    base = os.environ.get('XDG_CONFIG_HOME') or ''
    root = Path(base) if base.strip() else Path.home() / '.config'
    return root / 'voxelmill'


def notifications_path() -> Path:
    return config_dir() / 'notifications.json'


def load_suppressed() -> set[str]:
    path = notifications_path()
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    categories = data.get('suppressed_categories') or []
    return {str(item) for item in categories}


def save_suppressed(categories) -> None:
    path = notifications_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'suppressed_categories': sorted({str(item) for item in categories})}
    path.write_text(json.dumps(payload, indent=2) + '\n')


class NotificationCenter(QtCore.QObject):
    """Queue of non-modal notices with optional category suppression."""

    notified = QtCore.Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.suppressed = load_suppressed()
        self.history: list[dict] = []

    def suppress(self, category: str):
        self.suppressed.add(str(category))
        save_suppressed(self.suppressed)

    def unsuppress(self, category: str):
        self.suppressed.discard(str(category))
        save_suppressed(self.suppressed)

    def is_suppressed(self, category: str) -> bool:
        return str(category) in self.suppressed

    def notify(self, message: str, *, category='general', level='warning', sticky=False):
        entry = {
            'message': str(message),
            'category': str(category),
            'level': str(level),
            'sticky': bool(sticky),
        }
        self.history.append(entry)
        if self.is_suppressed(category):
            return None
        self.notified.emit(entry)
        return entry


class NotificationBanner(QtWidgets.QWidget):
    """Compact status-bar strip: message + dismiss + suppress-category."""

    def __init__(self, center: NotificationCenter, parent=None):
        super().__init__(parent)
        self.center = center
        self.setObjectName('notification_banner')
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        self.label = QtWidgets.QLabel()
        self.label.setObjectName('notification_message')
        self.label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.label, 1)
        self.suppress_button = QtWidgets.QToolButton()
        self.suppress_button.setObjectName('notification_suppress')
        self.suppress_button.setText('Mute')
        self.suppress_button.setToolTip('Suppress further notices in this category.')
        self.suppress_button.clicked.connect(self._suppress_current)
        layout.addWidget(self.suppress_button)
        self.dismiss_button = QtWidgets.QToolButton()
        self.dismiss_button.setObjectName('notification_dismiss')
        self.dismiss_button.setText('×')
        self.dismiss_button.clicked.connect(self.clear)
        layout.addWidget(self.dismiss_button)
        self._current = None
        self.clear()
        center.notified.connect(self.show_entry)

    def show_entry(self, entry: dict):
        self._current = entry
        level = entry.get('level', 'warning')
        prefix = {'error': 'Error', 'warning': 'Warning', 'info': 'Info'}.get(level, level)
        self.label.setText(f"{prefix} [{entry.get('category', 'general')}]: {entry.get('message', '')}")
        color = {'error': '#b00020', 'warning': '#b8860b', 'info': '#336699'}.get(level, '#444')
        self.label.setStyleSheet(f'color: {color};')
        self.setVisible(True)

    def clear(self):
        self._current = None
        self.label.clear()
        self.setVisible(False)

    def _suppress_current(self):
        if not self._current:
            return
        self.center.suppress(self._current.get('category', 'general'))
        self.clear()
