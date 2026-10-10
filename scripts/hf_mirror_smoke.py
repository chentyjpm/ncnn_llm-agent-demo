#!/usr/bin/env python3
"""Live HF direct/mirror route acceptance with real small files and one 34 MB tensor.

No complete model download/inference and no speed guarantee. No SDK, HF tokens,
system proxy changes, fabricated network replies or automatic route retries.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_agent.hf_download_pins import PINNED_HF
from local_agent.model_sources import resolve_manifest,file_url,open_https,digest,response_host,verify_mirror_manifest


def run(output: Path):
    output.mkdir(parents=True,exist_ok=True)
    report={'scope':__doc__,'ok':False,'commit':os.getenv('GITHUB_SHA','local'),
            'utc':datetime.now(timezone.utc).isoformat(),'cases':[]}
    for route in ('direct','hf_mirror'):
        for model in PINNED_HF:
            case={'route':route,'model':model,'ok':False,'files':[]};started=time.monotonic()
            try:
                m=resolve_manifest(model,'huggingface',download_route_id=route)
                # Verify the independent origin too. This is CI only; the installed
                # mirror route never requires a separate direct-origin request.
                verify_mirror_manifest(model,m['repository'],m['revision'],m['files'])
                case.update(revision=m['revision'],metadata_requested_host=m['metadata_requested_host'],
                            metadata_final_host=m['metadata_final_host'],manifest_matches_origin_pin=True)
                (output/(model+'-'+route+'-manifest.json')).write_text(json.dumps(m,indent=2),encoding='utf-8')
                wanted=('vae/decoder.ncnn.param','transformer/output.ncnn.bin') if model=='qwenimage21' else ('config.json',)
                with tempfile.TemporaryDirectory(prefix='hf-route-check-') as tmp:
                    for name in wanted:
                        item=next(f for f in m['files'] if f['name']==name)
                        if item['bytes']>40*1024**2:raise ValueError('Sample exceeds the explicit 40 MiB test limit')
                        url=file_url(m,item);p=Path(tmp)/'sample';n=0;t=time.monotonic()
                        with open_https(url,timeout=30) as r,p.open('wb') as f:
                            host=response_host(r,url)
                            while data:=r.read(256*1024):
                                n+=len(data)
                                if n>item['bytes'] or time.monotonic()-t>180:raise ValueError('Sample size/time limit exceeded')
                                f.write(data)
                        if n!=item['bytes'] or digest(p,item['algorithm'])!=item['digest']:raise ValueError('Sample checksum/size mismatch')
                        case['files'].append({'name':name,'requested_url':url,'final_host':host,'bytes':n,
                            'digest':item['digest'],'algorithm':item['algorithm'],'checksum_verified':True,
                            'seconds':round(time.monotonic()-t,3)})
                case['ok']=True
            except Exception as e:case['error']=type(e).__name__+': '+str(e)
            case['seconds']=round(time.monotonic()-started,3);report['cases'].append(case)
            (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(case,ensure_ascii=False),flush=True)
    report['ok']=all(c['ok'] for c in report['cases']) and len(report['cases'])==8
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        lines=['## HF download routes','Metadata + checksummed samples only; no inference or guaranteed acceleration.',
               '| Model | Route | Result | Metadata final host |','|---|---|---|---|']
        for c in report['cases']:lines.append(f"| {c['model']} | {c['route']} | {'passed' if c['ok'] else 'failed'} | {c.get('metadata_final_host','unavailable')} |")
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
    return 0 if report['ok'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'reports/hf-mirror-live')
    raise SystemExit(run(p.parse_args().output))
