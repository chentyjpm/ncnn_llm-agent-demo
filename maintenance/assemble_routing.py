"""One-time reviewed branch assembly; never distributed or run by end users."""
import hashlib
import json
from pathlib import Path
import subprocess

BASE='2368f243cb54b7d98b9da1fdb706399254726f0e'
expected=json.loads(Path('maintenance/routing-hashes.json').read_text())
# Existing changed files must still be byte-identical to the reviewed baseline.
for path in expected:
    old=subprocess.run(['git','show',BASE+':'+path],capture_output=True)
    if old.returncode==0 and old.stdout!=Path(path).read_bytes():
        raise ValueError('Base file changed before patch: '+path)
for part in ('backend','frontend','docs-ci'):
    subprocess.run(['git','apply','--unidiff-zero','--whitespace=fix','maintenance/routing-'+part+'.patch'],check=True)
items={}
paths=sorted(Path('.inventory').glob('*.json'))
assert len(paths)==10,paths
for path in paths:
    d=json.loads(path.read_text(encoding='utf-8'));assert d['ok']
    folder=d['model_id'];mid='ncnn_'+folder.replace('.','_')
    e={k:d[k] for k in ('repository','revision','files','download_bytes','expected_type')}
    e.update(source_run_id=36721194429,source_commit='2a35ee8c0189d8c40f8e633293906270e238d0b4',
        precision='int8' if folder.endswith('_int8') else 'native',
        text_only=d['config'].get('setting',{}).get('vision',{}).get('type','close')!='close',config=d['config'])
    items[mid]=e
header='''"""Reviewed native ncnn bundles; real-byte inventory 36721194429.
Generated data, not an inference result. Immutable per-file SHA-256 protects
mutable upstream URLs; do not refresh without reviewing a new inventory.
"""
import json

NATIVE_MODELS = json.loads(r\'\'\'
'''
Path('local_agent/native_catalog.py').write_text(header+json.dumps(items,ensure_ascii=False,separators=(',',':'))+"\n''')\n",encoding='utf-8')
checks={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in expected}
errors={p:{'expected':expected[p],'actual':checks[p]} for p in expected if expected[p]!=checks[p]}
Path('assembly-checks.json').write_text(json.dumps({'ok':not errors,'checks':checks,'errors':errors},indent=2)+'\n')
if errors:raise ValueError(errors)
# Stage exactly the approved feature files. Remove the one-time branch helpers
# before exporting the clean tree; do not write main here.
subprocess.run(['git','add','--',*expected],check=True)
subprocess.run(['git','rm','-r','--','maintenance','.github/workflows/assemble-routing.yml',
                '.github/workflows/native-model-inventory.yml','scripts/inventory_native_models.py'],check=True)
