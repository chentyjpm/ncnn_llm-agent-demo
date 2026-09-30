"""Read-only sampled service telemetry. psutil is optional for source-only CLI.

Sampling is on one worker thread (rate counters need a stable thread/objects).
No prompts, argv, credentials or file contents are included in snapshots.
"""
from __future__ import annotations
import copy
import os
from pathlib import Path
import threading
import time


def runtime_snapshot(app):
    with app.lock:
        cfg = copy.deepcopy(app.config)
        active = app.runs.get(app.active)
        recent = list(app.runs.values())[-10:]
        tasks = []
        for r in recent:
            with r.condition:
                event = r.events[-1] if r.events else {}
                backend = r.backend
                choice = getattr(backend, 'device_selection', None)
                tasks.append({'id': r.id, 'mode': r.mode, 'status': r.status,
                    'stage': 'approval_required' if r.approval else event.get('event', 'starting'),
                    'tool': (r.approval or event).get('tool'), 'device': copy.deepcopy(choice)})
        hub = getattr(app, 'model_hub', None)
    model_job = {}
    if hub is not None:
        with hub.lock:
            job = hub.job
            model_job = {k: job[k] for k in ('status', 'model', 'downloaded', 'total', 'file', 'provider') if k in job}
    return {'paused': bool(getattr(app, 'paused', False)), 'task': active.id if active else None,
        'models': {k: {'name': Path(cfg.get(k, {}).get('model', '')).name or '未配置',
                       'device_policy': cfg.get(k, {}).get('device', 'legacy'),
                       'enabled': k == 'llm' or bool(cfg.get(k, {}).get('enabled'))} for k in ('llm', 'image')},
        'tasks': tasks, 'model_job': model_job}


class ResourceSampler:
    def __init__(self, ps=None, gpu=None):
        if ps is None:
            import psutil as ps
        from .gpu_stats import GpuSampler
        self.ps = ps
        self.gpu = gpu if gpu is not None else GpuSampler()
        self.root = ps.Process(os.getpid())
        self.processes = {}
        self.logical = ps.cpu_count() or 1
        self.started = time.monotonic()
        self.primed = False
        self.gpu_data = {'devices': [], 'note': '正在采集 GPU 指标'}
        self.last_gpu = 0
        ps.cpu_percent(interval=None)

    def sample(self):
        ps = self.ps
        mem = ps.virtual_memory()
        cpu = ps.cpu_percent(interval=None)
        rows, live = [], set()
        partial = False
        try:
            children = self.root.children(recursive=True)
        except (ps.NoSuchProcess, ps.AccessDenied):
            children = []; partial = True
        for process in [self.root, *children]:
            try:
                key = (process.pid, process.create_time())
                live.add(key)
                old = self.processes.get(key)
                if old is None:
                    self.processes[key] = process
                    process.cpu_percent(interval=None)
                    utilization = None
                else:
                    process = old
                    utilization = min(100., max(0., process.cpu_percent(interval=None) / self.logical))
                with process.oneshot():
                    rows.append({'pid': process.pid, 'name': process.name(), 'cpu_pct': utilization,
                        'rss_bytes': process.memory_info().rss, 'threads': process.num_threads(), 'status': process.status()})
            except (ps.NoSuchProcess, ps.AccessDenied, ps.ZombieProcess):
                partial = True
        self.processes = {k: v for k, v in self.processes.items() if k in live}
        now = time.monotonic()
        if now - self.last_gpu >= 3:
            try:
                self.gpu_data = self.gpu.sample({r['pid'] for r in rows})
            except Exception as e:
                self.gpu_data = {'devices': [], 'note': f'GPU 采集不可用：{type(e).__name__}: {e}', 'sampled_at': time.time()}
            self.last_gpu = now
        result = {'sampled_at': time.time(), 'system': {'cpu_pct': cpu if self.primed else None,
            'logical_cpus': self.logical, 'memory_total_bytes': mem.total, 'memory_used_bytes': mem.total - mem.available,
            'memory_available_bytes': mem.available, 'memory_pct': mem.percent},
            'application': {'cpu_pct': sum(r['cpu_pct'] or 0 for r in rows) if self.primed else None,
                'rss_bytes': sum(r['rss_bytes'] for r in rows), 'partial': partial,
                'note': '本程序及当前子孙进程；CPU 已按整机逻辑核归一化，RSS 合计可能重复计入共享页。'},
            'processes': rows, 'gpu': copy.deepcopy(self.gpu_data)}
        self.primed = True
        return result

    def close(self):
        self.gpu.close()


class MonitorService:
    def __init__(self, app, sampler_factory=ResourceSampler):
        self.app, self.factory = app, sampler_factory
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.focus = threading.Event()
        self.value = {'sampled_at': None, 'status': 'starting'}
        self.thread = threading.Thread(target=self._loop, name='resource-monitor', daemon=True)
        self.thread.start()

    def _loop(self):
        sampler = None
        try:
            sampler = self.factory()
            while not self.stop.is_set():
                try:
                    value = sampler.sample()
                    value['runtime'] = runtime_snapshot(self.app)
                    value['status'] = 'ok'
                except Exception as e:
                    value = {'status': 'error', 'sampled_at': time.time(), 'error': f'{type(e).__name__}: {e}'}
                with self.lock:
                    self.value = value
                self.stop.wait(1)
        except Exception as e:
            with self.lock:
                self.value = {'status': 'unavailable', 'error': f'{type(e).__name__}: {e}', 'sampled_at': time.time()}
        finally:
            if sampler:
                sampler.close()

    def snapshot(self):
        with self.lock:
            value = copy.deepcopy(self.value)
        value['age_seconds'] = max(0., time.time() - value['sampled_at']) if value.get('sampled_at') else None
        return value

    def pause(self, paused):
        with self.app.lock:
            self.app.paused = bool(paused)
        return bool(paused)

    def cancel_task(self):
        with self.app.lock:
            rid = self.app.active
        if rid:
            self.app.cancel(rid)

    def close(self):
        self.stop.set()
        self.thread.join(5)
