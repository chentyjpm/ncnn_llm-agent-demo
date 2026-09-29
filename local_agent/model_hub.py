"""Fixed-source model installer. Explicit consent, hashes and atomic activation.

No caller-controlled URLs or executable installation. Incomplete downloads are
never active models. HF metadata resolves an immutable revision before consent.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import ssl
import threading
import time
import urllib.request

CATALOG = {
    'qwen05': {'name': 'Qwen2.5 0.5B · Vulkan / CPU', 'kind': 'llm',
        'repository': 'Qwen/Qwen2.5-0.5B-Instruct',
        'revision': '7ae557604adf67be50417f59c2c2f167def9a775',
        'description': '短问答与简单文档任务；自动选择加速设备。下载约 1 GB，转换后约 2 GB。'},
    'qwenimage21': {'name': 'Qwen Image 2.1 · 可选生图', 'kind': 'image',
        'repository': 'nihui-szyl/qwen-image-ncnn', 'revision': 'main',
        'description': '大型模型；先查看下载量再确认。优先 Vulkan，内存与耗时需实测。'},
}
QWEN_FILES = {'config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json', 'LICENSE'}
QWEN_WEIGHT = 'fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe'
IMAGE_REQUIRED = {'processor/vocab.txt', 'processor/merges.txt', 'text_encoder/text_encoder.ncnn.param',
    'text_encoder/text_encoder.ncnn.bin', 'transformer/input.ncnn.param', 'transformer/input.ncnn.bin',
    'transformer/blocks.ncnn.param', 'transformer/blocks.ncnn.bin', 'transformer/output.ncnn.param',
    'transformer/output.ncnn.bin', 'vae/decoder.ncnn.param', 'vae/decoder.ncnn.bin'}


def safe_name(name: str) -> str:
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('Invalid model filename')
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or name != p.as_posix():
        raise ValueError('Invalid model path')
    return name


def digest(path: Path, algorithm='sha256') -> str:
    h = hashlib.new('sha1' if algorithm == 'git' else algorithm)
    if algorithm == 'git':
        h.update(f'blob {path.stat().st_size}\0'.encode())
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save_json(path: Path, data):
    tmp = path.with_suffix('.tmp')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def open_https(url, timeout=90):
    if not url.startswith('https://huggingface.co/'):
        raise ValueError('Only the fixed Hugging Face source is supported')
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    request = urllib.request.Request(url, headers={'User-Agent': 'LocalAgent-Desktop/0.3'})
    result = urllib.request.urlopen(request, timeout=timeout, context=ctx)
    if not result.url.startswith('https://'):
        result.close()
        raise ValueError('Refusing non-HTTPS redirect')
    return result


def resolve_files(model_id: str) -> dict:
    item = CATALOG[model_id]
    url = f"https://huggingface.co/api/models/{item['repository']}/revision/{item['revision']}?blobs=true"
    with open_https(url, timeout=20) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Model metadata exceeded limit')
    metadata = json.loads(raw)
    revision = metadata['sha']
    if not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('No immutable model revision in source metadata')
    if model_id == 'qwen05' and revision != item['revision']:
        raise ValueError('Wrong Qwen model snapshot')
    files = []
    for source in metadata['siblings']:
        remote = source['rfilename']
        if model_id == 'qwen05':
            if remote not in QWEN_FILES:
                continue
            name = remote
        else:
            if not remote.startswith('qwenimage21/'):
                continue
            name = remote[len('qwenimage21/'):]
            if not name.endswith(('.bin', '.param', '.txt', '.f32')):
                continue
        safe_name(name)
        lfs = source.get('lfs') or {}
        size = source.get('size', lfs.get('size'))
        if type(size) is not int or not 0 < size < 100 * 1024 ** 3:
            raise ValueError('Invalid model file size')
        checksum = lfs.get('sha256') or lfs.get('oid') or source.get('blobId')
        algo = 'sha256' if lfs else 'git'
        if not isinstance(checksum, str) or not re.fullmatch('[a-f0-9]{64}' if lfs else '[a-f0-9]{40}', checksum):
            raise ValueError('Missing source checksum: ' + name)
        if model_id == 'qwen05' and name == 'model.safetensors' and checksum != QWEN_WEIGHT:
            raise ValueError('Official Qwen weight checksum differs from the tested release')
        files.append({'name': name, 'remote': safe_name(remote), 'bytes': size, 'digest': checksum, 'algorithm': algo})
    names = {f['name'] for f in files}
    required = QWEN_FILES if model_id == 'qwen05' else IMAGE_REQUIRED
    if not required.issubset(names) or len(names) != len(files) or len(files) > 100:
        raise ValueError('Required model files are missing or duplicated')
    total = sum(f['bytes'] for f in files)
    return {'id': model_id, 'repository': item['repository'], 'revision': revision, 'files': files,
            'download_bytes': total, 'disk_required_bytes': total + (3 * 1024 ** 3 if model_id == 'qwen05' else 1024 ** 3)}


class ModelHub:
    def __init__(self, home: Path, engines: dict, on_change=None):
        self.home = Path(home).resolve()
        self.models = self.home / 'models'
        self.models.mkdir(parents=True, exist_ok=True)
        self.engines = engines
        self.on_change = on_change or (lambda: None)
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.job = {'status': 'idle'}
        self.quotes = {}
        self.prefs = self.home / 'models.json'

    def installed(self, model_id):
        folder = self.models / model_id
        try:
            m = json.loads((folder / 'READY.json').read_text(encoding='utf-8'))
            return m['id'] == model_id and m['files'] and all(
                (folder / safe_name(f['name'])).is_file() and (folder / f['name']).stat().st_size == f['bytes']
                for f in m['files'])
        except (OSError, KeyError, ValueError):
            return False

    def active(self):
        try:
            p = json.loads(self.prefs.read_text(encoding='utf-8'))
            return {k: v for k, v in p.items() if k in ('llm', 'image') and v in CATALOG
                    and CATALOG[v]['kind'] == k and self.installed(v)}
        except (OSError, ValueError, AttributeError):
            return {}

    def status(self):
        with self.lock:
            active = self.active()
            return {'managed': True, 'home': str(self.home), 'engines': {k: Path(v).is_file() for k, v in self.engines.items()},
                'models': [dict(id=k, **v, installed=bool(self.installed(k)), active=active.get(v['kind']) == k)
                           for k, v in CATALOG.items()], 'job': copy.deepcopy(self.job)}

    def prepare(self, model_id):
        if model_id not in CATALOG:
            raise ValueError('Unknown model')
        quote = resolve_files(model_id)
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.quotes = {token: (time.monotonic(), quote)}
        return {k: v for k, v in quote.items() if k != 'files'} | {'ticket': token}

    def start(self, ticket, consent):
        with self.lock:
            if not isinstance(ticket, str):
                raise ValueError('Invalid download ticket')
            if consent is not True:
                raise ValueError('Explicit download consent required')
            if self.job['status'] in ('downloading', 'converting'):
                raise ValueError('A model installation is already running')
            entry = self.quotes.pop(ticket, None)
            if not entry or time.monotonic() - entry[0] > 900:
                raise ValueError('Download estimate expired; check the model again')
            quote = entry[1]
            if self.installed(quote['id']):
                raise ValueError('Model already installed; use activate')
            if shutil.disk_usage(self.home).free < quote['disk_required_bytes']:
                raise ValueError('Insufficient disk space')
            self.cancelled.clear()
            self.job = {'status': 'downloading', 'model': quote['id'], 'downloaded': 0,
                        'total': quote['download_bytes'], 'file': '', 'revision': quote['revision']}
            threading.Thread(target=self._install, args=(quote,), daemon=True).start()
            return copy.deepcopy(self.job)

    def activate(self, model_id):
        with self.lock:
            if model_id not in CATALOG or not self.installed(model_id):
                raise ValueError('Model is not installed')
            kind = CATALOG[model_id]['kind']
            if not Path(self.engines[kind]).is_file():
                raise ValueError('Bundled engine is missing')
            p = self.active()
            p[kind] = model_id
            save_json(self.prefs, p)
        self.on_change()

    def stop(self):
        self.cancelled.set()

    def _install(self, quote):
        model_id = quote['id']
        cache = self.models / ('.download-' + model_id + '-' + quote['revision'][:12])
        stage = self.models / ('.stage-' + model_id)
        final = self.models / model_id
        cache.mkdir(parents=True, exist_ok=True)
        try:
            if final.exists():
                raise ValueError('Existing model directory was not overwritten')
            if stage.exists():
                shutil.rmtree(stage)
            count = 0
            for item in quote['files']:
                if self.cancelled.is_set():
                    raise InterruptedError('Download cancelled')
                target = cache / item['name']
                target.parent.mkdir(parents=True, exist_ok=True)
                valid = target.is_file() and target.stat().st_size == item['bytes'] and digest(target, item['algorithm']) == item['digest']
                with self.lock:
                    self.job.update(file=item['name'], downloaded=count)
                if not valid:
                    temp = target.with_suffix(target.suffix + '.part')
                    url = f"https://huggingface.co/{quote['repository']}/resolve/{quote['revision']}/{item['remote']}?download=true"
                    got = 0
                    try:
                        with open_https(url) as response, temp.open('wb') as stream:
                            while data := response.read(1024 * 1024):
                                if self.cancelled.is_set():
                                    raise InterruptedError('Download cancelled')
                                got += len(data)
                                if got > item['bytes']:
                                    raise ValueError('Model download exceeded expected size')
                                stream.write(data)
                                with self.lock:
                                    self.job['downloaded'] = count + got
                        if got != item['bytes'] or digest(temp, item['algorithm']) != item['digest']:
                            raise ValueError('Model file checksum or size mismatch: ' + item['name'])
                        os.replace(temp, target)
                    finally:
                        temp.unlink(missing_ok=True)
                count += item['bytes']
            with self.lock:
                self.job.update(status='converting', downloaded=count, file='校验完成，正在准备模型')
            if model_id == 'qwen05':
                from scripts.qwen05_export import export_model
                export_model(cache, stage)
            else:
                os.replace(cache, stage)
            if self.cancelled.is_set():
                raise InterruptedError('Installation cancelled before activation')
            files = [{'name': p.relative_to(stage).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
                     for p in stage.rglob('*') if p.is_file()]
            save_json(stage / 'READY.json', {'id': model_id, 'revision': quote['revision'], 'files': files})
            os.replace(stage, final)
            self.activate(model_id)
            if cache.exists():
                shutil.rmtree(cache)
            with self.lock:
                self.job.update(status='completed', file='已安装并启用')
        except Exception as exc:
            if stage.exists():
                shutil.rmtree(stage)
            with self.lock:
                self.job.update(status='cancelled' if isinstance(exc, InterruptedError) else 'failed', error=str(exc))
