"""Zero-JSON desktop launcher with bundled engines and Vulkan-first selection."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import sys


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def user_home():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'LocalAgent'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/LocalAgent'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'local-agent'


def engine_paths():
    suffix = '.exe' if os.name == 'nt' else ''
    root = resource_root() / 'engines'
    return {'llm': str(root / 'bridge' / ('ncnn_agent_bridge' + suffix)),
            'image': str(root / 'image' / ('qwenimage-ncnn-vulkan' + suffix))}


def automatic_config(home: Path, hub):
    active = hub.active()
    return {'workspace': str(home / 'workspace'), 'max_steps': 12,
        'llm': {'backend': 'ncnn_bridge', 'command': [hub.engines['llm']],
                'model': str(hub.models / active.get('llm', 'qwen05')), 'device': 'auto',
                'threads': min(4, os.cpu_count() or 1), 'timeout': 300, 'max_new_tokens': 512},
        'image': {'enabled': 'image' in active, 'command': [hub.engines['image']],
                  'model': str(hub.models / active.get('image', 'qwenimage21')), 'device': 'auto', 'timeout': 5400},
        'documents': {'enabled': True}, 'python': {'mode': 'disabled'}, 'commands': {}, 'mcp_servers': []}


class InstanceLock:
    def __init__(self, home):
        self.file = (home / 'instance.lock').open('a+b')
        self.file.seek(0); self.file.write(b'0'); self.file.flush(); self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('Local Agent is already running for this user data directory')
    def close(self):
        self.file.close()


def self_test(output: Path):
    """Actual bundled engine/probe startup and document round trips; no weights."""
    import tempfile
    from .documents import DocumentTools
    from .paths import Workspace
    from .process import run_process
    from .model_hub import digest
    from .device import select, engine_env, probe as run_probe
    report = {'frozen': bool(getattr(sys, 'frozen', False)), 'platform': platform.platform(),
              'engines': {}, 'documents': {}, 'model_inference': 'not_run', 'ok': False}
    try:
        manifest = resource_root() / 'BUNDLE.json'
        info = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
        with tempfile.TemporaryDirectory(prefix='local-agent-check-') as tmp:
            root = Path(tmp)
            for kind, file in engine_paths().items():
                p = Path(file)
                if not p.is_file():
                    raise RuntimeError('Missing bundled engine: ' + kind)
                sha = digest(p)
                if info.get('engines', {}).get(kind, {}).get('sha256') != sha:
                    raise RuntimeError('Bundled engine checksum mismatch')
                result = run_process([str(p), '--help' if kind == 'llm' else '-h'], cwd=root,
                                     timeout=30, env=engine_env([str(p)]))
                expected = 'Usage: ncnn_agent_bridge' if kind == 'llm' else 'Usage: qwenimage-ncnn-vulkan'
                passed = result['returncode'] == 0 and not result['timed_out'] and expected in result['stdout'] + result['stderr']
                probe = p.with_name('ncnn_device_probe.exe' if os.name == 'nt' else 'ncnn_device_probe')
                if digest(probe) != info['engines'][kind]['probe_sha256']:
                    raise RuntimeError('Bundled probe checksum mismatch')
                # Preserve the actual probe report BEFORE declaring failure. A
                # windowed frozen app has no stderr console; an abbreviated
                # reason alone otherwise hides dyld/driver/protocol failures.
                capabilities = run_probe([str(p)])
                selection = select({'command': [str(p)], 'device': 'auto'}, kind, report=capabilities)
                probe_ok = selection['reason'] not in ('probe_unavailable', 'matching_probe_missing')
                report['engines'][kind] = {'passed': passed and probe_ok, 'sha256': sha,
                    'result': result, 'probe_report': capabilities, 'device_selection': selection,
                    'executable': str(p.resolve()), 'probe_path': str(probe.resolve()),
                    'driver_environment': engine_env([str(p)])}
                if not probe_ok:
                    report.setdefault('failures', []).append(kind + ': ' + selection['reason'] + ': ' + capabilities.get('detail', ''))
            docs = DocumentTools(Workspace(root / 'documents'))
            for fmt in ('md', 'docx', 'xlsx', 'pptx'):
                made = docs.create(fmt, '# Test\n\nHello local documents\n\n| Name | Value |\n| --- | --- |\n| CHECK | 42 |', 'Bundle verification')
                read = docs.read(made['path'])
                report['documents'][fmt] = {'passed': 'CHECK' in read['content'] or 'Hello local' in read['content']}
            report['ok'] = all(x['passed'] for x in report['engines'].values()) and all(x['passed'] for x in report['documents'].values())
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1


def main():
    ap = argparse.ArgumentParser(description='Local Agent: launch once, configure models in the browser')
    ap.add_argument('--home', type=Path)
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--self-test', type=Path, metavar='REPORT_JSON')
    ap.add_argument('--no-monitor', action='store_true', help='Headless service for automation; normal startup shows the native manager')
    ap.add_argument('--monitor-test', type=Path, help='Run actual native UI and OS telemetry acceptance')
    args = ap.parse_args()
    if args.monitor_test:
        from .monitor_ui import native_self_test
        args.monitor_test.parent.mkdir(parents=True, exist_ok=True)
        return native_self_test(args.monitor_test)
    if args.self_test:
        return self_test(args.self_test)
    from .model_hub import ModelHub, save_json
    from .monitor_http import ManagedWebApp as WebApp, ManagedServer as LocalServer
    home = (args.home or user_home()).resolve()
    home.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None or sys.stderr is None:
        log = (home / 'launcher.log').open('a', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = log
    try:
        lock = InstanceLock(home)
    except RuntimeError:
        import re
        from urllib.request import urlopen
        connection = json.loads((home / 'connection.json').read_text(encoding='utf-8'))
        url = connection.get('url', '')
        if connection.get('application') != 'LocalAgent' or not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', url):
            raise RuntimeError('Existing instance is not reachable; close it before relaunching')
        with urlopen(url + '/api/bootstrap', timeout=3) as response:
            existing = json.load(response)
        if not existing.get('runtime', {}).get('managed_install'):
            raise RuntimeError('Existing port is not a Local Agent desktop service')
        if not args.no_monitor:
            from urllib.request import Request
            request = Request(url + '/api/monitor/show', data=b'{}', method='POST',
                headers={'Content-Type': 'application/json', 'X-Agent-Token': existing['token']})
            with urlopen(request, timeout=3) as response:
                response.read()
        if not args.no_browser:
            import webbrowser
            webbrowser.open(url)
        return 0
    hub = ModelHub(home, engine_paths())
    app = WebApp(automatic_config(home, hub), home / 'state')
    app.model_hub = hub
    def refresh():
        with app.lock:
            app.config = automatic_config(home, hub)
    hub.on_change = refresh
    server = None
    monitor = None
    try:
        try:
            server = LocalServer(app, args.port)
        except OSError:
            if args.port != 8765:
                raise
            server = LocalServer(app, 0)
        url = f'http://127.0.0.1:{server.server_port}'
        save_json(home / 'connection.json', {'url': url, 'pid': os.getpid(), 'application': 'LocalAgent'})
        print('Local Agent:', url, flush=True)
        if not args.no_browser:
            import webbrowser
            webbrowser.open(url)
        if args.no_monitor:
            server.serve_forever()
        else:
            import threading
            from .monitor import MonitorService
            from .monitor_ui import Dashboard
            monitor = MonitorService(app)
            app.monitor = monitor
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.2), daemon=True)
            thread.start()
            try:
                Dashboard(app, monitor, url, server.shutdown, thread.is_alive, home).run()
            finally:
                server.shutdown()
                thread.join(5)
    except KeyboardInterrupt:
        pass
    finally:
        if monitor:
            monitor.close()
        hub.stop()
        for rid in list(app.runs):
            app.cancel(rid)
        if server:
            server.server_close()
        (home / 'connection.json').unlink(missing_ok=True)
        lock.close()
    return 0
