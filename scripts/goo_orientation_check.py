#!/usr/bin/env python3
"""Compare decoded reference-GOO layers with our own raster of the source STL.

The reference file was produced by UVtools for this printer from
``right_temporal_bone_mars5_oriented.stl``. Its supports and Z placement are not
ours, so the model cross-section is matched by shape: for each candidate flip we
align centroids and report the intersection over union. Only the flip that wins
by a wide margin is evidence about the printer's pixel orientation.
"""
import argparse
import json
import sys

import numpy as np

from voxelmill import _native
from voxelmill.goo import GooReader
from voxelmill.mesh import open_stl


def shift_to_match(a, b):
    """Translate ``b`` so its centroid lands on ``a``'s, then compare."""
    if not a.any() or not b.any():
        return 0.0
    rows_a, cols_a = np.nonzero(a)
    rows_b, cols_b = np.nonzero(b)
    dr = int(round(rows_a.mean() - rows_b.mean()))
    dc = int(round(cols_a.mean() - cols_b.mean()))
    moved = np.zeros_like(b)
    src_r = slice(max(0, -dr), b.shape[0] - max(0, dr))
    dst_r = slice(max(0, dr), b.shape[0] - max(0, -dr))
    src_c = slice(max(0, -dc), b.shape[1] - max(0, dc))
    dst_c = slice(max(0, dc), b.shape[1] - max(0, -dc))
    moved[dst_r, dst_c] = b[src_r, src_c]
    union = np.count_nonzero(a | moved)
    return float(np.count_nonzero(a & moved) / union) if union else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('goo')
    parser.add_argument('stl')
    parser.add_argument('--layers', type=int, nargs='*', default=None)
    parser.add_argument('--z-search', type=float, default=2.0,
                        help='mm of Z offset to search around the naive alignment')
    parser.add_argument('--z-step', type=float, default=0.05,
                        help='Z search step in mm; the default is one printer layer')
    parser.add_argument('--output', help='write the full result as JSON here as well')
    parser.add_argument('--verbose', action='store_true', help='slice progress on stderr')
    args = parser.parse_args()
    results = []
    with GooReader(args.goo) as goo, open_stl(args.stl) as mesh:
        height, width = goo.shape
        pitch = goo.header['display_width'] / width
        bounds = np.asarray(mesh.asset.bounds, dtype=float)
        top_goo = goo.layers[-1].values['position_z']
        chosen = args.layers or [int(len(goo.layers) * f) for f in (0.4, 0.55, 0.7, 0.85)]
        references = {index: goo.decode(index) > 0 for index in chosen}

        # One Rasterizer for the whole run. raster.cpp enforces nondecreasing Z
        # within a single instance, so every (layer, offset) pair is generated
        # up front and visited in ascending Z; rebuilding it per offset costs a
        # full active-set construction over six million triangles each time.
        plan = []
        for index in chosen:
            z_goo = goo.layers[index].values['position_z']
            for offset in np.arange(-args.z_search, args.z_search + 1e-9, args.z_step):
                # The model's own top is the only shared landmark: assume the
                # model top coincides with the printed top and search a small
                # Z window around it.
                z_model = bounds[1][2] - (top_goo - z_goo) + offset
                if bounds[0][2] <= z_model <= bounds[1][2]:
                    plan.append((float(z_model), index, float(z_goo)))
        plan.sort()
        best = {}
        raster = _native.Rasterizer(mesh.triangles)
        for position, (z_model, index, z_goo) in enumerate(plan):
            mine = raster.slice(z_model, width, height,
                                -goo.header['display_width'] / 2,
                                -goo.header['display_height'] / 2,
                                pitch, pitch, None, 'nonzero')['mask'] > 0
            for name, candidate in (('identity', mine), ('flip_x', mine[:, ::-1]),
                                    ('flip_y', mine[::-1, :]), ('flip_xy', mine[::-1, ::-1])):
                score = shift_to_match(references[index], candidate)
                if index not in best or score > best[index]['iou']:
                    best[index] = {'flip': name, 'iou': score, 'z_model_mm': z_model,
                                   'z_goo_mm': z_goo,
                                   # Distinguishes "the shapes differ" from "we
                                   # sliced empty space", which look the same in
                                   # an IoU alone.
                                   'candidate_pixels': int(mine.sum())}
            if args.verbose and position % 20 == 0:
                print(f'  {position + 1}/{len(plan)} slices', file=sys.stderr, flush=True)
        for index in chosen:
            results.append({'layer': index, 'reference_pixels': int(references[index].sum()),
                            **best.get(index, {})})
            print(json.dumps(results[-1]), flush=True)
    ranked = {}
    for entry in results:
        ranked.setdefault(entry.get('flip'), []).append(entry['iou'])
    conclusive = (len(set(entry.get('flip') for entry in results)) == 1
                  and min(entry.get('iou', 0.0) for entry in results) >= 0.6)
    summary = {flip: round(float(np.mean(scores)), 4) for flip, scores in ranked.items()}
    with GooReader(args.goo) as goo:
        header = goo.header
    payload = {
        'goo': args.goo, 'stl': args.stl,
        'software': header['software_name'], 'machine': header['machine_name'],
        'mirror_x_flag': int(header['mirror_x']), 'mirror_y_flag': int(header['mirror_y']),
        'per_layer': results,
        'best_flip_by_layer': {str(entry['layer']): entry.get('flip') for entry in results},
        'mean_iou_by_flip': summary,
        'conclusive': conclusive,
        'conclusion': ('every sampled layer agrees on one flip with a strong overlap'
                       if conclusive else
                       'inconclusive: the sampled layers do not agree on one flip, or the '
                       'best overlap is too weak to be a shape match. The slicer posed the '
                       'model itself, so a Z window around a shared top is not enough to '
                       'recover that pose'),
        'establishes': 'which axis flip of our own raster best matches the stored pixels, '
                       'with centroids aligned; it is shape evidence, not a pose recovery',
        'does_not_establish': 'the physical orientation on the LCD, or that a firmware honours '
                              'the mirror flags it is given',
    }
    print(json.dumps({k: payload[k] for k in ('software', 'mirror_x_flag', 'mirror_y_flag',
                                              'best_flip_by_layer', 'mean_iou_by_flip',
                                              'conclusive', 'conclusion')}, indent=2))
    if args.output:
        from pathlib import Path
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(payload, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
