"""Build-layout fixtures only; actual dyld/Vulkan behavior is tested on macOS CI."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import macos_runtime as runtime


class MacRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = self.root / 'MoltenVK_icd.json'
        self.manifest.write_text(json.dumps({'file_format_version':'1.0.0','ICD':{
            'api_version':'1.4.0','library_path':'/opt/homebrew/lib/libMoltenVK.dylib'}}))

    def test_icd_uses_relative_bundled_library(self):
        data = runtime.local_icd(self.manifest)
        self.assertEqual(data['ICD']['library_path'], './libMoltenVK.dylib')
        self.assertEqual(data['ICD']['api_version'], '1.4.0')
        self.assertTrue(data['ICD']['is_portability_driver'])
        self.assertNotIn('/opt/', json.dumps(data))

    def test_icd_requires_valid_api_version(self):
        for value in (None, True, '1.4.0;bad', '../../lib'):
            self.manifest.write_text(json.dumps({'ICD':{'api_version':value}}))
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.local_icd(self.manifest)

    def test_icd_size_is_bounded(self):
        self.manifest.write_text(' '*16385)
        with self.assertRaises(ValueError): runtime.local_icd(self.manifest)

    def test_otool_paths_with_spaces_are_preserved(self):
        output='file:\n\t@rpath/libvulkan.dylib (compatibility version 1.0.0)\n\t/System/Library/Frameworks/Metal.framework/Metal (current version 1.0.0)\n'
        with patch.object(runtime.subprocess, 'check_output', return_value=output):
            self.assertEqual(runtime.load_names(self.root/'space name'), ['@rpath/libvulkan.dylib', '/System/Library/Frameworks/Metal.framework/Metal'])

    def test_runtime_rejects_unbundled_homebrew_dependency(self):
        with patch.object(runtime, 'load_names', return_value=['@rpath/self.dylib','/opt/homebrew/lib/libmissing.dylib']):
            with self.assertRaisesRegex(RuntimeError,'Unbundled'): runtime.check_runtime_library(self.root/'x')

    def test_runtime_allows_system_frameworks(self):
        with patch.object(runtime, 'load_names', return_value=['@rpath/self.dylib','/usr/lib/libSystem.B.dylib','/System/Library/Frameworks/Metal.framework/Metal']):
            runtime.check_runtime_library(self.root/'x')

    def test_complete_runtime_and_relocation_are_staged(self):
        prefixes = {name:self.root/name for name in ('molten-vk','vulkan-loader')}
        for prefix in prefixes.values():
            (prefix/'lib').mkdir(parents=True)
            (prefix/'LICENSE').write_text('test license')
        (prefixes['molten-vk']/'lib/libMoltenVK.dylib').write_bytes(b'ICD fixture')
        (prefixes['vulkan-loader']/'lib/libvulkan.dylib').write_bytes(b'loader fixture')
        icd=prefixes['molten-vk']/'etc/vulkan/icd.d';icd.mkdir(parents=True)
        (icd/'MoltenVK_icd.json').write_bytes(self.manifest.read_bytes())
        staging=self.root/'space staging'
        def output(argv, **kw):
            if argv[:2]==['brew','--prefix']: return str(prefixes[argv[2]])+'\n'
            if argv[:3]==['brew','list','--versions']: return argv[-1]+' TEST\n'
            self.fail(str(argv))
        def names(p):
            return ['@rpath/self.dylib','/usr/lib/libSystem.B.dylib'] if p.suffix=='.dylib' else ['@rpath/libvulkan.dylib']
        with patch.object(runtime.subprocess,'check_output',side_effect=output), patch.object(runtime,'load_names',side_effect=names), patch.object(runtime.subprocess,'run') as change:
            result=runtime.prepare_runtime(staging)
        self.assertEqual(change.call_count,4)
        self.assertEqual(len(result['native_relocations']),4)
        self.assertEqual(set(result['libraries']),{'libMoltenVK.dylib','libvulkan.dylib','libvulkan.1.dylib'})
        self.assertEqual((staging/'engines/vulkan/libvulkan.dylib').read_bytes(),b'loader fixture')
        self.assertEqual(change.call_args.args[0][3],str((staging/'engines/vulkan/libvulkan.dylib').resolve()))
        self.assertTrue((staging/'licenses/vulkan-loader/LICENSE').is_file())
        self.assertEqual(json.loads((staging/'engines/vulkan/MoltenVK_icd.json').read_text())['ICD']['library_path'],'./libMoltenVK.dylib')

    def test_frozen_dependencies_reject_developer_sdk_path(self):
        with patch.object(runtime,'load_names',return_value=['/Users/developer/VulkanSDK/lib/libvulkan.dylib']):
            with self.assertRaisesRegex(RuntimeError,'Nonportable'): runtime.verify_frozen_dependencies(self.root)

    def test_frozen_manifest_cannot_replace_missing_loader(self):
        with patch.object(runtime,'load_names',return_value=['@rpath/libvulkan.dylib']):
            with self.assertRaisesRegex(RuntimeError,'omitted'): runtime.verify_frozen_dependencies(self.root)


if __name__=='__main__': unittest.main()
