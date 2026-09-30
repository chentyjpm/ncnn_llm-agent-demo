#!/usr/bin/env python3
"""Actual Qwen Image through HTTP + explicit approval + persisted image card.
Requires real engine/weights already built and hash-verified in this CI run.
No text model, canned picture, browser mock, or image-quality assertion.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_agent.web import WebApp,LocalServer
from scripts.qwenimage_real_test import validate_image,check_device,verify_native,validate_manifest,verified_file,model_file,Samples


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--binary',type=Path,required=True);ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--device',choices=['cpu','vulkan'],default='cpu');args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);root=args.output.resolve()
    report={'kind':'REAL_QWEN_IMAGE_HTTP_APPROVAL','commit':os.getenv('GITHUB_SHA'),'run_id':os.getenv('GITHUB_RUN_ID'),'ok':False,'llm':'not_required','quality':'not_evaluated'}
    started=time.monotonic();server=None;app=None
    def call(path,method='GET',data=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=30)
        try:
            c.request(method,path,body=json.dumps(data) if data is not None else None,headers={'X-Agent-Token':app.token,'Content-Type':'application/json'})
            r=c.getresponse();body=r.read();value=json.loads(body)
            if r.status not in (200,201,202):raise RuntimeError(f'HTTP {r.status}: {value}')
            return value
        finally:c.close()
    try:
        report['native']=verify_native(args.binary,ROOT/'reports/ci/image/status.json')
        m=json.loads((ROOT/'reports/qwenimage-real/model-manifest.json').read_text());validate_manifest(m)
        for f in m['files']:
            if not verified_file(model_file(args.model,f['name']),f):raise ValueError('Unverified model file')
        cfg={'workspace':str(root/'workspace'),'llm':{'backend':'ncnn_bridge','command':['missing-text-model'],'model':str(root/'missing')},
             'image':{'enabled':True,'command':[str(args.binary.resolve())],'model':str(args.model.resolve()),'device':args.device,'timeout':2400},
             'python':{'mode':'disabled'},'commands':{},'mcp_servers':[]}
        app=WebApp(cfg,root/'state');server=LocalServer(app,0)
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start()
        assert app.factory is None and not app.info()['ready'] and app.info()['image_ready']
        sid=call('/api/sessions','POST',{})['id']
        payload={'mode':'image','message':'A red wooden cube on a white table, studio photograph.',
                 'image_options':{'width':256,'height':256,'steps':2,'seed':42}}
        run=call(f'/api/sessions/{sid}/messages','POST',payload)['run_id'];deadline=time.monotonic()+2500;approved=False;cursor=0;events=[]
        with Samples(root/'resources.jsonl'):
            while True:
                if time.monotonic()>deadline:raise TimeoutError('HTTP image acceptance deadline')
                result=call(f'/api/runs/{run}/events?after={cursor}');cursor=result['cursor'];events+=result['events']
                for e in result['events']:
                    if e['event']=='approval_required':
                        approval=e['approval'];assert approval['tool']=='images.generate' and not approved
                        assert not app.ws(sid).path(approval['arguments']['output']).exists()
                        call(f'/api/runs/{run}/approve','POST',{'approval_id':approval['id'],'allow':True});approved=True
                if result['status'] in ('completed','failed','cancelled'):break
        msg=call('/api/sessions/'+sid)['messages'][-1];report['message']=msg
        if not approved or result['status']!='completed' or not msg['artifacts']:raise ValueError('HTTP image route did not complete')
        check_device(msg['device_selection'],args.device)
        if any(e['event']=='model_start' for e in events):raise ValueError('Unexpected text model inference')
        path=app.ws(sid).path(msg['artifacts'][0]['path'])
        report['decoded']=validate_image(path,256,256);(root/'generated.png').write_bytes(path.read_bytes());report['ok']=True
        report['events']=events
    except Exception as e:report['error']=f'{type(e).__name__}: {e}'
    finally:
        if app:
            for rid in list(app.runs):app.cancel(rid)
        if server:server.shutdown();server.server_close()
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        (root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
