"""Frozen-report error retention with explicit native/Office test doubles."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_agent import desktop
from local_agent.documents import DocumentTools


class DesktopEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.engines = {}
        metadata = {'engines': {}}
        import os
        for name in ('llm', 'image'):
            folder = self.root / name; folder.mkdir()
            file = folder / 'engine'; file.write_bytes(b'Explicit fixture; never executed')
            probe = folder / ('ncnn_device_probe.exe' if os.name == 'nt' else 'ncnn_device_probe')
            probe.write_bytes(b'Explicit probe fixture; never executed')
            self.engines[name] = str(file)
            metadata['engines'][name] = {'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                                        'probe_sha256': hashlib.sha256(probe.read_bytes()).hexdigest()}
        (self.root / 'BUNDLE.json').write_text(json.dumps(metadata))

    def check(self, capabilities):
        good = {'returncode': 0, 'timed_out': False, 'stdout': 'Usage: ncnn_agent_bridge Usage: qwenimage-ncnn-vulkan', 'stderr': ''}
        output = self.root / 'result.json'
        with patch.object(desktop, 'resource_root', return_value=self.root), \
             patch.object(desktop, 'engine_paths', return_value=self.engines), \
             patch('local_agent.process.run_process', return_value=good), \
             patch('local_agent.device.probe', return_value=capabilities), \
             patch.object(DocumentTools, 'create', return_value={'path': 'fixture'}), \
             patch.object(DocumentTools, 'read', return_value={'content': 'CHECK'}):
            code = desktop.self_test(output)
        return code, json.loads(output.read_text())

    def test_both_probe_failures_remain_failures_with_diagnostics(self):
        code, result = self.check({'version': 1, 'compiled': False, 'devices': [],
            'reason': 'probe_unavailable', 'detail': 'probe_process_failed: exit=-6; stderr=dyld test diagnostic'})
        self.assertEqual(code, 1); self.assertFalse(result['ok'])
        self.assertEqual(len(result['engines']), 2)
        self.assertEqual(len(result['failures']), 2)
        for data in result['engines'].values():
            self.assertFalse(data['passed'])
            self.assertIn('dyld test diagnostic', data['probe_report']['detail'])
            self.assertTrue(Path(data['probe_path']).is_absolute())

    def test_valid_no_driver_response_is_cpu_not_process_failure(self):
        code, result = self.check({'version': 1, 'compiled': True, 'devices': []})
        self.assertEqual(code, 0); self.assertTrue(result['ok'])
        for data in result['engines'].values():
            self.assertEqual(data['device_selection']['selected'], 'cpu')
            self.assertEqual(data['device_selection']['reason'], 'no_usable_hardware_vulkan')

    def test_invalid_protocol_cannot_pass(self):
        code, result = self.check({'version': 'invalid', 'compiled': True, 'devices': []})
        self.assertEqual(code, 1); self.assertFalse(result['ok'])
        self.assertIn('Invalid Vulkan probe protocol', result['error'])
