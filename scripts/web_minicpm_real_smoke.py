#!/usr/bin/env python3
"""Real MiniCPM import/activation/upload/HTTP inference; no mock backend.

Uses existing converted weights and native executable. No download, conversion,
or copying of weights. This exercises the UI's HTTP routes, not a browser.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import http.client
import json
from pathlib import Path
import platform
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_agent.desktop import automatic_config
from local_agent.model_hub import ModelHub
from local_agent.local_vision_model import MODEL_ID
from local_agent.web import LocalServer, WebApp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--expect', action='append', required=True, help='Required answer phrase; repeat as needed')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    home = output / 'home'
    hub = ModelHub(home, {'llm': str(args.binary.resolve()), 'image': str(home / 'absent-image-engine')})
    app = WebApp(automatic_config(home, hub), home / 'state')
    app.model_hub = hub
    def refresh():
        with app.lock:
            app.config = automatic_config(home, hub)
    hub.on_change = refresh
    server = LocalServer(app, 0)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    report = {'scope': 'Real ModelHub import/activate and H5 HTTP upload/chat to native ncnn; no fixture backend; not browser interaction',
              'platform': platform.platform(), 'ok': False, 'checks': [],
              'binary_sha256': hashlib.sha256(args.binary.read_bytes()).hexdigest(),
              'image_sha256': hashlib.sha256(args.image.read_bytes()).hexdigest()}
    started = time.monotonic()
    def call(path, method='GET', data=None, expected=(200, 201, 202)):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=30)
        try:
            connection.request(method, path, body=json.dumps(data) if data is not None else None,
                               headers={'X-Agent-Token': app.token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            body = response.read()
            if response.status not in expected:
                raise RuntimeError((response.status, body.decode(errors='replace')))
            return json.loads(body)
        finally:
            connection.close()
    def passed(name):
        report['checks'].append(name)
        print('PASS', name, flush=True)
    try:
        assert app.factory is None and not app.info()['ready']
        call('/api/setup/import-vision', 'POST', {'directory': str(args.model.resolve()), 'accept_local_model': False}, expected=(400,))
        passed('local import requires explicit trust confirmation')
        imported = call('/api/setup/import-vision', 'POST', {'directory': str(args.model.resolve()), 'accept_local_model': True})
        assert imported['id'] == MODEL_ID and imported['activated'] is False and not hub.active().get('llm')
        passed('real converted package registered without copying or activating weights')
        call('/api/setup/activate', 'POST', {'id': MODEL_ID})
        info = app.info()
        assert info['ready'] and info['experimental_vision'] and not info['agent_enabled'] and info['device'] == 'CPU'
        assert app.config['llm']['model'] == str(args.model.resolve())
        report['runtime'] = {key: info[key] for key in ('model', 'ready', 'experimental_vision', 'agent_enabled', 'device', 'device_reason')}
        passed('activation derives real CPU single-image configuration without manual JSON edits')
        restored = ModelHub(home, hub.engines)
        assert automatic_config(home, restored)['llm'] == app.config['llm']
        passed('activation survives ModelHub reconstruction')
        sid = call('/api/sessions', 'POST', {})['id']
        call(f'/api/sessions/{sid}/messages', 'POST', {'message': 'Write a file', 'mode': 'agent'}, expected=(400,))
        passed('Agent mode rejected for MiniCPM before inference')
        uploaded = call(f'/api/sessions/{sid}/upload', 'POST', {'name': args.image.name, 'data': base64.b64encode(args.image.read_bytes()).decode()})
        payload = {'message': 'Describe this image in one short sentence.', 'mode': 'chat', 'interpret_image': True,
                   'attachments': [uploaded['path']], 'max_new_tokens': 64}
        call(f'/api/sessions/{sid}/messages', 'POST', dict(payload, attachments=[uploaded['path'], uploaded['path']]), expected=(400,))
        passed('multiple image attachments rejected')
        run = call(f'/api/sessions/{sid}/messages', 'POST', payload)['run_id']
        deadline = time.monotonic() + 420
        cursor = 0
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('Real MiniCPM HTTP inference exceeded deadline')
            state = call(f'/api/runs/{run}/events?after={cursor}')
            cursor = state['cursor']
            if state['status'] in ('completed', 'failed', 'cancelled'):
                break
            time.sleep(.1)
        reply = call('/api/sessions/' + sid)['messages'][-1]
        report['reply'] = reply['content']
        report['reply_state'] = reply['state']
        report['actual_device'] = reply.get('device_selection')
        assert reply['state'] == 'completed', reply
        assert all(word.casefold() in reply['content'].casefold() for word in args.expect), reply['content']
        assert report['actual_device']['selected'] == 'cpu'
        passed('uploaded pixels produced required real native image answer on CPU')
        report['ok'] = True
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        print(report['error'], file=sys.stderr, flush=True)
    finally:
        for run_id in list(app.runs):
            app.cancel(run_id)
        server.shutdown()
        server.server_close()
        thread.join(3)
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
