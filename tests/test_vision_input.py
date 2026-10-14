"""Security/routing protocol tests using explicit fixtures; not model inference."""
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from local_agent.backends import NcnnBridgeBackend
from local_agent.paths import PolicyError, Workspace
from local_agent.vision_input import enabled, image_path
from local_agent.web import WebApp, WebError


class VisionInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.model = self.root / 'model'; self.model.mkdir()
        (self.model / 'model.json').write_text(json.dumps({'setting': {'vision': {'type': 'minicpm_v4_7'}}}))
        self.cfg = {'backend': 'ncnn_bridge', 'command': ['explicit-protocol-fixture'],
                    'model': str(self.model), 'experimental_vision': True}
        self.ws = Workspace(self.root / 'workspace')
        # Header fixture intentionally tests validation only, never image decoding.
        self.png = b'\x89PNG\r\n\x1a\n' + struct.pack('>I',13) + b'IHDR' + struct.pack('>II',448,448)
        self.ws.path('image.png').write_bytes(self.png)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_disabled_and_model_specific(self):
        self.assertTrue(enabled(self.cfg))
        for change in ({'experimental_vision': False}, {'experimental_vision': 'true'}, {'backend':'ncnn_cli'}):
            self.assertFalse(enabled(dict(self.cfg, **change)))
        (self.model / 'model.json').write_text('{}')
        self.assertFalse(enabled(self.cfg))

    def test_workspace_and_size_guards(self):
        self.assertEqual(image_path(self.ws, 'image.png'), self.ws.path('image.png'))
        for path in ('../image.png', str(self.ws.path('image.png')), 'missing.png'):
            with self.assertRaises(PolicyError): image_path(self.ws,path)
        self.ws.path('bad.png').write_bytes(b'plain text')
        with self.assertRaises(PolicyError): image_path(self.ws,'bad.png')
        self.ws.path('huge.png').write_bytes(self.png[:16]+struct.pack('>II',20000,20000))
        with self.assertRaises(PolicyError): image_path(self.ws,'huge.png')
        self.ws.path('link.png').symlink_to(self.ws.path('image.png'))
        with self.assertRaises(PolicyError): image_path(self.ws,'link.png')

    def test_jpeg_header_dimensions(self):
        jpeg=b'\xff\xd8\xff\xc0'+struct.pack('>HBHHB',8,8,448,448,3)
        self.ws.path('image.jpg').write_bytes(jpeg)
        self.assertEqual(image_path(self.ws,'image.jpg'),self.ws.path('image.jpg'))
        self.ws.path('bomb.jpg').write_bytes(b'\xff\xd8\xff\xc0'+struct.pack('>HBHHB',8,8,9000,9000,3))
        with self.assertRaises(PolicyError): image_path(self.ws,'bomb.jpg')
        self.ws.path('short.jpg').write_bytes(b'\xff\xd8\xff\xc0\xff\xff')
        with self.assertRaises(PolicyError): image_path(self.ws,'short.jpg')

    def test_bridge_capability_handshake_and_no_text_fallback(self):
        backend=NcnnBridgeBackend(self.cfg,self.root)
        backend.rpc=Mock(); backend.rpc.request.side_effect=[{'single_image':'unsupported'}]
        with self.assertRaises(PolicyError): backend.complete_with_image([{'role':'user','content':'describe'}],self.ws,'image.png')
        self.assertEqual(backend.rpc.request.call_count,1)
        backend.rpc.reset_mock(); backend.rpc.request.side_effect=[{'single_image':'minicpm_v4_7_experimental'}, {'text':'PROTOCOL FIXTURE'}]
        self.assertEqual(backend.complete_with_image([{'role':'user','content':'describe'}],self.ws,'image.png'),'PROTOCOL FIXTURE')
        method, params=backend.rpc.request.call_args.args
        self.assertEqual(method,'infer'); self.assertEqual(params['image_path'],str(self.ws.path('image.png')))

    def test_experimental_backend_forces_cpu(self):
        backend=NcnnBridgeBackend(dict(self.cfg,device='vulkan'),self.root)
        with patch('local_agent.backends.StdioRPC') as rpc:
            rpc.return_value.request.return_value={'text':'CPU PROTOCOL FIXTURE'}
            backend.complete([{'role':'user','content':'hello'}])
            self.assertNotIn('--vulkan',rpc.call_args.args[0])
            self.assertEqual(backend.device_selection['selected'],'cpu')
            self.assertEqual(backend.device_selection['reason'],'minicpm_experimental_cpu_fp32')

    def test_regular_text_unchanged(self):
        backend=NcnnBridgeBackend(self.cfg,self.root); backend.rpc=Mock()
        backend.rpc.request.return_value={'text':'TEXT FIXTURE'}
        self.assertEqual(backend.complete([{'role':'user','content':'hello'}]),'TEXT FIXTURE')
        self.assertNotIn('image_path',backend.rpc.request.call_args.args[1])
        self.assertEqual(backend.rpc.request.call_count,1)

    def test_web_requires_opt_in_single_image_chat(self):
        no_runtime=patch("local_agent.web.make_runtime", side_effect=AssertionError("Image chat must not initialize tools"))
        no_runtime.start(); self.addCleanup(no_runtime.stop)
        fixture=Mock(spec=['complete','complete_with_image','release','label']); fixture.label='VISION_PROTOCOL_FIXTURE'; fixture.complete_with_image.return_value='VISION PROTOCOL FIXTURE'; fixture.complete.return_value='TEXT FIXTURE'
        cfg={'workspace':str(self.root/'web-workspace'),'llm':self.cfg,'image':{'enabled':False},'python':{'mode':'disabled'},'commands':{},'mcp_servers':[]}
        app=WebApp(cfg,self.root/'data',backend_factory=lambda _:fixture)
        sid=app.sessions.new()['id']; app.ws(sid).path('image.png').write_bytes(self.png)
        payload={'message':'Describe the attached photograph','mode':'chat','attachments':['image.png'],'interpret_image':True}
        for change in ({'attachments':[]},{'attachments':['image.png','image.png']},{'mode':'agent'},{'interpret_image':'true'}):
            with self.assertRaises(WebError): app.submit(sid,dict(payload,**change))
        run=app.submit(sid,payload)['run_id']
        deadline=time.monotonic()+5
        while app.get_run(run).status=='running' and time.monotonic()<deadline: time.sleep(.01)
        self.assertEqual(app.get_run(run).status,'completed')
        self.assertEqual(fixture.complete_with_image.call_count,1); fixture.complete.assert_not_called()
        self.assertIn('pixels supplied separately',fixture.complete_with_image.call_args.args[0][-1]['content'])
        conversation=fixture.complete_with_image.call_args.args[0]
        self.assertEqual([message['role'] for message in conversation],['system','user'])
        self.assertIn('You have no tools in this chat mode.',conversation[0]['content'])
        self.assertTrue(all(set(message)=={'role','content'} for message in conversation))

