"""Read-only recovery mapping from active CZI state and MAX manifests."""
import json,argparse
from pathlib import Path

def inside(root,p):
    p=p.resolve()
    if not p.is_relative_to(root.resolve()): raise ValueError('Path escapes project: '+str(p))
    return p

def plan(root):
    root=root.resolve()
    cfg=json.loads((root/'.masonjar/czi_import_config.json').read_text(encoding='utf-8-sig'))
    cfg=cfg.get('czi_import',cfg)
    state=json.loads((root/'.masonjar/czi_import_state.json').read_text(encoding='utf-8-sig'))
    runs=state.get('max_runs',{})
    if not runs: raise ValueError('No active MAX runs')
    configured={ch.get('role') for ch in cfg.get('channels',[])}
    branches={'signal_somata':'somata','signal_nuclei':'nuclei','signal_axons':'axons'}
    rows=[]; seen=set()
    for role,rel in runs.items():
        if role not in branches or role not in configured: raise ValueError('Unconfigured role: '+role)
        branch=branches[role]
        run=inside(root,root/'data/counting/03_max'/rel)
        expected=root/'data/counting/03_max'/branch/'max'
        if run.parent!=expected.resolve(): raise ValueError('Run/role mismatch')
        manifest=json.loads((run/'run_manifest.json').read_text(encoding='utf-8-sig'))
        if manifest.get('branch')!=branch: raise ValueError('Manifest branch mismatch')
        names=manifest.get('input_files',[])
        if not names: raise ValueError('Empty manifest')
        for name in names:
            if Path(name).name!=name or '/' in name or '\\' in name: raise ValueError('Invalid filename')
            source=inside(root,root/'data/original_scans'/branch/name)
            target=inside(root,run/(Path(name).stem+'.tif'))
            if not source.is_file() or not target.is_file(): raise ValueError('Missing input/output: '+name)
            key=str(target).lower()
            if key in seen: raise ValueError('Duplicate output')
            seen.add(key)
            rows.append({'source':str(source),'existing':str(target),'candidate_relative':str(target.relative_to(root)),'role':role})
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path)
    a=p.parse_args()
    print(json.dumps(plan(a.project),indent=2))
