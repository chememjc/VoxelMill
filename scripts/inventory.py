#!/usr/bin/env python3
"""Full originals, one fresh subprocess per mesh for meaningful peak RSS."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
from voxelmill.contracts import ResourceBudget
from voxelmill.mesh import open_stl, inspect_mesh


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input-dir', default='inputstl')
    parser.add_argument('--output', default='fixtures/manifest.json')
    parser.add_argument('--worker')
    parser.add_argument('--memory-gib',type=float,default=32)
    args=parser.parse_args()
    if args.worker:
        started=time.monotonic()
        with open_stl(args.worker,ResourceBudget(memory_gib=args.memory_gib,workers=1)) as mesh:
            report=inspect_mesh(mesh)
        report['wall_seconds']=time.monotonic()-started
        report['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        report['source_size_bytes']=Path(args.worker).stat().st_size
        report['scratch_peak_bytes']=0
        print(json.dumps(report,allow_nan=False))
        return
    results=[]
    manifest={'schema_version':1,'generated_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'method':'all original triangles; exact-coordinate topology and exact double-double predicates; sequential isolated workers','memory_ceiling_gib':args.memory_gib,'meshes':results}
    for path in sorted(Path(args.input_dir).glob('*.stl')):
        print(f'Inspecting {path}',file=sys.stderr,flush=True)
        result=subprocess.run([sys.executable,__file__,'--worker',str(path),'--memory-gib',str(args.memory_gib)],capture_output=True,text=True)
        if result.returncode:
            results.append({'path':str(path),'status':'failed','error':result.stderr,'returncode':result.returncode})
        else:
            results.append({'status':'inspected',**json.loads(result.stdout)})
        Path(args.output).write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n')
        print(json.dumps({k:results[-1].get(k) for k in ('path','status','triangle_count','self_intersections','wall_seconds','peak_rss_bytes')}),file=sys.stderr,flush=True)
    if any(r['status']=='failed' for r in results):sys.exit(1)

if __name__=='__main__':main()
