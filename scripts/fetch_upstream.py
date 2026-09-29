#!/usr/bin/env python3
"""Explicit network operation: fetch a pinned upstream source, never models.
Does not modify any existing checkout or build/install a dependency automatically.
"""
import argparse
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('repository', choices=['ncnn_llm','qwenimage-ncnn-vulkan'])
    p.add_argument('--directory',required=True,help='A new, empty destination')
    args=p.parse_args()
    lock=json.loads((ROOT/'upstream-lock.json').read_text())
    repo=next(r for r in lock['repositories'] if r['name'].split('/')[1]==args.repository)
    target=Path(args.directory).resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit('Refusing to modify a nonempty directory')
    target.mkdir(parents=True,exist_ok=True)
    commands=[['git','init',str(target)],['git','-C',str(target),'remote','add','origin',repo['url']+'.git'],
              ['git','-C',str(target),'fetch','--depth','1','origin',repo['commit']],
              ['git','-C',str(target),'checkout','--detach','FETCH_HEAD']]
    for cmd in commands:
        print('ARGV:',json.dumps(cmd),flush=True)
        subprocess.run(cmd,check=True,timeout=300)
    got=subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True).strip()
    if got != repo['commit']: raise SystemExit('Commit mismatch')
    print('Verified source commit:',got)
    print('Submodules/dependency binaries/model weights are NOT downloaded by this script.')
if __name__=='__main__': main()
