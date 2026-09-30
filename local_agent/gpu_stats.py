"""Read-only GPU telemetry, separate from Vulkan device selection.

Windows: language-neutral WDDM PDH counters (all vendors when exposed).
NVIDIA fallback: driver-provided nvidia-smi; Linux AMD: kernel sysfs counters.
Missing/unsupported fields are None, never guessed as zero. No driver install.
"""
from __future__ import annotations
import csv
import ctypes as C
import math
import os
from pathlib import Path
import re
import shutil
import sys
from .process import run_process


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError):
        return None


def parse_nvidia(text):
    devices = []
    for row in csv.reader(text.splitlines()):
        if len(row) != 6:
            continue
        uuid, name, util, used, total, temp = [v.strip() for v in row]
        def mib(v):
            n = number(v)
            return int(n * 1048576) if n is not None else None
        devices.append({'id': uuid, 'name': name, 'source': 'nvidia-smi (whole GPU)',
            'utilization_pct': number(util), 'dedicated_used_bytes': mib(used),
            'dedicated_total_bytes': mib(total), 'shared_used_bytes': None,
            'app_dedicated_bytes': None, 'temperature_c': number(temp)})
    return devices


def pdh_devices(counters, pids):
    """Aggregate by LUID/physical adapter, not Vulkan's unrelated device index.

    GPU utilization = busiest engine after summing its process instances.
    Process GPU memory is separate from whole-adapter memory.
    """
    devices, engine = {}, {}
    for key, values in counters.items():
        for name, value in values.items():
            n = number(value)
            match = re.search(r'(luid_0x[\da-f]+_0x[\da-f]+_phys_\d+)', name, re.I)
            if not match or n is None:
                continue
            ident = match[1].lower()
            d = devices.setdefault(ident, {'id': ident, 'name': 'WDDM ' + ident,
                'source': 'Windows PDH/WDDM (whole adapter)', 'utilization_pct': None,
                'dedicated_used_bytes': None, 'dedicated_total_bytes': None,
                'shared_used_bytes': None, 'app_dedicated_bytes': None, 'temperature_c': None})
            if key == 'engine':
                eng = re.search(r'_eng_(\d+)', name)
                if eng:
                    index = (ident, eng[1])
                    engine[index] = engine.get(index, 0) + n
            elif key in ('dedicated', 'shared'):
                d['dedicated_used_bytes' if key == 'dedicated' else 'shared_used_bytes'] = int(n)
            elif key == 'process_dedicated':
                pid = re.search(r'^pid_(\d+)_', name)
                if pid and int(pid[1]) in pids:
                    d['app_dedicated_bytes'] = (d['app_dedicated_bytes'] or 0) + int(n)
    for (ident, _), utilization in engine.items():
        d = devices[ident]
        d['utilization_pct'] = min(100., max(d['utilization_pct'] or 0., utilization))
    return list(devices.values())


class WindowsPDH:
    """Use pointer-sized query handles, fixed-width PDH status and aligned unions."""
    def __init__(self):
        from ctypes import wintypes as W
        self.dll = C.WinDLL('pdh.dll')
        self.query = C.c_void_p()
        self.handles = {}
        self.errors = []
        self.primed = False
        self.dll.PdhOpenQueryW.argtypes = [W.LPCWSTR, C.c_size_t, C.POINTER(C.c_void_p)]
        self.dll.PdhAddEnglishCounterW.argtypes = [C.c_void_p, W.LPCWSTR, C.c_size_t, C.POINTER(C.c_void_p)]
        self.dll.PdhCollectQueryData.argtypes = [C.c_void_p]
        self.dll.PdhGetFormattedCounterArrayW.argtypes = [C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.POINTER(W.DWORD), C.c_void_p]
        self.dll.PdhCloseQuery.argtypes = [C.c_void_p]
        for n in ('PdhOpenQueryW', 'PdhAddEnglishCounterW', 'PdhCollectQueryData', 'PdhGetFormattedCounterArrayW', 'PdhCloseQuery'):
            getattr(self.dll, n).restype = W.LONG
        status = self.dll.PdhOpenQueryW(None, 0, C.byref(self.query))
        if status:
            raise OSError(f'PdhOpenQuery: 0x{status & 0xffffffff:08x}')
        try:
            for key, path in {'engine': r'\GPU Engine(*)\Utilization Percentage',
                'dedicated': r'\GPU Adapter Memory(*)\Dedicated Usage',
                'shared': r'\GPU Adapter Memory(*)\Shared Usage',
                'process_dedicated': r'\GPU Process Memory(*)\Dedicated Usage'}.items():
                handle = C.c_void_p()
                status = self.dll.PdhAddEnglishCounterW(self.query, path, 0, C.byref(handle))
                if status == 0:
                    self.handles[key] = handle
                else:
                    self.errors.append(f'{key}: 0x{status & 0xffffffff:08x}')
            self.dll.PdhCollectQueryData(self.query)  # Prime rate counters, never report first zero as utilization.
        except Exception:
            self.close()
            raise

    def sample(self, pids):
        from ctypes import wintypes as W
        class Union(C.Union):
            _fields_ = [('doubleValue', C.c_double), ('largeValue', C.c_longlong), ('longValue', W.LONG), ('stringValue', C.c_void_p)]
        class Value(C.Structure):
            _anonymous_ = ('data',)
            _fields_ = [('status', W.DWORD), ('data', Union)]
        class Item(C.Structure):
            _fields_ = [('name', W.LPWSTR), ('value', Value)]
        status = self.dll.PdhCollectQueryData(self.query)
        if status:
            return [], f'PDH 无有效采样: 0x{status & 0xffffffff:08x}'
        counters = {}
        for key, handle in self.handles.items():
            size, count = W.DWORD(), W.DWORD()
            self.dll.PdhGetFormattedCounterArrayW(handle, 0x200 | 0x8000, C.byref(size), C.byref(count), None)
            if not 0 < size.value <= 8 * 1024 * 1024:
                continue
            buf = C.create_string_buffer(size.value)
            rc = self.dll.PdhGetFormattedCounterArrayW(handle, 0x200 | 0x8000, C.byref(size), C.byref(count), buf)
            if rc or count.value * C.sizeof(Item) > len(buf):
                continue
            values = C.cast(buf, C.POINTER(Item))
            counters[key] = {values[i].name: values[i].value.doubleValue for i in range(count.value)
                             if values[i].name and values[i].value.status in (0, 1)}
        devices = pdh_devices(counters, pids)
        if not self.primed:
            for device in devices:
                device['utilization_pct'] = None
        self.primed = True
        return devices, ('; '.join(self.errors) if devices else '驱动未提供 WDDM GPU 性能计数器，不能测量显存/利用率。')

    def close(self):
        if self.query:
            self.dll.PdhCloseQuery(self.query)
            self.query = C.c_void_p()


def dxgi_adapters():
    """Read adapter names and dedicated-memory capacity; does not create a GPU device."""
    import uuid
    from ctypes import wintypes as W
    class GUID(C.Structure):
        _fields_ = [('a', C.c_uint32), ('b', C.c_uint16), ('c', C.c_uint16), ('d', C.c_ubyte * 8)]
    class LUID(C.Structure):
        _fields_ = [('low', W.DWORD), ('high', W.LONG)]
    class Desc(C.Structure):
        _fields_ = [('name', W.WCHAR * 128), ('vendor', W.UINT), ('device', W.UINT),
            ('subsystem', W.UINT), ('revision', W.UINT), ('dedicated_video', C.c_size_t),
            ('dedicated_system', C.c_size_t), ('shared_system', C.c_size_t), ('luid', LUID), ('flags', W.UINT)]
    dll = C.WinDLL('dxgi.dll')
    dll.CreateDXGIFactory1.argtypes = [C.POINTER(GUID), C.POINTER(C.c_void_p)]
    dll.CreateDXGIFactory1.restype = W.LONG
    guid = GUID.from_buffer_copy(uuid.UUID('770aae78-f26f-4dba-a829-253c83d1b387').bytes_le)
    factory = C.c_void_p()
    if dll.CreateDXGIFactory1(C.byref(guid), C.byref(factory)) != 0:
        return {}
    def method(obj, index, *args):
        table = C.cast(obj, C.POINTER(C.POINTER(C.c_void_p))).contents
        return C.WINFUNCTYPE(W.LONG, C.c_void_p, *args)(table[index])
    result = {}
    try:
        for index in range(16):
            adapter = C.c_void_p()
            if method(factory, 12, W.UINT, C.POINTER(C.c_void_p))(factory, index, C.byref(adapter)) != 0:
                break
            try:
                desc = Desc()
                if method(adapter, 10, C.POINTER(Desc))(adapter, C.byref(desc)) == 0:
                    key = f'luid_0x{desc.luid.high & 0xffffffff:08x}_0x{desc.luid.low:08x}'
                    result[key] = {'name': desc.name, 'total': desc.dedicated_video}
            finally:
                method(adapter, 2)(adapter)
    finally:
        method(factory, 2)(factory)
    return result


class GpuSampler:
    def __init__(self):
        self.pdh = None
        self.error = ''
        self.adapters = {}
        if os.name == 'nt':
            try:
                self.pdh = WindowsPDH()
                self.adapters = dxgi_adapters()
            except (OSError, AttributeError) as e:
                self.error = str(e)
        self.smi = shutil.which('nvidia-smi')
        if not self.smi and os.name == 'nt':
            p = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/nvidia-smi.exe'
            if p.is_file():
                self.smi = str(p)

    def sample(self, pids):
        devices, errors = [], []
        if self.pdh:
            try:
                devices, message = self.pdh.sample(pids)
                if message:
                    errors.append(message)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        if not devices and self.smi:
            try:
                r = run_process([self.smi, '--query-gpu=uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu',
                                 '--format=csv,noheader,nounits'], cwd=Path(self.smi).parent, timeout=3, max_output=16384)
                if r['returncode'] == 0 and not r['timed_out']:
                    devices = parse_nvidia(r['stdout'])
                else:
                    errors.append('nvidia-smi 采集失败/超时')
            except OSError as exc:
                errors.append(str(exc))
        if not devices and sys.platform.startswith('linux'):
            for p in sorted(Path('/sys/class/drm').glob('card[0-9]*/device')):
                if not (p / 'mem_info_vram_used').is_file():
                    continue
                def read(name):
                    try:
                        return number((p / name).read_text().strip())
                    except OSError:
                        return None
                devices.append({'id': p.parent.name, 'name': p.parent.name + ' (DRM)', 'source': 'Linux DRM sysfs (whole GPU)',
                    'utilization_pct': read('gpu_busy_percent'), 'dedicated_used_bytes': read('mem_info_vram_used'),
                    'dedicated_total_bytes': read('mem_info_vram_total'), 'shared_used_bytes': None,
                    'app_dedicated_bytes': None, 'temperature_c': None})
        if not devices:
            errors.append(self.error or ('macOS 未接入 GPU 性能采集；统一内存不能冒充独立显存。' if sys.platform == 'darwin'
                                        else 'GPU 性能指标不可用：无可读驱动计数器。不表示没有 GPU，也不改变 Vulkan 选择。'))
        for device in devices:
            ident = device['id'].rsplit('_phys_', 1)[0]
            metadata = self.adapters.get(ident)
            if metadata:
                device['name'] = metadata['name']
                device['dedicated_total_bytes'] = metadata['total']
        return {'devices': devices, 'note': '; '.join(errors), 'sampled_at': __import__('time').time()}

    def close(self):
        if self.pdh:
            self.pdh.close()
