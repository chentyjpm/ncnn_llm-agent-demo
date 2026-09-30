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


# Only the two models explicitly approved by the user. Other catalog models
# keep the existing answer-quality gate; never infer policy from a name suffix.
QUALITY_ADVISORY_MODELS = frozenset({
    'ncnn_qwen3_5_0_8b',
    'ncnn_qwen3_5_0_8b_int8',
})
EXPECTED_ANSWERS = 2


def runtime_answer_error(raw):
    """Transport/output integrity, deliberately separate from factual accuracy."""
    if not isinstance(raw, str):
        return 'Non-text model response'
    try:
        raw.encode('utf-8', errors='strict')
    except UnicodeError:
        return 'Invalid UTF-8 model response'
    if any(ord(c) < 32 and c not in '\t\r\n' for c in raw):
        return 'Control bytes in model response'
    if raw.lstrip().startswith('<think>') and '</think>' not in raw:
        return 'Incomplete reasoning block; no final answer'
    if not answer_text(raw):
        return 'Empty visible answer'
    return None


def evaluate_acceptance(report):
    """Return CI gates without altering raw responses or per-answer correctness.

    A warning-only quality gate cannot override an exception, missing response,
    encoding error, failed install/hash check or requested tool-contract failure.
    pipeline_completed is set only after the actual install/inference try block.
    """
    advisory = report.get('model') in QUALITY_ADVISORY_MODELS
    answers = report.get('answers', [])
    complete = len(answers) == EXPECTED_ANSWERS
    output_errors = [runtime_answer_error(a.get('raw')) for a in answers]
    runtime_ok = (report.get('pipeline_completed') is True and 'error' not in report
                  and complete and not any(output_errors))
    quality_correct = sum(a.get('passed') is True for a in answers)
    quality_ok = complete and quality_correct == EXPECTED_ANSWERS
    tool_required = report.get('image_intent_requested') is True
    tool_ok = not tool_required or report.get('image_intent', {}).get('passed') is True
    ok = runtime_ok and tool_ok and (advisory or quality_ok)
    if not runtime_ok:
        status = 'failed_runtime'
    elif not tool_ok:
        status = 'failed_tool_contract'
    elif not quality_ok:
        status = 'passed_with_quality_warnings' if advisory else 'failed_quality'
    else:
        status = 'passed'
    warnings = []
    if advisory and complete and not quality_ok:
        warnings.append('Answer-quality mismatch recorded; factual accuracy is advisory for this approved 0.8B model only.')
    return {
        'ok': ok,
        'acceptance_status': status,
        'acceptance_policy': {'version': 1, 'answer_quality_required': not advisory,
                              'scope': 'Explicit Qwen3.5 0.8B and INT8 allowlist only; runtime and tool gates stay mandatory.'},
        'runtime': {'passed': runtime_ok, 'expected_answers': EXPECTED_ANSWERS,
                    'received_answers': len(answers), 'output_errors': output_errors},
        'quality': {'passed': quality_ok, 'correct': quality_correct, 'evaluated': len(answers),
                    'expected': EXPECTED_ANSWERS,
                    'accuracy': quality_correct / len(answers) if answers else None,
                    'required_for_ci': not advisory},
        'tool_contract': {'required': tool_required, 'passed': tool_ok if tool_required else None},
        'quality_warnings': warnings,
    }


def ci_summary(report):
    quality = report['quality']
    return ('## Native model acceptance: ' + report['model'] + '\n\n'
            '| Check | Result |\n|---|---|\n'
            f"| Overall | {report['acceptance_status']} |\n"
            f"| Runtime / output integrity | {'passed' if report['runtime']['passed'] else 'failed'} |\n"
            f"| Answer quality | {quality['correct']}/{quality['evaluated']} correct; expected {quality['expected']} cases |\n"
            f"| Quality gate | {'required' if quality['required_for_ci'] else 'advisory (user-approved 0.8B models only)'} |\n"
            f"| Requested image-tool contract | {report['tool_contract']['passed'] if report['tool_contract']['required'] else 'not requested'} |\n\n"
            'Raw answers and individual `passed` flags remain unchanged in result.json. '
            'A successful runtime check does not certify answer accuracy or image generation.\n')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--archive',type=Path,required=True);ap.add_argument('--model',choices=list(NATIVE_MODELS),required=True)
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--image-intent',action='store_true');args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    report={'commit':os.getenv('GITHUB_SHA'),'run_id':os.getenv('GITHUB_RUN_ID'),'model':args.model,'provider':'sdu',
        'kind':'REAL_NATIVE_MODEL_INSTALL_AND_CPU','ok':False,'answers':[],'image_generation':'not_run',
        'pipeline_completed':False,'image_intent_requested':args.image_intent}
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
                print(json.dumps(report['answers'][-1],ensure_ascii=True),flush=True)
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
            report['pipeline_completed']=True
        except Exception as e:report['error']=f'{type(e).__name__}: {e}'
        finally:
            try:
                if backend:
                    rpc=backend.rpc;backend.release()
                    if rpc:report['engine_stderr']=rpc.stderr.decode('utf-8',errors='replace')
                if 'hub' in locals() and hub.status()['job']['status'] in BUSY:
                    hub.stop();until=time.monotonic()+65
                    while hub.status()['job']['status'] in BUSY and time.monotonic()<until:time.sleep(.5)
            except Exception as e:
                report['cleanup_error']=f'{type(e).__name__}: {e}'
                report.setdefault('error', report['cleanup_error'])
    report.update(evaluate_acceptance(report))
    report['elapsed_seconds']=round(time.monotonic()-started,3)
    # JSON escaping preserves invalid Unicode diagnostically; it does not repair
    # the answer or turn invalid UTF-8 into an accepted model output.
    (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2),flush=True)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f:f.write(ci_summary(report))
    if report['acceptance_status']=='passed_with_quality_warnings':
        print('::warning title=0.8B answer quality::Runtime passed, but answer-quality mismatches remain. See result.json; this is not a quality pass.',flush=True)
    return 0 if report['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
