"""Actual OS processes/HTTP tests; GPU parsers use explicit counter fixtures."""
import copy
import http.client
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from local_agent.process import run_process, background_options
from local_agent.rpc import StdioRPC
from local_agent.gpu_stats import number, parse_nvidia, pdh_devices
from local_agent.monitor import ResourceSampler, MonitorService, runtime_snapshot
from local_agent.monitor_http import ManagedWebApp, ManagedServer

HAS_PS = importlib.util.find_spec('psutil') is not None
CONSOLE_INFO = '''import ctypes as C, json, sys
k=C.windll.kernel32
k.GetConsoleWindow.restype=C.c_void_p
k.GetStdHandle.argtypes=[C.c_uint32];k.GetStdHandle.restype=C.c_void_p
k.GetFileType.argtypes=[C.c_void_p];k.GetFileType.restype=C.c_uint32
info={'hwnd':k.GetConsoleWindow(), 'cp':k.GetConsoleCP(),
      'stdout_type':k.GetFileType(k.GetStdHandle(-11)),
      'stderr_type':k.GetFileType(k.GetStdHandle(-12))}
'''
CONSOLE = CONSOLE_INFO + '''print(json.dumps(info),flush=True)
print('diagnostic preserved',file=sys.stderr,flush=True)
'''


def console_evidence(kind, data):
    folder = Path(__file__).resolve().parents[1] / 'reports/monitor'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ('windows-console-' + kind + '.json')).write_text(json.dumps(data, indent=2), encoding='utf-8')



class BackgroundTests(unittest.TestCase):
    def test_nonwindows_gets_no_windows_flags(self):
        with patch('local_agent.process.os', SimpleNamespace(name='posix')):
            self.assertEqual(background_options(), {})

    def test_windows_options_not_detached(self):
        fake = SimpleNamespace(STARTUPINFO=lambda: SimpleNamespace(dwFlags=0), STARTF_USESHOWWINDOW=1,
                               SW_HIDE=0, CREATE_NO_WINDOW=0x08000000)
        with patch('local_agent.process.os', SimpleNamespace(name='nt')), patch('local_agent.process.subprocess', fake):
            options = background_options()
            self.assertEqual(options['creationflags'], 0x08000000)
            self.assertEqual(options['startupinfo'].dwFlags, 1)
            self.assertEqual(options['startupinfo'].wShowWindow, 0)

    def test_background_preserves_stdout_stderr_and_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_process([sys.executable, '-c', "import sys; print('output'); print('diagnostic',file=sys.stderr); sys.exit(7)"], cwd=Path(tmp))
            self.assertEqual(r['returncode'], 7)
            self.assertIn('output', r['stdout']); self.assertIn('diagnostic', r['stderr'])

    def test_background_timeout_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_process([sys.executable, '-c', 'import time; time.sleep(10)'], cwd=Path(tmp), timeout=.1)
            self.assertTrue(r['timed_out']); self.assertNotEqual(r['returncode'], 0)

    @unittest.skipUnless(os.name == 'nt', 'Windows console API only')
    def test_windows_actual_worker_has_no_console(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run_process([sys.executable, '-c', CONSOLE], cwd=Path(tmp))
            self.assertEqual(r['returncode'], 0, r)
            data = json.loads(r['stdout'])
            self.assertFalse(data['hwnd']); self.assertEqual(data['stdout_type'], 3)
            self.assertEqual(data['stderr_type'], 3)
            console_evidence('worker', data)
            self.assertIn('diagnostic preserved', r['stderr'])

    @unittest.skipUnless(os.name == 'nt', 'Windows console API only')
    def test_windows_actual_rpc_has_no_console(self):
        script = CONSOLE_INFO + '''for line in sys.stdin:
 m=json.loads(line)
 if 'id' in m:
  print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':info}),flush=True)
'''
        with tempfile.TemporaryDirectory() as tmp:
            with StdioRPC([sys.executable, '-u', '-c', script], cwd=Path(tmp)) as rpc:
                data = rpc.request('ping')
                self.assertFalse(data['hwnd']); self.assertEqual(data['stdout_type'], 3)
                self.assertEqual(data['stderr_type'], 3)
                console_evidence('rpc', data)


class GpuParserTests(unittest.TestCase):
    def test_unavailable_not_zero(self):
        for value in ('N/A', '[Not Supported]', 'nan', '-1', 'inf', None):
            self.assertIsNone(number(value))
        self.assertEqual(number('0'), 0)

    def test_nvidia_units_and_scope(self):
        devices = parse_nvidia('GPU-uuid, Example, 22, 512, 4096, 42')
        d = devices[0]
        self.assertEqual(d['dedicated_used_bytes'], 512*1048576)
        self.assertIsNone(d['app_dedicated_bytes'])
        self.assertIn('whole GPU', d['source'])

    def test_nvidia_unsupported_fields(self):
        d = parse_nvidia('GPU-a, Card, N/A, [Not Supported], 8192, N/A')[0]
        self.assertIsNone(d['utilization_pct']); self.assertIsNone(d['dedicated_used_bytes'])

    def test_malformed_nvidia_is_not_success(self):
        self.assertEqual(parse_nvidia('error message'), [])

    def test_pdh_busiest_engine_not_sum_all_engines(self):
        ident='luid_0x00000000_0x0000abcd_phys_0'
        counters={'engine':{f'pid_11_{ident}_eng_0_engtype_Compute':30,
                            f'pid_12_{ident}_eng_0_engtype_Compute':25,
                            f'pid_11_{ident}_eng_1_engtype_Copy':40},
                  'dedicated':{ident:4096},'process_dedicated':{f'pid_11_{ident}':1024,f'pid_12_{ident}':2048}}
        d=pdh_devices(counters,{11})[0]
        self.assertEqual(d['utilization_pct'],55)
        self.assertEqual(d['dedicated_used_bytes'],4096)
        self.assertEqual(d['app_dedicated_bytes'],1024)
        self.assertIsNone(d['dedicated_total_bytes'])

    def test_multiple_adapters_keep_separate(self):
        ds=pdh_devices({'shared':{'luid_0x0_0x1_phys_0':12,'luid_0x0_0x2_phys_0':34}},set())
        self.assertEqual(len(ds),2)
        self.assertEqual(sorted(d['shared_used_bytes'] for d in ds),[12,34])

    def test_no_process_counter_does_not_fake_zero(self):
        d=pdh_devices({'dedicated':{'luid_0x0_0x1_phys_0':12}},set())[0]
        self.assertIsNone(d['app_dedicated_bytes']);self.assertIsNone(d['utilization_pct'])

    def test_invalid_counter_names_and_nan_ignored(self):
        self.assertEqual(pdh_devices({'dedicated':{'evil':4,'luid_0x0_0x1_phys_0':float('nan')}},set()),[])

    @unittest.skipUnless(os.name=='nt','Windows PDH only')
    def test_real_pdh_provider_does_not_require_a_gpu(self):
        from local_agent.gpu_stats import WindowsPDH
        p=WindowsPDH()
        try:
            data,note=p.sample({os.getpid()})
            self.assertIsInstance(data,list)
            if not data:self.assertTrue(note)
        finally:p.close()


@unittest.skipUnless(HAS_PS,'psutil optional for source CLI; required in desktop CI')
class SamplerTests(unittest.TestCase):
    def test_real_cpu_memory_and_no_private_fields(self):
        sampler=ResourceSampler()
        try:
            first=sampler.sample();self.assertIsNone(first['system']['cpu_pct'])
            time.sleep(.12);second=sampler.sample()
            self.assertGreater(second['system']['memory_total_bytes'],0)
            self.assertGreater(second['application']['rss_bytes'],0)
            self.assertTrue(0<=second['system']['cpu_pct']<=100)
            self.assertTrue(any(p['pid']==os.getpid() for p in second['processes']))
            for p in second['processes']:
                self.assertNotIn('cmdline',p);self.assertNotIn('environ',p)
        finally:sampler.close()

    def test_actual_child_appears_then_is_removed(self):
        sampler=ResourceSampler()
        p=subprocess.Popen([sys.executable,'-c','import time; data=bytearray(32*1024*1024); time.sleep(10)'],**background_options())
        try:
            deadline=time.monotonic()+3
            while True:
                s=sampler.sample();children=[r for r in s['processes'] if r['pid']==p.pid]
                if children and children[0]['rss_bytes']>20*1024*1024:break
                if time.monotonic()>deadline:self.fail('child resources not detected')
                time.sleep(.05)
            p.terminate();p.wait(3)
            self.assertFalse(any(r['pid']==p.pid for r in sampler.sample()['processes']))
        finally:
            if p.poll() is None:p.kill();p.wait()
            sampler.close()


class MonitorHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.app=ManagedWebApp({'workspace':str(self.root/'workspace'),'llm':{'model':'model','device':'auto'}},self.root/'state')
        self.server=ManagedServer(self.app,0)
        self.thread=threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.01),daemon=True);self.thread.start()
    def tearDown(self):
        if self.app.monitor:self.app.monitor.close()
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.tmp.cleanup()
    def request(self,path,method='GET',data=None,auth=True):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            headers={'Content-Type':'application/json'}
            if auth:headers['X-Agent-Token']=self.app.token
            c.request(method,path,body=json.dumps(data) if data is not None else None,headers=headers)
            r=c.getresponse();return r.status,json.loads(r.read())
        finally:c.close()
    def test_metrics_auth_required(self):
        self.assertEqual(self.request('/api/monitor',auth=False)[0],403)
    def test_headless_monitor_not_fake(self):
        self.assertEqual(self.request('/api/monitor')[1]['status'],'not_started')
    def test_pause_rejects_new_tasks_but_preserves_session(self):
        sid=self.app.sessions.new()['id'];self.app.paused=True
        status,body=self.request(f'/api/sessions/{sid}/messages','POST',{'message':'hello'})
        self.assertEqual(status,409);self.assertIn('暂停',body['error'])
        self.assertEqual(self.app.sessions.get(sid)['messages'],[])
    def test_runtime_snapshot_does_not_run_device_probes(self):
        with patch('local_agent.device.probe',side_effect=AssertionError('Must not probe from monitor')):
            s=runtime_snapshot(self.app)
        self.assertEqual(s['models']['llm']['device_policy'],'auto')
    def test_snapshot_excludes_prompts_and_arguments(self):
        r=SimpleNamespace(id='1',mode='agent',status='running',approval=None,backend=None,
            condition=threading.Condition(),events=[{'event':'tool_start','tool':'files.write','arguments':{'content':'SECRET'}}])
        self.app.runs['1']=r;self.app.active='1'
        self.assertNotIn('SECRET',json.dumps(runtime_snapshot(self.app)))
    @unittest.skipUnless(HAS_PS,'psutil required')
    def test_real_monitor_and_foreground_signal(self):
        self.app.monitor=MonitorService(self.app)
        deadline=time.monotonic()+5
        while self.app.monitor.snapshot()['status']=='starting' and time.monotonic()<deadline:time.sleep(.03)
        status,s=self.request('/api/monitor')
        self.assertEqual(status,200);self.assertEqual(s['status'],'ok',s)
        self.assertEqual(self.request('/api/monitor/show','POST',{})[1],{'ok':True})
        self.assertTrue(self.app.monitor.focus.is_set())
        snap=self.app.monitor.snapshot();snap['status']='tampered'
        self.assertEqual(self.app.monitor.snapshot()['status'],'ok')
