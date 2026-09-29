"""Render a voxelmill JSON report as a readable static HTML page."""
from __future__ import annotations

from html import escape
from typing import Any


SEVERITY_ORDER = ('error', 'warning', 'info', 'debug')


def _passed(report: dict[str, Any]):
    if 'passed' in report:
        return bool(report['passed'])
    validation = report.get('validation')
    if isinstance(validation, dict) and 'passed' in validation:
        return bool(validation['passed'])
    nested = report.get('report')
    if isinstance(nested, dict) and 'passed' in nested:
        return bool(nested['passed'])
    if 'written' in report:
        return bool(report.get('written')) and not report.get('warned')
    return None


def _collect_diagnostics(report: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for key in ('diagnostics',):
        items = report.get(key)
        if isinstance(items, list):
            found.extend(d for d in items if isinstance(d, dict))
    for nest_key in ('validation', 'report'):
        nested = report.get(nest_key)
        if isinstance(nested, dict):
            items = nested.get('diagnostics')
            if isinstance(items, list):
                found.extend(d for d in items if isinstance(d, dict))
    return found


def _metrics_rows(report: dict[str, Any]) -> list[tuple[str, str]]:
    metrics = {}
    validation = report.get('validation')
    if isinstance(validation, dict) and isinstance(validation.get('metrics'), dict):
        metrics.update(validation['metrics'])
    if isinstance(report.get('metrics'), dict):
        metrics.update(report['metrics'])
    for key in ('layers', 'seconds', 'print_time_s', 'written', 'warned', 'output', 'source'):
        if key in report and key not in metrics:
            metrics[key] = report[key]
    rows = []
    for key, value in metrics.items():
        if isinstance(value, (dict, list)):
            continue
        rows.append((str(key), value))
    return rows


def _find_resin_usage(report: dict[str, Any]) -> dict[str, Any] | None:
    """The report's resin usage, wherever this report kind keeps it."""
    candidates = [report.get('resin_usage')]
    for key in ('stages', 'metrics'):
        section = report.get(key)
        if isinstance(section, dict):
            candidates.append(section.get('resin_usage'))
    for nest_key in ('validation', 'report'):
        nested = report.get(nest_key)
        if isinstance(nested, dict) and isinstance(nested.get('metrics'), dict):
            candidates.append(nested['metrics'].get('resin_usage'))
    return next((c for c in candidates if isinstance(c, dict)), None)


def _format_amount(value: Any, digits: int) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'{value:.{digits}f}'
    return '\u2014'


def _resin_section(usage: dict[str, Any]) -> list[str]:
    found = usage.get('breakdown')
    breakdown: dict[str, Any] = found if isinstance(found, dict) else {}
    found = breakdown.get('total')
    total: dict[str, Any] = found if isinstance(found, dict) else usage
    rows: list[tuple[str, dict[str, Any]]] = [('total', total)]
    for name, label in (('model', 'model'), ('supports', 'supports (incl. raft/base)')):
        row = breakdown.get(name)
        if isinstance(row, dict):
            rows.append((label, row))
    show_cost = any(isinstance(row.get('cost'), (int, float)) for _, row in rows)
    currency = escape(str(usage.get('currency') or ''))
    parts = ['<h2>Resin usage</h2>', '<table><tr><th>part</th><th>volume (mL)</th><th>mass (g)</th>'
             + (f'<th>cost ({currency})</th>' if show_cost else '') + '</tr>']
    for label, row in rows:
        cost = f'<td>{_format_amount(row.get("cost"), 2)}</td>' if show_cost else ''
        parts.append(f'<tr><td>{escape(label)}</td><td>{_format_amount(row.get("volume_ml"), 2)}</td>'
                     f'<td>{_format_amount(row.get("mass_g"), 2)}</td>{cost}</tr>')
    parts.append('</table>')
    density = usage.get('density_g_cm3')
    if isinstance(density, (int, float)):
        parts.append(f'<p>Density {escape(str(density))} g/cm\u00b3.</p>')
    for key in ('note',):
        if usage.get(key):
            parts.append(f'<p>{escape(str(usage[key]))}.</p>')
    if breakdown.get('unavailable_reason'):
        parts.append(f'<p>Model/support split unavailable: {escape(str(breakdown["unavailable_reason"]))}.</p>')
    return parts


def _group_diagnostics(diagnostics: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name in SEVERITY_ORDER}
    groups['other'] = []
    for item in diagnostics:
        severity = str(item.get('severity') or 'other').lower()
        groups.setdefault(severity if severity in groups else 'other', []).append(item)
    return groups


def render_report(report_dict: dict[str, Any]) -> str:
    """Return a self-contained HTML document for ``report_dict``."""
    if not isinstance(report_dict, dict):
        raise TypeError('report_dict must be a mapping')
    title = escape(str(report_dict.get('command')
                       or report_dict.get('source')
                       or report_dict.get('output')
                       or 'voxelmill report'))
    passed = _passed(report_dict)
    if passed is True:
        verdict, verdict_class = 'PASS', 'pass'
    elif passed is False:
        verdict, verdict_class = 'FAIL', 'fail'
    else:
        verdict, verdict_class = 'UNKNOWN', 'unknown'
    diagnostics = _collect_diagnostics(report_dict)
    groups = _group_diagnostics(diagnostics)
    metric_rows = _metrics_rows(report_dict)

    parts = [
        '<!DOCTYPE html>',
        '<html lang="en">',
        '<head>',
        '<meta charset="utf-8"/>',
        f'<title>{title}</title>',
        '<style>',
        'body{font-family:system-ui,sans-serif;margin:1.5rem;line-height:1.4;color:#122}',
        'h1,h2{margin:1rem 0 0.4rem}',
        '.verdict{display:inline-block;padding:0.2rem 0.6rem;border-radius:0.3rem;'
        'font-weight:700;letter-spacing:0.04em}',
        '.pass{background:#d8f5d8;color:#0a5}',
        '.fail{background:#f8d4d4;color:#a10}',
        '.unknown{background:#eee;color:#444}',
        'table{border-collapse:collapse;margin:0.5rem 0 1rem}',
        'th,td{border:1px solid #ccd;padding:0.3rem 0.55rem;text-align:left;vertical-align:top}',
        'th{background:#f2f4f8}',
        '.sev-error{border-left:4px solid #c33}',
        '.sev-warning{border-left:4px solid #c80}',
        '.sev-info{border-left:4px solid #38c}',
        '.sev-debug,.sev-other{border-left:4px solid #888}',
        'code{font-family:ui-monospace,monospace}',
        '</style>',
        '</head>',
        '<body>',
        f'<h1>{title}</h1>',
        f'<p>Result: <span class="verdict {verdict_class}">{verdict}</span></p>',
    ]
    schema_version = report_dict.get('schema_version')
    if schema_version is not None:
        parts.append(f'<p>schema_version: {escape(str(schema_version))}</p>')

    parts.append('<h2>Diagnostics</h2>')
    if not diagnostics:
        parts.append('<p>No diagnostics.</p>')
    else:
        for severity in (*SEVERITY_ORDER, 'other'):
            items = groups.get(severity) or []
            if not items:
                continue
            parts.append(f'<h3>{escape(severity)} ({len(items)})</h3>')
            parts.append('<table>')
            parts.append('<tr><th>code</th><th>message</th><th>layer</th></tr>')
            for item in items:
                code = escape(str(item.get('code', '')))
                message = escape(str(item.get('message', '')))
                layer = item.get('layer')
                layer_text = '' if layer is None else escape(str(layer))
                parts.append(
                    f'<tr class="sev-{escape(severity)}"><td><code>{code}</code></td>'
                    f'<td>{message}</td><td>{layer_text}</td></tr>')
            parts.append('</table>')

    resin = _find_resin_usage(report_dict)
    if resin is not None:
        parts.extend(_resin_section(resin))

    parts.append('<h2>Metrics</h2>')
    if not metric_rows:
        parts.append('<p>No scalar metrics.</p>')
    else:
        parts.append('<table><tr><th>name</th><th>value</th></tr>')
        for name, value in metric_rows:
            parts.append(f'<tr><td><code>{escape(name)}</code></td>'
                         f'<td>{escape(str(value))}</td></tr>')
        parts.append('</table>')

    parts.extend(['</body>', '</html>', ''])
    return '\n'.join(parts)
