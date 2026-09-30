"""Local model center: chosen source, real byte progress, explicit consent.
Completed verified files can be reused on retry. Partial files are never active.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import time
from .model_catalog import CATALOG, PROFILES, PROVIDERS
from .model_sources import safe_name, digest, open_https, resolve_manifest, file_url, QWEN_FILES, IMAGE_REQUIRED

QWEN_WEIGHT = PROFILES['qwen05']['weight_sha256']
BUSY = ('downloading', 'verifying', 'converting', 'activating')


def save_json(path: Path, data):
    tmp = path.with_suffix('.tmp')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def resolve_files(model_id, provider='huggingface'):
    return resolve_manifest(model_id, provider, opener=open_https)


class ModelHub:
    def __init__(self, home: Path, engines: dict, on_change=None):
        self.home = Path(home).resolve()
        self.models = self.home / 'models'
        self.models.mkdir(parents=True, exist_ok=True)
        self.engines = engines
        self.on_change = on_change or (lambda: None)
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.job = {'status':'idle'}
        self.quotes = {}
        self.prefs = self.home / 'models.json'
        self.job_path = self.home / 'model-job.json'
        self._last_save = 0.0
        self._sample = None
        try:
            previous = json.loads(self.job_path.read_text(encoding='utf-8'))
            if isinstance(previous, dict) and previous.get('model') in CATALOG:
                self.job = previous
                if previous.get('status') in BUSY:
                    self.job.update(status='interrupted', error='服务已重启，安装中断。重试会复用完整且校验通过的文件。')
        except (OSError, ValueError):
            pass

    def installed(self, model_id):
        if model_id not in CATALOG or not CATALOG[model_id].get('installable', True): return False
        folder = self.models / model_id
        try:
            m = json.loads((folder / 'READY.json').read_text(encoding='utf-8'))
            return bool(m['id'] == model_id and m['files'] and all(
                (folder / safe_name(f['name'])).is_file() and (folder / f['name']).stat().st_size == f['bytes']
                for f in m['files']))
        except (OSError, KeyError, ValueError, TypeError):
            return False

    def active(self):
        try:
            p = json.loads(self.prefs.read_text(encoding='utf-8'))
            return {k:v for k,v in p.items() if k in ('llm','image') and isinstance(v,str) and v in CATALOG
                    and CATALOG[v]['kind'] == k and self.installed(v)}
        except (OSError, ValueError, AttributeError):
            return {}

    def _update(self, **changes):
        with self.lock:
            before = self.job.get('status')
            self.job.update(changes, updated_at=time.time())
            now = time.monotonic()
            if now - self._last_save > 1 or before != self.job['status']:
                save_json(self.job_path, self.job)
                self._last_save = now

    def _bytes(self, count, network_count):
        now = time.monotonic()
        with self.lock:
            if self._sample:
                at, network = self._sample
                if now - at >= .2:
                    rate = (network_count - network) / (now - at)
                    self.job['speed_bps'] = rate
                    self._sample = (now, network_count)
            else:
                self._sample = (now, network_count)
            self.job['last_data_at'] = time.time()
        self._update(downloaded=count, network_bytes=network_count)

    def status(self):
        with self.lock:
            active, job = self.active(), copy.deepcopy(self.job)
            if job.get('started_at'):
                job['elapsed_seconds'] = max(0, int((job.get('finished_at') or time.time()) - job['started_at']))
            if job.get('status') in BUSY:
                waiting = time.time() - job.get('last_data_at', job.get('started_at', time.time()))
                stalled = job['status'] == 'downloading' and waiting > 10
                job['waiting_for_data'] = stalled
                rate = job.get('speed_bps', 0) if job['status'] == 'downloading' and not stalled else 0
                job['speed_bps'] = rate
                job['eta_seconds'] = int((job.get('total',0)-job.get('downloaded',0))/rate) if rate > 0 else None
            if job.get('total',0) > 0:
                job['download_percent'] = round(min(100, 100*job.get('downloaded',0)/job['total']), 1)
            return {'managed':True, 'home':str(self.home), 'providers':PROVIDERS,
                'engines':{k:Path(v).is_file() for k,v in self.engines.items()},
                'models':[dict(id=k, **v, installed=self.installed(k), active=active.get(v['kind']) == k)
                          for k,v in CATALOG.items()], 'job':job}

    def prepare(self, model_id, provider='huggingface'):
        quote = resolve_files(model_id, provider)
        token = secrets.token_urlsafe(24)
        with self.lock:
            # One immutable consent ticket binds source + model + files, not user-editable URLs.
            self.quotes = {token:(time.monotonic(), quote)}
        free = shutil.disk_usage(self.home).free
        return {k:v for k,v in quote.items() if k != 'files'} | {'ticket':token, 'disk_free_bytes':free,
                    'enough_space':free >= quote['disk_required_bytes']}

    def start(self, ticket, consent):
        with self.lock:
            if not isinstance(ticket,str): raise ValueError('Invalid download ticket')
            if consent is not True: raise ValueError('Explicit download consent required')
            if self.job['status'] in BUSY: raise ValueError('已有模型正在安装，请先完成或取消')
            entry = self.quotes.pop(ticket, None)
            if not entry or time.monotonic()-entry[0] > 900: raise ValueError('大小查询已过期，请重新查询后确认')
            quote = entry[1]
            if self.installed(quote['id']): raise ValueError('模型已安装，选择启用即可')
            if shutil.disk_usage(self.home).free < quote['disk_required_bytes']: raise ValueError('可用磁盘空间不足')
            self.cancelled.clear()
            self._sample = None
            self.job = {'status':'downloading', 'model':quote['id'], 'provider':quote.get('provider','huggingface'),
                'downloaded':0, 'network_bytes':0, 'total':quote['download_bytes'], 'file':'',
                'revision':quote['revision'], 'started_at':time.time(), 'total_files':len(quote['files']),
                'verified_files':0, 'message':'正在连接下载源…', 'speed_bps':0, 'cancel_requested':False}
            self._update()
            threading.Thread(target=self._install, args=(quote,), daemon=True).start()
            return copy.deepcopy(self.job)

    def activate(self, model_id, *, _installation=False):
        with self.lock:
            if self.job['status'] in BUSY and not _installation: raise ValueError('安装进行中，暂不能切换模型')
            if not isinstance(model_id,str) or model_id not in CATALOG or not self.installed(model_id):
                raise ValueError('Model is not installed')
            kind = CATALOG[model_id]['kind']
            if not Path(self.engines[kind]).is_file(): raise ValueError('Bundled engine is missing')
            prefs = self.active(); prefs[kind] = model_id
            save_json(self.prefs, prefs)
        self.on_change()

    def stop(self):
        self.cancelled.set()
        if self.job['status'] in BUSY:
            self._update(cancel_requested=True, message='正在取消；等待当前网络读取或转换步骤返回，不会启用未完成模型。')

    def _check_stop(self):
        if self.cancelled.is_set(): raise InterruptedError('安装已取消，完整且校验通过的下载文件可在重试时复用')

    def _install(self, quote):
        model_id = quote['id']
        provider = quote.get('provider','huggingface')
        cache = self.models / f'.download-{model_id}-{provider}-{quote["revision"][:12]}'
        stage = self.models / ('.stage-' + model_id)
        final = self.models / model_id
        try:
            cache.mkdir(parents=True, exist_ok=True)
            if final.exists(): raise ValueError('Existing model directory was not overwritten')
            if stage.exists(): shutil.rmtree(stage)
            count, transferred = 0, 0
            for index, item in enumerate(quote['files'],1):
                self._check_stop()
                target = cache / safe_name(item['name']); target.parent.mkdir(parents=True, exist_ok=True)
                self._update(status='verifying', file=item['name'], file_index=index, message='检查已下载文件')
                valid = target.is_file() and target.stat().st_size == item['bytes'] and digest(target,item['algorithm'],self.cancelled.is_set) == item['digest']
                if not valid:
                    temp = target.with_suffix(target.suffix + '.part')
                    got = 0
                    self._update(status='downloading', message='连接下载源，等待数据…', file_downloaded=0, file_total=item['bytes'])
                    try:
                        with open_https(file_url(quote,item)) as response, temp.open('wb') as stream:
                            # Small reads keep visible progress moving, not waiting for an entire MiB.
                            while data := response.read(64*1024):
                                self._check_stop(); got += len(data); transferred += len(data)
                                if got > item['bytes']: raise ValueError('文件大小超过下载清单，已停止')
                                stream.write(data)
                                self._bytes(count+got,transferred)
                                self._update(file_downloaded=got, message='正在下载')
                        self._update(status='verifying', message='正在核对文件校验和')
                        if got != item['bytes'] or digest(temp,item['algorithm'],self.cancelled.is_set) != item['digest']:
                            raise ValueError('文件大小或校验和不匹配：' + item['name'])
                        os.replace(temp,target)
                    finally:
                        temp.unlink(missing_ok=True)
                count += item['bytes']
                self._update(downloaded=count, verified_files=index)
            self._check_stop()
            self._update(status='converting', message='下载完成，正在转换／准备本地模型', speed_bps=0)
            if model_id in PROFILES:
                from scripts.qwen05_export import export_model
                def progress(done,total,message):
                    self._check_stop()
                    self._update(stage_done=done, stage_total=total, message=message)
                export_model(cache,stage,model_key=model_id,progress=progress,cancelled=self.cancelled.is_set)
            else:
                if CATALOG[model_id].get('install_kind') == 'ncnn':
                    from .native_models import validate_install
                    validate_install(cache, model_id)
                os.replace(cache,stage)
            self._check_stop()
            self._update(status='activating', message='准备完成，正在校验安装文件', stage_done=0, stage_total=None)
            all_files = [p for p in stage.rglob('*') if p.is_file()]
            files = []
            for index,p in enumerate(all_files,1):
                self._check_stop()
                files.append({'name':p.relative_to(stage).as_posix(),'bytes':p.stat().st_size,'sha256':digest(p,cancelled=self.cancelled.is_set)})
                self._update(stage_done=index,stage_total=len(all_files),file=p.name)
            save_json(stage/'READY.json', {'id':model_id,'provider':provider,'revision':quote['revision'],'files':files})
            self._check_stop(); os.replace(stage,final)
            self.activate(model_id, _installation=True)
            if cache.exists(): shutil.rmtree(cache)
            self._update(status='completed', message='已安装并启用', finished_at=time.time(), file='', speed_bps=0)
        except Exception as exc:
            if stage.exists(): shutil.rmtree(stage)
            self._update(status='cancelled' if isinstance(exc,InterruptedError) else 'failed',
                error=f'{type(exc).__name__}: {exc}', message='安装未完成；没有自动更换下载源。', finished_at=time.time(), speed_bps=0)
