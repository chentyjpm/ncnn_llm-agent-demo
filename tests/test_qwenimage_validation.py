"""Synthetic files test the verifier ONLY. No Qwen model inference here."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts.qwenimage_real_test import (IMAGE_REQUIRED, MODEL_REPO, t2i_manifest,
    validate_manifest, validate_image, verified_file, model_file, download, check_device,
    verify_native, save, read_pins)
try:
    from PIL import Image
except ImportError:
    Image = None


def fixture_manifest():
    data = b'EXPLICIT_VALIDATOR_FIXTURE_NOT_MODEL'
    files = [{'name': n, 'remote': 'qwenimage21/' + n, 'bytes': len(data),
              'algorithm': 'sha256', 'digest': hashlib.sha256(data).hexdigest()} for n in sorted(IMAGE_REQUIRED)]
    return {'id':'qwenimage21', 'provider':'huggingface', 'repository':MODEL_REPO,
            'revision':'a'*40, 'files':files, 'download_bytes':sum(f['bytes'] for f in files)}, data


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.m, self.data = fixture_manifest()
    def test_exact_members_accepted(self): validate_manifest(self.m)
    def test_excludes_unused_edit_and_controlnet_weights(self):
        full = copy.deepcopy(self.m); full['files'] += [{'name':'controlnet/controlnet.ncnn.bin','bytes':99}]
        self.assertEqual(t2i_manifest(full)['download_bytes'], self.m['download_bytes'])
    def test_missing_member_rejected(self):
        self.m['files'].pop()
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_duplicate_member_rejected(self):
        self.m['files'][-1] = self.m['files'][0]
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_unpinned_revision_rejected(self):
        self.m['revision'] = 'main'
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_different_model_rejected(self):
        self.m['id'] = 'qwen05'
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_source_repository_rejected(self):
        self.m['repository'] = 'attacker/model'
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_bad_remote_path_rejected(self):
        self.m['files'][0]['remote'] = '../../test'
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_bad_total_rejected(self):
        self.m['download_bytes'] += 1
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_bool_length_rejected(self):
        self.m['files'][0]['bytes'] = True
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_weight_requires_sha256(self):
        f = next(f for f in self.m['files'] if f['name'].endswith('.bin'))
        f.update(algorithm='git',digest='a'*40)
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_over_budget_rejected(self):
        self.m['files'][0]['bytes'] = 50*1024**3
        with self.assertRaises(ValueError): validate_manifest(self.m)
    def test_content_changed_same_size_is_rejected(self):
        p = self.root/'file'; p.write_bytes(b'x'*len(self.data))
        self.assertFalse(verified_file(p, self.m['files'][0]))
    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError): model_file(self.root, '../outside')
    def test_download_hashes_actual_bytes_and_reuses_completed(self):
        calls=[]
        def open_fixture(url, timeout): calls.append(url); return io.BytesIO(self.data)
        folder=self.root/'model'; reports=self.root/'evidence'
        download(self.m,folder,reports,opener=open_fixture)
        self.assertEqual(len(calls),len(IMAGE_REQUIRED))
        download(self.m,folder,reports,opener=open_fixture)
        self.assertEqual(len(calls),len(IMAGE_REQUIRED))
        self.assertTrue(json.loads((reports/'download-result.json').read_text())['ok'])
    def test_bad_download_is_never_published(self):
        folder=self.root/'model'
        with patch('scripts.qwenimage_real_test.time.sleep'):
            with self.assertRaises(ValueError):
                download(self.m,folder,self.root/'reports',opener=lambda *a,**k:io.BytesIO(b'x'*len(self.data)))
        self.assertFalse(any(p.is_file() for p in folder.rglob('*')))
        self.assertFalse((self.root/'reports/download-result.json').exists())
    def test_download_length_limit(self):
        with patch('scripts.qwenimage_real_test.time.sleep'):
            with self.assertRaises(ValueError):
                download(self.m,self.root/'model',self.root/'reports',opener=lambda *a,**k:io.BytesIO(self.data+b'extra'))
    def test_cpu_must_be_explicit(self):
        check_device({'selected':'cpu','gpu':-1},'cpu')
        with self.assertRaises(ValueError): check_device({'selected':'cpu','gpu':0},'cpu')
    def test_gpu_lane_cannot_silently_fall_back(self):
        with self.assertRaises(ValueError): check_device({'selected':'cpu','gpu':-1},'vulkan')
    def test_software_vulkan_is_not_hardware_acceptance(self):
        with self.assertRaises(ValueError): check_device({'selected':'vulkan','gpu':0,'hardware':False},'vulkan')
        check_device({'selected':'vulkan','gpu':0,'hardware':True},'vulkan')
    def test_native_hash_tampering_detected(self):
        p=self.root/'test-binary';p.write_bytes(b'fixture only, never executed')
        status={'component':'image','built_binary_sha256':'0'*64,'smoke_binary_sha256':'0'*64}
        s=self.root/'native.json'; save(s,status)
        with self.assertRaises(ValueError): verify_native(p,s)
    def test_missing_native_stage_rejected(self):
        p=self.root/'test-binary';p.write_bytes(b'fixture only, never executed');h=hashlib.sha256(p.read_bytes()).hexdigest()
        s=self.root/'native.json';save(s,{'component':'image','built_binary_sha256':h,'smoke_binary_sha256':h})
        with self.assertRaises(ValueError): verify_native(p,s)


@unittest.skipIf(Image is None, 'Pillow unavailable; CI real-image lane installs its pinned decoder')
class ImageValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=Path(self.temp.name)/'synthetic-validator-fixture.png'
    def image(self, mode='RGB', size=(256,256)):
        im=Image.new(mode,size,(128,128,128,255) if mode=='RGBA' else (128,128,128))
        im.putpixel((0,0),(255,0,0,255) if mode=='RGBA' else (255,0,0));return im
    def test_decodes_actual_png_pixels(self):
        self.image().save(self.p);r=validate_image(self.p,256,256)
        self.assertTrue(r['fully_decoded']);self.assertEqual(r['size'],[256,256])
    def test_decodes_rgba(self):
        self.image('RGBA').save(self.p);self.assertEqual(validate_image(self.p,256,256)['mode'],'RGBA')
    def test_wrong_dimensions_rejected(self):
        self.image(size=(128,128)).save(self.p)
        with self.assertRaises(ValueError):validate_image(self.p,256,256)
    def test_jpeg_named_png_rejected(self):
        self.image().save(self.p,format='JPEG')
        with self.assertRaises(ValueError):validate_image(self.p,256,256)
    def test_truncated_png_rejected(self):
        self.image().save(self.p);data=self.p.read_bytes();self.p.write_bytes(data[:len(data)//2])
        with self.assertRaises((ValueError,OSError,SyntaxError)):validate_image(self.p,256,256)
    def test_transparent_output_rejected(self):
        Image.new('RGBA',(256,256),(50,50,50,0)).save(self.p)
        with self.assertRaisesRegex(ValueError,'transparent'):validate_image(self.p,256,256)
    def test_flat_output_rejected(self):
        Image.new('RGB',(256,256),(0,0,0)).save(self.p)
        with self.assertRaisesRegex(ValueError,'constant'):validate_image(self.p,256,256)
    def test_missing_output_rejected(self):
        with self.assertRaises(ValueError):validate_image(self.p,256,256)
    def test_zero_length_rejected(self):
        self.p.write_bytes(b'')
        with self.assertRaises(ValueError):validate_image(self.p,256,256)
