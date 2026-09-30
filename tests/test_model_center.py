"""Provider and progress unit tests use labelled manifests/transports, not weights."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from local_agent.model_catalog import CATALOG, PROFILES
from local_agent.model_hub import ModelHub, BUSY, save_json
from local_agent.model_sources import resolve_manifest, source_for, file_url, safe_name, digest, QWEN_FILES, open_https
from scripts.qwen05_export import export_model


def manifest(provider='huggingface', model='qwen05'):
    profile=PROFILES[model];source=source_for(model,provider)
    files=[]
    for name in sorted(QWEN_FILES):
        sha=profile['weight_sha256'] if name=='model.safetensors' else '1'*64
        if provider=='huggingface':files.append({'rfilename':name,'size':10,'lfs':{'sha256':sha}})
        else:files.append({'Path':name,'Type':'blob','Size':10,'Sha256':sha})
    return {'sha':source['revision'],'siblings':files} if provider=='huggingface' else {'Code':200,'Success':True,'Data':{'Files':files}}


class ModelCenterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.engine=self.root/'engine';self.engine.write_text('test fixture')
        self.hub=ModelHub(self.root,{'llm':str(self.engine),'image':str(self.engine)})

    def resolve(self,model='qwen05',provider='huggingface',data=None):
        data=data or manifest(provider,model)
        fake=lambda *a,**k:contextlib.closing(io.BytesIO(json.dumps(data).encode()))
        return resolve_manifest(model,provider,opener=fake)

    def test_three_text_models_have_both_sources(self):
        self.assertEqual(len(PROFILES),3)
        for p in PROFILES.values():self.assertEqual(set(p['sources']),{'huggingface','modelscope'})

    def test_provider_commit_histories_are_not_confused(self):
        for model in PROFILES:
            self.assertNotEqual(source_for(model,'modelscope')['revision'],source_for(model,'huggingface')['revision'])

    def test_both_provider_manifests_parse_without_cross_provider_requests(self):
        for model in PROFILES:
            for provider,host in [('huggingface','huggingface.co'),('modelscope','modelscope.cn')]:
                urls=[]
                def fake(url,**kw):urls.append(url);return contextlib.closing(io.BytesIO(json.dumps(manifest(provider,model)).encode()))
                q=resolve_manifest(model,provider,opener=fake)
                self.assertEqual(q['download_bytes'],50);self.assertEqual(q['provider'],provider)
                self.assertEqual(len(urls),1);self.assertIn('https://'+host+'/',urls[0])

    def test_hf_wrong_commit_rejected(self):
        m=manifest();m['sha']='0'*40
        with self.assertRaises(ValueError):self.resolve(data=m)

    def test_wrong_weights_cannot_be_substituted(self):
        for provider in ['huggingface','modelscope']:
            m=manifest(provider)
            files=m['siblings'] if provider=='huggingface' else m['Data']['Files']
            for f in files:
                if f.get('rfilename',f.get('Path'))=='model.safetensors':
                    if provider=='huggingface':f['lfs']['sha256']='0'*64
                    else:f['Sha256']='0'*64
            with self.assertRaises(ValueError):self.resolve(provider=provider,data=m)

    def test_no_fake_modelscope_image_mirror(self):
        with self.assertRaisesRegex(ValueError,'尚未核实'):source_for('qwenimage21','modelscope')

    def test_provider_url_injection_rejected(self):
        for provider in ['https://evil.test','../hf',None,True]:
            with self.assertRaises(ValueError):source_for('qwen05',provider)

    def test_unknown_models_rejected(self):
        for model in ['Qwen/Anything','../x',None]:
            with self.assertRaises(ValueError):source_for(model,'modelscope')

    def test_missing_file_rejected(self):
        m=manifest();m['siblings'].pop()
        with self.assertRaises(ValueError):self.resolve(data=m)

    def test_duplicate_file_rejected(self):
        m=manifest();m['siblings'].append(copy.deepcopy(m['siblings'][0]))
        with self.assertRaises(ValueError):self.resolve(data=m)

    def test_modelscope_api_failure_not_empty_success(self):
        with self.assertRaises(ValueError):self.resolve(provider='modelscope',data={'Code':500,'Message':'error'})

    def test_negative_size_rejected(self):
        m=manifest();m['siblings'][0]['size']=-1
        with self.assertRaises(ValueError):self.resolve(data=m)

    def test_source_url_requires_catalog_repo_and_immutable_revision(self):
        q=self.resolve(provider='modelscope');u=file_url(q,q['files'][0]);self.assertIn('modelscope.cn',u);self.assertIn('FilePath=',u)
        for change in [{'repository':'other/model'},{'revision':'master'}]:
            with self.assertRaises(ValueError):file_url(dict(q,**change),q['files'][0])

    def test_initial_url_security(self):
        for url in ['http://modelscope.cn/x','https://modelscope.cn.evil.test/x','https://huggingface.co@127.0.0.1/x','https://huggingface.co:444/x']:
            with self.assertRaises(ValueError):open_https(url)

    def test_prepare_binds_provider_and_does_not_download_weights(self):
        q=self.resolve(provider='modelscope')
        with patch('local_agent.model_hub.resolve_files',return_value=q) as resolve:
            offered=self.hub.prepare('qwen05','modelscope')
        resolve.assert_called_once_with('qwen05','modelscope')
        self.assertEqual(self.hub.quotes[offered['ticket']][1]['provider'],'modelscope')
        self.assertEqual(self.hub.job['status'],'idle');self.assertFalse(list(self.hub.models.iterdir()))

    def test_explicit_consent_required(self):
        for value in [False,'true',1,None]:
            with self.assertRaises(ValueError):self.hub.start('anything',value)

    def test_preparation_states_block_new_install(self):
        for phase in BUSY:
            self.hub.job={'status':phase}
            with self.assertRaises(ValueError):self.hub.start('ticket',True)

    def test_restart_retains_failure_not_busy_success(self):
        save_json(self.hub.job_path,{'status':'downloading','model':'qwen05','downloaded':42,'total':100})
        other=ModelHub(self.root,{'llm':str(self.engine)})
        self.assertEqual(other.status()['job']['status'],'interrupted')
        self.assertEqual(other.status()['job']['download_percent'],42)

    def test_progress_uses_actual_bytes(self):
        self.hub.job={'status':'downloading','model':'qwen05','total':200,'started_at':time.time()}
        self.hub._bytes(50,50)
        report=self.hub.status()['job'];self.assertEqual(report['download_percent'],25)
        self.assertIsNone(report['eta_seconds'])

    def test_waiting_for_data_does_not_invent_speed(self):
        self.hub.job={'status':'downloading','model':'qwen05','total':200,'downloaded':50,'started_at':time.time()-60,'last_data_at':time.time()-20,'speed_bps':123}
        report=self.hub.status()['job'];self.assertTrue(report['waiting_for_data']);self.assertEqual(report['speed_bps'],0);self.assertIsNone(report['eta_seconds'])

    def test_conversion_never_shows_download_speed(self):
        self.hub.job={'status':'converting','total':200,'downloaded':200,'speed_bps':123}
        self.assertEqual(self.hub.status()['job']['speed_bps'],0)

    def test_cancel_does_not_claim_immediate_completion(self):
        self.hub.job={'status':'converting','model':'qwen05'};self.hub.stop()
        self.assertEqual(self.hub.job['status'],'converting');self.assertTrue(self.hub.job['cancel_requested'])

    def test_digest_cancel_cooperates(self):
        p=self.root/'small';p.write_bytes(b'42')
        with self.assertRaises(InterruptedError):digest(p,cancelled=lambda:True)

    def test_unknown_export_profile_rejected_before_weight_access(self):
        with self.assertRaisesRegex(ValueError,'No reviewed'):export_model(self.root,self.root/'export',model_key='unknown')

    def test_15b_geometry_rejects_05b_file(self):
        (self.root/'config.json').write_text(json.dumps(PROFILES['qwen05']['profile']))
        with self.assertRaisesRegex(ValueError,'geometry'):export_model(self.root,self.root/'export',model_key='qwen15')

    def test_coder_keeps_own_identity_and_hash(self):
        self.assertEqual(PROFILES['qwen05']['profile'],PROFILES['qwen_coder05']['profile'])
        self.assertNotEqual(PROFILES['qwen05']['weight_sha256'],PROFILES['qwen_coder05']['weight_sha256'])

    def test_download_failures_never_auto_switch_providers(self):
        q=self.resolve(provider='modelscope')
        calls=[]
        def offline(url,**kw):calls.append(url);raise OSError('offline')
        with patch('local_agent.model_hub.open_https',side_effect=offline):self.hub._install(q)
        self.assertEqual(self.hub.job['status'],'failed');self.assertFalse(self.hub.active());self.assertEqual(len(calls),1);self.assertIn('modelscope.cn',calls[0])

    def test_bad_checksum_never_activates(self):
        q=self.resolve();q['files']=q['files'][:1]
        with patch('local_agent.model_hub.open_https',return_value=contextlib.closing(io.BytesIO(b'1234567890'))):self.hub._install(q)
        self.assertEqual(self.hub.job['status'],'failed');self.assertFalse(self.hub.active());self.assertIn('校验和',self.hub.job['error'])
