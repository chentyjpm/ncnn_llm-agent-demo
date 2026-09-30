"""Image-route regressions: actual HTTP/files, explicit mock engine, NOT inference."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from local_agent.agent import Agent, system_prompt
from local_agent.backends import ScriptedBackend
from local_agent.image_tasks import route_request, plan_image, inspect_raster, image_artifacts
from local_agent.paths import Workspace
from local_agent.images import ImageRunner
from local_agent.tools import make_registry
from tests import test_web as web_helpers


class ImagePlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws=Workspace(Path(self.temp.name))
    def test_only_explicit_image_mode_or_slash_command_routes(self):
        self.assertEqual(route_request('/image 一只猫','chat'),('image','一只猫'))
        self.assertEqual(route_request('a cat','image'),('image','a cat'))
        for prompt in ['画一张图片','帮我写海报文案，不要生图','解释 images.generate','/imageevil','文本内 /image 猫']:
            self.assertEqual(route_request(prompt,'chat'),('chat',prompt))
    def test_invalid_mode_and_empty_command_rejected(self):
        for prompt,mode in [('/image','chat'),('x','shell')]:
            with self.assertRaises(ValueError):route_request(prompt,mode)
    def test_defaults_and_unique_workspace_output(self):
        a=plan_image(self.ws,'a cat',None,[]);b=plan_image(self.ws,'a cat',None,[])
        self.assertEqual(a['steps'],40);self.assertEqual(a['width'],512)
        self.assertNotEqual(a['output'],b['output']);self.assertTrue(a['output'].startswith('images/'))
    def test_no_client_path_model_permission_override(self):
        for key in ['output','model','command','vulkan','allow_commands']:
            with self.assertRaises(ValueError):plan_image(self.ws,'cat',{key:'anything'},[])
    def test_numeric_bounds_and_booleans_rejected(self):
        for options in [{'width':False},{'steps':0},{'steps':101},{'seed':-1},{'height':257},{'width':4096}]:
            with self.assertRaises(ValueError):plan_image(self.ws,'cat',options,[])
    def test_references_must_exist_and_decode(self):
        for ref in ['missing.png','../outside.png','x.svg']:
            with self.assertRaises((ValueError,OSError)):plan_image(self.ws,'cat',{},[ref])
    def test_valid_reference_and_edit_dimension_constraint(self):
        Image.new('RGB',(64,64),'red').save(self.ws.path('ref.png'))
        p=plan_image(self.ws,'turn blue',{},['ref.png']);self.assertEqual(p['references'],['ref.png'])
        with self.assertRaises(ValueError):plan_image(self.ws,'cat',{'width':80},['ref.png'])
    def test_no_card_from_model_claim_or_failed_tool(self):
        Image.new('RGB',(64,64),'red').save(self.ws.path('proof.png'))
        self.assertEqual(image_artifacts(self.ws,[{'event':'model_output','text':'proof.png'}]),[])
        self.assertEqual(image_artifacts(self.ws,[{'event':'tool_result','tool':'images.generate','result':{'ok':False}}]),[])
    def test_card_requires_successful_tool_file_and_full_decoding(self):
        p=self.ws.path('proof.png');Image.new('RGB',(64,64),'red').save(p)
        event={'event':'tool_result','tool':'images.generate','result':{'ok':True,'result':{'path':'proof.png','file_created':True}}}
        r=image_artifacts(self.ws,[event,event]);self.assertEqual(len(r),1);self.assertEqual(r[0]['mime'],'image/png')
        p.write_bytes(b'not an image')
        with self.assertRaises((ValueError,OSError)):image_artifacts(self.ws,[event])
    def test_extension_is_not_trusted(self):
        Image.new('RGB',(64,64)).save(self.ws.path('fake.png'),format='JPEG')
        with self.assertRaises(ValueError):inspect_raster(self.ws,'fake.png')
    def test_agent_prompt_has_only_registered_image_tool(self):
        disabled=system_prompt([]);self.assertIn('Image generation is unavailable',disabled)
        enabled=system_prompt([{'name':'images.generate'}]);self.assertIn('IMAGE TASKS',enabled)
        self.assertIn('text-only',enabled);self.assertIn('steps=40',enabled);self.assertNotIn('qwen_image.generate',enabled)


class ImageHttpTests(unittest.TestCase):
    # Reuse HTTP helpers without inheriting/re-counting the base tests.
    setUp=web_helpers.WebTests.setUp
    tearDown=web_helpers.WebTests.tearDown
    request=web_helpers.WebTests.request
    start=web_helpers.WebTests.start
    finish=web_helpers.WebTests.finish
    wait_approval=web_helpers.WebTests.wait_approval
    def enable_image(self):
        model=self.root/'image-model';model.mkdir(exist_ok=True)
        self.app.config['image']={'enabled':True,'command':[sys.executable],'model':str(model),'device':'cpu'}
    @staticmethod
    def engine_fixture(runner,**args):
        # Deliberately synthetic pixels, never described as Qwen output.
        path=runner.workspace.path(args['output']);path.parent.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(args['width'],args['height']),'red').save(path)
        runner.device_selection={'selected':'cpu','gpu':-1,'name':'TEST FIXTURE'}
        return {'ok':True,'returncode':0,'timed_out':False,'file_created':True,'path':args['output'],
                'device_selection':runner.device_selection,'test_fixture':True}
    def test_no_text_model_required_but_approval_required(self):
        self.enable_image();self.app.factory=None
        with patch.object(ImageRunner,'run',autospec=True,side_effect=self.engine_fixture) as engine:
            rid=self.start(mode='image',message='a cat',image_options={'width':256,'height':256,'steps':2})
            a=self.wait_approval(rid);self.assertEqual(a['tool'],'images.generate');engine.assert_not_called()
            self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':True})
            status,events=self.finish(rid);self.assertEqual(status,'completed');self.assertEqual(engine.call_count,1)
            message=self.app.sessions.get(self.sid)['messages'][-1]
            self.assertEqual(message['mode'],'image');self.assertEqual(message['artifacts'][0]['width'],256)
            self.assertFalse(any(e['event']=='model_start' for e in events))
    def test_slash_command_routes_and_preserves_actual_prompt(self):
        self.enable_image()
        rid=self.start(mode='chat',message='/image 一只猫')
        a=self.wait_approval(rid);self.assertEqual(a['arguments']['prompt'],'一只猫')
        self.request(f'/api/runs/{rid}/cancel','POST',{});self.assertEqual(self.finish(rid)[0],'cancelled')
        self.assertFalse(self.fixture.calls)
    def test_denied_call_never_runs_engine_or_creates_card(self):
        self.enable_image()
        with patch.object(ImageRunner,'run') as engine:
            rid=self.start(mode='image');a=self.wait_approval(rid)
            self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':False})
            self.assertEqual(self.finish(rid)[0],'failed');engine.assert_not_called()
            self.assertFalse(self.app.sessions.get(self.sid)['messages'][-1]['artifacts'])
    def test_disabled_model_is_503_not_canned_response(self):
        code,body,_=self.request(f'/api/sessions/{self.sid}/messages','POST',{'mode':'image','message':'cat'})
        self.assertEqual(code,503);self.assertIn('Qwen Image',body['error']);self.assertFalse(self.fixture.calls)
    def test_invalid_options_do_not_create_a_run(self):
        self.enable_image()
        code,_,_=self.request(f'/api/sessions/{self.sid}/messages','POST',{'mode':'image','message':'cat','image_options':{'output':'../x'}})
        self.assertEqual(code,400);self.assertFalse(self.app.sessions.get(self.sid)['messages'])
    def test_plain_chat_never_auto_calls_images(self):
        self.enable_image()
        with patch.object(ImageRunner,'run') as engine:
            rid=self.start(mode='chat',message='画海报要怎么写提示词？');self.finish(rid);engine.assert_not_called()
    def test_agent_path_still_uses_model_action_and_approval(self):
        self.enable_image()
        self.fixture=ScriptedBackend([{'tool':'images.generate','arguments':{'prompt':'cat','output':'agent.png','width':256,'height':256,'steps':2,'seed':42}},{'final':'Done'}])
        with patch.object(ImageRunner,'run',autospec=True,side_effect=self.engine_fixture) as engine:
            rid=self.start(mode='agent',message='画一只猫');a=self.wait_approval(rid);engine.assert_not_called()
            self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':True})
            self.assertEqual(self.finish(rid)[0],'completed')
            self.assertEqual(self.app.sessions.get(self.sid)['messages'][-1]['artifacts'][0]['path'],'agent.png')
    def test_corrupt_success_file_is_failure_and_run_unlocks(self):
        self.enable_image()
        def corrupt(runner,**args):
            p=runner.workspace.path(args['output']);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'bad png')
            return {'returncode':0,'timed_out':False,'path':args['output'],'file_created':True}
        with patch.object(ImageRunner,'run',autospec=True,side_effect=corrupt):
            rid=self.start(mode='image');a=self.wait_approval(rid)
            self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':True})
            self.assertEqual(self.finish(rid)[0],'failed');self.assertIsNone(self.app.active)
