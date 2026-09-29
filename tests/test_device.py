"""Policy/adapter capability fixtures are NOT GPU execution evidence."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_agent import device
from local_agent.backends import NcnnBridgeBackend
from local_agent.images import ImageRunner
from local_agent.paths import PolicyError, Workspace


def report(*devices, compiled=True):
    return {'version': 1, 'compiled': compiled, 'devices': list(devices)}


def gpu(index=0, kind='discrete', ok=True, hardware=True):
    return {'id': index, 'name': 'TEST GPU ' + str(index), 'type': kind, 'hardware': hardware, 'compute_ok': ok}


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = self.root / 'engine'; self.engine.write_bytes(b'test marker, not executable')
        self.probe = self.root / ('ncnn_device_probe.exe' if os.name == 'nt' else 'ncnn_device_probe')
        self.probe.write_bytes(b'test marker, subprocess is explicitly mocked')
        self.config = {'command': [str(self.engine)], 'device': 'auto'}
        device._CACHE.clear()

    def test_empty_config_defaults_both_auto(self):
        for kind in ('llm', 'image'): self.assertEqual(device.policy({}, kind), 'auto')

    def test_auto_selects_discrete_before_integrated(self):
        self.assertEqual(device.select(self.config, 'llm', report=report(gpu(0,'integrated'),gpu(1)))['gpu'],1)

    def test_integrated_is_accelerator(self):
        self.assertEqual(device.select(self.config, 'image', report=report(gpu(0,'integrated')))['selected'],'vulkan')

    def test_bad_device_moves_to_working_device(self):
        self.assertEqual(device.select(self.config, 'llm', report=report(gpu(0,ok=False),gpu(1)))['gpu'],1)

    def test_no_devices_falls_back(self):
        value=device.select(self.config,'llm',report=report())
        self.assertEqual((value['selected'],value['gpu'],value['reason']),('cpu',-1,'no_usable_hardware_vulkan'))

    def test_cpu_only_build_falls_back(self):
        self.assertEqual(device.select(self.config,'image',report=report(compiled=False))['reason'],'vulkan_not_compiled')

    def test_failed_compute_is_not_usable(self):
        self.assertEqual(device.select(self.config,'llm',report=report(gpu(ok=False)))['selected'],'cpu')

    def test_software_not_mislabelled_as_hardware(self):
        self.assertEqual(device.select(self.config,'image',report=report(gpu(kind='cpu',hardware=False)))['selected'],'cpu')

    def test_auto_ignores_software_opt_in(self):
        self.assertEqual(device.select(dict(self.config,allow_software_vulkan=True),'llm',report=report(gpu(kind='cpu',hardware=False)))['selected'],'cpu')

    def test_explicit_vulkan_allows_test_software_only_when_requested(self):
        c=dict(self.config,device='vulkan',allow_software_vulkan=True)
        self.assertEqual(device.select(c,'llm',report=report(gpu(kind='cpu',hardware=False)))['selected'],'vulkan')

    def test_strict_vulkan_unavailable_is_error_not_success(self):
        with self.assertRaises(PolicyError): device.select(dict(self.config,device='vulkan'),'llm',report=report())

    def test_explicit_cpu_does_not_probe(self):
        with patch.object(device,'probe',side_effect=AssertionError('must not probe')):
            self.assertEqual(device.select({'device':'cpu'},'image')['reason'],'explicit_cpu')

    def test_legacy_cpu_remains_cpu(self):
        self.assertEqual(device.select({'vulkan':False},'llm')['selected'],'cpu')
        self.assertEqual(device.select({'gpu':-1},'image')['selected'],'cpu')

    def test_legacy_true_is_strict_vulkan(self):
        self.assertEqual(device.policy({'vulkan':True},'llm'),'vulkan')

    def test_invalid_policy_rejected(self):
        for invalid in ('vk',True,None):
            with self.subTest(invalid=invalid),self.assertRaises(PolicyError): device.select({'device':invalid},'llm')

    def test_explicit_device_preference(self):
        self.assertEqual(device.select(dict(self.config,gpu=1),'image',report=report(gpu(0),gpu(1)))['gpu'],1)

    def test_bad_preference_does_not_silently_select_wrong_gpu(self):
        self.assertEqual(device.select(dict(self.config,gpu=2),'llm',report=report(gpu()))['selected'],'cpu')

    def test_boolean_gpu_rejected(self):
        with self.assertRaises(PolicyError): device.select(dict(self.config,gpu=True),'llm',report=report(gpu()))

    def test_protocol_rejects_malformed_fields(self):
        malformed=[{}, {'version':True,'compiled':True,'devices':[]},report(gpu(),gpu()),report(gpu(),compiled=False),report(dict(gpu(),compute_ok='yes'))]
        for value in malformed:
            with self.subTest(value=value),self.assertRaises(ValueError): device.validate_report(value)

    def test_missing_engine_reason(self):
        self.assertEqual(device.select({'command':['/nonexistent/engine']},'llm')['reason'],'engine_missing')

    def test_matching_probe_required(self):
        self.probe.unlink()
        self.assertEqual(device.select(self.config,'image')['reason'],'matching_probe_missing')

    def test_probe_timeout_falls_back_with_reason(self):
        with patch.object(device,'run_process',return_value={'timed_out':True}):
            self.assertEqual(device.select(self.config,'llm')['reason'],'probe_unavailable')

    def test_probe_crash_falls_back(self):
        with patch.object(device,'run_process',return_value={'timed_out':False,'returncode':-11}):
            self.assertEqual(device.select(self.config,'llm')['selected'],'cpu')

    def test_probe_invalid_json_falls_back(self):
        value={'timed_out':False,'returncode':0,'output_truncated':False,'stdout':'not json'}
        with patch.object(device,'run_process',return_value=value):
            self.assertEqual(device.select(self.config,'image')['reason'],'probe_unavailable')

    def test_probe_is_cached_and_bounded_subprocess(self):
        value={'timed_out':False,'returncode':0,'output_truncated':False,'stdout':json.dumps(report(gpu()))}
        with patch.object(device,'run_process',return_value=value) as run:
            for _ in range(2): device.select(self.config,'llm')
            self.assertEqual(run.call_count,1);self.assertEqual(run.call_args.kwargs['timeout'],15)
            self.assertEqual(run.call_args.args[0],[str(self.probe)])

    def test_driver_change_invalidates_cache(self):
        value={'timed_out':False,'returncode':0,'output_truncated':False,'stdout':json.dumps(report())}
        with patch.object(device,'run_process',return_value=value) as run:
            device.select(self.config,'llm')
            with patch.dict(os.environ,{'VK_DRIVER_FILES':'changed.json'}): device.select(self.config,'llm')
            self.assertEqual(run.call_count,2)

    def test_mac_runtime_is_local_to_engines(self):
        folder=self.root/'engines/bridge';folder.mkdir(parents=True);binary=folder/'bridge';binary.touch()
        runtime=self.root/'engines/vulkan';runtime.mkdir();(runtime/'libMoltenVK.dylib').touch()
        with patch.object(device.sys,'platform','darwin'): env=device.engine_env([str(binary)])
        self.assertEqual(env['DYLD_LIBRARY_PATH'],str(runtime))

    def test_engine_env_does_not_leak_credentials(self):
        with patch.dict(os.environ,{'TEST_SECRET':'secret','VK_DRIVER_FILES':'driver.json'}): env=device.engine_env([str(self.engine)])
        self.assertNotIn('TEST_SECRET',env);self.assertEqual(env['VK_DRIVER_FILES'],'driver.json')

    def test_image_selects_own_probe_and_passes_actual_gpu(self):
        model=self.root/'model';model.mkdir()
        c={'enabled':True,'command':[str(self.engine)],'model':str(model),'device':'auto'}
        runner=ImageRunner(Workspace(self.root/'ws'),c)
        with patch.object(device,'probe',return_value=report(gpu(3))) as probed:
            argv=runner.command(prompt='cube',output='cube.png')
        self.assertEqual(argv[argv.index('-g')+1],'3');self.assertEqual(probed.call_args.args[0],c['command'])

    def test_image_auto_without_driver_passes_cpu(self):
        model=self.root/'model';model.mkdir()
        runner=ImageRunner(Workspace(self.root/'ws'),{'enabled':True,'command':[str(self.engine)],'model':str(model)})
        with patch.object(device,'probe',return_value=report()): argv=runner.command(prompt='cube',output='cube.png')
        self.assertEqual(argv[argv.index('-g')+1],'-1')

    def test_bridge_vulkan_argv_uses_probed_device(self):
        model=self.root/'model';model.mkdir();(model/'model.json').write_text('{}')
        with patch.object(device,'probe',return_value=report(gpu(2))),patch('local_agent.backends.StdioRPC') as rpc:
            rpc.return_value.request.return_value={'text':'4'}
            backend=NcnnBridgeBackend(dict(self.config,model=str(model)),self.root)
            self.assertEqual(backend.complete([{'role':'user','content':'2+2'}]),'4')
            self.assertIn('--vulkan',rpc.call_args.args[0]);self.assertEqual(rpc.call_args.args[0][-1],'2')
            backend.release()

    def test_model_error_is_not_hidden_by_cpu_retry(self):
        model=self.root/'model';model.mkdir();(model/'model.json').write_text('{}')
        with patch.object(device,'probe',return_value=report(gpu())),patch('local_agent.backends.StdioRPC') as rpc:
            rpc.return_value.request.side_effect=RuntimeError('invalid weights')
            backend=NcnnBridgeBackend(dict(self.config,model=str(model)),self.root)
            with self.assertRaisesRegex(RuntimeError,'invalid weights'): backend.complete([])
            self.assertEqual(rpc.call_count,1);backend.release()
