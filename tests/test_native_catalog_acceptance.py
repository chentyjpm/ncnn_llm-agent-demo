"""CI gate regression only: synthetic replies, never real model inference."""
from contextlib import ExitStack, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_agent.native_catalog import NATIVE_MODELS
from scripts import native_catalog_smoke as smoke

MODEL = 'ncnn_qwen3_5_0_8b'


def fixture_report(model=MODEL, raw=('2', '北京'), correct=(False, True)):
    return {'model': model, 'pipeline_completed': True,
            'image_intent_requested': False,
            'answers': [{'raw': r, 'passed': p} for r, p in zip(raw, correct)]}


class AcceptancePolicyTests(unittest.TestCase):
    def test_allowlist_exactly_matches_user_approved_08b_pair(self):
        self.assertEqual(smoke.QUALITY_ADVISORY_MODELS, {MODEL, MODEL + '_int8'})
        self.assertTrue(smoke.QUALITY_ADVISORY_MODELS.issubset(NATIVE_MODELS))

    def test_wrong_answer_warns_for_both_08b_models(self):
        for mid in smoke.QUALITY_ADVISORY_MODELS:
            with self.subTest(model=mid):
                r = smoke.evaluate_acceptance(fixture_report(mid))
                self.assertTrue(r['ok'])
                self.assertTrue(r['runtime']['passed'])
                self.assertFalse(r['quality']['passed'])
                self.assertEqual(r['quality']['accuracy'], .5)
                self.assertFalse(r['quality']['required_for_ci'])
                self.assertEqual(r['acceptance_status'], 'passed_with_quality_warnings')
                self.assertTrue(r['quality_warnings'])

    def test_all_wrong_can_warn_but_does_not_pass_quality(self):
        r = smoke.evaluate_acceptance(fixture_report(raw=('2', '上海'), correct=(False, False)))
        self.assertTrue(r['ok'])
        self.assertFalse(r['quality']['passed'])
        self.assertEqual(r['quality']['accuracy'], 0)

    def test_all_correct_is_passed_without_warning(self):
        r = smoke.evaluate_acceptance(fixture_report(raw=('4', '北京'), correct=(True, True)))
        self.assertTrue(r['ok'])
        self.assertTrue(r['quality']['passed'])
        self.assertEqual(r['acceptance_status'], 'passed')
        self.assertEqual(r['quality_warnings'], [])

    def test_every_other_catalog_model_keeps_strict_quality_gate(self):
        for mid in set(NATIVE_MODELS) - smoke.QUALITY_ADVISORY_MODELS:
            with self.subTest(model=mid):
                r = smoke.evaluate_acceptance(fixture_report(mid))
                self.assertFalse(r['ok'])
                self.assertEqual(r['acceptance_status'], 'failed_quality')
                self.assertTrue(r['quality']['required_for_ci'])

    def test_unknown_08b_name_is_not_implicitly_allowed(self):
        r = smoke.evaluate_acceptance(fixture_report('unreviewed_0_8b'))
        self.assertFalse(r['ok'])
        self.assertTrue(r['quality']['required_for_ci'])

    def test_raw_answers_and_individual_correctness_unchanged(self):
        source = fixture_report(raw=('<think>\nanalysis\n</think>\n2', '北京'))
        before = copy.deepcopy(source)
        smoke.evaluate_acceptance(source)
        self.assertEqual(source, before)
        self.assertFalse(source['answers'][0]['passed'])

    def test_runtime_exception_overrides_advisory(self):
        for message in ('TimeoutError: inference timeout', 'RuntimeError: engine exit -6',
                        'RPCError: invalid UTF-8', 'ValueError: weight hash mismatch'):
            with self.subTest(error=message):
                source = fixture_report(); source['error'] = message
                r = smoke.evaluate_acceptance(source)
                self.assertFalse(r['ok'])
                self.assertEqual(r['acceptance_status'], 'failed_runtime')

    def test_incomplete_pipeline_cannot_pass(self):
        source = fixture_report(); source['pipeline_completed'] = False
        self.assertFalse(smoke.evaluate_acceptance(source)['ok'])

    def test_missing_pipeline_marker_cannot_pass(self):
        source = fixture_report(); del source['pipeline_completed']
        self.assertFalse(smoke.evaluate_acceptance(source)['ok'])

    def test_missing_and_extra_answers_cannot_pass(self):
        for count in (0, 1, 3):
            source = fixture_report(); source['answers'] = [{'raw': '4', 'passed': True}] * count
            r = smoke.evaluate_acceptance(source)
            self.assertFalse(r['ok'])
            self.assertFalse(r['runtime']['passed'])

    def test_empty_visible_answer_remains_runtime_failure(self):
        for raw in ('', ' \n\t', '<think>analysis</think>\n'):
            source = fixture_report(raw=(raw, '北京'))
            r = smoke.evaluate_acceptance(source)
            self.assertFalse(r['ok'])
            self.assertIn('Empty visible answer', r['runtime']['output_errors'])

    def test_unclosed_think_is_not_a_completed_response(self):
        r = smoke.evaluate_acceptance(fixture_report(raw=('<think>unfinished', '北京')))
        self.assertFalse(r['ok'])
        self.assertFalse(r['runtime']['passed'])

    def test_invalid_unicode_is_not_wrong_factual_answer(self):
        r = smoke.evaluate_acceptance(fixture_report(raw=('bad\ud800', '北京')))
        self.assertFalse(r['ok'])
        self.assertIn('Invalid UTF-8 model response', r['runtime']['output_errors'])

    def test_control_bytes_remain_runtime_errors(self):
        for raw in ('x\x00y', '\x1b[31m4'):
            self.assertIsNotNone(smoke.runtime_answer_error(raw))

    def test_nontext_response_cannot_pass(self):
        for raw in (None, 4, b'4', {}):
            self.assertIsNotNone(smoke.runtime_answer_error(raw))

    def test_multilingual_text_is_not_restricted_to_reference(self):
        self.assertIsNone(smoke.runtime_answer_error('答错也应保留。\nRésumé 🙂\t2'))

    def test_failed_requested_tool_contract_still_blocks(self):
        source = fixture_report(); source.update(image_intent_requested=True,
                                                image_intent={'raw': '{bad}', 'passed': False})
        r = smoke.evaluate_acceptance(source)
        self.assertFalse(r['ok'])
        self.assertEqual(r['acceptance_status'], 'failed_tool_contract')

    def test_missing_requested_tool_result_still_blocks(self):
        source = fixture_report(); source['image_intent_requested'] = True
        self.assertFalse(smoke.evaluate_acceptance(source)['ok'])

    def test_successful_tool_contract_does_not_turn_wrong_answer_correct(self):
        source = fixture_report(); source.update(image_intent_requested=True, image_intent={'passed': True})
        r = smoke.evaluate_acceptance(source)
        self.assertTrue(r['ok'])
        self.assertTrue(r['tool_contract']['passed'])
        self.assertFalse(r['quality']['passed'])

    def test_optional_tool_contract_is_marked_not_requested(self):
        r = smoke.evaluate_acceptance(fixture_report())
        self.assertFalse(r['tool_contract']['required'])
        self.assertIsNone(r['tool_contract']['passed'])

    def test_summary_shows_warning_and_actual_correct_count(self):
        source = fixture_report(); source.update(smoke.evaluate_acceptance(source))
        text = smoke.ci_summary(source)
        self.assertIn('passed_with_quality_warnings', text)
        self.assertIn('1/2 correct', text)
        self.assertIn('advisory', text)
        self.assertIn('does not certify answer accuracy', text)


class SmokeEntrypointFixtureTests(unittest.TestCase):
    """Exercise main/exit code/JSON using explicit in-process model fixtures."""
    def execute_fixture(self, responses=('2', '北京'), model=MODEL, failure=None,
                        intent=False, cleanup_failure=False):
        with tempfile.TemporaryDirectory(prefix='acceptance-fixture-') as tmp:
            root = Path(tmp); model_path = root / 'models' / model
            model_path.mkdir(parents=True); (model_path / 'READY.json').write_text('{}')
            hub = Mock(); hub.models = root / 'models'
            hub.prepare.return_value = {'ticket': 'UNIT_FIXTURE_ONLY'}
            hub.status.return_value = {'job': {'status': 'completed'}}
            hub.active.return_value = {'llm': model}
            backend = Mock(); backend.rpc = None
            backend.complete.side_effect = failure or list(responses)
            if cleanup_failure: backend.release.side_effect = RuntimeError('cleanup fixture failure')
            argv = ['native_catalog_smoke.py', '--archive', 'fixture-not-used.tar.gz',
                    '--model', model, '--output', str(root / 'out')]
            if intent: argv.append('--image-intent')
            summary_path = root / 'summary.md'
            with ExitStack() as stack:
                stack.enter_context(patch.object(smoke, 'restore', return_value=(root/'never-executed', {'fixture': True})))
                stack.enter_context(patch.object(smoke, 'ModelHub', return_value=hub))
                stack.enter_context(patch.object(smoke, 'NcnnBridgeBackend', return_value=backend))
                stack.enter_context(patch.object(smoke.sys, 'argv', argv))
                stack.enter_context(patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary_path)}))
                output = stack.enter_context(redirect_stdout(io.StringIO()))
                code = smoke.main()
            report = json.loads((root/'out/result.json').read_text(encoding='utf-8'))
            return code, report, output.getvalue(), summary_path.read_text(encoding='utf-8')

    def test_wrong_08b_reply_returns_zero_and_warning(self):
        code, r, logs, _ = self.execute_fixture()
        self.assertEqual(code, 0)
        self.assertFalse(r['answers'][0]['passed'])
        self.assertEqual(r['answers'][0]['raw'], '2')
        self.assertIn('::warning', logs)
        self.assertEqual(r['acceptance_status'], 'passed_with_quality_warnings')

    def test_int8_08b_uses_same_policy(self):
        code, r, _, _ = self.execute_fixture(model=MODEL+'_int8')
        self.assertEqual(code, 0); self.assertFalse(r['quality']['passed'])

    def test_wrong_06b_reply_still_returns_nonzero(self):
        code, r, _, _ = self.execute_fixture(model='ncnn_qwen3_0_6b')
        self.assertEqual(code, 1); self.assertEqual(r['acceptance_status'], 'failed_quality')

    def test_timeout_still_returns_nonzero_and_persists_report(self):
        code, r, _, _ = self.execute_fixture(failure=TimeoutError('model fixture timeout'))
        self.assertEqual(code, 1); self.assertIn('TimeoutError', r['error'])
        self.assertEqual(r['acceptance_status'], 'failed_runtime')

    def test_rpc_utf8_error_is_not_suppressed(self):
        code, r, _, _ = self.execute_fixture(failure=RuntimeError('RPC invalid UTF-8 fixture'))
        self.assertEqual(code, 1); self.assertIn('invalid UTF-8', r['error'])

    def test_empty_answer_still_returns_nonzero(self):
        code, r, _, _ = self.execute_fixture(responses=('', '北京'))
        self.assertEqual(code, 1); self.assertEqual(r['acceptance_status'], 'failed_runtime')

    def test_surrogate_is_preserved_as_json_escape_but_fails(self):
        code, r, _, _ = self.execute_fixture(responses=('bad\ud800', '北京'))
        self.assertEqual(code, 1); self.assertEqual(r['answers'][0]['raw'], 'bad\ud800')
        self.assertEqual(r['acceptance_status'], 'failed_runtime')

    def test_bad_tool_json_is_never_downgraded(self):
        code, r, _, _ = self.execute_fixture(responses=('2', '北京', 'not JSON'), intent=True)
        self.assertEqual(code, 1); self.assertEqual(r['acceptance_status'], 'failed_tool_contract')
        self.assertFalse(r['image_intent']['passed'])

    def test_cleanup_failure_remains_fatal_even_after_good_answers(self):
        code, r, _, _ = self.execute_fixture(responses=('4', '北京'), cleanup_failure=True)
        self.assertEqual(code, 1); self.assertIn('cleanup', r['error'])
        self.assertTrue(r['quality']['passed']); self.assertFalse(r['runtime']['passed'])

    def test_correct_answers_return_zero_without_warning(self):
        code, r, logs, summary = self.execute_fixture(responses=('4', '北京'))
        self.assertEqual(code, 0); self.assertTrue(r['quality']['passed'])
        self.assertNotIn('::warning', logs); self.assertIn('2/2 correct', summary)
