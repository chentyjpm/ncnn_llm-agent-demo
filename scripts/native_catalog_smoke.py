#!/usr/bin/env python3
"""Real ModelHub installation and ncnn CPU generation, not fixture inference.
Optional image-intent check records real text-model JSON; no image tool executes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_agent.native_catalog import NATIVE_MODELS
from local_agent.model_hub import ModelHub,BUSY
from local_agent.backends import NcnnBridgeBackend
from local_agent.agent import system_prompt,parse_action
from local_agent.tools import make_registry,Registry
from local_agent.images import ImageRunner
from local_agent.paths import Workspace


def restore(archive,folder):
    folder.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive,'r:gz') as tar:
        for name in ('ncnn_agent_bridge','ncnn_device_probe','BUILD_INFO.json'):
            member=tar.getmember('bridge/'+name)
            if not member.isfile() or not 0<member.size<200*1024**2:raise ValueError('Invalid executable archive member')
            with tar.extractfile(member) as f:(folder/name).write_bytes(f.read())
            (folder/name).chmod(0o755 if name!='BUILD_INFO.json' else 0o644)
    info=json.loads((folder/'BUILD_INFO.json').read_text())
    if info.get('commit')!=os.getenv('GITHUB_SHA') or str(info.get('run_id'))!=os.getenv('GITHUB_RUN_ID'):
        raise ValueError('Engine artifact comes from another commit/run')
    for name,key in [('ncnn_agent_bridge','binary_sha256'),('ncnn_device_probe','probe_sha256')]:
        if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=info.get(key):raise ValueError('Executable hash mismatch')
    for stage in ('configure','build','smoke'):
        if info['stages'][stage]['status']!='passed':raise ValueError('Engine stage did not pass')
    return folder/'ncnn_agent_bridge',info


def answer_text(text):
    # Preserve raw text. No lookup/correction, only remove a closed reasoning block.
    return re.sub(r'^\s*<think>.*?</think>\s*','',text,flags=re.S).strip()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive',type=Path,required=True);ap.add_argument('--model',choices=list(NATIVE_MODELS),required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--image-intent',action='store_true');args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    report={'commit':os.getenv('GITHUB_SHA'),'run_id':os.getenv('GITHUB_RUN_ID'),'model':args.model,'provider':'sdu',
        'kind':'REAL_NATIVE_MODEL_INSTALL_AND_CPU','ok':False,'answers':[],'image_generation':'not_run'}
    backend=None;started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='native-model-') as tmp:
        root=Path(tmp)
        try:
            binary,info=restore(args.archive,root/'engine');report['engine']=info
            hub=ModelHub(root/'home',{'llm':str(binary),'image':'not-needed'})
            q=hub.prepare(args.model,'sdu');report['offer']={k:v for k,v in q.items() if k!='ticket'}
            hub.start(q['ticket'],True);deadline=time.monotonic()+1800;last=0
            while hub.status()['job']['status'] in BUSY:
                if time.monotonic()>deadline:hub.stop();raise TimeoutError('Native model install timeout')
                if time.monotonic()-last>20:print(hub.status()['job'],flush=True);last=time.monotonic()
                time.sleep(.5)
            report['installation']=hub.status()['job']
            if report['installation']['status']!='completed':raise RuntimeError(report['installation'].get('error'))
            if hub.active().get('llm')!=args.model:raise ValueError('Model not activated')
            report['manifest']=json.loads((hub.models/args.model/'READY.json').read_text())
            cfg={'command':[str(binary)],'model':str(hub.models/args.model),'device':'cpu','threads':4,'timeout':180,'max_new_tokens':384}
            backend=NcnnBridgeBackend(cfg,ROOT)
            for prompt,pattern in [('What is 2 + 2? Reply with only the number. /no_think',r'4[.!]?'),
                                   ('中国的首都是哪里？只回答城市名称。 /no_think',r'北京[。！!]?')]:
                t=time.monotonic();raw=backend.complete([{'role':'system','content':'You are a helpful assistant. Answer directly and briefly. /no_think'}, {'role':'user','content':prompt}])
                report['answers'].append({'prompt':prompt,'raw':raw,'visible':answer_text(raw),
                    'passed':re.fullmatch(pattern,answer_text(raw)) is not None,'seconds':round(time.monotonic()-t,3)})
                print(json.dumps(report['answers'][-1],ensure_ascii=False),flush=True)
            if args.image_intent:
                # Real model sees the application's tool schema; no approval or
                # image execution occurs in this narrower intent test.
                cfg['max_new_tokens']=512
                registry=Registry();ws=Workspace(root/'intent-ws')
                registry.add(make_registry(ws,image_runner=ImageRunner(ws,{'enabled':True})).tools['images.generate'])
                task='请实际生成一张白色桌面上红色方块的图片。只返回一次 images.generate 工具动作，输出路径使用 images/cube.png。不要只写文案。'
                raw=backend.complete([{'role':'system','content':system_prompt(registry.schemas())},{'role':'user','content':task}])
                intent={'raw':raw,'passed':False,'scope':'Real text-model tool selection only; image execution not run here'}
                try:
                    action=parse_action(raw)
                    intent['passed']=action.get('tool')=='images.generate' and isinstance(action.get('arguments',{}).get('prompt'),str) and action['arguments'].get('output')=='images/cube.png'
                except ValueError as e:intent['error']=str(e)
                report['image_intent']=intent
            report['ok']=all(c['passed'] for c in report['answers']) and len(report['answers'])==2 and report.get('image_intent',{}).get('passed',True)
        except Exception as e:report['error']=f'{type(e).__name__}: {e}'
        finally:
            if backend:
                rpc=backend.rpc;backend.release()
                if rpc:report['engine_stderr']=rpc.stderr.decode('utf-8',errors='replace')
            if 'hub' in locals() and hub.status()['job']['status'] in BUSY:
                hub.stop();until=time.monotonic()+65
                while hub.status()['job']['status'] in BUSY and time.monotonic()<until:time.sleep(.5)
    report['elapsed_seconds']=round(time.monotonic()-started,3)
    (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
