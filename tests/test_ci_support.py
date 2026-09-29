"""CI helper regression tests; these do NOT substitute for native compilation."""
from pathlib import Path
import sys
import tempfile
import unittest
from scripts.ci_native import configure_command, find_binary, read_pins, run_logged


class CISupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ci-helper-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_dependency_pins_are_full_shas_and_match_upstream(self):
        self.assertEqual(set(read_pins()), {'ncnn_llm', 'qwenimage', 'ncnn', 'json'})

    def test_windows_uses_x64_and_utf8(self):
        cmd = configure_command('bridge', 'Windows', self.root)
        self.assertIn('-A', cmd)
        self.assertEqual(cmd[cmd.index('-A') + 1], 'x64')
        self.assertIn('-DCMAKE_CXX_FLAGS=/utf-8', cmd)
        self.assertIn('-DNCNN_VULKAN=ON', cmd)

    def test_macos_uses_native_runner_architecture(self):
        cmd = configure_command('bridge', 'Darwin', self.root)
        self.assertNotIn('-A', cmd)
        self.assertFalse(any('OSX_ARCHITECTURES' in value for value in cmd))
        self.assertIn('-DNCNN_OPENMP=OFF', cmd)

    def test_image_configures_real_upstream_source(self):
        cmd = configure_command('image', 'Linux', self.root / 'with spaces')
        self.assertEqual(cmd[cmd.index('-S') + 1], str(self.root / 'with spaces/.ci-src/qwenimage/src'))
        self.assertIn('-DNCNN_SIMPLEVK=ON', cmd)

    def test_unknown_component_rejected(self):
        with self.assertRaises(ValueError):
            configure_command('invalid', 'Linux', self.root)

    def test_find_binary_handles_visual_studio_release_directory(self):
        binary = self.root / 'build/ci-bridge/Release/ncnn_agent_bridge.exe'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'locator test, not an executable')
        self.assertEqual(find_binary('bridge', self.root, 'Windows'), binary.resolve())

    def test_missing_or_empty_binary_rejected(self):
        with self.assertRaises(RuntimeError):
            find_binary('image', self.root, 'Linux')
        binary = self.root / 'build/ci-image/qwenimage-ncnn-vulkan'
        binary.parent.mkdir(parents=True)
        binary.touch()
        with self.assertRaises(RuntimeError):
            find_binary('image', self.root, 'Linux')

    def test_failed_command_is_not_reported_as_passed(self):
        with self.assertRaisesRegex(RuntimeError, 'exit code'):
            run_logged([sys.executable, '-c', 'raise SystemExit(7)'], self.root / 'failed.log')

    def test_smoke_diagnostic_cannot_be_satisfied_by_command_echo(self):
        # The required word exists in ARGV only, not in actual program output.
        with self.assertRaisesRegex(RuntimeError, 'not emitted'):
            run_logged([sys.executable, '-c', 'pass # UNIQUE_DIAGNOSTIC'],
                       self.root / 'no-output.log', contains='UNIQUE_DIAGNOSTIC')

    def test_real_subprocess_output_is_logged_and_checked(self):
        result = run_logged([sys.executable, '-c', "print('helper executed')"],
                            self.root / 'ok.log', contains='helper executed')
        self.assertEqual(result['returncode'], 0)
        self.assertTrue((self.root / 'ok.log').is_file())


if __name__ == '__main__':
    unittest.main()
