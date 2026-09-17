"""Optional post-slice command hooks.

A hook is arbitrary code from settings. It is opt-in, never inherited from a
printer profile (see ``resolve_settings``), and must not delete a published GOO
when it fails.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from typing import Any


HOOK_TIMEOUT_S = 120


def run_post_slice_hook(settings, output_path, report_path, cancel=None) -> dict[str, Any] | None:
    """Run ``resources.post_slice_hook`` after a successful export.

    Returns ``None`` when no hook is configured. Otherwise returns a
    ``report['hooks']['post_slice']`` record. The published output path is never
    removed here, including when the command is missing or exits nonzero.
    """
    resources = settings.get('resources') if isinstance(settings, dict) else None
    hook = None if not isinstance(resources, dict) else resources.get('post_slice_hook')
    if hook is None:
        return None
    if cancel is not None:
        cancel.check()
    output = '' if output_path is None else str(output_path)
    report = '' if report_path is None else str(report_path)
    record: dict[str, Any] = {
        'command': hook,
        'output': output or None,
        'report': report or None,
        'timeout_s': HOOK_TIMEOUT_S,
    }
    env = os.environ.copy()
    env['VOXELMILL_OUTPUT'] = output
    env['VOXELMILL_REPORT'] = report
    try:
        argv = shlex.split(hook)
        if not argv:
            record.update({'ok': False, 'error': 'empty_command',
                           'message': 'post_slice_hook is empty after splitting'})
            return record
        completed = subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=HOOK_TIMEOUT_S,
            check=False)
        record.update({
            'ok': completed.returncode == 0,
            'returncode': completed.returncode,
            'stdout': completed.stdout,
            'stderr': completed.stderr,
        })
        if completed.returncode != 0:
            record['error'] = 'nonzero_exit'
    except FileNotFoundError as exc:
        record.update({
            'ok': False,
            'error': 'command_not_found',
            'message': str(exc),
            'returncode': None,
            'stdout': '',
            'stderr': '',
        })
    except subprocess.TimeoutExpired as exc:
        record.update({
            'ok': False,
            'error': 'timeout',
            'message': f'post_slice_hook exceeded {HOOK_TIMEOUT_S}s',
            'returncode': None,
            'stdout': getattr(exc, 'stdout', None) or '',
            'stderr': getattr(exc, 'stderr', None) or '',
        })
    except OSError as exc:
        record.update({
            'ok': False,
            'error': 'os_error',
            'message': str(exc),
            'returncode': None,
            'stdout': '',
            'stderr': '',
        })
    return record
