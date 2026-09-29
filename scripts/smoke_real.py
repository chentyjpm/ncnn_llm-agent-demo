#!/usr/bin/env python3
"""Opt-in REAL installed-model smoke test. Never substitutes fake weights/backends.
Usage: python scripts/smoke_real.py --config configs/local.json [--image] [--trust-mcp]
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
import uuid
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_agent.agent import Agent, Audit
from local_agent.backends import NcnnCLIBackend, NcnnBridgeBackend
from local_agent.cli import load_config, make_runtime, doctor
from local_agent.images import ImageRunner

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True)
    p.add_argument('--workspace')
    p.add_argument('--image',action='store_true')
    p.add_argument('--trust-mcp',action='store_true')
    p.add_argument('--allow-commands',action='store_true')
    p.add_argument('--allow-unsafe-host-python',action='store_true')
    args=p.parse_args()
    report={'test_kind':'REAL_MODEL_SMOKE_TEST','ok':False,'llm':{'status':'not_run'},'image':{'status':'not_requested'}}
    clients=[]; start=time.monotonic()
    try:
        config=load_config(args.config)
        report['environment']=doctor(config)
        ws,registry,clients=make_runtime(config,args)
        kind=config['llm']['backend']
        classes={'ncnn_cli':NcnnCLIBackend,'ncnn_bridge':NcnnBridgeBackend}
        if kind not in classes:
            raise ValueError('Only real ncnn backends are accepted')
        backend=classes[kind](config['llm'],ROOT)
        name='real_smoke_'+uuid.uuid4().hex[:12]+'.json'
        audit_path=ROOT/'reports/runs'/('real-'+uuid.uuid4().hex+'.jsonl')
        result=Agent(backend,registry,max_steps=8,audit=Audit(audit_path)).run(
            'Use files.write to create '+name+' with UTF-8 content exactly {"product":42}. '
            'Then use files.read to verify it. Do not run Python. Finally report completion as a final JSON action.')
        check=ws.path(name).is_file() and json.loads(ws.read(name))=={'product':42}
        report['llm']={'status':'passed' if result['ok'] and check else 'failed',
                       'file_verified':check,'agent_result':result,'audit_log':str(audit_path)}
        if args.image:
            name='real_image_'+uuid.uuid4().hex[:12]+'.png'
            image_result=ImageRunner(ws,config['image']).run(
                prompt='A simple red wooden cube on a white table, studio photo.',
                output=name,width=512,height=512,steps=40,seed=42)
            good=image_result['returncode']==0 and image_result['file_created'] and not image_result['timed_out']
            report['image']={'status':'passed' if good else 'failed','result':image_result,
                             'quality_note':'Only execution/file presence is checked; image quality is not automatically evaluated.'}
        report['ok']=report['llm']['status']=='passed' and report['image']['status'] in ('passed','not_requested')
    except Exception as e:
        report['error']=f'{type(e).__name__}: {e}'
    finally:
        for c in clients: c.close()
        report['elapsed_seconds']=round(time.monotonic()-start,3)
        out=ROOT/'reports/real-smoke-result.json'
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['ok'] else 1
if __name__=='__main__': raise SystemExit(main())
