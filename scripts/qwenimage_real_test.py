#!/usr/bin/env python3
"""Independent real Qwen Image acceptance. No text LLM, fixture or fallback.

Stages resolve an immutable model manifest, stream/hash ONLY the complete
text-to-image components, and invoke the application ImageRunner unchanged.
Image decoding checks execution integrity, NOT prompt adherence or quality.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import threading
import time
import urllib.request
import uuid
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_agent.model_sources import IMAGE_REQUIRED, digest, file_url, open_https, resolve_manifest, safe_name
from local_agent.images import ImageRunner
from local_agent.paths import Workspace
from scripts.ci_native import read_pins

GIB = 1024 ** 3
MODEL_REPO = 'nihui-szyl/qwen-image-ncnn'
PROMPT = 'A red wooden cube on a white table, studio photograph.'


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def identity():
    return {'commit': os.getenv('GITHUB_SHA', 'local'), 'run_id': os.getenv('GITHUB_RUN_ID', 'local'),
            'run_attempt': os.getenv('GITHUB_RUN_ATTEMPT', 'local'),
            'utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform()}


def t2i_manifest(full: dict) -> dict:
    selected = dict(full)
    selected['files'] = [dict(f) for f in full['files'] if f['name'] in IMAGE_REQUIRED]
    selected['download_bytes'] = sum(f['bytes'] for f in selected['files'])
    selected['scope'] = 'Complete text-to-image weights; no editing, vision, ControlNet or LoRA components.'
    selected.pop('disk_required_bytes', None)
    selected.pop('installed_estimate_bytes', None)
    validate_manifest(selected)
    return selected


def validate_manifest(m: dict):
    if not isinstance(m, dict) or (m.get('id'), m.get('provider'), m.get('repository')) != ('qwenimage21', 'huggingface', MODEL_REPO):
        raise ValueError('Unexpected model/source identity')
    if not re.fullmatch('[a-f0-9]{40}', str(m.get('revision', ''))):
        raise ValueError('Immutable model revision required')
    files = m.get('files')
    if not isinstance(files, list) or len(files) != len(IMAGE_REQUIRED):
        raise ValueError('Incomplete text-to-image components')
    names = set()
    for f in files:
        name = safe_name(f['name'])
        if name not in IMAGE_REQUIRED or name in names or f['remote'] != 'qwenimage21/' + name:
            raise ValueError('Unexpected or duplicate model member')
        names.add(name)
        if type(f['bytes']) is not int or not 0 < f['bytes'] < 40 * GIB:
            raise ValueError('Invalid model member length')
        algorithm = f.get('algorithm')
        if algorithm not in ('sha256', 'git') or not re.fullmatch('[a-f0-9]{64}' if algorithm == 'sha256' else '[a-f0-9]{40}', str(f.get('digest', ''))):
            raise ValueError('Invalid model checksum')
        if name.endswith('.bin') and algorithm != 'sha256':
            raise ValueError('Real weight blobs require SHA-256')
    if names != IMAGE_REQUIRED or m.get('download_bytes') != sum(f['bytes'] for f in files):
        raise ValueError('Manifest total/membership mismatch')
    if m['download_bytes'] > 40 * GIB:
        raise ValueError('Model exceeds this test download budget')


def verified_file(path: Path, item: dict) -> bool:
    return (path.is_file() and not path.is_symlink() and path.stat().st_size == item['bytes']
            and digest(path, item['algorithm']) == item['digest'])


def model_file(root: Path, name: str) -> Path:
    path = root / safe_name(name)
    if not path.resolve().is_relative_to(root.resolve()) or any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise ValueError('Model paths must not contain symlinks')
    return path


def resource_report(root: Path):
    import psutil
    vm = psutil.virtual_memory()
    result = {'cpu_count': os.cpu_count(), 'memory_total': vm.total, 'memory_available': vm.available,
              'swap_total': psutil.swap_memory().total, 'disk_free': shutil.disk_usage(root).free}
    # Linux containers may report host RAM; preserve the cgroup bound separately.
    p = Path('/sys/fs/cgroup/memory.max')
    if p.is_file():
        raw = p.read_text().strip()
        if raw.isdigit(): result['cgroup_memory_limit'] = int(raw)
    return result


def audit_upstream(output: Path):
    pins = read_pins()
    repo = pins['qwenimage']['repository']
    def get(path):
        req = urllib.request.Request('https://api.github.com/repos/' + repo + path,
                                     headers={'User-Agent': 'LocalAgent-QwenImage-CI', 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024: raise ValueError('GitHub metadata too large')
        return json.loads(raw)
    meta = get('')
    branch = meta['default_branch']
    from urllib.parse import quote
    head = get('/git/ref/heads/' + quote(branch, safe=''))['object']['sha']
    result = {**identity(), 'repository': repo, 'default_branch': branch, 'latest_commit': head,
              'tested_commit': pins['qwenimage']['commit'], 'lock_matches_latest': head == pins['qwenimage']['commit'],
              'policy': 'Record latest HEAD, build the immutable lock. A newer upstream is not silently adopted.'}
    save(output, result)
    print(json.dumps(result, indent=2), flush=True)


def prepare(model: Path, output: Path):
    model.mkdir(parents=True, exist_ok=True)
    m = t2i_manifest(resolve_manifest('qwenimage21', 'huggingface'))
    save(output / 'model-manifest.json', m)
    resources = resource_report(model)
    required = m['download_bytes'] + 2 * GIB
    report = {**identity(), **resources, 'required_free_bytes': required, 'download_bytes': m['download_bytes'],
              'fits_disk': resources['disk_free'] >= required,
              'note': 'Memory is measured, not a guarantee. Small resolution does not shrink model weights. No swap/SDK cleanup is performed.'}
    save(output / 'resources-before.json', report)
    print(json.dumps(report, indent=2), flush=True)
    if not report['fits_disk']:
        raise RuntimeError('Insufficient disk for full T2I model; select a larger preconfigured runner. No fake model or silent skip.')


def download(m: dict, root: Path, report_dir: Path, *, opener=open_https, timeout_seconds=1800):
    validate_manifest(m)
    root.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    records = []
    count = 0
    for f in m['files']:
        if time.monotonic() > deadline: raise TimeoutError('Model download deadline')
        path = model_file(root, f['name'])
        path.parent.mkdir(parents=True, exist_ok=True)
        if not verified_file(path, f):
            temp = path.with_name(path.name + '.part')
            model_file(root, temp.relative_to(root).as_posix())
            for attempt in range(3):
                got = 0
                try:
                    print('Download', f['name'], f['bytes'], flush=True)
                    # Fixed manifest URLs only, no token or arbitrary endpoint input.
                    with opener(file_url(m, f), timeout=60) as r, temp.open('wb') as out:
                        while block := r.read(4 * 1024 * 1024):
                            if time.monotonic() > deadline: raise TimeoutError('Model download deadline')
                            got += len(block)
                            if got > f['bytes']: raise ValueError('Model download exceeded expected length')
                            out.write(block)
                            if got // (256 * 1024**2) != (got-len(block)) // (256 * 1024**2):
                                print(f'{count + got}/{m["download_bytes"]} bytes', flush=True)
                    if not verified_file(temp, f): raise ValueError('Model checksum/length mismatch: ' + f['name'])
                    os.replace(temp, path)
                    break
                except (OSError, ValueError):
                    if attempt == 2 or time.monotonic() > deadline: raise
                    time.sleep(1 + attempt)
                finally:
                    temp.unlink(missing_ok=True)
        records.append({'name': f['name'], 'bytes': f['bytes'], 'source_digest': f['digest'], 'verified': True})
        count += f['bytes']
        save(report_dir / 'download-progress.json', {'verified_bytes': count, 'total_bytes': m['download_bytes'], 'files': records})
    save(report_dir / 'download-result.json', {**identity(), 'ok': True, 'revision': m['revision'], 'files': records})


def validate_image(path: Path, width: int, height: int) -> dict:
    from PIL import Image, ImageStat
    if not path.is_file() or not 0 < path.stat().st_size <= 16 * 1024**2:
        raise ValueError('Missing/empty/oversized output')
    with Image.open(path) as im:
        if im.format != 'PNG' or im.size != (width, height): raise ValueError('Output format/dimensions mismatch')
        im.verify()
    with Image.open(path) as im:
        im.load()  # Decode all pixels, not merely the header.
        rgba = im.convert('RGBA')
        if rgba.getchannel('A').getextrema()[1] == 0: raise ValueError('Fully transparent output')
        rgb = rgba.convert('RGB')
        stats = ImageStat.Stat(rgb)
        if max(stats.stddev) < 0.05: raise ValueError('Constant/near-constant output')
        return {'format': im.format, 'mode': im.mode, 'size': list(im.size), 'mean_rgb': stats.mean,
                'stddev_rgb': stats.stddev, 'alpha_extrema': list(rgba.getchannel('A').getextrema()),
                'bytes': path.stat().st_size, 'sha256': digest(path), 'fully_decoded': True,
                'quality': 'NOT evaluated; 2-step smoke is not a quality benchmark.'}


def check_device(selection: dict, requested: str):
    if selection.get('selected') != requested:
        raise ValueError('Actual device selection differs from requested acceptance lane')
    if requested == 'vulkan' and selection.get('hardware') is not True:
        raise ValueError('Vulkan acceptance requires real hardware, not a software ICD')
    if requested == 'cpu' and selection.get('gpu') != -1:
        raise ValueError('CPU lane must explicitly pass -g -1')


def verify_native(binary: Path, path: Path):
    state = json.loads(path.read_text(encoding='utf-8'))
    sha = digest(binary)
    if state.get('component') != 'image' or state.get('built_binary_sha256') != sha or state.get('smoke_binary_sha256') != sha:
        raise ValueError('Binary is not the same image executable built and preflighted')
    for stage in ('configure', 'build', 'smoke'):
        if state.get('stages', {}).get(stage, {}).get('status') != 'passed':
            raise ValueError('Missing native build/preflight stage')
    if state.get('dependencies', {}).get('qwenimage', {}).get('commit') != read_pins()['qwenimage']['commit']:
        raise ValueError('Wrong upstream engine commit')
    for k in ('commit', 'run_id', 'run_attempt'):
        env = os.getenv({'commit': 'GITHUB_SHA', 'run_id': 'GITHUB_RUN_ID', 'run_attempt': 'GITHUB_RUN_ATTEMPT'}[k])
        if env is not None and str(state.get(k)) != env: raise ValueError('Stale native CI evidence')
    return {'sha256': sha, 'source': state.get('dependencies'), 'native_status': str(path)}


class Samples:
    """Observe this test's child processes; never changes engine device/memory policy."""
    def __init__(self, out: Path):
        self.out = out
        self.stop = threading.Event()
        self.peak_rss = 0
        self.count = 0
        self.error = None
        self.thread = threading.Thread(target=self._sample, daemon=True)
    def _sample(self):
        import psutil
        parent = psutil.Process()
        start = time.monotonic()
        try:
            with self.out.open('w', encoding='utf-8') as f:
                while not self.stop.is_set():
                    rows = []
                    for p in parent.children(recursive=True):
                        try:
                            with p.oneshot():
                                m = p.memory_info(); t = p.cpu_times()
                                rows.append({'pid': p.pid, 'name': p.name(), 'rss': m.rss, 'vms': m.vms,
                                             'cpu_seconds': t.user + t.system})
                        except (psutil.NoSuchProcess, psutil.AccessDenied): pass
                    total = sum(p['rss'] for p in rows)
                    self.peak_rss = max(self.peak_rss, total)
                    self.count += 1
                    f.write(json.dumps({'seconds': round(time.monotonic()-start, 3), 'child_rss_sum': total, 'processes': rows}) + '\n'); f.flush()
                    if self.count % 30 == 0: print('Real inference active; sampled child RSS:', total, flush=True)
                    self.stop.wait(1)
        except Exception as e:
            self.error = f'{type(e).__name__}: {e}'
    def __enter__(self): self.thread.start(); return self
    def __exit__(self, *_): self.stop.set(); self.thread.join(5)


def infer(args, m, output):
    validate_manifest(m)
    for f in m['files']:
        if not verified_file(model_file(args.model, f['name']), f):
            raise ValueError('Unverified model member: ' + f['name'])
    binary = args.binary.resolve(strict=True)
    report = {**identity(), 'kind': 'REAL_QWEN_IMAGE_T2I', 'ok': False,
              'model_revision': m['revision'], 'engine': verify_native(binary, args.native_status),
              'device_requested': args.device, 'width': 256, 'height': 256, 'steps': 2, 'seed': 42,
              'prompt': PROMPT, 'status': 'running', 'quality_test': 'not_run', 'llm': 'not_required',
              'weights_verified': True}
    save(output / 'result.json', report)
    ws = Workspace(output / ('workspace-' + uuid.uuid4().hex[:10]))
    runner = ImageRunner(ws, {'enabled': True, 'command': [str(binary)], 'model': str(args.model.resolve()),
                            'device': args.device, 'timeout': args.timeout})
    params = dict(prompt=PROMPT, output='generated.png', width=256, height=256, steps=2, seed=42)
    started = time.monotonic()
    try:
        report['argv'] = runner.command(**params)
        check_device(runner.device_selection, args.device)
        report['device_selection'] = runner.device_selection
        print('Starting real image model:', json.dumps(report['argv']), flush=True)
        save(output / 'result.json', report)
        with Samples(output / 'resources.jsonl') as sample:
            result = runner.run(**params)  # Application code path, not a canned/direct image substitute.
        (output / 'engine.stdout.log').write_text(result.get('stdout', ''), encoding='utf-8')
        (output / 'engine.stderr.log').write_text(result.get('stderr', ''), encoding='utf-8')
        report['process'] = {k: v for k, v in result.items() if k not in ('stdout', 'stderr')}
        report['memory'] = {'sampled_peak_child_rss_sum_bytes': sample.peak_rss, 'samples': sample.count,
                            'sampling_error': sample.error, 'scope': 'Sampled RSS sum, shared pages may repeat. Not VRAM or a guaranteed absolute peak.'}
        if result['timed_out']: raise TimeoutError('Real model generation timed out')
        if result['returncode'] != 0: raise RuntimeError('Image engine failed: exit=' + str(result['returncode']))
        if not result['file_created']: raise RuntimeError('Image engine did not create output')
        check_device(result['device_selection'], args.device)
        report['image'] = validate_image(ws.path('generated.png'), 256, 256)
        shutil.copyfile(ws.path('generated.png'), output / 'generated.png')
        report.update(ok=True, status='passed')
    except Exception as e:
        report.update(status='failed', error=f'{type(e).__name__}: {e}')
        raise
    finally:
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        save(output / 'result.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage', choices=['upstream', 'prepare', 'download', 'run'])
    ap.add_argument('--model', type=Path, default=ROOT / 'models/ci-qwenimage21')
    ap.add_argument('--output', type=Path, default=ROOT / 'reports/qwenimage-real')
    ap.add_argument('--binary', type=Path)
    ap.add_argument('--native-status', type=Path, default=ROOT / 'reports/ci/image/status.json')
    ap.add_argument('--device', choices=['cpu', 'vulkan'], default='cpu')
    ap.add_argument('--timeout', type=int, default=2400)
    args = ap.parse_args()
    if not 1 <= args.timeout <= 10800: ap.error('timeout must be 1..10800 seconds')
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    report = {**identity(), 'stage': args.stage, 'ok': False}
    if args.stage == 'run':
        # Prevent stale success/image artifacts when verification fails before launch.
        (out / 'generated.png').unlink(missing_ok=True)
        save(out / 'result.json', {**identity(), 'ok': False, 'status': 'not_started', 'device_requested': args.device})
    try:
        if args.stage == 'upstream': audit_upstream(out / 'upstream.json')
        elif args.stage == 'prepare': prepare(args.model, out)
        else:
            m = json.loads((out / 'model-manifest.json').read_text(encoding='utf-8'))
            if args.stage == 'download': download(m, args.model, out)
            else:
                if args.binary is None: raise ValueError('--binary required for run')
                infer(args, m, out)
        report['ok'] = True
    except Exception as e:
        report['error'] = f'{type(e).__name__}: {e}'
        print(report['error'], file=sys.stderr, flush=True)
    finally:
        save(out / (args.stage + '-stage.json'), report)
    return 0 if report['ok'] else 1


if __name__ == '__main__': raise SystemExit(main())
