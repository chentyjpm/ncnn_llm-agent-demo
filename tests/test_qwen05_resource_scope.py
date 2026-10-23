"""Exercise the CI resource preflight without downloading or installing models."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class QwenResourcePreflightTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / '.github/workflows/qwen05-cpu.yml').read_text(encoding='utf-8')
        self.source = self.workflow.split("python - <<'PY_RESOURCES'\n", 1)[1].split('          PY_RESOURCES', 1)[0]
        self.source = '\n'.join(line[10:] for line in self.source.splitlines())

    def run_preflight(self, disk_gib, memory_gib):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'resources.json'
            real_path = Path
            def path(value):
                if value == '/proc/meminfo':
                    return SimpleNamespace(read_text=lambda: f'MemAvailable: {memory_gib * 1024 ** 2} kB\n')
                if value == 'reports/qwen05/resources.json':
                    return output
                return real_path(value)
            with patch('pathlib.Path', side_effect=path), \
                 patch('shutil.disk_usage', return_value=SimpleNamespace(free=disk_gib * 1024 ** 3)), \
                 patch('builtins.print'):
                exec(compile(self.source, 'qwen05-ci-resource-preflight', 'exec'), {})
            self.assertTrue(output.is_file())

    def test_preflight_precedes_first_model_download(self):
        self.assertLess(self.workflow.index('Check runner resources before model downloads'),
                        self.workflow.index('python scripts/qwen05_export.py download'))

    def test_sufficient_resources_are_recorded(self):
        self.run_preflight(12, 6)

    def test_insufficient_disk_or_memory_stops_before_download(self):
        for disk, memory in ((11, 6), (12, 5)):
            with self.subTest(disk=disk, memory=memory), self.assertRaisesRegex(SystemExit, 'no weights downloaded'):
                self.run_preflight(disk, memory)


if __name__ == '__main__':
    unittest.main()
