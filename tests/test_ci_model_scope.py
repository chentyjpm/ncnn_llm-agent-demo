"""Offline model-download selection tests; never download or run a model."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from scripts import ci_model_scope as scope

ROOT = Path(__file__).resolve().parents[1]
IMAGE_REPOSITORY = 'nihui/qwenimage-ncnn-vulkan'
IMAGE_BEFORE = '14953559f39c7658e5a6cc117481e1922660cc9e'
IMAGE_AFTER = '4e48f36dd356c9b48fce43a349a972b1f310afa0'
IMAGE_NCNN = 'c6b351b56fbe32e0381ae00331e3df649b20d7b7'
LOCK_PATHS = ['ci/dependencies.json', 'upstream-lock.json']


class ModelScopeTests(unittest.TestCase):
    def setUp(self):
        self.files = {p: (ROOT / p).read_text() for p in ('ci/dependencies.json', 'upstream-lock.json')}

    def changed_pin(self, name, key, value):
        after = dict(self.files)
        dependencies = json.loads(after['ci/dependencies.json'])
        dependencies[name][key] = value
        after['ci/dependencies.json'] = json.dumps(dependencies)
        return after

    def image_files(self, commit, *, dependency_changes=None, upstream_changes=None):
        """Keep fixtures stable when the repository's current pins change."""
        files = dict(self.files)
        dependencies = json.loads(files['ci/dependencies.json'])
        dependencies['qwenimage'] = {
            'repository': IMAGE_REPOSITORY, 'commit': commit, 'ncnn_commit': IMAGE_NCNN,
            **(dependency_changes or {}),
        }
        files['ci/dependencies.json'] = json.dumps(dependencies)
        lock = json.loads(files['upstream-lock.json'])
        lock['repositories'] = [r for r in lock['repositories'] if r['name'] != IMAGE_REPOSITORY] + [{
            'name': IMAGE_REPOSITORY, 'commit': commit,
            'url': 'https://github.com/' + IMAGE_REPOSITORY,
            **(upstream_changes or {}),
        }]
        files['upstream-lock.json'] = json.dumps(lock)
        return files

    def assert_image_models_required(self, before, after):
        for name in ('image', 'turbo'):
            with self.subTest(scope=name):
                self.assertTrue(scope.requires_models(name, LOCK_PATHS, before, after)[0])

    def test_exact_reviewed_ci_only_upgrade_skips_both_image_model_suites(self):
        before, after = self.image_files(IMAGE_BEFORE), self.image_files(IMAGE_AFTER)
        original = dict(before), dict(after)
        for name in ('image', 'turbo'):
            with self.subTest(scope=name):
                required, reason = scope.requires_models(name, LOCK_PATHS, before, after)
                self.assertFalse(required)
                self.assertIn('Exact reviewed', reason)
        self.assertEqual((before, after), original)

    def test_reverse_or_unknown_image_upgrade_requests_models(self):
        for old, new in ((IMAGE_AFTER, IMAGE_BEFORE), (IMAGE_BEFORE, 'b' * 40),
                         ('b' * 40, IMAGE_AFTER), (IMAGE_AFTER, 'b' * 40)):
            with self.subTest(old=old, new=new):
                self.assert_image_models_required(self.image_files(old), self.image_files(new))

    def test_reviewed_upgrade_requires_synchronized_dependency_and_upstream_pins(self):
        before, after = self.image_files(IMAGE_BEFORE), self.image_files(IMAGE_AFTER)
        for path in LOCK_PATHS:
            with self.subTest(partially_updated=path):
                self.assert_image_models_required(before, {**before, path: after[path]})
        for old_upstream, new_upstream in (('b' * 40, IMAGE_AFTER), (IMAGE_BEFORE, 'b' * 40)):
            with self.subTest(old_upstream=old_upstream, new_upstream=new_upstream):
                self.assert_image_models_required(
                    self.image_files(IMAGE_BEFORE, upstream_changes={'commit': old_upstream}),
                    self.image_files(IMAGE_AFTER, upstream_changes={'commit': new_upstream}))

    def test_reviewed_upgrade_rejects_other_dependency_or_upstream_changes(self):
        before = self.image_files(IMAGE_BEFORE)
        changes = [
            {'dependency_changes': {'repository': 'other/qwenimage-ncnn-vulkan'}},
            {'dependency_changes': {'ncnn_commit': 'b' * 40}},
            {'dependency_changes': {'checkout_ref': 'refs/heads/other'}},
            {'dependency_changes': {'model_inputs': {'weights': 'other'}}},
            {'upstream_changes': {'url': 'https://example.invalid/other'}},
            {'upstream_changes': {'runtime_abi': 'other'}},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assert_image_models_required(before, self.image_files(IMAGE_AFTER, **change))
                # Removing an unknown field is also a metadata change.
                self.assert_image_models_required(self.image_files(IMAGE_BEFORE, **change),
                                                  self.image_files(IMAGE_AFTER))

    def test_reviewed_upgrade_is_bound_to_reviewed_repository_url_and_ncnn(self):
        for change in (
                {'dependency_changes': {'repository': 'other/qwenimage-ncnn-vulkan'}},
                {'dependency_changes': {'ncnn_commit': 'b' * 40}},
                {'upstream_changes': {'url': 'https://example.invalid/other'}}):
            with self.subTest(change=change):
                self.assert_image_models_required(self.image_files(IMAGE_BEFORE, **change),
                                                  self.image_files(IMAGE_AFTER, **change))

    def test_reviewed_upgrade_does_not_mask_relevant_model_input_changes(self):
        before, after = self.image_files(IMAGE_BEFORE), self.image_files(IMAGE_AFTER)
        for name in ('image', 'turbo'):
            for pattern in scope.PATHS[name]:
                path = pattern.replace('**', 'example.cpp')
                with self.subTest(scope=name, path=path):
                    required, reason = scope.requires_models(name, LOCK_PATHS + [path], before, after)
                    self.assertTrue(required)
                    self.assertIn(path, reason)

    def test_reviewed_upgrade_with_missing_or_ambiguous_upstream_is_an_error(self):
        before, after = self.image_files(IMAGE_BEFORE), self.image_files(IMAGE_AFTER)
        for count in (0, 2):
            lock = json.loads(after['upstream-lock.json'])
            upstream = next(r for r in lock['repositories'] if r['name'] == IMAGE_REPOSITORY)
            lock['repositories'] = [r for r in lock['repositories'] if r['name'] != IMAGE_REPOSITORY]
            lock['repositories'].extend([upstream] * count)
            invalid = {**after, 'upstream-lock.json': json.dumps(lock)}
            for name in ('image', 'turbo'):
                with self.subTest(count=count, scope=name), self.assertRaises(ValueError):
                    scope.requires_models(name, LOCK_PATHS, before, invalid)

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

    def test_real_git_push_skips_exact_reviewed_upgrade_and_keeps_manual_override(self):
        with tempfile.TemporaryDirectory(prefix='model-scope-') as temp:
            root = Path(temp)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                               stderr=subprocess.STDOUT, timeout=30).strip()
            def commit(files, message):
                for path, contents in files.items():
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(contents)
                git('add', '.')
                git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '-qm', message)
                return git('rev-parse', 'HEAD')
            git('init', '-q')
            before = commit(self.image_files(IMAGE_BEFORE), 'before')
            after = commit(self.image_files(IMAGE_AFTER), 'reviewed CI-only upgrade')
            for name in ('image', 'turbo'):
                with self.subTest(scope=name):
                    required, reason = scope.decide(name, 'push', before, after, root)
                    self.assertFalse(required)
                    self.assertIn('Exact reviewed', reason)
                    self.assertTrue(scope.decide(name, 'push', after, before, root)[0])
                    self.assertTrue(scope.decide(name, 'workflow_dispatch', before, after, root)[0])

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
