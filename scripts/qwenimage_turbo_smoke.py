#!/usr/bin/env python3
"""Real Turbo checkpoint smoke: shared weights + six Turbo files, eight steps.
No synthetic weights or images. A natural-language HTTP chat request routes through
WebApp approval and the application ImageRunner, without a text-model backend.
Decoding proves execution integrity, NOT semantic quality or a speedup benchmark.
"""
from __future__ import annotations
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from urllib.parse import urlencode
import uuid
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_agent.image_profiles import BASE_IMAGE,TURBO_IMAGE,TURBO_FILES,shared_components
from local_agent.images import ImageRunner
from local_agent.web import LocalServer, WebApp, TERMINAL
from local_agent.model_sources import resolve_manifest,file_url,open_https,verify_mirror_manifest,response_host
from scripts.qwenimage_real_test import (Samples,validate_image,check_device,verify_native,
                                        identity,save,resource_report,model_file,verified_file)


def prepare_models(root: Path, route: str, output: Path, *, open_file=None):
    """Keep all shared components and Turbo; omit only unused base Transformer."""
    root.mkdir(parents=True,exist_ok=True)
    manifests={mid:resolve_manifest(mid,'huggingface',download_route_id=route) for mid in (BASE_IMAGE,TURBO_IMAGE)}
    for mid,m in manifests.items():verify_mirror_manifest(mid,m['repository'],m['revision'],m['files'])
    selected={mid:[f for f in m['files'] if mid==TURBO_IMAGE or not f['name'].startswith('transformer/')]
              for mid,m in manifests.items()}
    if {f['name'] for f in selected[TURBO_IMAGE]}!=TURBO_FILES:raise ValueError('Missing Turbo files')
    total=sum(f['bytes'] for fs in selected.values() for f in fs)
    save(output/'model-manifests.json',manifests)
    resources=resource_report(root);resources.update(required_download_bytes=total,route=route)
    save(output/'resources-before.json',resources)
    if resources['disk_free']<total+2*1024**3:raise RuntimeError('Insufficient disk for reviewed Turbo/shared weights')
    if total>60*1024**3:raise ValueError('Weight size exceeds test download budget')
    opener=open_file or open_https
    started=time.monotonic();records=[];count=0
    for mid,fs in selected.items():
        for f in fs:
            p=model_file(root/mid,f['name']);p.parent.mkdir(parents=True,exist_ok=True)
            final_host=None;cached=verified_file(p,f)
            if not cached:
                tmp=model_file(root/mid,f['name']+'.part');n=0;url=file_url(manifests[mid],f)
                try:
                    with opener(url,timeout=90) as response,tmp.open('wb') as stream:
                        final_host=response_host(response,url)
                        print(f'Download {mid}/{f["name"]}: {f["bytes"]} bytes from {final_host}',flush=True)
                        while block:=response.read(4*1024**2):
                            if time.monotonic()-started>2700:raise TimeoutError('Turbo weight download exceeded 45 minutes')
                            n+=len(block)
                            if n>f['bytes']:raise ValueError('Download exceeds manifest size')
                            stream.write(block)
                            if n//(256*1024**2)!=(n-len(block))//(256*1024**2):print(f'{count+n}/{total} bytes received',flush=True)
                    if not verified_file(tmp,f):raise ValueError('Weight checksum/length mismatch: '+mid+'/'+f['name'])
                    os.replace(tmp,p)
                finally:tmp.unlink(missing_ok=True)
            count+=f['bytes'];records.append(dict(model=mid,name=f['name'],bytes=f['bytes'],digest=f['digest'],verified=True,cached=cached,final_host=final_host))
            save(output/'download-progress.json',{'verified_bytes':count,'total_bytes':total,'files':records})
    dependency=shared_components(root/BASE_IMAGE,verify_hashes=True)
    save(output/'download-result.json',{**identity(),'ok':True,'files':records,'dependency':dependency,
        'download_bytes':total,'seconds':round(time.monotonic()-started,3),
        'scope':'Full Turbo and all shared components. Unused base Transformer omitted; not a base-model installation test.'})
    return manifests


PROMPT = 'Draw a red wooden cube on a white table, studio photograph.'


class LocalClient:
    """Use the public HTTP contract; never skip authentication or approvals."""
    def __init__(self, port):
        self.port = port
        self.token = None

    def request(self, path, method='GET', data=None, *, auth=True, expected=200):
        headers = {'X-Agent-Token': self.token} if auth and self.token else {}
        body = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(data)
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read(20 * 1024**2 + 1)
            if len(raw) > 20 * 1024**2:
                raise ValueError('Acceptance HTTP response exceeds bounded size')
            value = json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json') else raw
            if response.status != expected:
                raise RuntimeError(f'HTTP {method} {path}: expected {expected}, got {response.status}: {value!r}')
            return value, dict(response.headers)
        finally:
            conn.close()


def validate_automatic_trace(message, events, approval):
    """Reject an explicit-mode shortcut, bypassed approval, or text LLM route."""
    if (message.get('mode'), message.get('requested_mode'), message.get('state')) != ('image', 'chat', 'completed'):
        raise ValueError('Persisted message did not complete the automatic chat-to-image route')
    starts = [e for e in events if e.get('event') == 'run_started']
    if len(starts) != 1 or (starts[0].get('routing'), starts[0].get('requested_mode')) != ('natural_language_image_request', 'chat'):
        raise ValueError('Missing natural_language_image_request routing evidence')
    if any(e.get('event') in ('model_start', 'model_done', 'model_output') for e in events):
        raise ValueError('Automatic image route unexpectedly invoked a text model')
    required = [e for e in events if e.get('event') == 'approval_required']
    resolved = [e for e in events if e.get('event') == 'approval_resolved']
    executions = [e for e in events if e.get('event') == 'tool_execute']
    results = [e for e in events if e.get('event') == 'tool_result']
    if not all(len(group) == 1 for group in (required, resolved, executions, results)):
        raise ValueError('Expected exactly one approval and one image execution/receipt')
    if required[0].get('approval') != approval or resolved[0].get('approval_id') != approval['id'] or resolved[0].get('allowed') is not True:
        raise ValueError('Execution lacks its matching allowed approval')
    if any(e.get('tool') != 'images.generate' for e in executions + results):
        raise ValueError('Unexpected non-image tool execution')
    if not required[0]['seq'] < resolved[0]['seq'] < executions[0]['seq'] < results[0]['seq']:
        raise ValueError('Image execution did not occur after approval')
    if results[0].get('arguments') != approval['arguments'] or results[0].get('result', {}).get('ok') is not True:
        raise ValueError('Image receipt differs from the approved request or failed')
    return starts[0]['routing']


def generate_through_http(args, cfg, out, report):
    # A real WebApp without backend_factory; deliberately configure no text LLM.
    app = WebApp({'workspace': str(out/'workspace'), 'llm': {'device': 'cpu'},
                  'python': {'mode': 'disabled'}, 'commands': {}, 'mcp_servers': [], 'image': cfg},
                 out/'http-data'/uuid.uuid4().hex)
    server = LocalServer(app, 0)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.05), daemon=True)
    thread.start()
    client = LocalClient(server.server_port)
    rid = None
    events = []
    try:
        bootstrap, _ = client.request('/api/bootstrap', auth=False)
        client.token = bootstrap['token']  # Never persist the ephemeral session token.
        runtime = bootstrap['runtime']
        save(out/'http-runtime.json', runtime)
        if runtime.get('test_fixture') is not False or runtime.get('ready') is not False or runtime.get('image_ready') is not True:
            raise ValueError('Acceptance requires a real image runtime and no ready text model')
        session, _ = client.request('/api/sessions', 'POST', {}, expected=201)
        sid = session['id']
        payload = {'mode': 'chat', 'message': PROMPT,
                   'image_options': {'width': 256, 'height': 256, 'seed': 42}}
        report.update(prompt=PROMPT, requested_mode='chat', llm='not_required', http_session_id=sid)
        save(out/'http-request.json', payload)
        submitted, _ = client.request(f'/api/sessions/{sid}/messages', 'POST', payload, expected=202)
        rid = submitted['run_id']
        report['http_run_id'] = rid
        cursor, approval = 0, None
        deadline = time.monotonic() + 60
        while approval is None:
            if time.monotonic() > deadline:
                raise TimeoutError('Automatic image request did not ask for approval')
            update, _ = client.request(f'/api/runs/{rid}/events?after={cursor}')
            events.extend(update['events']); cursor = update['cursor']
            pending = [e['approval'] for e in events if e.get('event') == 'approval_required']
            if len(pending) > 1 or any(e.get('event') == 'tool_execute' for e in events):
                raise ValueError('Unexpected extra approval or execution before approval')
            approval = pending[0] if pending else None
            if update['status'] in TERMINAL:
                raise RuntimeError('Automatic image request terminated before approval')
        params = approval['arguments']
        if approval.get('tool') != 'images.generate' or any(params.get(k) != v for k, v in
                {'prompt': PROMPT, 'width': 256, 'height': 256, 'seed': 42, 'steps': 8, 'references': []}.items()):
            raise ValueError('HTTP approval does not match natural-language request/default Turbo profile')
        ws = app.ws(sid)
        output = ws.path(params['output'])
        if output.exists():
            raise ValueError('An output image exists before approval')
        # command() is a preflight/argv preview only. Inference is invoked exactly
        # once, by WebApp's confirmed images.generate tool after the HTTP approval.
        preview = ImageRunner(ws, cfg)
        report['argv'] = preview.command(**params)
        report['argv_source'] = 'Application command preview for the HTTP-approved arguments; no direct inference'
        check_device(preview.device_selection, args.device)
        report['device_selection'] = preview.device_selection
        save(out/'http-approval.json', approval)
        save(out/'result.json', report)
        start = time.monotonic()
        try:
            with Samples(out/'resources.jsonl') as samples:
                client.request(f'/api/runs/{rid}/approve', 'POST', {'approval_id': approval['id'], 'allow': True})
                deadline = time.monotonic() + args.timeout + 60
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError('HTTP image run exceeded its engine timeout and completion allowance')
                    update, _ = client.request(f'/api/runs/{rid}/events?after={cursor}')
                    events.extend(update['events']); cursor = update['cursor']
                    if update['status'] in TERMINAL:
                        break
        finally:
            report['generation_seconds'] = round(time.monotonic()-start, 3)
            report['sampled_peak_rss_bytes'] = samples.peak_rss
            report['resource_samples'] = samples.count
            report['sampling_error'] = samples.error
        # Read the full on-disk receipt, not the UI's bounded/truncated log preview.
        audit_path = app.data_dir/'runs'/(rid+'.jsonl')
        shutil.copyfile(audit_path, out/'http-audit.jsonl')
        audit = [json.loads(line) for line in audit_path.read_text(encoding='utf-8').splitlines()]
        receipts = [e for e in audit if e.get('event') == 'tool_result' and e.get('tool') == 'images.generate']
        if len(receipts) != 1 or receipts[0].get('arguments') != params:
            raise ValueError('Missing unique approved image engine receipt')
        receipt = receipts[0]
        save(out/'engine-receipt.json', receipt)
        result = receipt.get('result', {}).get('result', {})
        for stream in ('stdout', 'stderr'):
            (out/f'engine.{stream}.log').write_text(result.get(stream, ''), encoding='utf-8')
        report['process'] = {k: v for k, v in result.items() if k not in ('stdout', 'stderr')}
        session, _ = client.request(f'/api/sessions/{sid}')
        persisted = json.loads((app.sessions.root/(sid+'.json')).read_text(encoding='utf-8'))
        save(out/'http-session.json', persisted)
        if session != persisted:
            raise ValueError('HTTP session differs from persisted session')
        message = next(m for m in persisted['messages'] if m.get('run_id') == rid)
        report['mode'] = message.get('mode')
        report['routing'] = validate_automatic_trace(message, message['trace'], approval)
        validate_automatic_trace(message, events, approval)
        if update['status'] != 'completed' or session['active_run'] is not None:
            raise RuntimeError('Real HTTP image run failed or is not finalized')
        if receipt.get('result', {}).get('ok') is not True or result.get('returncode') != 0 or result.get('timed_out') is not False or result.get('file_created') is not True:
            raise RuntimeError('Real Turbo process failed, timed out or produced no file')
        check_device(result['device_selection'], args.device)
        combined = result.get('stdout', '') + '\n' + result.get('stderr', '')
        for marker in ('model-type = Turbo', 'step 8/8 done', 'vae done'):
            if marker not in combined:
                raise ValueError('Missing actual Turbo execution evidence: ' + marker)
        if result.get('steps') != 8 or result.get('image_profile', {}).get('variant') != 'turbo':
            raise ValueError('Wrong application image profile')
        cards = message.get('artifacts', [])
        if len(cards) != 1 or cards[0].get('path') != params['output'] or result.get('path') != params['output'] or cards[0].get('source_tool') != 'images.generate':
            raise ValueError('Persisted image card does not match the confirmed engine receipt')
        report['artifact'] = cards[0]
        report['image'] = validate_image(output, 256, 256)
        report['image']['quality'] = 'NOT evaluated; 8-step execution smoke is not a quality benchmark.'
        shutil.copyfile(output, out/'generated.png')
        path = f'/api/sessions/{sid}/download?' + urlencode({'path': cards[0]['path']})
        client.request(path, auth=False, expected=403)
        downloaded, headers = client.request(path)
        downloaded_path = out/'downloaded.png'
        downloaded_path.write_bytes(downloaded)
        downloaded_image = validate_image(downloaded_path, 256, 256)
        if downloaded != output.read_bytes() or downloaded_image['sha256'] != report['image']['sha256']:
            raise ValueError('Authenticated image download does not match the persisted artifact')
        if headers.get('Content-Type') != 'application/octet-stream' or headers.get('Content-Disposition') != 'attachment':
            raise ValueError('Image download did not use the authenticated attachment response')
        report['http_download'] = {'authenticated': True, 'unauthenticated_status': 403, 'bytes': len(downloaded),
            'sha256': hashlib.sha256(downloaded).hexdigest(), 'fully_decoded': downloaded_image['fully_decoded'],
            'matches_persisted_artifact': True}
        report['engine_after'] = verify_native(args.binary.resolve(strict=True), args.native_status)
        if report['engine_after'] != report['engine']:
            raise ValueError('Native image executable identity changed during HTTP acceptance')
    finally:
        save(out/'http-events.json', events)
        if rid and app.get_run(rid).status not in TERMINAL:
            app.cancel(rid)
            # ImageRunner cancellation is cooperative, so allow its configured
            # subprocess timeout to reap the process rather than orphaning it.
            deadline = time.monotonic() + args.timeout + 30
            while app.get_run(rid).status not in TERMINAL and time.monotonic() < deadline:
                time.sleep(.1)
        server.shutdown()
        server.server_close()
        thread.join(5)


def run(args):
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    report={**identity(),'ok':False,'kind':'REAL_QWEN_IMAGE_TURBO','device_requested':args.device,
            'route':args.route,'width':256,'height':256,'steps':8,'quality_test':'not_run'}
    for name in ('generated.png', 'downloaded.png'):(out/name).unlink(missing_ok=True)
    save(out/'result.json',report)
    try:
        report['engine']=verify_native(args.binary.resolve(strict=True),args.native_status)
        manifests=prepare_models(args.models.resolve(),args.route,out)
        report['model_revisions']={k:v['revision'] for k,v in manifests.items()}
        cfg={'enabled':True,'command':[str(args.binary.resolve())], 'model':str(args.models.resolve()/TURBO_IMAGE),
             'device':args.device,'timeout':args.timeout}
        generate_through_http(args,cfg,out,report)
        report.update(ok=True,status='passed',scope='Natural-language chat HTTP route, explicit approval, real 8-step Turbo execution, persisted image and authenticated download. No quality rating or speedup comparison.')
    except Exception as e:report.update(status='failed',error=type(e).__name__+': '+str(e))
    finally:
        save(out/'result.json',report);print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
        if os.getenv('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as s:
                s.write(f"## Real Qwen Image Turbo\n\nStatus: **{report.get('status')}**. Device: {args.device}; fixed 8 steps.\n\nNatural-language chat → approval → real image engine → persisted artifact → authenticated download.\n\nImage quality and performance improvement are not certified by this smoke test.\n")
    return 0 if report['ok'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--native-status',type=Path,default=ROOT/'reports/ci/image/status.json')
    p.add_argument('--models',type=Path,default=ROOT/'models/turbo-ci')
    p.add_argument('--output',type=Path,default=ROOT/'reports/turbo-real')
    p.add_argument('--route',choices=('direct','hf_mirror'),default='direct')
    p.add_argument('--device',choices=('cpu','vulkan'),default='cpu')
    p.add_argument('--timeout',type=int,default=3600)
    args=p.parse_args()
    if not 1<=args.timeout<=5400:p.error('timeout must be 1..5400')
    raise SystemExit(run(args))
