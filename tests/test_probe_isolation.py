"""Explicit protocol fixtures; actual Vulkan compute belongs to native CI."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_agent import device
from local_agent.paths import PolicyError
from tests.test_device import gpu, report


def reply(*devices, phase='enumerate', compiled=True):
    return {'timed_out': False, 'returncode': 0, 'output_truncated': False,
            'stdout': json.dumps(dict(report(*devices, compiled=compiled), phase=phase)), 'stderr': ''}


class ProbeIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.engine = self.root / 'engine'; self.engine.write_bytes(b'Explicit fixture, never executed')
        self.binary = self.root / ('ncnn_device_probe.exe' if os.name == 'nt' else 'ncnn_device_probe')
        self.binary.write_bytes(b'Explicit probe fixture, never executed')
        self.config = {'command': [str(self.engine)], 'device': 'auto'}
        device._CACHE.clear()

    def test_success_requires_separate_enumeration_and_compute(self):
        with patch.object(device, 'run_process', side_effect=[reply(gpu(ok=False)), reply(gpu(), phase='compute')]) as run:
            got = device.select(self.config, 'llm')
        self.assertEqual(got['selected'], 'vulkan')
        self.assertEqual(run.call_args_list[0].args[0], [str(self.binary), '--enumerate'])
        self.assertEqual(run.call_args_list[1].args[0], [str(self.binary), '--check-device', '0'])
        self.assertEqual(got['probe_devices'][0]['compute_status'], 'passed')

    def test_enumeration_must_not_claim_it_executed_compute(self):
        with patch.object(device, 'run_process', return_value=reply(gpu())) as run:
            got = device.probe(self.config['command'])
        self.assertEqual(got['reason'], 'probe_unavailable'); self.assertEqual(run.call_count, 1)

    def test_wrong_enumeration_phase_rejected(self):
        with patch.object(device, 'run_process', return_value=reply(gpu(ok=False), phase='compute')):
            self.assertEqual(device.probe(self.config['command'])['reason'], 'probe_unavailable')

    def test_crashing_driver_retains_error_and_cpu_fallback(self):
        crash = {'timed_out': False, 'returncode': -11, 'stderr': 'EXPLICIT TEST driver crash'}
        with patch.object(device, 'run_process', side_effect=[reply(gpu(ok=False)), crash]):
            capabilities = device.probe(self.config['command'])
        self.assertTrue(capabilities['compiled']); self.assertEqual(capabilities['phase'], 'complete')
        data = capabilities['devices'][0]
        self.assertFalse(data['compute_ok']); self.assertEqual(data['compute_status'], 'failed')
        self.assertIn('exit=-11', data['compute_error']); self.assertIn('TEST driver crash', data['compute_error'])
        self.assertEqual(device.select(self.config, 'llm', report=capabilities)['selected'], 'cpu')
        with self.assertRaises(PolicyError): device.select(dict(self.config, device='vulkan'), 'llm', report=capabilities)

    def test_failed_first_device_does_not_hide_working_second(self):
        with patch.object(device, 'run_process', side_effect=[reply(gpu(0, ok=False), gpu(1, ok=False)),
             {'timed_out': False, 'returncode': -11}, reply(gpu(1), phase='compute')]):
            got = device.select(self.config, 'image')
        self.assertEqual((got['selected'], got['gpu']), ('vulkan', 1))
        self.assertFalse(got['probe_devices'][0]['compute_ok'])

    def test_compute_timeout_is_not_a_success(self):
        with patch.object(device, 'run_process', side_effect=[reply(gpu(ok=False)), {'timed_out': True}]):
            got = device.probe(self.config['command'])
        self.assertEqual(got['devices'][0]['compute_error'], 'probe_timeout')
        self.assertFalse(got['devices'][0]['compute_ok'])

    def test_changed_device_identity_rejected(self):
        changed = dict(gpu(), name='A DIFFERENT DEVICE')
        with patch.object(device, 'run_process', side_effect=[reply(gpu(ok=False)), reply(changed, phase='compute')]):
            got = device.probe(self.config['command'])['devices'][0]
        self.assertFalse(got['compute_ok']); self.assertIn('identity changed', got['compute_error'])

    def test_wrong_compute_phase_rejected(self):
        with patch.object(device, 'run_process', side_effect=[reply(gpu(ok=False)), reply(gpu())]):
            got = device.probe(self.config['command'])['devices'][0]
        self.assertFalse(got['compute_ok']); self.assertIn('compute response', got['compute_error'])

    def test_extra_compute_devices_rejected(self):
        with patch.object(device, 'run_process', side_effect=[reply(gpu(ok=False)), reply(gpu(), gpu(1), phase='compute')]):
            self.assertFalse(device.probe(self.config['command'])['devices'][0]['compute_ok'])

    def test_compute_false_is_retained(self):
        with patch.object(device, 'run_process', side_effect=[reply(gpu(ok=False)), reply(gpu(ok=False), phase='compute')]):
            got = device.probe(self.config['command'])['devices'][0]
        self.assertFalse(got['compute_ok']); self.assertEqual(got['compute_status'], 'failed')

    def test_software_excluded_without_launching_compute(self):
        soft = gpu(kind='cpu', hardware=False, ok=False)
        with patch.object(device, 'run_process', return_value=reply(soft)) as run:
            got = device.probe(self.config['command'])
        self.assertEqual(run.call_count, 1); self.assertEqual(got['devices'][0]['compute_status'], 'software_excluded')

    def test_software_opt_in_still_requires_actual_compute(self):
        soft = gpu(kind='cpu', hardware=False, ok=False)
        with patch.object(device, 'run_process', side_effect=[reply(soft), reply(dict(soft, compute_ok=True), phase='compute')]) as run:
            got = device.probe(self.config['command'], software=True)
        self.assertEqual(run.call_count, 2); self.assertTrue(got['devices'][0]['compute_ok'])
        self.assertFalse(got['devices'][0]['hardware'])

    def test_elapsed_total_budget_never_starts_an_unbounded_child(self):
        with patch.object(device.time, 'monotonic', side_effect=[0, 16, 17]), \
             patch.object(device, 'run_process', return_value=reply(gpu(ok=False))) as run:
            got = device.probe(self.config['command'])
        self.assertEqual(run.call_count, 1); self.assertEqual(got['devices'][0]['compute_status'], 'budget_exhausted')

    def test_cached_report_cannot_be_mutated_into_success(self):
        with patch.object(device, 'run_process', side_effect=[reply(gpu(ok=False)), {'timed_out': True}]) as run:
            first = device.probe(self.config['command']); first['devices'][0]['compute_ok'] = True
            second = device.probe(self.config['command'])
        self.assertEqual(run.call_count, 2); self.assertFalse(second['devices'][0]['compute_ok'])

    def test_broken_enumerator_remains_a_packaging_failure_reason(self):
        with patch.object(device, 'run_process', return_value={'timed_out': False, 'returncode': -11}):
            got = device.probe(self.config['command'])
        self.assertEqual(got['reason'], 'probe_unavailable'); self.assertFalse(got['compiled'])
