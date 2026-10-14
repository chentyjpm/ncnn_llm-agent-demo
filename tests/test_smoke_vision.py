"""CLI rejection tests only; these never claim real model inference passed."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'smoke_vision.py'
spec = importlib.util.spec_from_file_location('smoke_vision_test_module', SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class VisionSmokeGuardTests(unittest.TestCase):
    def run_invalid(self, backend, image):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config.json'
            config.write_text(json.dumps({'workspace': str(root / 'workspace'),
                                         'llm': {'backend': backend}}))
            with patch.object(smoke, 'ROOT', root), patch.object(smoke, 'NcnnBridgeBackend') as native, redirect_stdout(io.StringIO()):
                result = smoke.main(['--config', str(config), '--image', image])
            native.assert_not_called()
            report = json.loads((root / 'reports' / 'real-vision-smoke-result.json').read_text())
            self.assertEqual(result, 1)
            self.assertFalse(report['ok'])
            self.assertIn('error', report)
            self.assertNotIn('answer', report)
            return report

    def test_fake_or_cli_backend_rejected(self):
        for backend in ('scripted', 'ncnn_cli', 'unknown'):
            with self.subTest(backend=backend):
                self.assertIn('Only the real native bridge', self.run_invalid(backend, 'image.png')['error'])

    def test_traversal_rejected_before_native_launch(self):
        self.assertIn('traversal', self.run_invalid('ncnn_bridge', '../image.png')['error'].lower())
