"""Local runtime-selection fixtures, NOT hardware/driver execution evidence."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_agent import device


class MacDeviceEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / 'engines/bridge/bridge'; self.binary.parent.mkdir(parents=True); self.binary.touch()
        self.runtime = self.root / 'engines/vulkan'; self.runtime.mkdir()
        (self.runtime / 'libMoltenVK.dylib').touch()
        self.manifest = self.runtime / 'MoltenVK_icd.json'; self.manifest.write_text('{}')
        self.command = [str(self.binary)]
        device._CACHE.clear()

    def test_bundled_icd_is_default_not_homebrew(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(device.sys, 'platform', 'darwin'):
            env = device.engine_env(self.command)
        self.assertEqual(env['VK_DRIVER_FILES'], str(self.manifest.resolve()))
        self.assertEqual(env['DYLD_LIBRARY_PATH'], str(self.runtime.resolve()))

    def test_explicit_driver_selection_wins(self):
        with patch.dict(os.environ, {'VK_DRIVER_FILES':'/nonexistent/driver.json'}, clear=True), patch.object(device.sys,'platform','darwin'):
            env = device.engine_env(self.command)
        self.assertEqual(env['VK_DRIVER_FILES'], '/nonexistent/driver.json')

    def test_legacy_icd_selection_is_not_overridden(self):
        with patch.dict(os.environ, {'VK_ICD_FILENAMES':'legacy.json'}, clear=True), patch.object(device.sys,'platform','darwin'):
            env = device.engine_env(self.command)
        self.assertEqual(env['VK_ICD_FILENAMES'],'legacy.json')
        self.assertNotIn('VK_DRIVER_FILES',env)

    def test_missing_manifest_does_not_invent_driver(self):
        self.manifest.unlink()
        with patch.dict(os.environ,{},clear=True),patch.object(device.sys,'platform','darwin'):
            self.assertNotIn('VK_DRIVER_FILES',device.engine_env(self.command))

    def test_failed_probe_retains_exit_and_bounded_diagnostic(self):
        probe = self.binary.with_name('ncnn_device_probe.exe' if os.name=='nt' else 'ncnn_device_probe'); probe.touch()
        failure={'returncode':-6,'timed_out':False,'stderr':'x'*600+' missing libvulkan.dylib'}
        with patch.object(device,'run_process',return_value=failure):
            report = device.probe(self.command)
        self.assertEqual(report['reason'],'probe_unavailable')
        self.assertIn('exit=-6',report['detail'])
        self.assertIn('libvulkan.dylib',report['detail'])
        self.assertLessEqual(len(report['detail']),500)
