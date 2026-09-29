"""Per-engine Vulkan-first selection, with a bounded native compute probe.

The matching ncnn_device_probe is built with each engine's ncnn revision. Auto
never treats a software Vulkan implementation as hardware acceleration. A probe
failure can select CPU; arbitrary model/inference errors are NOT retried/hidden.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from .paths import PolicyError
from .process import run_process

_CACHE = {}
_LOCK = threading.Lock()


def engine_path(command: list[str]) -> Path | None:
    if not isinstance(command, list) or not command or not isinstance(command[0], str):
        return None
    found = shutil.which(command[0]) or command[0]
    p = Path(found).expanduser().resolve()
    return p if p.is_file() else None


def engine_env(command: list[str]) -> dict[str, str]:
    """Only documented driver selectors, plus an app-local macOS Vulkan runtime.

    Preserve deliberately selected ICDs for CI/driver diagnostics. Never pass
    the whole application environment (which may contain credentials).
    """
    env = {key: os.environ[key] for key in ('VK_DRIVER_FILES', 'VK_ICD_FILENAMES') if key in os.environ}
    executable = engine_path(command)
    if sys.platform == 'darwin' and executable:
        runtime = executable.parent.parent / 'vulkan'
        if (runtime / 'libMoltenVK.dylib').is_file():
            env['DYLD_LIBRARY_PATH'] = str(runtime)
            env['DYLD_FALLBACK_LIBRARY_PATH'] = str(runtime)
    return env


def policy(config: dict, kind: str) -> str:
    if 'device' in config:
        mode = config['device']
    elif kind == 'llm' and 'vulkan' in config:
        if type(config['vulkan']) is not bool:
            raise PolicyError('Legacy vulkan setting must be boolean; use device=auto/cpu/vulkan')
        mode = 'vulkan' if config['vulkan'] else 'cpu'
    elif kind == 'image' and 'gpu' in config:
        mode = 'cpu' if config['gpu'] == -1 else 'vulkan'
    else:
        mode = 'auto'
    if mode not in ('auto', 'cpu', 'vulkan'):
        raise PolicyError('device must be auto, cpu or vulkan')
    return mode


def validate_report(report: dict) -> dict:
    if not isinstance(report, dict) or type(report.get('version')) is not int or report.get('version') != 1 or type(report.get('compiled')) is not bool:
        raise ValueError('Invalid Vulkan probe protocol')
    devices = report.get('devices')
    if not isinstance(devices, list) or len(devices) > 16:
        raise ValueError('Invalid Vulkan device list')
    ids = set()
    for item in devices:
        if not isinstance(item, dict) or type(item.get('id')) is not int or not 0 <= item['id'] < 16 or item['id'] in ids:
            raise ValueError('Invalid/duplicate Vulkan device ID')
        ids.add(item['id'])
        if not isinstance(item.get('name'), str) or len(item['name']) > 256:
            raise ValueError('Invalid Vulkan device name')
        if type(item.get('hardware')) is not bool or type(item.get('compute_ok')) is not bool:
            raise ValueError('Invalid Vulkan capability flags')
        if item.get('type') not in ('discrete', 'integrated', 'virtual', 'cpu', 'other'):
            raise ValueError('Invalid Vulkan device type')
    if not report['compiled'] and devices:
        raise ValueError('CPU-only probe cannot expose Vulkan devices')
    return report


def probe(command: list[str], *, software: bool = False) -> dict:
    executable = engine_path(command)
    if executable is None:
        return {'version': 1, 'compiled': False, 'devices': [], 'reason': 'engine_missing'}
    path = executable.with_name('ncnn_device_probe' + ('.exe' if os.name == 'nt' else ''))
    if not path.is_file():
        return {'version': 1, 'compiled': False, 'devices': [], 'reason': 'matching_probe_missing'}
    env = engine_env(command)
    info = path.stat()
    key = (str(path), info.st_mtime_ns, info.st_size, software, tuple(sorted(env.items())))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 60:
            return copy.deepcopy(cached[1])
    try:
        result = run_process([str(path)] + (['--include-software'] if software else []),
                             cwd=path.parent, timeout=15, max_output=65536, env=env)
        if result['timed_out']:
            raise RuntimeError('probe_timeout')
        if result['returncode'] != 0 or result['output_truncated']:
            raise RuntimeError('probe_process_failed')
        report = validate_report(json.loads(result['stdout']))
    except (OSError, ValueError, RuntimeError) as exc:
        report = {'version': 1, 'compiled': False, 'devices': [], 'reason': 'probe_unavailable',
                  'detail': str(exc)[:500]}
    with _LOCK:
        if len(_CACHE) >= 32:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic(), report)
    return copy.deepcopy(report)


def select(config: dict, kind: str, *, report: dict | None = None) -> dict:
    """report injection is for policy unit tests; HTTP never accepts it."""
    mode = policy(config, kind)
    choice = {'requested': mode, 'selected': 'cpu', 'gpu': -1, 'name': 'CPU',
              'reason': 'explicit_cpu', 'hardware': False, 'probe_scope': 'ncnn Vulkan ReLU; not full-model validation'}
    if mode == 'cpu':
        return choice
    allow_software = mode == 'vulkan' and config.get('allow_software_vulkan') is True
    data = validate_report(report) if report is not None else probe(config.get('command', []), software=allow_software)
    candidates = [d for d in data['devices'] if d['compute_ok'] and (d['hardware'] or allow_software)]
    preferred = config.get('gpu', None)
    if preferred is not None and (type(preferred) is not int or preferred < 0):
        raise PolicyError('gpu must be a non-negative device index when device is auto/vulkan')
    if preferred is not None:
        candidates = [d for d in candidates if d['id'] == preferred]
    ranks = {'discrete': 0, 'integrated': 1, 'virtual': 2, 'other': 3, 'cpu': 4}
    candidates.sort(key=lambda d: (ranks[d['type']], d['id']))
    if candidates:
        selected = candidates[0]
        return dict(choice, selected='vulkan', gpu=selected['id'], name=selected['name'],
                    reason='vulkan_compute_verified', hardware=selected['hardware'])
    reason = data.get('reason') or ('vulkan_not_compiled' if not data['compiled'] else
             'no_usable_hardware_vulkan' if preferred is None else 'requested_device_unavailable')
    if mode == 'vulkan':
        raise PolicyError('Explicit Vulkan request unavailable: ' + reason + '; use device=auto for CPU fallback')
    return dict(choice, reason=reason)


def describe(config: dict, kind: str) -> dict:
    try:
        return select(config, kind)
    except PolicyError as exc:
        return {'requested': config.get('device', 'vulkan'), 'selected': 'unavailable', 'gpu': -1,
                'name': '不可用', 'reason': str(exc), 'hardware': False}
