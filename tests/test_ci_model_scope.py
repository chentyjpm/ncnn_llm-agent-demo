"""Offline model-download selection tests; never download or run a model."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from scripts import ci_model_scope as scope

ROOT = Path(__file__).resolve().parents[1]


class ModelScopeTests(unittest.TestCase):
    def setUp(self):
        self.files = {p: (ROOT / p).read_text() for p in ('ci/dependencies.json', 'upstream-lock.json')}

    def changed_pin(self, name, key, value):
        after = dict(self.files)
        dependencies = json.loads(after['ci/dependencies.json'])
        dependencies[name][key] = value
        after['ci/dependencies.json'] = json.dumps(dependencies)
        return after

    def test_bridge_only_pin_does_not_request_image_models(self):
        after = self.changed_pin('ncnn', 'commit', 'b' * 40)
        for name in ('image', 'turbo'):
            self.assertFalse(scope.requires_models(name, ['ci/dependencies.json'], self.files, after)[0])

    def test_llm_only_pin_does_not_request_image_models(self):
        after = self.changed_pin('ncnn_llm', 'commit', 'b' * 40)
        for name in ('image', 'turbo'):
            self.assertFalse(scope.requires_models(name, ['ci/dependencies.json'], self.files, after)[0])

    def test_image_engine_embedded_ncnn_and_repository_changes_request_models(self):
        for key in ('commit', 'ncnn_commit', 'repository'):
            for name in ('image', 'turbo'):
                with self.subTest(key=key, scope=name):
                    after = self.changed_pin('qwenimage', key, 'b' * 40)
                    self.assertTrue(scope.requires_models(name, ['ci/dependencies.json'], self.files, after)[0])

    def test_image_upstream_lock_change_requests_models(self):
        after = dict(self.files)
        lock = json.loads(after['upstream-lock.json'])
        next(r for r in lock['repositories'] if r['name'] == 'nihui/qwenimage-ncnn-vulkan')['commit'] = 'b' * 40
        after['upstream-lock.json'] = json.dumps(lock)
        self.assertTrue(scope.requires_models('image', ['upstream-lock.json'], self.files, after)[0])

    def test_pr_metadata_and_review_date_do_not_request_image_models(self):
        after = dict(self.files)
        lock = json.loads(after['upstream-lock.json'])
        lock['reviewed_on'] = '2099-01-01'
        lock['temporary_pull_requests']['ncnn_llm']['commit'] = 'b' * 40
        after['upstream-lock.json'] = json.dumps(lock)
        self.assertFalse(scope.requires_models('image', ['upstream-lock.json'], self.files, after)[0])

    def test_relevant_sources_still_request_model_tests(self):
        for name, files in scope.PATHS.items():
            for pattern in files:
                path = pattern.replace('**', 'example.cpp')
                with self.subTest(scope=name, path=path):
                    self.assertTrue(scope.requires_models(name, [path], self.files, self.files)[0])

    def test_checkout_and_workflow_maintenance_skip_large_downloads(self):
        paths = ['scripts/ci_native.py', 'scripts/fetch_upstream.py', 'scripts/ci_model_scope.py',
                 '.github/workflows/native-catalog.yml', '.github/workflows/qwenimage-real.yml',
                 '.github/workflows/qwenimage-turbo.yml', 'ci/dependencies.json', 'upstream-lock.json']
        for name in scope.PATHS:
            self.assertFalse(scope.requires_models(name, paths, self.files, self.files)[0])

    def test_manual_dispatch_always_requests_models(self):
        for name in scope.PATHS:
            self.assertTrue(scope.decide(name, 'workflow_dispatch', '', '')[0])

    def test_unknown_or_missing_history_is_an_error_not_a_skip(self):
        for event, before, after in [('push', '', 'b' * 40), ('push', '0' * 40, 'b' * 40),
                                     ('push', 'main', 'b' * 40), ('pull_request', 'a' * 40, 'b' * 40)]:
            with self.subTest(event=event, before=before), self.assertRaises(ValueError):
                scope.decide('image', event, before, after)

    def test_invalid_image_lock_is_an_error_not_a_skip(self):
        for body in ('not json', '{}', '{"qwenimage": {}}'):
            files = dict(self.files, **{'ci/dependencies.json': body})
            with self.subTest(body=body), self.assertRaises((KeyError, ValueError)):
                scope.requires_models('image', ['ci/dependencies.json'], files, self.files)

    def test_workflow_gate_is_before_heavy_job_and_keeps_manual_entry(self):
        for filename, name, job in [('qwenimage-real', 'image', 'image'), ('qwenimage-turbo', 'turbo', 'turbo'),
                                    ('native-catalog', 'catalog', 'bridge')]:
            text = (ROOT / '.github/workflows' / (filename + '.yml')).read_text()
            self.assertIn('workflow_dispatch:', text)
            self.assertIn('fetch-depth: 0', text)
            self.assertIn('python scripts/ci_model_scope.py --scope ' + name, text)
            heavy = text.split('  ' + job + ':\n', 1)[1]
            self.assertIn('needs: scope', heavy)
            self.assertIn("if: needs.scope.outputs.required == 'true'", heavy)

    def test_real_git_push_diff_selects_relevant_file(self):
        with tempfile.TemporaryDirectory(prefix='model-scope-') as temp:
            root = Path(temp)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                               stderr=subprocess.STDOUT, timeout=30).strip()
            git('init', '-q')
            for path, contents in self.files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(contents)
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'before')
            before = git('rev-parse', 'HEAD')
            target = root / 'scripts/qwenimage_real_test.py'
            target.parent.mkdir()
            target.write_text('# Changed test fixture\n')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'after')
            after = git('rev-parse', 'HEAD')
            self.assertTrue(scope.decide('image', 'push', before, after, root)[0])
            self.assertFalse(scope.decide('catalog', 'push', before, after, root)[0])


if __name__ == '__main__':
    unittest.main()
