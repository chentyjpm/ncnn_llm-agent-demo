"""Runtime projection and real HTTP/file tests. Models are labelled fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from local_agent.activity import Activity, MAX_STEPS, display_value, interrupted, visible_answer
from local_agent.backends import ScriptedBackend
from local_agent.web import Sessions, Run, LiveAudit
from tests import test_web as web_fixture


class Clock:
    def __init__(self): self.t = 10.0
    def __call__(self): return self.t
    def advance(self, seconds=1): self.t += seconds


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(); self.a = Activity('agent', clock=self.clock)
    def emit(self, kind, **kw): self.a.record(kind, kw)
    def start_tool(self): self.emit('tool_start', tool='files.write', step=1, arguments={'path':'a.txt', 'content':'data'})
    def test_no_invented_plan_before_events(self):
        self.assertEqual(self.a.snapshot()['steps'], [])
        self.assertEqual(self.a.snapshot()['source'], 'runtime_events')
    def test_context_does_not_copy_prompt_or_attached_text(self):
        self.emit('run_started', model='Qwen', history_turns=2, attachments=1, task='PRIVATE_TASK')
        self.assertNotIn('PRIVATE_TASK', json.dumps(self.a.snapshot()))
        self.assertEqual(self.a.snapshot()['context']['history_turns'], 2)
    def test_model_timing_from_monotonic_clock(self):
        self.emit('model_start',step=1); self.clock.advance(2.5); self.emit('model_done',output_chars=3)
        row=self.a.snapshot()['steps'][0]
        self.assertEqual(row['duration_ms'],2500); self.assertEqual(row['status'],'completed')
    def test_model_content_is_not_a_summary(self):
        self.emit('model_start'); self.emit('model_done',text='<think>PRIVATE_REASONING</think>',output_chars=37,reasoning_detected=True)
        self.assertNotIn('PRIVATE_REASONING',json.dumps(self.a.snapshot()))
        self.assertTrue(self.a.snapshot()['reasoning_detected'])
    def test_requested_tool_not_yet_executing(self):
        self.start_tool();self.clock.advance(2)
        row=self.a.snapshot()['steps'][0];self.assertEqual(row['status'],'prepared');self.assertNotIn('execution_ms',row)
    def test_approval_and_execution_durations_separate(self):
        self.start_tool();self.emit('approval_required',approval={'id':'a'})
        self.clock.advance(5);self.emit('approval_resolved',allowed=True)
        self.emit('tool_execute');self.clock.advance(2);self.emit('tool_result',result={'ok':True,'result':{'bytes':4}})
        row=self.a.snapshot()['steps'][0]
        self.assertEqual((row['wait_ms'],row['execution_ms'],row['duration_ms']),(5000,2000,7000))
    def test_denial_is_not_execution(self):
        self.start_tool();self.emit('approval_required',approval={'id':'a'});self.clock.advance(3)
        self.emit('approval_resolved',allowed=False,reason='denied');self.emit('tool_result',result={'ok':False})
        row=self.a.snapshot()['steps'][0];self.assertEqual(row['status'],'denied');self.assertEqual(row['execution_ms'],0)
        self.assertEqual(self.a.snapshot()['tool_counts']['denied'],1)
    def test_expiry_distinct_from_denial(self):
        self.start_tool();self.emit('approval_required',approval={'id':'a'});self.clock.advance(300)
        self.emit('approval_resolved',allowed=False,reason='expired');self.emit('tool_result',result={'ok':False})
        self.assertEqual(self.a.snapshot()['steps'][0]['status'],'expired')
    def test_failed_result_not_relabelled_after_final(self):
        self.start_tool();self.emit('tool_execute');self.emit('tool_result',result={'ok':False,'error':'failed'})
        self.emit('run_finished',status='completed')
        self.assertEqual(self.a.snapshot()['steps'][0]['status'],'failed')
        self.assertEqual(self.a.snapshot()['tool_counts']['failed'],1)
    def test_cancel_request_does_not_claim_stopped(self):
        self.start_tool();self.emit('tool_execute');self.emit('stopping')
        self.assertEqual(self.a.snapshot()['state'],'stopping')
        self.assertEqual(self.a.snapshot()['steps'][0]['status'],'running')
    def test_cancel_keeps_completed_tool_result(self):
        self.start_tool();self.emit('tool_execute');self.emit('tool_result',result={'ok':True})
        self.emit('run_finished',status='cancelled')
        self.assertEqual(self.a.snapshot()['steps'][0]['status'],'completed')
    def test_cancel_closes_unfinished_execution(self):
        self.start_tool();self.emit('tool_execute');self.clock.advance(2);self.emit('run_finished',status='cancelled')
        self.assertEqual(self.a.snapshot()['steps'][0]['status'],'cancelled')
        self.assertEqual(self.a.snapshot()['tool_counts']['cancelled'],1)
    def test_failure_closes_running_model(self):
        self.emit('model_start');self.emit('run_finished',status='failed',error='TimeoutError')
        self.assertEqual(self.a.snapshot()['steps'][0]['status'],'interrupted')
    def test_same_tool_repeated_has_distinct_ids(self):
        for i in range(2): self.start_tool();self.emit('tool_execute');self.emit('tool_result',result={'ok':True})
        rows=self.a.snapshot()['steps'];self.assertNotEqual(rows[0]['id'],rows[1]['id'])
    def test_format_retry_visible_and_not_success(self):
        self.emit('format_error',error='Invalid JSON')
        self.assertEqual(self.a.snapshot()['steps'][0]['status'],'failed')
    def test_terminal_duration_frozen(self):
        self.clock.advance(4);self.emit('run_finished',status='completed');self.clock.advance(10)
        self.assertEqual(self.a.snapshot()['elapsed_ms'],4000)
        self.assertFalse(self.a.record('model_start',{}))
    def test_step_history_bounded(self):
        for _ in range(MAX_STEPS+10):self.emit('format_error',error='invalid')
        self.assertEqual(len(self.a.snapshot()['steps']),MAX_STEPS);self.assertEqual(self.a.snapshot()['omitted_steps'],10)
    def test_snapshot_is_a_copy(self):
        self.start_tool();s=self.a.snapshot();s['steps'][0]['tool']='evil'
        self.assertEqual(self.a.snapshot()['steps'][0]['tool'],'files.write')
    def test_display_masks_nested_secrets_without_changing_call(self):
        params={'path':'a.txt','inner':{'api_key':'SECRET'},'text':'Bearer secret123','url':'https://x/?token=SECRET'}
        saved=copy.deepcopy(params);view=display_value(params)
        self.assertEqual(params,saved);self.assertNotIn('SECRET',json.dumps(view));self.assertNotIn('secret123',json.dumps(view))
    def test_display_bounds_large_content(self):
        view=display_value({'content':'x'*100000,'many':[{'a':'y'*10000}]*1000})
        self.assertLess(len(json.dumps(view,ensure_ascii=False)),30000)
        self.assertIn('截断',str(view))
    def test_html_remains_data(self):self.assertEqual(display_value('<script>bad()</script>'),'<script>bad()</script>')
    def test_visible_answer_leaves_code_and_later_tags(self):
        raw='Example:\n```\n<think>literal</think>\n```';self.assertEqual(visible_answer(raw),(raw,False))
    def test_visible_answer_removes_leading_reasoning_only(self):
        self.assertEqual(visible_answer(' <think>PRIVATE</think>\n北京'),('北京',True))
    def test_unclosed_reasoning_not_presented_as_answer(self):self.assertEqual(visible_answer('<think>unfinished'),('',True))
    def test_multiple_think_blocks_removed(self):self.assertEqual(visible_answer('<think>a</think> <think>b</think>Answer'),('Answer',True))
    def test_restart_recovery_marks_incomplete_without_replay(self):
        self.start_tool();before=self.a.snapshot();after=interrupted(before)
        self.assertEqual(before['state'],'running');self.assertEqual(after['state'],'failed')
        self.assertEqual(after['steps'][0]['status'],'interrupted')
    def test_live_audit_keeps_raw_only_in_private_log(self):
        run=Run('a'*32,'b'*32,'c'*32,'chat')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'audit.jsonl';audit=LiveAudit(path,run)
            audit.add('model_output',step=1,text='<think>PRIVATE_REASONING</think>4')
            self.assertIn('PRIVATE_REASONING',path.read_text())
            self.assertNotIn('PRIVATE_REASONING',json.dumps(run.events))


class ActivityHTTPTests(unittest.TestCase):
    # Reuse the actual local HTTP server harness without duplicating its test cases.
    setUp=web_fixture.WebTests.setUp
    tearDown=web_fixture.WebTests.tearDown
    request=web_fixture.WebTests.request
    start=web_fixture.WebTests.start
    finish=web_fixture.WebTests.finish
    wait_approval=web_fixture.WebTests.wait_approval
    def message(self):return self.app.sessions.get(self.sid)['messages'][-1]
    def test_chat_summary_and_visible_answer_persisted(self):
        self.fixture.reply='<think>SECRET_INTERNAL_TEXT</think>Public answer'
        rid=self.start();status,events=self.finish(rid)
        self.assertEqual(status,'completed');m=self.message()
        self.assertEqual(m['content'],'Public answer');self.assertEqual(m['activity']['model_calls'],1)
        self.assertNotIn('SECRET_INTERNAL_TEXT',json.dumps(m))
        self.assertNotIn('SECRET_INTERNAL_TEXT',json.dumps(events))
        self.assertEqual(m['activity'],Sessions(self.app.sessions.root).get(self.sid)['messages'][-1]['activity'])
    def test_poll_snapshot_exists_while_model_is_running(self):
        self.fixture.delay=.2;rid=self.start();_,r,_=self.request(f'/api/runs/{rid}/events?after=0')
        self.assertIn('activity',r);self.assertEqual(r['activity']['source'],'runtime_events');self.finish(rid)
    def test_approved_file_tools_have_real_results(self):
        self.fixture=ScriptedBackend([{'tool':'files.write','arguments':{'path':'trace.txt','content':'42'}},
                                     {'tool':'files.read','arguments':{'path':'trace.txt'}},{'final':'Done'}])
        rid=self.start(mode='agent');a=self.wait_approval(rid)
        self.assertEqual(self.app.get_run(rid).activity.snapshot()['steps'][-1]['status'],'waiting')
        self.assertFalse((self.app.workspace/self.sid/'trace.txt').exists())
        self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':True})
        self.finish(rid);data=self.message()['activity']
        self.assertEqual(data['tool_counts']['completed'],2);self.assertEqual(data['model_calls'],3)
        self.assertEqual(self.app.ws(self.sid).read('trace.txt'),'42')
        calls=[x for x in data['steps'] if x['kind']=='tool']
        self.assertTrue(all(x['execution_ms']>=0 for x in calls));self.assertNotEqual(calls[0]['id'],calls[1]['id'])
    def test_deny_has_no_side_effect_and_records_denied(self):
        self.fixture=ScriptedBackend([{'tool':'files.write','arguments':{'path':'no.txt','content':'no'}},{'final':'Denied'}])
        rid=self.start(mode='agent');a=self.wait_approval(rid)
        self.request(f'/api/runs/{rid}/approve','POST',{'approval_id':a['id'],'allow':False});self.finish(rid)
        self.assertEqual(self.message()['activity']['tool_counts']['denied'],1)
        self.assertFalse((self.app.workspace/self.sid/'no.txt').exists())
    def test_cancel_approval_closes_trace(self):
        self.fixture=ScriptedBackend([{'tool':'files.write','arguments':{'path':'no.txt','content':'no'}}])
        rid=self.start(mode='agent');self.wait_approval(rid);self.app.cancel(rid);self.finish(rid)
        data=self.message()['activity'];self.assertEqual(data['state'],'cancelled')
        self.assertFalse(any(x['status'] in ('running','waiting','prepared') for x in data['steps']))
    def test_approval_expiry_visible_without_execution(self):
        self.fixture=ScriptedBackend([{'tool':'files.write','arguments':{'path':'expired.txt','content':'no'}},{'final':'Expired'}])
        with patch('local_agent.web.APPROVAL_TIMEOUT', .04):
            rid=self.start(mode='agent');self.finish(rid)
        data=self.message()['activity'];self.assertEqual(data['tool_counts']['expired'],1)
        self.assertFalse((self.app.workspace/self.sid/'expired.txt').exists())
    def test_same_cursor_replay_does_not_duplicate_projection(self):
        rid=self.start();self.finish(rid)
        a=self.request(f'/api/runs/{rid}/events?after=0')[1]
        b=self.request(f'/api/runs/{rid}/events?after=0')[1]
        self.assertEqual(a['activity'],b['activity']);self.assertEqual(a['cursor'],b['cursor'])
    def test_timeout_becomes_failed_not_completed(self):
        with patch.object(self.fixture,'complete',side_effect=TimeoutError('fixture timed out')):
            rid=self.start();self.finish(rid)
        self.assertEqual(self.message()['activity']['state'],'failed');self.assertIn('TimeoutError',self.message()['activity']['error'])
    def test_unfinished_think_is_failure_not_answer(self):
        self.fixture.reply='<think>UNFINISHED_PRIVATE'
        self.finish(self.start());m=self.message();self.assertEqual(m['state'],'failed');self.assertNotIn('UNFINISHED_PRIVATE',json.dumps(m))
    def test_retry_error_appears_without_raw_model_text(self):
        self.fixture=ScriptedBackend(['invalid',{'final':'Corrected'}]);self.finish(self.start(mode='agent'))
        data=self.message()['activity'];self.assertTrue(any(x['kind']=='correction' for x in data['steps']))
    def test_trace_endpoint_token_still_required(self):
        rid=self.start();self.finish(rid)
        self.assertEqual(self.request(f'/api/runs/{rid}/events',auth=False)[0],403)
    def test_new_assets_are_local_and_have_csp(self):
        for path in ('/activity.js','/activity.css'):
            code,body,headers=self.request(path,auth=False);self.assertEqual(code,200)
            self.assertIn("script-src 'self'",headers['Content-Security-Policy']);self.assertGreater(len(body),100)
    def test_active_checkpoint_written_for_recovery(self):
        self.fixture.delay=.2;rid=self.start();path=self.app.data_dir/'runs'/(rid+'.activity.json')
        self.assertTrue(path.is_file());self.finish(rid)
        self.assertEqual(json.loads(path.read_text())['state'],'completed')
    def test_restart_uses_checkpoint_no_tool_replay(self):
        rid='f'*32;s=self.app.sessions.get(self.sid)
        s['messages']=[{'role':'assistant','state':'running','content':'','run_id':rid}];s['active_run']=rid
        self.app.sessions.save(s)
        a=Activity('agent');a.record('tool_start',{'tool':'files.write','arguments':{'path':'no.txt'}})
        path=self.app.data_dir/'runs'/(rid+'.activity.json');path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(a.snapshot()))
        store=Sessions(self.app.sessions.root);msg=store.get(self.sid)['messages'][0]
        self.assertEqual(msg['activity']['steps'][0]['status'],'interrupted')
        self.assertFalse((self.app.workspace/self.sid/'no.txt').exists())
    def test_unrelated_new_session_has_no_trace(self):
        self.finish(self.start());sid=self.app.sessions.new()['id']
        self.assertEqual(self.request('/api/sessions/'+sid)[1]['messages'],[])
    def test_history_does_not_include_display_summary_or_reasoning(self):
        self.fixture.reply='<think>PRIVATE</think>4';self.finish(self.start());self.finish(self.start())
        history=self.fixture.calls[-1];self.assertEqual(history[2]['content'],'4')
        self.assertNotIn('runtime_events',str(history));self.assertNotIn('PRIVATE',str(history))

if __name__=='__main__':unittest.main()
