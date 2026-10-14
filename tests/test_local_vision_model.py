"""Local import/activation protocol fixtures, not model inference."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_agent.local_vision_model import MODEL_ID, validate_package, check_bridge
from local_agent.model_hub import ModelHub
from local_agent.desktop import automatic_config
from local_agent.backends import NcnnBridgeBackend
from local_agent.web import WebApp, WebError


def package(root):
    root.mkdir()
    def put(name,data=b'EXPLICIT MODEL FILE FIXTURE'):
        (root/name).write_bytes(data);return name
    params={name+'_'+kind:put(name+'.'+kind) for name in ('embed_token','decoder','proj_out') for kind in ('param','bin')}
    pre=put('tokenizer.txt',b'minicpm_v47_tokenizer_v1\nfixture\n')
    cfg={'params':params,'tokenizer':{'type':'bbpe','vocab_file':put('vocab.txt'),'merges_file':put('merges.txt'),'pretokenizer':{'type':'minicpm_v47','file':pre}},'setting':{'vision':{'type':'minicpm_v4_7','manifest':'vision.json'}}}
    manifest={'type':'minicpm_v47','format_version':1,'weight_dtype':'float32','scope':'single_image_thumbnail','downsample_mode':'16x','patch_size':14,'hidden_size':1152,'output_hidden_size':1024,
        'components':{name:{kind:put(name+'.'+kind) for kind in ('param','bin')} for name in ('patch_embed','encoder_pre','window_attn','window_mlp','encoder_post','projector')},
        'position_embeddings':{'file':'position.f32','rows':4900,'cols':1152}}
    with (root/'position.f32').open('wb') as f:f.truncate(4900*1152*4)
    (root/'model.json').write_text(json.dumps(cfg));(root/'vision.json').write_text(json.dumps(manifest))
    return cfg


class LocalVisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.model=self.root/'model';self.cfg=package(self.model)
        self.engine=self.root/'bridge';self.engine.write_text('EXPLICIT EXECUTABLE FIXTURE')
        self.hub=ModelHub(self.root/'home',{'llm':str(self.engine),'image':str(self.root/'image')})

    def test_import_activate_restart_automatic_config(self):
        with patch('local_agent.model_hub.check_bridge') as probe:
            self.assertEqual(self.hub.import_vision(str(self.model),True)['activated'],False)
            self.assertNotIn('llm',self.hub.active())
            self.hub.activate(MODEL_ID);self.assertEqual(probe.call_count,2)
            with self.assertRaisesRegex(ValueError,'切换'):self.hub.import_vision(str(self.model),True)
        restarted=ModelHub(self.root/'home',self.hub.engines)
        cfg=automatic_config(self.root/'home',restarted)
        self.assertEqual(cfg['llm']['model'],str(self.model));self.assertEqual(cfg['llm']['device'],'cpu')
        self.assertTrue(cfg['llm']['experimental_vision']);self.assertFalse(cfg['llm']['agent_enabled'])
        card=next(m for m in restarted.status()['models'] if m['id']==MODEL_ID)
        self.assertTrue(card['active']);self.assertNotIn('agent-experimental',card['capabilities'])
        self.assertFalse((self.hub.models/MODEL_ID).exists())
        with self.assertRaises(ValueError):self.hub.prepare(MODEL_ID)

    def test_qwen_default_unchanged(self):
        cfg=automatic_config(self.root/'home',self.hub)
        self.assertEqual(cfg['llm']['device'],'auto');self.assertNotIn('experimental_vision',cfg['llm'])
        self.assertEqual(Path(cfg['llm']['model']).name,'qwen05')

    def test_requires_consent_and_bridge_capability(self):
        with patch('local_agent.model_hub.check_bridge',side_effect=ValueError('unsupported')):
            with self.assertRaises(ValueError):self.hub.import_vision(str(self.model),True)
        with self.assertRaises(ValueError):self.hub.import_vision(str(self.model),False)
        self.assertFalse((self.hub.home/'imported-vision.json').exists())
        good={'returncode':0,'timed_out':False,'stdout':json.dumps({'minicpm_v4_7_runtime':1,'single_image_protocol':True})}
        with patch('local_agent.local_vision_model.run_process',return_value=good) as run:
            check_bridge(self.engine,self.hub.home)
            self.assertEqual(run.call_args.args[0],[str(self.engine),'--capabilities'])
        with patch('local_agent.local_vision_model.run_process',return_value=dict(good,output_truncated=True)):
            with self.assertRaises(ValueError):check_bridge(self.engine,self.hub.home)
        for data in ({},{'minicpm_v4_7_runtime':True,'single_image_protocol':True},{'minicpm_v4_7_runtime':1,'single_image_protocol':False}):
            with patch('local_agent.local_vision_model.run_process',return_value=dict(good,stdout=json.dumps(data))):
                with self.assertRaises(ValueError):check_bridge(self.engine,self.hub.home)

    def test_package_containment_missing_and_changed(self):
        for directory in ('relative',str(self.root/'absent')):
            with self.assertRaises(ValueError):validate_package(directory)
        p=self.model/'model.json'
        for name in ('../outside.bin','/tmp/outside.bin','C:\\outside.bin'):
            changed=json.loads(json.dumps(self.cfg));changed['params']['decoder_bin']=name;p.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):validate_package(str(self.model))
        p.write_text(json.dumps(self.cfg))
        with patch('local_agent.model_hub.check_bridge'):self.hub.import_vision(str(self.model),True)
        (self.model/'decoder.bin').write_bytes(b'changed')
        self.assertFalse(self.hub.installed(MODEL_ID))
        (self.model/'decoder.bin').unlink();(self.model/'decoder.bin').symlink_to(self.engine)
        with self.assertRaises(ValueError):validate_package(str(self.model))

    def test_agent_mode_blocked_without_image_opt_in(self):
        cfg={'workspace':str(self.root/'workspace'),'llm':{'backend':'ncnn_bridge','model':str(self.model),'command':[str(self.engine)],'experimental_vision':False},'image':{'enabled':False}}
        app=WebApp(cfg,self.root/'data');sid=app.sessions.new()['id']
        with self.assertRaisesRegex(WebError,'Agent'):app.submit(sid,{'message':'hello','mode':'agent'})
        self.assertFalse(app.info()['agent_enabled']);self.assertEqual(app.info()['device'],'CPU')
        cfg['llm']['device']='vulkan'
        self.assertEqual(app.info()['devices']['llm']['selected'],'cpu')
        self.assertEqual(app.info()['devices']['llm']['reason'],'minicpm_experimental_cpu_fp32')
        backend=NcnnBridgeBackend(cfg['llm'],self.root)
        with patch('local_agent.backends.StdioRPC') as rpc:
            rpc.return_value.request.return_value={'text':'PROTOCOL FIXTURE'}
            backend.config['device']='vulkan';backend.complete([{'role':'user','content':'hello'}])
            self.assertNotIn('--vulkan',rpc.call_args.args[0]);self.assertEqual(backend.device_selection['selected'],'cpu')
