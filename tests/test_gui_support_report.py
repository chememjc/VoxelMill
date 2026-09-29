"""The Report tab carries the router's explanation after Compute attachments."""
from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from types import SimpleNamespace  # noqa: E402

from PySide6 import QtCore  # noqa: E402

from voxelmill.contracts import Diagnostic  # noqa: E402
from voxelmill.gui.window import MainWindow  # noqa: E402
from test_gui import small_settings  # noqa: E402


def _guard(resolved, positions):
    return {'resolved': resolved, 'islands_remaining': len(positions), 'max_passes': 4,
            'island_positions': positions,
            'passes': [{'contacts_added': 3, 'layers_scanned': 40}]}


def test_unroutable_contacts_are_listed_with_their_reason_and_settings(application):
    window = MainWindow(small_settings(), None, headless=True)
    unroutable = Diagnostic(
        'support_unroutable', 'the part is 0.08 mm below the contact; too close for a model anchor',
        severity='warning', position_mm=[1.0, 2.0, 0.55],
        details={'reason': 'anchor_rejected:gap_too_short',
                 'suggest': ['min_tip_length_mm', 'allow_part_to_part']})
    plan = SimpleNamespace(diagnostics=[unroutable, Diagnostic('tree_note', 'unrelated')])
    window._report_island_guard(_guard(False, [(1.0, 2.0, 0.55)]), plan)
    items = [window.diagnostic_list.topLevelItem(i)
             for i in range(window.diagnostic_list.topLevelItemCount())]
    texts = [item.text(0) for item in items]
    assert any(text.startswith('raster_island') for text in texts)
    routed = [item for item in items if item.text(0).startswith('support_unroutable')]
    assert len(routed) == 1 and 'too close for a model anchor' in routed[0].text(0)
    assert 'support.min_tip_length_mm' in routed[0].toolTip(0)
    assert not any(text.startswith('tree_note') for text in texts)
    payload = window.diagnostics.toPlainText()
    assert '"support_routes": "fail"' in payload
    # A support diagnostic has a position but no layer: activating it still
    # jumps to the layer at its height.
    window._select_diagnostic(routed[0], 0)
    height = window.document.settings['process']['layer_height_mm']
    assert window.layers.slider.value() == int(0.55 / height)
    assert routed[0].data(0, QtCore.Qt.UserRole)['details']['reason'].startswith('anchor_rejected')
    window.close()


def test_a_clean_routing_reports_support_routes_passed(application):
    window = MainWindow(small_settings(), None, headless=True)
    window._report_island_guard(_guard(True, []), SimpleNamespace(diagnostics=[]))
    assert '"support_routes": "pass"' in window.diagnostics.toPlainText()
    window.close()
