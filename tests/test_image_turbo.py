"""Turbo adapter and installer regressions. Tiny fixtures are NOT inference."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, unquote
from local_agent.image_profiles import BASE_IMAGE, TURBO_IMAGE, TURBO_FILES, image_profile, image_steps, shared_components
from local_agent.model_catalog import CATALOG
from local_agent.hf_download_pins import PINNED_HF
from local_agent.model_sources import file_url, source_for
from local_agent.model_hub import ModelHub
from local_agent.images import ImageRunner
from local_agent.paths import Workspace
from local_agent.tools import make_registry
from local_agent.desktop import automatic_config
from local_agent.image_tasks import plan_image
from tests.test_hf_mirror import metadata, resolve, Reply
from tests import test_image_tasks as image_helpers
from tests import test_web


class TurboProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.ws=Workspace(self.root/'workspace')
        self.config={'enabled':True,'command':[sys.executable],'device':'cpu','model':str(self.root/TURBO_IMAGE)}
        Path(self.config['model']).mkdir()
    def test_distinct_registered_checkpoint_and_dependency(self):
        c=CATALOG[TURBO_IMAGE]
        self.assertEqual(c['kind'],'image');self.assertEqual(c['depends_on'],[BASE_IMAGE])
        self.assertIn(BASE_IMAGE,CATALOG);self.assertEqual(c['fixed_steps'],8)
    def test_only_reviewed_image_source_enabled(self):
        self.assertEqual(set(CATALOG[TURBO_IMAGE]['sources']),{'huggingface'})
        for provider in ['modelscope','sdu']:
            with self.assertRaises(ValueError):source_for(TURBO_IMAGE,provider)
    def test_new_model_uses_exact_six_transformer_files(self):
        self.assertEqual({x['name'] for x in PINNED_HF[TURBO_IMAGE]['files']},TURBO_FILES)
        self.assertTrue(all(f['remote'].startswith(TURBO_IMAGE+'/') for f in PINNED_HF[TURBO_IMAGE]['files']))
    def test_profiles_match_native_directory_policy(self):
        for suffix in (TURBO_IMAGE,TURBO_IMAGE.upper(),TURBO_IMAGE+'/',TURBO_IMAGE+'/./'):
            p=image_profile({'model':str(self.root/suffix)})
            self.assertEqual(p['fixed_steps'],8);self.assertEqual(p['scheduler'],'turbo-fixed-8')
    def test_base_8_steps_is_not_turbo(self):
        c={'model':str(self.root/BASE_IMAGE)}
        self.assertEqual(image_profile(c)['default_steps'],40)
        self.assertEqual(image_steps(c,8),8);self.assertIsNone(image_profile(c)['fixed_steps'])
    def test_default_turbo_steps_are_eight(self):
        self.assertEqual(image_steps(self.config),8)
        self.assertEqual(plan_image(self.ws,'cat',{},[],image_config=self.config)['steps'],8)
    def test_wrong_turbo_steps_are_not_silently_corrected(self):
        for steps in (1,2,4,40,100,0,True,'8'):
            with self.subTest(steps=steps),self.assertRaises(ValueError):image_steps(self.config,steps)
    def test_planning_rejects_wrong_steps_before_approval(self):
        with self.assertRaisesRegex(ValueError,'8'):plan_image(self.ws,'cat',{'steps':40},[],image_config=self.config)
    def test_tool_schema_is_bound_to_current_variant(self):
        r=make_registry(self.ws,image_runner=ImageRunner(self.ws,self.config))
        s=next(x for x in r.schemas() if x['name']=='images.generate')
        self.assertEqual(s['inputSchema']['properties']['steps']['enum'],[8])
        self.assertEqual(s['inputSchema']['properties']['steps']['default'],8)
    def test_default_command_passes_actual_eight_steps(self):
        with patch('local_agent.images.shared_components'):
            cmd=ImageRunner(self.ws,self.config).command(prompt='cat',output='out.png')
        self.assertEqual(cmd[cmd.index('-l')+1],'8')
        self.assertEqual(cmd[cmd.index('-m')+1],str(Path(self.config['model']).resolve()))
        self.assertEqual(cmd[cmd.index('-g')+1],'-1')
    def test_missing_shared_base_rejected_before_process(self):
        with patch('local_agent.images.run_process') as process:
            with self.assertRaises(ValueError):ImageRunner(self.ws,self.config).run(prompt='cat',output='out.png')
            process.assert_not_called()
    def test_bad_steps_rejected_before_probe(self):
        with patch('local_agent.images.select') as probe:
            with self.assertRaises(ValueError):ImageRunner(self.ws,self.config).command(prompt='cat',output='out.png',steps=2)
            probe.assert_not_called()
    def test_report_steps_do_not_parse_prompt_as_option(self):
        with patch('local_agent.images.shared_components'),patch('local_agent.images.run_process',return_value={'returncode':1,'timed_out':False}):
            r=ImageRunner(self.ws,self.config).run(prompt='-l',output='out.png')
        self.assertEqual(r['steps'],8)
    def test_origin_and_mirror_same_pinned_files(self):
        for route in ('direct','hf_mirror'):
            seen=[];m=resolve(TURBO_IMAGE,route,seen=seen)
            self.assertEqual(m['revision'],PINNED_HF[TURBO_IMAGE]['revision'])
            self.assertEqual({f['name'] for f in m['files']},TURBO_FILES);self.assertEqual(len(seen),1)
            for f in m['files']:self.assertIn('/'+TURBO_IMAGE+'/',file_url(m,f))
    def test_origin_turbo_is_also_validated_against_pin(self):
        m=metadata(TURBO_IMAGE);m['siblings'][0]['size']+=1
        for route in ('direct','hf_mirror'):
            with self.assertRaises(ValueError):resolve(TURBO_IMAGE,route,data=m)
    def test_base_files_cannot_masquerade_as_turbo(self):
        m=metadata(BASE_IMAGE);m['sha']=PINNED_HF[TURBO_IMAGE]['revision']
        with self.assertRaises(ValueError):resolve(TURBO_IMAGE,data=m)


class TurboInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.engine=self.root/'nonexecutable-fixture';self.engine.write_bytes(b'FIXTURE')
        self.hub=ModelHub(self.root,{'image':str(self.engine),'llm':str(self.engine)})
        self.data={};self.small={}
        for mid,names in [(BASE_IMAGE,['processor/vocab.txt','text_encoder/text_encoder.ncnn.bin','vae/decoder.ncnn.bin']), (TURBO_IMAGE,sorted(TURBO_FILES))]:
            files=[]
            for name in names:
                data=('EXPLICIT TINY TEST FILE '+mid+'/'+name).encode();self.data[mid+'/'+name]=data
                files.append({'name':name,'remote':mid+'/'+name,'bytes':len(data),'algorithm':'sha256','digest':hashlib.sha256(data).hexdigest()})
            self.small[mid]={'repository':'nihui-szyl/qwen-image-ncnn','revision':('a' if mid==BASE_IMAGE else 'b')*40,'files':files}
        self.patcher=patch.dict(PINNED_HF,self.small);self.patcher.start();self.addCleanup(self.patcher.stop)
    def base(self):
        folder=self.hub.models/BASE_IMAGE;folder.mkdir(exist_ok=True);files=[]
        for f in self.small[BASE_IMAGE]['files']:
            p=folder/f['name'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(self.data[f['remote']])
            files.append({'name':f['name'],'bytes':f['bytes'],'sha256':f['digest']})
        (folder/'READY.json').write_text(json.dumps({'id':BASE_IMAGE,'files':files}))
        return folder
    def quote(self,route='direct'):
        fs=self.small[TURBO_IMAGE]['files']
        return {**self.small[TURBO_IMAGE],'id':TURBO_IMAGE,'provider':'huggingface','download_route':route,
                'download_bytes':sum(f['bytes'] for f in fs),'disk_required_bytes':10000}
    def install(self,quote=None):
        self.urls=[]
        def get(url,**kwargs):
            self.urls.append(url);path=unquote(urlsplit(url).path)
            key=next(key for key in self.data if path.endswith('/'+key))
            return Reply(self.data[key],url)
        with patch('local_agent.model_hub.open_https',side_effect=get):self.hub._install(quote or self.quote())
    def test_dependency_missing_is_visible(self):
        item=next(x for x in self.hub.status()['models'] if x['id']==TURBO_IMAGE)
        self.assertEqual(item['missing_dependencies'],[BASE_IMAGE])
    def test_no_network_before_missing_dependency_error(self):
        with patch('local_agent.model_hub.resolve_files') as resolver:
            with self.assertRaisesRegex(ValueError,'基础模型'):self.hub.prepare(TURBO_IMAGE,'huggingface')
            resolver.assert_not_called()
    def test_prepare_discloses_reuse_and_route(self):
        self.base()
        with patch('local_agent.model_hub.resolve_files',return_value=self.quote('hf_mirror')):
            q=self.hub.prepare(TURBO_IMAGE,'huggingface',download_route_id='hf_mirror')
        self.assertEqual(q['base_dependency']['copied_bytes'],0);self.assertEqual(q['download_route'],'hf_mirror')
        self.assertEqual(q['download_bytes'],sum(x['bytes'] for x in self.small[TURBO_IMAGE]['files']))
    def test_install_downloads_six_files_not_shared_components(self):
        base=self.base();before={p.relative_to(base):p.read_bytes() for p in base.rglob('*') if p.is_file()}
        self.install();self.assertEqual(self.hub.job['status'],'completed',self.hub.job)
        self.assertEqual(len(self.urls),6);self.assertTrue(all('/'+TURBO_IMAGE+'/' in u for u in self.urls))
        self.assertEqual(before,{p.relative_to(base):p.read_bytes() for p in base.rglob('*') if p.is_file()})
        self.assertFalse((self.hub.models/TURBO_IMAGE/'processor').exists())
        ready=json.loads((self.hub.models/TURBO_IMAGE/'READY.json').read_text())
        self.assertTrue(ready['base_dependency']['hashes_verified']);self.assertEqual(ready['base_dependency']['copied_bytes'],0)
    def test_mirror_installer_stays_on_selected_route(self):
        self.base();self.install(self.quote('hf_mirror'))
        self.assertEqual(self.hub.job['status'],'completed',self.hub.job)
        self.assertTrue(all(u.startswith('https://hf-mirror.com/') for u in self.urls))
    def test_automatic_config_keeps_required_directory_name(self):
        self.base();self.install();cfg=automatic_config(self.root,self.hub)
        self.assertEqual(Path(cfg['image']['model']).name,TURBO_IMAGE);self.assertEqual(cfg['image']['device'],'auto')
    def test_switch_back_does_not_remove_turbo(self):
        self.base();self.install();self.hub.activate(BASE_IMAGE)
        self.assertTrue(self.hub.installed(TURBO_IMAGE));self.assertEqual(self.hub.active()['image'],BASE_IMAGE)
    def test_missing_shared_file_invalidates_installed_turbo(self):
        base=self.base();self.install();(base/'processor/vocab.txt').unlink()
        self.assertFalse(self.hub.installed(TURBO_IMAGE));self.assertNotIn('image',self.hub.active())
    def test_changed_same_size_base_file_fails_final_hash(self):
        base=self.base();p=base/'processor/vocab.txt';p.write_bytes(b'X'*p.stat().st_size)
        self.install();self.assertEqual(self.hub.job['status'],'failed')
        self.assertFalse((self.hub.models/TURBO_IMAGE/'READY.json').exists())
    def test_activation_rechecks_dependency_hash(self):
        base=self.base();self.install();self.hub.activate(BASE_IMAGE)
        p=base/'processor/vocab.txt';p.write_bytes(b'X'*p.stat().st_size)
        with self.assertRaisesRegex(ValueError,'校验失败'):self.hub.activate(TURBO_IMAGE)
    def test_cancel_during_shared_verification(self):
        base=self.base()
        with self.assertRaises(InterruptedError):shared_components(base,verify_hashes=True,cancelled=lambda:True)
    def test_symlink_inside_base_rejected(self):
        base=self.base().resolve();target=base/'processor'
        with patch.object(Path,'is_symlink',autospec=True,side_effect=lambda p:p==target):
            with self.assertRaisesRegex(ValueError,'符号链接'):shared_components(base)
    def test_busy_install_cannot_be_switched(self):
        self.base();self.hub.job['status']='downloading'
        with self.assertRaises(ValueError):self.hub.activate(BASE_IMAGE)


class TurboHttpTests(unittest.TestCase):
    setUp=test_web.WebTests.setUp;tearDown=test_web.WebTests.tearDown
    request=test_web.WebTests.request;start=test_web.WebTests.start
    finish=test_web.WebTests.finish;wait_approval=test_web.WebTests.wait_approval
    def turbo(self):
        model=self.root/TURBO_IMAGE;model.mkdir()
        self.app.config['image']={'enabled':True,'command':[sys.executable],'model':str(model),'device':'cpu'}
    def test_runtime_exposes_eight_steps(self):
        self.turbo();self.assertEqual(self.app.info()['image_profile']['fixed_steps'],8)
    def test_http_default_approval_and_execution_use_eight(self):
        self.turbo();self.app.factory=None
        with patch.object(ImageRunner,'run',autospec=True,side_effect=image_helpers.ImageHttpTests.engine_fixture) as engine:
            rid=self.start(mode='image',message='TEST FIXTURE CAT');a=self.wait_approval(rid)
            self.assertEqual(a['arguments']['steps'],8);engine.assert_not_called()
            self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':True})
            self.assertEqual(self.finish(rid)[0],'completed');self.assertEqual(engine.call_args.kwargs['steps'],8)
    def test_wrong_http_steps_return_400_without_run(self):
        self.turbo()
        code,body,_=self.request(f'/api/sessions/{self.sid}/messages','POST',{'mode':'image','message':'cat','image_options':{'steps':2}})
        self.assertEqual(code,400);self.assertIn('8',body['error']);self.assertFalse(self.app.sessions.get(self.sid)['messages'])
    def test_turbo_denial_never_runs_engine(self):
        self.turbo()
        with patch.object(ImageRunner,'run') as engine:
            rid=self.start(mode='image');a=self.wait_approval(rid)
            self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':False})
            self.assertEqual(self.finish(rid)[0],'failed');engine.assert_not_called()
