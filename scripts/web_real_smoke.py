#!/usr/bin/env python3
"""Serve the actual ncnn backend and verify two real model replies through HTTP.
No fixture, browser intercept or canned response. Invoked after the 0.5B CPU CI.
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    root=args.output.resolve()
    config={'workspace':str(root/'workspace'),'llm':{'backend':'ncnn_bridge','command':[str(args.binary.resolve())],
        'model':str(args.model.resolve()),'vulkan':False,'threads':4,'timeout':180,'max_new_tokens':64},
        'python':{'mode':'disabled'},'mcp_servers':[],'commands':{},'image':{'enabled':False}}
    app=WebApp(config,root/'state')
    server=LocalServer(app,0)
    thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start()
    report={'scope':'REAL ncnn CPU model through the H5 HTTP API; not a browser/fixture test','ok':False,
            'device':'CPU','binary_sha256':hashlib.sha256(args.binary.read_bytes()).hexdigest(),'cases':[]}
    started=time.monotonic()
    def call(path,method='GET',data=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=30)
        headers={'X-Agent-Token':app.token,'Content-Type':'application/json'}
        try:
            c.request(method,path,body=json.dumps(data) if data is not None else None,headers=headers)
            r=c.getresponse();b=r.read();assert r.status in (200,201,202),(r.status,b)
            return json.loads(b)
        finally:c.close()
    try:
        assert app.factory is None and app.info()['ready']
        sid=call('/api/sessions','POST',{})['id']
        for prompt in ['What is 2 + 2? Reply with only the number.',
                       'What number did you just answer? Reply with only that number.']:
            run=call(f'/api/sessions/{sid}/messages','POST',{'message':prompt,'mode':'chat','max_new_tokens':64})['run_id']
            deadline=time.monotonic()+200;cursor=0
            while True:
                if time.monotonic()>deadline:raise TimeoutError('HTTP model test deadline exceeded')
                result=call(f'/api/runs/{run}/events?after={cursor}');cursor=result['cursor']
                if result['status'] in ('completed','failed','cancelled'):break
            session=call('/api/sessions/'+sid);reply=session['messages'][-1]
            passed=reply['state']=='completed' and re.fullmatch(r'\s*4[.!]?\s*',reply['content']) is not None
            report['cases'].append({'prompt':prompt,'reply':reply['content'],'state':reply['state'],'passed':passed})
            print(json.dumps(report['cases'][-1],ensure_ascii=False),flush=True)
        report['ok']=all(c['passed'] for c in report['cases']) and len(report['cases'])==2
    except Exception as exc:report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        for rid in list(app.runs):app.cancel(rid)
        server.shutdown();server.server_close();thread.join(3)
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        (root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
