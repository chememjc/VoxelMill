#!/usr/bin/env python3
"""Measure fresh versus reused layer indices; assert every preview pixel equal.

Run each mode in a fresh process with the same STL and default printer profile:
.venv/bin/python scripts/preview_benchmark.py inputstl/Latch_fat_finger.stl
"""
import argparse
import json
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time


def child(source, mode):
    import hashlib
    from voxelmill.contracts import CancellationToken, no_progress
    from voxelmill.gui.document import Document
    from voxelmill.gui import services
    doc = Document(source=source)
    token = CancellationToken()
    placed = services.load_and_place(doc, token, no_progress)
    try:
        model = services.build_model(doc, placed['placed'], placed['placement'], token, no_progress)
        supports = services.build_supports(doc, model['triangles'], model['bounds'], None, token, no_progress)
        union = services.assemble(model['solid'], supports['plan'], supports['raft'])
        import math
        maximum = math.ceil(union.bounds[1, 2] / doc.settings['process']['layer_height_mm']) - 1
        indices = [round(maximum * f) for f in [0, .25, .5, .75, 1, .5, .25, 0]]
        budget = services.budget_for(doc)
        slicer = services.LayerSlicer(union, doc.settings, budget)
        records = []
        for index in indices:
            start = time.perf_counter()
            payload = (slicer.slice(index, token) if mode == 'reused' else
                       services.slice_layer(union, doc.settings, index, budget, token))
            elapsed = time.perf_counter() - start
            records.append({'index': index, 'seconds': elapsed,
                            'mask_sha256': hashlib.sha256(payload['mask']).hexdigest(),
                            'filled_pixels': payload['filled_pixels'], 'open_rows': payload['open_rows']})
        return {'mode': mode, 'triangles': union.num_tri(), 'layers': records,
                'preview_seconds': sum(r['seconds'] for r in records),
                'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}
    finally:
        del placed['placed']
        shutil.rmtree(placed['scratch'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--child', choices=['fresh', 'reused'])
    args = parser.parse_args()
    if args.child:
        print(json.dumps(child(args.source, args.child)))
        return
    results = []
    for mode in ['fresh', 'reused']:
        completed = subprocess.run([sys.executable, __file__, str(args.source), '--child', mode],
                                   text=True, capture_output=True, check=True)
        results.append(json.loads(completed.stdout))
    def pixels(result):
        return [(r['index'], r['mask_sha256'], r['open_rows']) for r in result['layers']]
    assert pixels(results[0]) == pixels(results[1]), 'cached preview changed raster pixels'
    payload = {'source': str(args.source), 'results': results, 'all_pixels_equal': True,
               'speedup': results[0]['preview_seconds'] / results[1]['preview_seconds']}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
