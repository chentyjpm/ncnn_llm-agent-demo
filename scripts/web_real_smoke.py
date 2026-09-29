#!/usr/bin/env python3
"""Real ncnn through HTTP: explicit CPU or auto fallback with unavailable Vulkan.

Observes inputs but never changes model replies or injects a substitute backend.
"""
from __future__ import annotations
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import re
import sys
import threading
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_agent.web import LocalServer,WebApp
from local_agent.backends import NcnnBridgeBackend


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--device',choices=['cpu','auto'],default='cpu')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    root=args.output.resolve()
    config={'workspace':str(root/'workspace'),'llm':{'backend':'ncnn_bridge','command':[str(args.binary.resolve())],
        'model':str(args.model.resolve()),'device':args.device,'threads':4,'timeout':180,'max_new_tokens':64},
        'python':{'mode':'disabled'},'mcp_servers':[],'commands':{},'image':{'enabled':False}}
    app=WebApp(config,root/'state')
    server=LocalServer(app,0)
    thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start()
    report={'scope':'REAL ncnn CPU through H5 HTTP; no fixture model','ok':False,
            'device_policy':args.device,'binary_sha256':hashlib.sha256(args.binary.read_bytes()).hexdigest(),'cases':[]}
    started=time.monotonic()
    observed=[]
    original_complete=NcnnBridgeBackend.complete
    def observe_complete(backend,messages):
        observed.append(json.loads(json.dumps(messages)))
        return original_complete(backend,messages)
    NcnnBridgeBackend.complete=observe_complete
    def call(path,method='GET',data=None):
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=30)
        try:
            connection.request(method,path,body=json.dumps(data) if data is not None else None,
                headers={'X-Agent-Token':app.token,'Content-Type':'application/json'})
            response=connection.getresponse();body=response.read()
            if response.status not in (200,201,202): raise RuntimeError((response.status,body))
            return json.loads(body)
        finally: connection.close()
    try:
        if app.factory is not None or not app.info()['ready']: raise RuntimeError('Actual model not ready')
        report['device_selection']=app.info()['devices']['llm']
        if args.device=='auto' and report['device_selection']['selected']!='cpu':
            raise RuntimeError('This CI case requires absent Vulkan and actual CPU fallback')
        sid=call('/api/sessions','POST',{})['id']
        for prompt in ['What is 2 + 2? Reply with only the number.',
                       'Repeat your previous assistant answer verbatim. Output only that text.']:
            run=call(f'/api/sessions/{sid}/messages','POST',{'message':prompt,'mode':'chat','max_new_tokens':64})['run_id']
            deadline=time.monotonic()+200;cursor=0
            while True:
                if time.monotonic()>deadline: raise TimeoutError('HTTP model test deadline exceeded')
                result=call(f'/api/runs/{run}/events?after={cursor}');cursor=result['cursor']
                if result['status'] in ('completed','failed','cancelled'): break
            reply=call('/api/sessions/'+sid)['messages'][-1]
            passed=reply['state']=='completed' and re.fullmatch(r'\s*4[.!]?\s*',reply['content']) is not None
            report['cases'].append({'prompt':prompt,'reply':reply['content'],'state':reply['state'],'passed':passed,
                                     'actual_device':reply.get('device_selection')})
            print(json.dumps(report['cases'][-1],ensure_ascii=False),flush=True)
        report['observed_backend_inputs']=observed
        report['history_verified']=(len(observed)==2 and
            [m['role'] for m in observed[1]]==['system','user','assistant','user'] and
            observed[1][1]['content']==report['cases'][0]['prompt'] and
            observed[1][2]['content']==report['cases'][0]['reply'])
        report['actual_cpu_verified']=all(c.get('actual_device',{}).get('selected')=='cpu' for c in report['cases'])
        report['ok']=len(report['cases'])==2 and all(c['passed'] for c in report['cases']) and report['history_verified'] and report['actual_cpu_verified']
    except Exception as exc: report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        for rid in list(app.runs): app.cancel(rid)
        server.shutdown();server.server_close();thread.join(3)
        NcnnBridgeBackend.complete=original_complete
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        (root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__=='__main__': raise SystemExit(main())
