"""Acceptance-harness regressions ONLY; generated pixels/receipts are synthetic.

These fixtures check that the CI driver uses HTTP, approval, persistence and
verified-download checks. They are not evidence of Qwen inference or quality.
The real CI entry point separately requires pinned native and model-file hashes.
"""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from local_agent.image_profiles import TURBO_IMAGE
from local_agent.images import ImageRunner
from scripts import qwenimage_turbo_smoke as smoke


class AutomaticTraceTests(unittest.TestCase):
    def setUp(self):
        self.message = {'mode': 'image', 'requested_mode': 'chat', 'state': 'completed'}
        self.approval = {'id': 'fixture-approval', 'tool': 'images.generate', 'arguments': {'prompt': 'Draw a cat'}}
        self.events = [
            {'seq': 1, 'event': 'run_started', 'routing': 'natural_language_image_request', 'requested_mode': 'chat'},
            {'seq': 2, 'event': 'approval_required', 'approval': self.approval},
            {'seq': 3, 'event': 'approval_resolved', 'approval_id': 'fixture-approval', 'allowed': True},
            {'seq': 4, 'event': 'tool_execute', 'tool': 'images.generate'},
            {'seq': 5, 'event': 'tool_result', 'tool': 'images.generate',
             'arguments': self.approval['arguments'], 'result': {'ok': True}},
        ]

    def validate(self):
        return smoke.validate_automatic_trace(self.message, self.events, self.approval)

    def test_requires_natural_language_route(self):
        self.assertEqual(self.validate(), 'natural_language_image_request')
        for route in ('explicit_image_mode', 'slash_image_command', 'chat'):
            self.events[0]['routing'] = route
            with self.subTest(route=route), self.assertRaisesRegex(ValueError, 'routing evidence'):
                self.validate()

    def test_cannot_substitute_explicit_image_mode(self):
        for key, value in [('requested_mode', 'image'), ('mode', 'chat'), ('state', 'failed')]:
            old = self.message[key]
            self.message[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.validate()
            self.message[key] = old

    def test_rejects_execution_before_approval(self):
        self.events[3]['seq'] = 1
        with self.assertRaisesRegex(ValueError, 'after approval'):
            self.validate()

    def test_rejects_denied_or_mismatched_approval(self):
        for key, value in [('allowed', False), ('approval_id', 'wrong')]:
            old = self.events[2][key]
            self.events[2][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'matching allowed approval'):
                self.validate()
            self.events[2][key] = old

    def test_rejects_multiple_generations(self):
        self.events.append(copy.deepcopy(self.events[-1]))
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.validate()

    def test_rejects_text_model_calls(self):
        self.events.append({'event': 'model_start'})
        with self.assertRaisesRegex(ValueError, 'text model'):
            self.validate()

    def test_rejects_changed_arguments(self):
        self.events[-1]['arguments'] = {'prompt': 'a different fixture'}
        with self.assertRaisesRegex(ValueError, 'differs from the approved'):
            self.validate()

    def test_accepts_ui_truncation_without_treating_preview_as_engine_receipt(self):
        self.events[-1]['result'] = {'ok': True, 'preview': 'TEST FIXTURE preview only', 'truncated': True}
        self.assertEqual(self.validate(), 'natural_language_image_request')


class HttpDriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root/'evidence'
        self.out.mkdir()
        self.model = self.root/TURBO_IMAGE
        self.model.mkdir()
        self.binary = self.root/'explicit-test-fixture-not-engine'
        self.binary.write_bytes(b'TEST FIXTURE, NEVER EXECUTED')
        self.args = SimpleNamespace(binary=self.binary, native_status=self.root/'not-real-status.json', device='cpu', timeout=5)
        self.cfg = {'enabled': True, 'command': [str(self.binary)], 'model': str(self.model), 'device': 'cpu', 'timeout': 5}
        self.report = {'engine': {'scope': 'TEST FIXTURE, NOT NATIVE EVIDENCE'}}
        self.params = None

    @staticmethod
    def command_fixture(runner, **params):
        runner.device_selection = {'selected': 'cpu', 'gpu': -1}
        return [str(runner.config['command'][0]), 'TEST_FIXTURE_ARGV_NOT_EXECUTED']

    def engine_fixture(self, runner, **params):
        """Explicitly synthetic image; only the orchestration harness is tested."""
        self.params = params
        output = runner.workspace.path(params['output'])
        output.parent.mkdir(parents=True, exist_ok=True)
        im = Image.new('RGB', (256, 256), 'white')
        im.putpixel((0, 0), (255, 0, 0))
        im.save(output)
        runner.device_selection = {'selected': 'cpu', 'gpu': -1}
        return {'returncode': 0, 'timed_out': False, 'file_created': True,
                'path': params['output'], 'device_selection': runner.device_selection,
                'steps': 8, 'image_profile': {'variant': 'turbo'},
                'stdout': 'TEST FIXTURE NOT REAL INFERENCE\nmodel-type = Turbo\nstep 8/8 done\nvae done',
                # Exercise the real UI's truncated preview while the driver reads
                # its complete engine receipt from the persisted JSONL audit.
                'stderr': 'TEST FIXTURE NOT REAL ENGINE LOG\n' + 'x' * 14000}

    def test_http_driver_uses_chat_approval_one_generation_and_authenticated_download(self):
        with patch.object(ImageRunner, 'command', autospec=True, side_effect=self.command_fixture), \
             patch.object(ImageRunner, 'run', autospec=True, side_effect=self.engine_fixture) as engine, \
             patch.object(smoke, 'verify_native', return_value=self.report['engine']):
            smoke.generate_through_http(self.args, self.cfg, self.out, self.report)
        self.assertEqual(engine.call_count, 1)
        request = json.loads((self.out/'http-request.json').read_text())
        self.assertEqual(request['mode'], 'chat')
        self.assertTrue(request['message'].startswith('Draw '))
        self.assertNotIn('steps', request['image_options'])
        self.assertEqual(self.params['steps'], 8)
        self.assertEqual(self.report['routing'], 'natural_language_image_request')
        self.assertEqual(self.report['requested_mode'], 'chat')
        self.assertEqual(self.report['mode'], 'image')
        self.assertTrue(self.report['image']['fully_decoded'])
        self.assertTrue(self.report['http_download']['matches_persisted_artifact'])
        self.assertEqual(self.report['http_download']['unauthenticated_status'], 403)
        self.assertEqual((self.out/'generated.png').read_bytes(), (self.out/'downloaded.png').read_bytes())
        session = json.loads((self.out/'http-session.json').read_text())
        receipt = json.loads((self.out/'engine-receipt.json').read_text())
        self.assertIn('TEST FIXTURE', receipt['result']['result']['stdout'])
        event = next(e for e in session['messages'][-1]['trace'] if e['event'] == 'tool_result')
        self.assertTrue(event['result']['truncated'])
        self.assertFalse(json.loads((self.out/'http-runtime.json').read_text())['ready'])
        self.assertNotIn('token', json.loads((self.out/'http-runtime.json').read_text()))

    def test_real_entrypoint_rejects_unverified_engine_before_weights_or_http(self):
        self.args.output = self.out
        self.args.models = self.root/'missing-weights'
        self.args.route = 'direct'
        self.args.native_status.write_text('{}')
        with patch.object(smoke, 'prepare_models') as download, patch.object(smoke, 'generate_through_http') as generate, \
             patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': ''}), redirect_stdout(io.StringIO()):
            self.assertEqual(smoke.run(self.args), 1)
        download.assert_not_called()
        generate.assert_not_called()
        report = json.loads((self.out/'result.json').read_text())
        self.assertFalse(report['ok'])
        self.assertIn('not the same image executable', report['error'])
