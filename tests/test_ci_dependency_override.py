"""Offline pin/checkout regressions; no download, build or inference claims."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import ci_native
from scripts import fetch_upstream
from scripts.fetch_upstream import source_pin

ROOT = Path(__file__).resolve().parents[1]
ci_native_read = ci_native.read_pins


class DependencyPullRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'ci').mkdir()
        self.pins = json.loads((ROOT / 'ci/dependencies.json').read_text())
        self.lock = json.loads((ROOT / 'upstream-lock.json').read_text())

    def write(self):
        (self.root / 'ci/dependencies.json').write_text(json.dumps(self.pins))
        (self.root / 'upstream-lock.json').write_text(json.dumps(self.lock))

    def test_exact_pull_request_and_fetch_consumer(self):
        self.write()
        self.assertEqual(ci_native.read_pins(self.root), self.pins)
        self.assertEqual(source_pin('ncnn_llm', self.root), {
            'url': 'https://github.com/futz12/ncnn_llm',
            'commit': ci_native.MINICPM_PULL_REQUEST['commit'],
            'checkout_ref': 'refs/pull/58/head'})
        self.assertEqual(source_pin('qwenimage-ncnn-vulkan', self.root)['commit'],
                         '14953559f39c7658e5a6cc117481e1922660cc9e')
        self.assertEqual(self.pins['qwenimage']['ncnn_commit'], 'c6b351b56fbe32e0381ae00331e3df649b20d7b7')

    def test_other_forks_shas_branches_and_urls_fail(self):
        original = copy.deepcopy(self.pins)
        for field, value in [('repository', 'other/ncnn_llm'),
                             ('repository', 'chentyjpm/ncnn_llm'),
                             ('repository', 'https://github.com/futz12/ncnn_llm'),
                             ('checkout_ref', 'refs/pull/59/head'),
                             ('commit', 'a' * 40), ('commit', 'main')]:
            with self.subTest(field=field, value=value):
                self.pins = copy.deepcopy(original)
                self.pins['ncnn_llm'][field] = value
                self.write()
                with self.assertRaises(ValueError): ci_native.read_pins(self.root)

    def test_pull_request_metadata_cannot_be_dropped_or_changed(self):
        original = copy.deepcopy(self.lock)
        for mutation in ('missing', 'sha', 'base', 'additional'):
            with self.subTest(mutation=mutation):
                self.lock = copy.deepcopy(original)
                if mutation == 'missing': self.lock.pop('temporary_pull_requests')
                elif mutation == 'additional': self.lock['temporary_pull_requests']['json'] = {}
                else:
                    key = 'commit' if mutation == 'sha' else 'base_commit'
                    self.lock['temporary_pull_requests']['ncnn_llm'][key] = 'b' * 40
                self.write()
                with self.assertRaises(ValueError): ci_native.read_pins(self.root)

    def test_upstream_monitor_divergence_requires_review(self):
        self.lock['repositories'][0]['commit'] = 'a' * 40
        self.write()
        with self.assertRaisesRegex(ValueError, 'divergence'): ci_native.read_pins(self.root)

    def test_official_source_migration_remains_possible_explicitly(self):
        self.pins['ncnn_llm'].pop('checkout_ref')
        self.pins['ncnn_llm']['commit'] = 'b' * 40
        self.lock['repositories'][0]['commit'] = 'b' * 40
        self.write()
        with self.assertRaises(ValueError): ci_native.read_pins(self.root)
        self.lock.pop('temporary_pull_requests')
        self.write()
        self.assertEqual(ci_native.read_pins(self.root)['ncnn_llm']['repository'], 'futz12/ncnn_llm')
        self.assertEqual(source_pin('ncnn_llm', self.root)['url'], 'https://github.com/futz12/ncnn_llm')

    def test_all_native_checkout_consumers_use_validated_repository_output(self):
        for name in ('native-build', 'installers', 'qwen05-cpu', 'native-catalog'):
            text = (ROOT / '.github/workflows' / (name + '.yml')).read_text()
            self.assertIn('repository: ${{ steps.pins.outputs.ncnn_llm_repository }}', text)
            self.assertIn('ref: ${{ steps.pins.outputs.ncnn_llm_checkout_ref }}', text)
            self.assertNotIn('repository: futz12/ncnn_llm', text)
            self.assertLess(text.index('python scripts/ci_native.py refs'), text.index('repository: ${{ steps.pins.outputs.ncnn_llm_repository }}'))

    def test_refs_outputs_are_written_only_after_validation(self):
        self.write()
        output = self.root / 'outputs'
        with patch.object(ci_native, 'ROOT', self.root), \
             patch.object(ci_native, 'read_pins', side_effect=lambda: ci_native_read(self.root)), \
             patch('sys.argv', ['ci_native.py', 'refs', '--component', 'bridge']), \
             patch.dict('os.environ', {'GITHUB_OUTPUT': str(output)}):
            self.assertEqual(ci_native.main(), 0)
            text = output.read_text()
            self.assertIn('ncnn_llm_repository=futz12/ncnn_llm\n', text)
            self.assertIn('ncnn_llm_checkout_ref=refs/pull/58/head\n', text)
            self.assertIn('ncnn_llm=a14fe16f49ea6ad36767ade62bba9d6d3d1d8e60\n', text)
            self.pins['ncnn_llm']['checkout_ref'] = 'main'
            self.write()
            self.assertNotEqual(ci_native.main(), 0)
            self.assertEqual(output.read_text(), text)

    def test_fetch_uses_official_pr_ref_and_rejects_moved_head(self):
        self.write()
        destination = self.root / 'source'
        with patch.object(fetch_upstream, 'source_pin', return_value=source_pin('ncnn_llm', self.root)), \
             patch.object(fetch_upstream.subprocess, 'run') as run, \
             patch.object(fetch_upstream.subprocess, 'check_output', return_value='a' * 40), \
             patch('sys.argv', ['fetch_upstream.py', 'ncnn_llm', '--directory', str(destination)]):
            with self.assertRaisesRegex(SystemExit, 'Commit mismatch'): fetch_upstream.main()
        commands = [call.args[0] for call in run.call_args_list]
        # The fetch helper canonicalizes macOS /var aliases and Windows
        # short paths before constructing argv.
        self.assertIn(['git', '-C', str(destination.resolve()), 'remote', 'add', 'origin', 'https://github.com/futz12/ncnn_llm.git'], commands)
        self.assertIn(['git', '-C', str(destination.resolve()), 'fetch', '--depth', '1', 'origin', 'refs/pull/58/head'], commands)

    def test_source_evidence_names_official_pr_and_reviewed_base(self):
        def output(argv, **kwargs):
            if 'rev-parse' in argv:
                name = Path(argv[2]).name
                return self.pins[name]['commit'] + '\n'
            return ''
        def logged(argv, log, **kwargs):
            log.write_text('ARGV: test\n')
        with patch.object(ci_native.subprocess, 'check_output', side_effect=output), \
             patch.object(ci_native, 'run_logged', side_effect=logged):
            evidence = ci_native.verify_sources('bridge', self.pins, self.root)
        self.assertEqual(evidence['ncnn_llm']['repository'], 'futz12/ncnn_llm')
        with patch.object(ci_native.subprocess, 'check_output', return_value='a' * 40):
            with self.assertRaisesRegex(RuntimeError, 'SHA mismatch'):
                ci_native.verify_sources('bridge', self.pins, self.root)
        self.assertEqual(evidence['ncnn_llm']['temporary_pull_request']['base_commit'],
                         '0538ae8b9e2d47f597ba3491e35d2f610c3a3114')


if __name__ == '__main__': unittest.main()
