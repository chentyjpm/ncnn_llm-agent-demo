#!/usr/bin/env python3
"""Actually install the two added profiles then infer with the real ncnn bridge.
This CI-only smoke downloads ~4 GB of official weights total. No scripted backend.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_agent.model_hub import ModelHub,BUSY
from local_agent.backends import NcnnBridgeBackend


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    cases=[]
    for model,provider in [('qwen_coder05','modelscope'),('qwen15','huggingface')]:
        case={'model':model,'provider':provider,'ok':False};backend=None
        with tempfile.TemporaryDirectory(prefix='real-catalog-') as tmp:
            hub=ModelHub(Path(tmp),{'llm':str(a.binary.resolve()),'image':'not-needed'})
            try:
                q=hub.prepare(model,provider)
                case['offer']={k:v for k,v in q.items() if k!='ticket'}
                hub.start(q['ticket'],True);deadline=time.monotonic()+1200;phases=[]
                while hub.status()['job']['status'] in BUSY:
                    if time.monotonic()>deadline:
                        hub.stop();raise TimeoutError('Real model installation exceeded 20 minutes')
                    phase=hub.status()['job']['status']
                    if not phases or phases[-1]!=phase:phases.append(phase)
                    time.sleep(.25)
                case['job']=hub.status()['job'];case['phases_observed']=phases
                if case['job']['status']!='completed':raise RuntimeError(case['job'].get('error','Installation failed'))
                if hub.active().get('llm')!=model:raise AssertionError('Installed model was not activated')
                backend=NcnnBridgeBackend({'command':[str(a.binary.resolve())],'model':str(hub.models/model),
                    'device':'cpu','threads':4,'timeout':240,'max_new_tokens':64},ROOT)
                started=time.monotonic()
                reply=backend.complete([{'role':'system','content':'You are a helpful assistant.'},
                    {'role':'user','content':'What is 2 + 2? Reply with only the number.'}])
                case.update(reply=reply,inference_seconds=round(time.monotonic()-started,3))
                case['ok']=re.fullmatch(r'\s*4[.!]?\s*',reply) is not None
                case['ready']=json.loads((hub.models/model/'READY.json').read_text())
            except Exception as exc:case['error']=f'{type(exc).__name__}: {exc}'
            finally:
                if backend:backend.release()
                if hub.status()['job']['status'] in BUSY:
                    hub.stop()
                    end=time.monotonic()+45
                    while hub.status()['job']['status'] in BUSY and time.monotonic()<end:time.sleep(.25)
        cases.append(case)
        report={'scope':__doc__,'commit':os.getenv('GITHUB_SHA'),'run_id':os.getenv('GITHUB_RUN_ID'),
                'ok':len(cases)==2 and all(c['ok'] for c in cases),'cases':cases}
        (a.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(model,provider,case['ok'],case.get('reply'),case.get('error'),flush=True)
    return 0 if len(cases)==2 and all(c['ok'] for c in cases) else 1


if __name__=='__main__':raise SystemExit(main())
