"""Pinned native bundle validation. Synthetic fixture weights are NOT inference."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_agent.model_catalog import CATALOG,PROFILES,PROVIDERS
from local_agent.native_catalog import NATIVE_MODELS
from local_agent.native_models import manifest,download_url,validate_install,MIRROR
from local_agent.model_sources import resolve_manifest,file_url,source_for
from local_agent.model_hub import ModelHub,BUSY

class NativeCatalogTests(unittest.TestCase):
    def test_native_manifests_are_pinned_consistent_and_independent(self):
        self.assertGreaterEqual(len(NATIVE_MODELS),8)
        for mid,spec in NATIVE_MODELS.items():
            with self.subTest(model=mid):
                m=manifest(mid);self.assertEqual(len(m['revision']),64)
                self.assertEqual(m['download_bytes'],sum(f['bytes'] for f in m['files']))
                for f in m['files']:self.assertTrue(file_url(m,f).startswith(MIRROR+spec['repository']+'/'))
                m['files'][0]['digest']='0'*64
                self.assertNotEqual(manifest(mid)['files'][0]['digest'],'0'*64)
    def test_native_manifest_resolution_does_not_fetch_hf_or_ms(self):
        mid=next(iter(NATIVE_MODELS))
        with patch('urllib.request.urlopen',side_effect=AssertionError('Unexpected network')):
            self.assertEqual(resolve_manifest(mid,'sdu')['provider'],'sdu')
    def test_unsupported_provider_cannot_silently_switch(self):
        for mid in NATIVE_MODELS:
            for provider in ('huggingface','modelscope'):
                with self.assertRaises(ValueError):resolve_manifest(mid,provider)
    def test_mutable_mirror_cannot_change_pinned_member(self):
        m=manifest(next(iter(NATIVE_MODELS)));f=dict(m['files'][0]);f['remote']='../escape'
        with self.assertRaises(ValueError):download_url(m,f)
        m['revision']='0'*64
        with self.assertRaises(ValueError):download_url(m,m['files'][0])
    def test_specialized_models_are_not_installable_chat_models(self):
        pending=[(k,v) for k,v in CATALOG.items() if v.get('installable') is False]
        self.assertGreaterEqual(len(pending),6)
        for mid,spec in pending:
            self.assertEqual(spec['kind'],'pending')
            for p in PROVIDERS:
                with self.assertRaises(ValueError):source_for(mid,p)
    def test_existing_dual_sources_remain(self):
        for mid in PROFILES:self.assertEqual(set(CATALOG[mid]['sources']),{'huggingface','modelscope'})
    def test_quantized_models_are_not_advertised_as_gpu_decoder(self):
        for mid,spec in NATIVE_MODELS.items():
            if spec['precision']=='int8':self.assertIn('CPU',CATALOG[mid]['runtime_note'])
    def test_visual_models_use_only_text_bridge_here(self):
        for mid,spec in NATIVE_MODELS.items():
            if spec['text_only']:
                self.assertIn('文本入口',CATALOG[mid]['name']);self.assertNotIn('vision',CATALOG[mid]['capabilities'])
    def test_templates_remain_upstream_defined(self):
        # Preserve known native types and tokenizer, don't coerce all into Qwen2.
        types={m['expected_type'] for m in NATIVE_MODELS.values()}
        self.assertTrue({'qwen3','minicpm4','youtu_llm'}.issubset(types))
        for m in NATIVE_MODELS.values():self.assertEqual(m['config']['type'],m['expected_type'])
    def test_real_native_model_install_code_with_tiny_labelled_fixture(self):
        # Exercise full download/hash/publish/activate lifecycle, not just a list.
        mid=next(iter(NATIVE_MODELS));spec=copy.deepcopy(NATIVE_MODELS[mid])
        config=spec['config'];files={}
        for f in spec['files']:
            files[f['name']]=json.dumps(config).encode() if f['name']=='model.json' else b'VALIDATOR_FIXTURE_NOT_WEIGHTS'
            f.update(bytes=len(files[f['name']]),digest=hashlib.sha256(files[f['name']]).hexdigest())
        spec['download_bytes']=sum(f['bytes'] for f in spec['files'])
        spec['revision']=hashlib.sha256(json.dumps(spec['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        catalog=copy.deepcopy(CATALOG[mid]);catalog['revision']=spec['revision'];catalog['sources']['sdu']['revision']=spec['revision']
        with tempfile.TemporaryDirectory() as tmp,patch.dict(NATIVE_MODELS,{mid:spec}),patch.dict(CATALOG,{mid:catalog}):
            root=Path(tmp);exe=root/'engine';exe.write_text('Not executed')
            hub=ModelHub(root,{'llm':str(exe),'image':'missing'})
            m=manifest(mid)
            with patch('local_agent.model_hub.open_https',side_effect=lambda url:io.BytesIO(files[url.split('/')[-1]])):
                hub._install(m)
            self.assertEqual(hub.job['status'],'completed',hub.job)
            self.assertEqual(hub.active(),{'llm':mid})
            self.assertTrue((hub.models/mid/'READY.json').is_file())
            validate_install(hub.models/mid,mid)
            (hub.models/mid/'model.json').write_text('{}')
            with self.assertRaises(ValueError):validate_install(hub.models/mid,mid)
