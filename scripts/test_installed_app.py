#!/usr/bin/env python3
"""Black-box frozen app acceptance with no Python/ncnn commands available on PATH."""
from __future__ import annotations
import argparse
import http.client
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--with-model', action='store_true', help='Explicit real 0.5B download through the installed app')
    args = parser.parse_args()
    report = {'scope': 'Actual frozen executable, HTTP and Office files; see model results for inference scope.',
              'model_inference': 'not_requested', 'ok': False, 'checks': {}}
    with tempfile.TemporaryDirectory(prefix='Local Agent clean-user-') as tmp:
        root = Path(tmp); home = root / 'User Data'; home.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','LD_LIBRARY_PATH')}
        env['VK_DRIVER_FILES'] = '/nonexistent/local-agent-vulkan.json'
        env['VK_ICD_FILENAMES'] = '/nonexistent/local-agent-vulkan.json'
        env['PATH'] = os.environ.get('SystemRoot', r'C:\Windows') + r'\System32' if os.name == 'nt' else '/nonexistent'
        log = (root / 'process.log').open('wb')
        process = subprocess.Popen([str(args.binary.resolve()), '--home', str(home), '--port', '0', '--no-browser'],
                                   cwd=root, env=env, stdout=log, stderr=log)
        port = None; token = ''
        def api(path, method='GET', data=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
            try:
                conn.request(method, path, body=json.dumps(data) if data is not None else None,
                             headers={'X-Agent-Token': token, 'Content-Type': 'application/json'})
                response = conn.getresponse(); raw = response.read()
                return response.status, json.loads(raw) if response.getheader('Content-Type','').startswith('application/json') else raw
            finally: conn.close()
        try:
            deadline = time.monotonic() + 90
            while not (home / 'connection.json').exists():
                if process.poll() is not None: raise RuntimeError('App exited before opening H5')
                if time.monotonic() > deadline: raise TimeoutError('App startup timeout')
                time.sleep(.2)
            port = urlsplit(json.loads((home / 'connection.json').read_text())['url']).port
            code, body = api('/api/bootstrap'); assert code == 200
            token = body['token']
            report['device_preflight'] = body['runtime']['devices']
            report['checks']['both_default_auto'] = all(d['requested'] == 'auto' for d in report['device_preflight'].values())
            if platform.system() == 'Linux':
                report['checks']['no_driver_cpu_fallback'] = all(d['selected'] == 'cpu' for d in report['device_preflight'].values())
            report['checks']['no_manual_config'] = body['runtime']['managed_install'] and not (home / 'local.json').exists()
            code, hub = api('/api/setup'); assert code == 200
            report['checks']['both_engines_bundled'] = all(hub['engines'].values())
            report['checks']['no_automatic_model_download'] = hub['job']['status'] == 'idle' and not list((home / 'models').iterdir())
            code, _ = api('/'); report['checks']['h5_served'] = code == 200
            code, session = api('/api/sessions', 'POST', {}); assert code == 201
            sid = session['id']
            code, _ = api(f'/api/sessions/{sid}/messages', 'POST', {'message': 'Hi'})
            report['checks']['missing_weights_fail_honestly'] = code == 503
            for format in ('md','docx','xlsx','pptx'):
                code, made = api(f'/api/sessions/{sid}/export', 'POST', {'format':format,
                    'content':'# Installer test\n\nHello\n\n| Item | Value |\n| --- | --- |\n| CHECK | 42 |',
                    'title':'Installed verification','confirm':True})
                assert code == 201, (code, made)
                code, extracted = api(f'/api/sessions/{sid}/document?path=' + quote(made['path']))
                report['checks']['office_' + format] = code == 200 and 'CHECK' in extracted['content']
            code, _ = api(f'/api/sessions/{sid}/export','POST', {'format':'md','content':'x'})
            report['checks']['export_requires_consent'] = code == 400
            if args.with_model:
                code, estimate = api('/api/setup/prepare', 'POST', {'id':'qwen05'}); assert code == 200, (code, estimate)
                report['model_source'] = estimate
                code, install = api('/api/setup/install', 'POST', {'ticket':estimate['ticket'], 'accept_download':True})
                assert code == 202, (code, install)
                deadline = time.monotonic() + 1200
                while True:
                    code, state = api('/api/setup'); assert code == 200
                    if state['job']['status'] not in ('downloading', 'converting'): break
                    if time.monotonic() > deadline: raise TimeoutError('Real model installation timeout')
                    time.sleep(2)
                report['model_install'] = state['job']
                assert state['job']['status'] == 'completed', state['job']
                code, launched = api(f'/api/sessions/{sid}/messages', 'POST', {
                    'message':'What is 2 + 2? Reply with only the number.', 'mode':'chat','max_new_tokens':64})
                assert code == 202, (code, launched)
                cursor = 0; deadline = time.monotonic() + 360
                while True:
                    code, events = api(f"/api/runs/{launched['run_id']}/events?after={cursor}"); assert code == 200
                    cursor = events['cursor']
                    if events['status'] in ('completed','failed','cancelled'): break
                    if time.monotonic() > deadline: raise TimeoutError('Real generation timeout')
                code, conversation = api(f'/api/sessions/{sid}')
                answer = conversation['messages'][-1]
                report['real_model_answer'] = answer['content'];report['actual_model_device'] = answer.get('device_selection')
                report['checks']['real_qwen05_cpu'] = answer['state'] == 'completed' and answer['content'].strip().rstrip('.!') == '4'
                report['checks']['actual_cpu_fallback'] = answer.get('device_selection', {}).get('selected') == 'cpu'
                report['model_inference'] = 'actual official Qwen2.5-0.5B via frozen app with auto CPU fallback'
            assert api('/api/setup/shutdown','POST',{'confirm':True})[0] == 200
            process.wait(timeout=30)
            report['checks']['clean_shutdown'] = process.returncode == 0
            report['checks']['history_retained'] = (home/'state/sessions'/f'{sid}.json').is_file()
            report['ok'] = all(report['checks'].values())
        except Exception as exc:
            report['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            log.close()
            report['process_log'] = (root/'process.log').read_text(encoding='utf-8',errors='replace')[-12000:]
            if (home/'launcher.log').exists():
                report['launcher_log'] = (home/'launcher.log').read_text(encoding='utf-8',errors='replace')[-12000:]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['ok'] else 1


if __name__ == '__main__': raise SystemExit(main())
