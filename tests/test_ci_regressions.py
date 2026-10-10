"""Regression tests for CI evidence and error handling, not model inference.

The temporary Git repositories and Python child commands are explicit test fixtures.
Actual ncnn/Qwen executables are tested separately by native_regression.py in CI.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from scripts import ci_native as ci
from scripts import native_regression as native


class FixtureCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='ci-regression-fixture-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)


class SubmoduleStatusTests(unittest.TestCase):
    sha = 'a' * 40

    def test_initialized_submodules_accepted(self):
        text = f' {self.sha} vendor/child (heads/main)\r\n {self.sha} path with spaces\r\n'
        self.assertEqual(len(ci.validate_submodules(text)), 2)

    def test_no_submodules_accepted(self):
        self.assertEqual(ci.validate_submodules(''), [])

    def test_uninitialized_submodule_rejected(self):
        with self.assertRaises(RuntimeError):
            ci.validate_submodules(f'-{self.sha} vendor/child')

    def test_mismatched_submodule_rejected(self):
        with self.assertRaises(RuntimeError):
            ci.validate_submodules(f'+{self.sha} vendor/child (heads/main)')

    def test_conflicted_submodule_rejected(self):
        with self.assertRaises(RuntimeError):
            ci.validate_submodules(f'U{self.sha} vendor/child')

    def test_malformed_submodule_output_rejected(self):
        for text in ['all good', ' abc child', f'{self.sha} no-status-prefix']:
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                ci.validate_submodules(text)


class SourcePinTests(FixtureCase):
    def git(self, directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), *args],
                                       encoding='utf-8', stderr=subprocess.STDOUT, timeout=30).strip()

    def create_repositories(self):
        pins = {}
        for name in ('ncnn_llm', 'ncnn', 'json'):
            directory = self.root / '.ci-src' / name
            directory.mkdir(parents=True)
            self.git(directory, 'init', '-q')
            (directory / 'fixture.txt').write_text('test fixture, no model code\n', encoding='utf-8')
            self.git(directory, 'add', 'fixture.txt')
            self.git(directory, '-c', 'user.name=CI Test Fixture', '-c', 'user.email=ci@example.invalid',
                     'commit', '-qm', 'fixture commit')
            pins[name] = {'commit': self.git(directory, 'rev-parse', 'HEAD')}
        return pins

    def test_real_uninitialized_gitlink_is_rejected(self):
        pins = self.create_repositories()
        directory = self.root / '.ci-src/ncnn_llm'
        (directory / '.gitmodules').write_text('[submodule "child"]\n\tpath = vendor/child\n\turl = ./fixture-child\n', encoding='utf-8')
        self.git(directory, 'add', '.gitmodules')
        self.git(directory, 'update-index', '--add', '--cacheinfo', f'160000,{pins["ncnn"]["commit"]},vendor/child')
        self.git(directory, '-c', 'user.name=CI Test Fixture', '-c', 'user.email=ci@example.invalid',
                 'commit', '-qm', 'uninitialized gitlink fixture')
        pins['ncnn_llm']['commit'] = self.git(directory, 'rev-parse', 'HEAD')
        with mock.patch.object(ci, 'ROOT', self.root), self.assertRaisesRegex(RuntimeError, 'Uninitialized'):
            ci.verify_sources('bridge', pins, self.root / 'evidence')

    def test_real_modified_dependency_is_rejected(self):
        pins = self.create_repositories()
        (self.root / '.ci-src/ncnn_llm/fixture.txt').write_text('changed after pinned commit\n', encoding='utf-8')
        with mock.patch.object(ci, 'ROOT', self.root), self.assertRaisesRegex(RuntimeError, 'Modified dependency'):
            ci.verify_sources('bridge', pins, self.root / 'evidence')

    def test_clean_local_fixture_checkouts_verify(self):
        pins = self.create_repositories()
        with mock.patch.object(ci, 'ROOT', self.root):
            result = ci.verify_sources('bridge', pins, self.root / 'evidence')
        self.assertEqual(set(result), {'ncnn_llm', 'ncnn', 'json'})
        self.assertTrue(all(value['submodules_verified'] == 0 for value in result.values()))


class EvidenceTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.path = self.root / 'state.json'
        self.env = {'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '100', 'GITHUB_RUN_ATTEMPT': '1'}
        self.state = ci.load_state(self.path, 'bridge', 'refs', self.env)
        self.state['stages'] = {name: {'status': 'passed'} for name in ('configure', 'build', 'smoke')}
        self.state['built_binary_sha256'] = 'old'
        self.state['smoke_binary_sha256'] = 'old'
        self.path.write_text(json.dumps(self.state), encoding='utf-8')

    def test_same_run_can_continue(self):
        self.assertEqual(ci.load_state(self.path, 'bridge', 'package', self.env), self.state)

    def test_other_commit_cannot_reuse_pass(self):
        state = ci.load_state(self.path, 'bridge', 'package', dict(self.env, GITHUB_SHA='b' * 40))
        with self.assertRaises(RuntimeError):
            ci.begin_stage(state, 'package')

    def test_other_run_cannot_reuse_pass(self):
        state = ci.load_state(self.path, 'bridge', 'package', dict(self.env, GITHUB_RUN_ID='101'))
        self.assertFalse(state['stages'])

    def test_rerun_attempt_cannot_reuse_pass(self):
        state = ci.load_state(self.path, 'bridge', 'package', dict(self.env, GITHUB_RUN_ATTEMPT='2'))
        self.assertFalse(state['stages'])

    def test_other_component_cannot_reuse_pass(self):
        self.assertFalse(ci.load_state(self.path, 'image', 'package', self.env)['stages'])

    def test_refs_always_starts_fresh(self):
        self.assertFalse(ci.load_state(self.path, 'bridge', 'refs', self.env)['stages'])

    def test_reconfigure_invalidates_build_and_tests(self):
        ci.begin_stage(self.state, 'configure')
        self.assertEqual(set(self.state['stages']), {'configure'})
        self.assertNotIn('built_binary_sha256', self.state)
        self.assertNotIn('smoke_binary_sha256', self.state)

    def test_rebuild_invalidates_smoke_and_package(self):
        ci.begin_stage(self.state, 'build')
        self.assertNotIn('smoke', self.state['stages'])
        self.assertNotIn('smoke_binary_sha256', self.state)

    def test_smoke_cannot_skip_build(self):
        self.state['stages']['build']['status'] = 'failed'
        with self.assertRaises(RuntimeError):
            ci.begin_stage(self.state, 'smoke')

    def test_package_requires_completed_smoke(self):
        self.state['stages']['smoke']['status'] = 'running'
        with self.assertRaises(RuntimeError):
            ci.begin_stage(self.state, 'package')

    def test_changed_binary_rejected(self):
        path = self.root / 'digest-fixture'; path.write_bytes(b'changed, not an executable')
        with self.assertRaisesRegex(RuntimeError, 'built'):
            ci.checked_digest(path, self.state)

    def test_untested_binary_rejected(self):
        path = self.root / 'digest-fixture'; path.write_bytes(b'not an executable')
        self.state['built_binary_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError, 'tested'):
            ci.checked_digest(path, self.state, require_smoke=True)

    def test_matching_build_and_test_digest_accepted(self):
        path = self.root / 'digest-fixture'; path.write_bytes(b'not an executable')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.state.update(built_binary_sha256=digest, smoke_binary_sha256=digest)
        self.assertEqual(ci.checked_digest(path, self.state, require_smoke=True), digest)


class NativeHarnessTests(FixtureCase):
    def command(self, code):
        return [sys.executable, '-I', '-X', 'utf8', '-c', code]

    def test_expected_nonzero_exit_is_valid_error_path(self):
        result = native.run_case(self.command("import sys; print('invalid value',file=sys.stderr); sys.exit(2)"),
                                 2, 'invalid value', stdout_empty=True)
        self.assertEqual(result['status'], 'passed')

    def test_wrong_returncode_is_failure(self):
        result = native.run_case(self.command("print('diagnostic')"), 2, 'diagnostic')
        self.assertEqual(result['status'], 'failed')

    def test_expected_text_in_argv_does_not_pass(self):
        result = native.run_case(self.command('pass # DIAGNOSTIC'), 0, 'DIAGNOSTIC')
        self.assertEqual(result['status'], 'failed')

    def test_stdout_protocol_pollution_is_failure(self):
        result = native.run_case(self.command("print('error'); raise SystemExit(2)"), 2, 'error', stdout_empty=True)
        self.assertEqual(result['status'], 'failed')

    def test_timeout_is_failure(self):
        result = native.run_case(self.command('import time; time.sleep(5)'), 0, 'nothing', timeout=0.1)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('TimeoutExpired', result['error'])

    def test_nonexistent_program_is_failure(self):
        result = native.run_case([str(self.root / 'does-not-exist')], 0, 'nothing')
        self.assertEqual(result['status'], 'failed')

    def test_script_cannot_masquerade_as_native_binary(self):
        script = self.root / 'fake.exe'; script.write_text('#!/bin/sh\necho hello\n')
        with self.assertRaises(ValueError):
            native.validate_binary(script)

    def test_empty_native_binary_rejected(self):
        path = self.root / 'empty'; path.touch()
        with self.assertRaises(ValueError):
            native.validate_binary(path)

    def test_native_cases_are_unique_and_have_expected_scope(self):
        for component, count in [('bridge', 22), ('image', 13)]:
            with self.subTest(component=component):
                cases = native.cases(component, self.root)
                self.assertEqual(len(cases), count)
                self.assertEqual(len({case[0] for case in cases}), count)
                self.assertTrue(all(case[2] in (0, 2) for case in cases))
                if component == 'image':
                    self.assertTrue(all(case[1] for case in cases))

    def test_unknown_native_component_rejected(self):
        with self.assertRaises(ValueError):
            native.cases('invalid', self.root)


if __name__ == '__main__':
    unittest.main()
