#!/usr/bin/env python3
"""Sequential full-original placement evidence, without modifying any input."""
import json
import resource
import time
from pathlib import Path
from voxelmill.mesh import open_stl
from voxelmill.geometry import auto_placement
from voxelmill.config import resolve_settings
from voxelmill.contracts import VoxelMillError, ResourceBudget
from voxelmill.resources import execution_limits
from dataclasses import asdict

if __name__ == '__main__':
    settings = resolve_settings()
    result = {'method': 'sampled finite orientation search; every accepted bounds check uses all original triangles', 'meshes': []}
    with execution_limits(ResourceBudget(memory_gib=16, workers=2)):
        for path in sorted(Path('inputstl').glob('*.stl')):
            start = time.monotonic()
            print(path.name, flush=True)
            with open_stl(path) as mesh:
                record = {'source': str(path), 'sha256': mesh.asset.sha256,
                          'triangles': mesh.asset.triangle_count}
                try:
                    record.update(status='placed', placement=asdict(auto_placement(mesh.triangles, settings)))
                except VoxelMillError as error:
                    record.update(status='failed', error=error.to_dict())
            record.update(seconds=time.monotonic()-start,
                          peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)
            result['meshes'].append(record)
            Path('output/sample-placements.json').write_text(json.dumps(result, indent=2)+'\n')
            print(record['status'], round(record['seconds'], 2), flush=True)
