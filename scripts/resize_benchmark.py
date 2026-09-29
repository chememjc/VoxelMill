#!/usr/bin/env python3
"""Time dock-separator drags in the real editor and count the VTK renders.

A separator drag between the 3D view and the right-hand panes is a
``resizeDocks`` per mouse step; this replays one with a model loaded and prints
the wall time per step, how many times VTK rendered during and after the drag,
and whether an idle window keeps painting (the macOS repaint loop fixed in
0.6.1 showed as hundreds of viewport paints a second with nothing moving).

On the Intel iMac, over ssh, from a source checkout::

    .venv/bin/python scripts/resize_benchmark.py inputstl/Latch_fat_finger.stl --supports

Linux, real VTK under Xvfb::

    xvfb-run -a .venv/bin/python scripts/resize_benchmark.py inputstl/Latch_fat_finger.stl

With no STL it loads the committed ``fixtures/shapes/cylinder.stl``, which is
small: the drag is only slow with a heavy scene, so pass a real part.

``--offscreen`` measures the Qt side only (no 3D view: VTK cannot render on
the offscreen platform). ``--render-interval-ms 0`` renders on the next idle
pass after each paint, which is close to the pre-0.6.1 cadence on macOS, for
a before/after comparison. The packaged app has no hook to run a script, so
this needs a source checkout; it sets ``VOXELMILL_NO_WIZARD`` and private
config/cache directories itself so no first-run or recovery prompt can block
an unattended ssh session.
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path, nargs='?',
                        default=ROOT / 'fixtures' / 'shapes' / 'cylinder.stl',
                        help='STL to load (default: fixtures/shapes/cylinder.stl)')
    parser.add_argument('--steps', type=int, default=80, help='separator steps to replay')
    parser.add_argument('--step-px', type=int, default=6, help='pixels the separator moves per step')
    parser.add_argument('--interval-ms', type=float, default=8.0,
                        help='event-loop time between steps, like mouse-move cadence (0: back to back)')
    parser.add_argument('--tab', choices=['setup', 'layers', 'faults', 'report'], default='setup',
                        help='which right-hand pane is showing during the drag')
    parser.add_argument('--supports', action='store_true', help='route attachments before dragging')
    parser.add_argument('--no-model', action='store_true', help='drag with an empty plate')
    parser.add_argument('--render-interval-ms', type=int, default=None,
                        help='override the deferred-render interval (deferring platforms only)')
    parser.add_argument('--defer', choices=['auto', 'on', 'off'], default='auto',
                        help='force the macOS deferred-render path on or off (auto: platform)')
    parser.add_argument('--offscreen', action='store_true',
                        help='QT_QPA_PLATFORM=offscreen and no 3D view')
    parser.add_argument('--size', default='1600x1000', help='main window size WxH')
    parser.add_argument('--timeout', type=float, default=300.0, help='seconds to wait for loading')
    parser.add_argument('--keep-user-dirs', action='store_true',
                        help='use the real ~/.config and ~/.cache (a stale autosave will block)')
    parser.add_argument('--json', action='store_true', help='print one JSON object only')
    return parser.parse_args()


def main():
    args = parse_args()
    os.environ['VOXELMILL_NO_WIZARD'] = '1'
    if args.offscreen:
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    if not args.keep_user_dirs:
        private = Path(tempfile.mkdtemp(prefix='voxelmill-resize-'))
        os.environ['XDG_CONFIG_HOME'] = str(private / 'config')
        os.environ['XDG_CACHE_HOME'] = str(private / 'cache')

    from PySide6 import QtCore, QtWidgets
    from voxelmill.config import resolve_settings
    from voxelmill.gui.window import MainWindow

    from voxelmill.gui import layerview

    counts = {'renders': 0, 'viewport_paints': 0, 'layer_images': 0}
    build_layer_image = layerview.render_layer_image

    def counted_layer_image(*positional, **keywords):
        counts['layer_images'] += 1
        return build_layer_image(*positional, **keywords)

    layerview.render_layer_image = counted_layer_image
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    source = None if args.no_model else str(args.source)
    window = MainWindow(resolve_settings(), source, headless=args.offscreen)
    width, height = (int(value) for value in args.size.lower().split('x'))
    window.resize(width, height)
    window.show()
    app.processEvents()
    interactor = window.viewport.interactor if window.viewport else None
    if interactor is not None:
        if args.defer != 'auto':
            interactor.defer_renders = args.defer == 'on'
        if args.render_interval_ms is not None:
            interactor._render_timer.setInterval(max(0, args.render_interval_ms))
        interactor.GetRenderWindow().AddObserver(
            'StartEvent', lambda *_: counts.__setitem__('renders', counts['renders'] + 1))

        class PaintCounter(QtCore.QObject):
            def eventFilter(self, watched, event):
                if event.type() == QtCore.QEvent.Paint:
                    counts['viewport_paints'] += 1
                return False

        paint_counter = PaintCounter()
        interactor.installEventFilter(paint_counter)
        window.viewport.start()
    window.complete_startup()

    def spin(seconds):
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            app.processEvents()
            time.sleep(0.001)

    def settle(predicate, what):
        deadline = time.monotonic() + args.timeout
        while not predicate():
            app.processEvents()
            if window.last_error:
                raise SystemExit(f'{what} failed: {window.last_error}')
            if time.monotonic() > deadline:
                raise SystemExit(f'{what} timed out: {window.statusBar().currentMessage()}')
            time.sleep(0.005)

    derived = window.document.derived
    if source:
        settle(lambda: derived.union is not None, 'loading')
        if args.supports:
            window.compute_attachments()
            settle(lambda: derived.plan is not None and derived.union is not None, 'supports')
    window.tabs.setCurrentIndex(['setup', 'layers', 'faults', 'report'].index(args.tab))
    if args.tab == 'layers' and source:
        settle(lambda: window.layers._image is not None, 'layer preview')
    window.jobs.wait(int(args.timeout * 1000))
    spin(1.0)

    for key in counts:
        counts[key] = 0
    spin(1.0)
    idle = dict(counts)

    dock = window.setup_dock
    start_width = dock.width()
    for key in counts:
        counts[key] = 0
    steps = []
    drag_started = time.perf_counter()
    for step in range(args.steps):
        # Out and back, the way a hand drags a separator and returns it.
        half = max(1, args.steps // 2)
        offset = (step if step < half else args.steps - step) * args.step_px
        began = time.perf_counter()
        window.resizeDocks([dock], [start_width + offset], QtCore.Qt.Horizontal)
        app.processEvents()
        steps.append(time.perf_counter() - began)
        if args.interval_ms > 0:
            spin(args.interval_ms / 1000.0)
    drag_seconds = time.perf_counter() - drag_started
    during = dict(counts)
    spin(0.5)
    after = {key: counts[key] - during[key] for key in counts}

    triangles = derived.union.num_tri() if derived.union is not None else 0
    ms = [1000 * value for value in steps]
    result = {
        'platform': sys.platform,
        'qpa': app.platformName(),
        'viewport': interactor is not None,
        'deferred_renders': bool(getattr(interactor, 'defer_renders', False)),
        'render_interval_ms': (interactor._render_timer.interval()
                               if interactor is not None else None),
        'source': source,
        'triangles': triangles,
        'tab': args.tab,
        'steps': args.steps,
        'step_ms_mean': round(statistics.fmean(ms), 2),
        'step_ms_median': round(statistics.median(ms), 2),
        'step_ms_max': round(max(ms), 2),
        'drag_seconds': round(drag_seconds, 3),
        'renders_during_drag': during['renders'],
        'renders_after_drag': after['renders'],
        'viewport_paints_during_drag': during['viewport_paints'],
        'layer_images_during_drag': during['layer_images'],
        'idle_renders_per_s': idle['renders'],
        'idle_viewport_paints_per_s': idle['viewport_paints'],
    }
    if args.json:
        print(json.dumps(result))
    else:
        for key, value in result.items():
            print(f'{key:30} {value}')
    window.jobs.wait(5000)
    # The loaded model marks the document dirty; closing must not prompt.
    window.document.dirty = False
    window.close()


if __name__ == '__main__':
    main()
