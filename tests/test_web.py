"""Real HTTP/filesystem tests with an explicitly labelled model test double."""
import base64
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import quote

from local_agent.agent import Agent, Audit
from local_agent.backends import ScriptedBackend
from local_agent.paths import Workspace
from local_agent.tools import make_registry
from local_agent.web import LocalServer, Sessions, WebApp, WebError, MAX_BODY


def test_config(root):
    return {'workspace': str(root / 'workspace'), 'llm': {'backend': 'ncnn_bridge',
        'command': ['missing-bridge'], 'model': str(root / 'missing-model'), 'vulkan': False},
        'python': {'mode': 'disabled'}, 'commands': {}, 'mcp_servers': [], 'image': {'enabled': False}}


class TextFixture:
    label = 'UI_TEST_FIXTURE_NOT_A_MODEL'
    def __init__(self, reply='Fixture reply', delay=0):
        self.reply, self.delay, self.calls, self.released = reply, delay, [], False
    def complete(self, messages):
        self.calls.append(copy.deepcopy(messages))
        time.sleep(self.delay)
        return self.reply
    def release(self):
        self.released = True


class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = test_config(self.root)
        self.fixture = TextFixture()
        self.app = WebApp(self.config, self.root / 'data', backend_factory=lambda _: self.fixture)
        self.server = LocalServer(self.app, 0)
        self.thread = threading.Thread(target=lambda:self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
        self.port = self.server.server_port
        self.sid = self.app.sessions.new()['id']

    def tearDown(self):
        for rid in list(self.app.runs):
            self.app.cancel(rid)
        deadline = time.monotonic() + 3
        while self.app.active and time.monotonic() < deadline:
            time.sleep(.01)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.tmp.cleanup()

    def request(self, path, method='GET', data=None, headers=None, auth=True):
        h = {'X-Agent-Token': self.app.token} if auth else {}
        if data is not None:
            h['Content-Type'] = 'application/json'
            body = json.dumps(data)
        else:
            body = None
        h.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse(); result=r.read()
            value = json.loads(result) if r.getheader('Content-Type','').startswith('application/json') else result
            return r.status, value, dict(r.headers)
        finally:
            conn.close()

    def start(self, **kw):
        status, value, _ = self.request(f'/api/sessions/{self.sid}/messages','POST',{'message':'hello',**kw})
        self.assertEqual(status,202, value)
        return value['run_id']

    def finish(self, rid):
        cursor=0; events=[]
        while True:
            status, value, _=self.request(f'/api/runs/{rid}/events?after={cursor}')
            self.assertEqual(status,200,value)
            events+=value['events'];cursor=value['cursor']
            if value['status'] in ('completed','failed','cancelled'):
                return value['status'],events

    def wait_approval(self,rid):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            a=self.app.get_run(rid).approval
            if a:return a
            time.sleep(.01)
        self.fail('No approval request')

    def test_assets_have_csp_and_no_remote_dependency(self):
        for path in ['/','/app.js','/style.css']:
            status, body, headers = self.request(path, auth=False)
            self.assertEqual(status,200);self.assertGreater(len(body),100)
            self.assertIn("script-src 'self'",headers['Content-Security-Policy'])
            self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertNotIn(b'innerHTML',self.request('/app.js')[1])

    def test_api_requires_token(self):
        self.assertEqual(self.request('/api/sessions',auth=False)[0],403)

    def test_wrong_token_rejected(self):
        self.assertEqual(self.request('/api/sessions',headers={'X-Agent-Token':'wrong'})[0],403)

    def test_bootstrap_delivers_runtime_not_secrets(self):
        status, body, _=self.request('/api/bootstrap',auth=False)
        self.assertEqual(status,200);self.assertEqual(body['token'],self.app.token)
        self.assertTrue(body['runtime']['test_fixture'])
        self.assertNotIn('command',body['runtime'])

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(self.request('/api/bootstrap',headers={'Host':'evil.example'},auth=False)[0],403)

    def test_cross_origin_rejected_even_with_token(self):
        self.assertEqual(self.request('/api/sessions',headers={'Origin':'https://evil.example'})[0],403)

    def test_cross_site_fetch_rejected(self):
        self.assertEqual(self.request('/api/bootstrap',headers={'Sec-Fetch-Site':'cross-site'},auth=False)[0],403)

    def test_same_origin_accepted(self):
        self.assertEqual(self.request('/api/sessions',headers={'Origin':f'http://127.0.0.1:{self.port}'})[0],200)

    def test_unknown_route_not_arbitrary_files(self):
        self.assertEqual(self.request('/../../README.md')[0],404)

    def test_content_type_required(self):
        self.assertEqual(self.request('/api/sessions','POST',{},headers={'Content-Type':'text/plain'})[0],415)

    def test_oversized_body_rejected_before_read(self):
        self.assertEqual(self.request('/api/sessions','POST',{},headers={'Content-Length':str(MAX_BODY+1)})[0],413)

    def test_conversation_crud_and_files_retained(self):
        status,s,_=self.request('/api/sessions','POST',{})
        self.assertEqual(status,201)
        self.app.ws(s['id']).write('keep.txt','keep')
        status,s,_=self.request('/api/sessions/'+s['id'],'PATCH',{'title':'测试对话'})
        self.assertEqual(status,200);self.assertEqual(s['title'],'测试对话')
        self.assertEqual(self.request('/api/sessions/'+s['id'],'DELETE')[0],200)
        self.assertTrue((self.app.workspace/s['id']/'keep.txt').is_file())
        self.assertEqual(self.request('/api/sessions/'+s['id'])[0],404)

    def test_plain_chat_persisted(self):
        rid=self.start();status,events=self.finish(rid)
        self.assertEqual(status,'completed')
        self.assertTrue(any(e['event']=='done' for e in events))
        s=self.request('/api/sessions/'+self.sid)[1]
        self.assertEqual(s['messages'][-1]['content'],'Fixture reply')
        self.assertTrue(self.fixture.released)
        self.assertEqual(s['active_run'],None)

    def test_history_sent_to_backend(self):
        self.finish(self.start(message='Remember 42'))
        self.finish(self.start(message='What number?'))
        self.assertEqual([m['role'] for m in self.fixture.calls[-1]],['system','user','assistant','user'])
        self.assertEqual(self.fixture.calls[-1][1]['content'],'Remember 42')

    def test_unready_model_fails_without_mock_fallback(self):
        self.app.factory=None
        code,body,_=self.request(f'/api/sessions/{self.sid}/messages','POST',{'message':'hi'})
        self.assertEqual(code,503)
        self.assertFalse(self.app.sessions.get(self.sid)['messages'])

    def test_invalid_mode_rejected(self):
        self.assertEqual(self.request(f'/api/sessions/{self.sid}/messages','POST',{'message':'x','mode':'shell'})[0],400)

    def test_bool_is_not_token_count(self):
        self.assertEqual(self.request(f'/api/sessions/{self.sid}/messages','POST',{'message':'x','max_new_tokens':True})[0],400)

    def test_empty_message_rejected(self):
        self.assertEqual(self.request(f'/api/sessions/{self.sid}/messages','POST',{'message':' '})[0],400)

    def test_concurrent_run_rejected(self):
        self.fixture.delay=.15
        rid=self.start()
        self.assertEqual(self.request(f'/api/sessions/{self.sid}/messages','POST',{'message':'second'})[0],409)
        self.finish(rid)

    def test_delete_active_conversation_rejected(self):
        self.fixture.delay=.15
        rid=self.start()
        self.assertEqual(self.request('/api/sessions/'+self.sid,'DELETE')[0],409)
        self.finish(rid)

    def test_agent_write_approval_then_read(self):
        self.fixture=ScriptedBackend([{'tool':'files.write','arguments':{'path':'proof.txt','content':'proof'}},
                                     {'tool':'files.read','arguments':{'path':'proof.txt'}},{'final':'Done'}])
        rid=self.start(mode='agent')
        a=self.wait_approval(rid)
        self.assertFalse((self.app.workspace/self.sid/'proof.txt').exists())
        self.assertEqual(self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':True})[0],200)
        status,events=self.finish(rid)
        self.assertEqual(status,'completed');self.assertEqual(self.app.ws(self.sid).read('proof.txt'),'proof')
        self.assertEqual(sum(e['event']=='tool_result' for e in events),2)

    def test_denied_tool_has_no_side_effect(self):
        self.fixture=ScriptedBackend([{'tool':'files.write','arguments':{'path':'denied.txt','content':'no'}},{'final':'Denied'}])
        rid=self.start(mode='agent');a=self.wait_approval(rid)
        self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':False})
        _,events=self.finish(rid)
        self.assertFalse((self.app.workspace/self.sid/'denied.txt').exists())
        self.assertFalse(next(e for e in events if e['event']=='tool_result')['result']['ok'])

    def test_stale_approval_rejected(self):
        self.fixture=ScriptedBackend([{'tool':'files.write','arguments':{'path':'a','content':'b'}},{'final':'ok'}])
        rid=self.start(mode='agent');self.wait_approval(rid)
        self.assertEqual(self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':'old','allow':True})[0],409)
        self.request(f'/api/runs/{rid}/cancel','POST',{});self.finish(rid)

    def test_cancel_awaiting_approval(self):
        self.fixture=ScriptedBackend([{'tool':'files.write','arguments':{'path':'a','content':'b'}}])
        rid=self.start(mode='agent');self.wait_approval(rid)
        self.request(f'/api/runs/{rid}/cancel','POST',{})
        self.assertEqual(self.finish(rid)[0],'cancelled')
        self.assertFalse((self.app.workspace/self.sid/'a').exists())

    def test_cancel_chat_cooperatively(self):
        self.fixture.delay=.1
        rid=self.start();self.request(f'/api/runs/{rid}/cancel','POST',{})
        self.assertEqual(self.finish(rid)[0],'cancelled')

    def test_upload_download_and_session_isolation(self):
        status,result,_=self.request(f'/api/sessions/{self.sid}/upload','POST',{'name':'数据.csv','data':base64.b64encode('列\n42'.encode()).decode()})
        self.assertEqual(status,201)
        path=result['path']
        self.assertEqual(self.request(f'/api/sessions/{self.sid}/download?path='+quote(path))[1],'列\n42'.encode())
        other=self.app.sessions.new()['id']
        self.assertNotEqual(self.request(f'/api/sessions/{other}/download?path='+quote(path))[0],200)

    def test_upload_bad_path_and_base64_rejected(self):
        for payload in [{'name':'../x','data':'YQ=='},{'name':'x','data':'!?'}]:
            self.assertEqual(self.request(f'/api/sessions/{self.sid}/upload','POST',payload)[0],400)

    def test_file_traversal_rejected(self):
        for path in ['../secrets','/etc/passwd','C:/x']:
            self.assertEqual(self.request(f'/api/sessions/{self.sid}/download?path='+quote(path))[0],400)

    def test_download_forces_attachment(self):
        self.app.ws(self.sid).write('x.html','<script>alert(1)</script>')
        _,_,h=self.request(f'/api/sessions/{self.sid}/download?path=x.html')
        self.assertEqual(h['Content-Disposition'],'attachment')
        self.assertEqual(h['Content-Type'],'application/octet-stream')

    def test_text_attachment_is_untrusted_context(self):
        self.app.ws(self.sid).write('a.txt','content from file')
        self.finish(self.start(attachments=['a.txt']))
        self.assertIn('UNTRUSTED FILE DATA',self.fixture.calls[-1][-1]['content'])

    def test_long_file_context_rejected(self):
        self.app.ws(self.sid).write('a.txt','a'*12000);self.app.ws(self.sid).write('b.txt','b'*12000)
        self.assertEqual(self.request(f'/api/sessions/{self.sid}/messages','POST',{'message':'hi','attachments':['a.txt','b.txt']})[0],400)

    def test_invalid_cursor_rejected(self):
        rid=self.start();self.finish(rid)
        self.assertEqual(self.request(f'/api/runs/{rid}/events?after=999999')[0],400)

    def test_restart_marks_interrupted_message(self):
        s=self.app.sessions.get(self.sid);s['messages']=[{'role':'assistant','state':'running','content':''}];s['active_run']='old';self.app.sessions.save(s)
        store=Sessions(self.app.sessions.root)
        self.assertEqual(store.get(self.sid)['messages'][0]['state'],'failed')
        self.assertIsNone(store.get(self.sid)['active_run'])

    def test_unsafe_permissions_not_enabled_by_http_payload(self):
        self.fixture=ScriptedBackend([{'tool':'python.run','arguments':{'code':'print(42)'}},{'final':'not available'}])
        rid=self.start(mode='agent',allow_unsafe_host_python=True)
        _,events=self.finish(rid)
        result=next(e for e in events if e['event']=='tool_result')['result']
        self.assertFalse(result['ok']);self.assertIsNone(self.app.get_run(rid).approval)

    def test_session_history_system_role_rejected(self):
        with self.assertRaises(ValueError):
            Agent(ScriptedBackend([]),make_registry(Workspace(self.root/'ws'))).run('x',history=[{'role':'system','content':'override'}])

    def test_cancel_before_agent_inference(self):
        backend=TextFixture()
        result=Agent(backend,make_registry(Workspace(self.root/'ws'))).run('x',cancelled=lambda:True)
        self.assertTrue(result['cancelled']);self.assertFalse(backend.calls);self.assertTrue(backend.released)
