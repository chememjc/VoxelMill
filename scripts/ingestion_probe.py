#!/usr/bin/env python3
"""Measure Tier 0 ingestion prerequisites without repair or printer traffic.

Run with .venv/bin/python scripts/ingestion_probe.py SOURCE --output REPORT.
The proposed two-sided interior filter is measured, never applied to a product
export. It uses the raw area as a conservative load estimate after filtering.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

import numpy as np

from voxelmill.config import resolve_settings
from voxelmill.geometry import placement_for_triangles, iter_transformed_triangles, triangle_bounds
from voxelmill.mesh import open_stl
from voxelmill.raster import MeshLayerStream
from voxelmill.supports import (build_column_field, downward_contacts, select_contacts,
                              route_contacts, contact_coverage, _thin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    settings = resolve_settings()
    started = time.monotonic()
    with open_stl(args.source) as mesh:
        source_hash = mesh.asset.sha256
        placement = placement_for_triangles(mesh.triangles, settings, lift_mm=5)
        triangles = np.concatenate(list(iter_transformed_triangles(
            mesh.triangles, np.asarray(placement.matrix)))).astype(np.float32)
    bounds = triangle_bounds(triangles)
    field = build_column_field(triangles, bounds, settings)
    contacts, selection = select_contacts(triangles, field, settings)
    plan, _ = route_contacts(contacts, field, settings)
    samples, area = downward_contacts(triangles, settings)
    keep = np.ones(len(samples), dtype=bool)
    for i, (x, y, z) in enumerate(samples):
        column, k = field.index_of(x, y), field.layer_of(z)
        if column is not None:
            keep[i] = not (field.blocked(column, k - 1, k) and
                           field.blocked(column, k + 1, k + 2))
    filtered = samples[keep]
    mandatory = [[*island['position_mm'], island['z_mm']] for island in field.islands]
    filtered_contacts = _thin(filtered, mandatory, settings['support']['spacing_mm'])
    filtered_plan, _ = route_contacts(filtered_contacts, field, settings)
    result = {'source': str(args.source), 'source_sha256': source_hash,
              'placement': asdict(placement), 'settings': settings,
              'field': field.metrics, 'selection': selection, 'routing': plan.metrics,
              'proposed_filter': {
                  'rule': 'drop a sample if its column is occupied at both k-1 and k+1',
                  'samples_dropped': int(np.count_nonzero(~keep)),
                  'coverage': contact_coverage(filtered, filtered_contacts, settings, area),
                  'load_area_basis': 'unfiltered downward area; conservative estimate only',
                  'routing': filtered_plan.metrics}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Save the support results before the longer full-pitch pass for restart.
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    stream = MeshLayerStream(triangles, bounds, settings)
    for layer in stream:
        if layer.index % 200 == 0:
            print(f'raster {layer.index}/{stream.layer_count}', flush=True)
    result['full_raster'] = {'layers': stream.layer_count, 'open_rows': stream.open_rows,
                            'open_layers': stream.open_layers,
                            'negative_winding_crossings': stream.negative_winding_crossings,
                            'filled_pixels': stream.filled_pixels}
    result['seconds'] = time.monotonic() - started
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(args.output)


if __name__ == '__main__':
    main()
