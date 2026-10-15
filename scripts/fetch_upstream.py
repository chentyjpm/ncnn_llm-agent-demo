#!/usr/bin/env python3
"""Explicit network operation: fetch a pinned upstream source, never models.
Does not modify any existing checkout or build/install a dependency automatically.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.ci_native import read_pins, verify_pull_request_source, REVIEWED_PR_LOCAL_REF
ROOT=Path(__file__).resolve().parents[1]

def source_pin(repository, root=ROOT):
    pins = read_pins(root)
    key = {'ncnn_llm': 'ncnn_llm', 'qwenimage-ncnn-vulkan': 'qwenimage'}[repository]
    pin = pins[key]
    return {'url': 'https://github.com/' + pin['repository'], 'commit': pin['commit'],
            'checkout_ref': pin.get('checkout_ref', pin['commit'])}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('repository', choices=['ncnn_llm','qwenimage-ncnn-vulkan'])
    p.add_argument('--directory',required=True,help='A new, empty destination')
    args=p.parse_args()
    repo = source_pin(args.repository)
    target=Path(args.directory).resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit('Refusing to modify a nonempty directory')
    target.mkdir(parents=True,exist_ok=True)
    is_pr = repo['checkout_ref'] != repo['commit']
    # A PR ref may advance after review. Fetch its reachable history, prove that
    # the exact reviewed object is an ancestor, then checkout only that object.
    # Never fall back to an arbitrary fork or to the latest PR head.
    fetch = (['git','-C',str(target),'fetch','--no-tags','origin',
              '+' + repo['checkout_ref'] + ':' + REVIEWED_PR_LOCAL_REF] if is_pr else
             ['git','-C',str(target),'fetch','--no-tags','--depth','1','origin',repo['commit']])
    commands=[['git','init',str(target)],['git','-C',str(target),'remote','add','origin',repo['url']+'.git'], fetch]
    for cmd in commands:
        print('ARGV:',json.dumps(cmd),flush=True)
        subprocess.run(cmd,check=True,timeout=300,shell=False)
    if is_pr:
        head = verify_pull_request_source(target, {
            'repository': repo['url'].removeprefix('https://github.com/'), 'commit': repo['commit']})
        print('Verified official PR head contains pinned commit:',head)
    checkout = ['git','-C',str(target),'checkout','--detach',repo['commit']]
    print('ARGV:',json.dumps(checkout),flush=True)
    subprocess.run(checkout,check=True,timeout=30,shell=False)
    got=subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True,timeout=30).strip()
    if got != repo['commit']: raise SystemExit('Commit mismatch')
    print('Verified source commit:',got)
    print('Submodules/dependency binaries/model weights are NOT downloaded by this script.')
if __name__=='__main__': main()
