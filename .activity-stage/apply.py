"""Temporary reviewed source assembly; excludes all staging files from main."""
from pathlib import Path
import hashlib,json
r=Path.cwd()
m=json.loads((r/'.activity-stage/manifest.json').read_text())
def blob(path):
 b=path.read_bytes();return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
for path,sha in m['before'].items():
 assert blob(r/path)==sha, 'Changed baseline: '+path
p=r/'local_agent/web.py';s=p.read_text()
def replace(old,new):
 global s
 assert old in s, old[:120]
 s=s.replace(old,new,1)
replace('from .tools import make_registry, Registry', 'from .tools import make_registry, Registry\nfrom .activity import Activity, visible_answer, interrupted')
replace("                    m.update(state='failed', content='服务已重启，任务已中断。已完成的文件操作不会回滚。')", """                    m.update(state='failed', content='服务已重启，任务已中断。已完成的文件操作不会回滚。')
                    rid = m.get('run_id', '')
                    checkpoint = self.root.parent / 'runs' / (rid + '.activity.json') if isinstance(rid, str) and ID.fullmatch(rid) else None
                    try:
                        activity = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint and checkpoint.is_file() else m.get('activity')
                        if isinstance(activity, dict) and activity.get('version') == 1:
                            m['activity'] = interrupted(activity)
                    except (ValueError, OSError):
                        pass  # A damaged checkpoint must not prevent session recovery.""")
replace('    image_plan: dict | None = None\n', """    image_plan: dict | None = None
    activity: object = None
    activity_path: Path | None = None

    def __post_init__(self):
        self.activity = Activity(self.mode)
""")
replace("            self.events.append({'seq': len(self.events) + 1, 'event': kind, 'time': time.time(), **data})", """            self.events.append({'seq': len(self.events) + 1, 'event': kind, 'time': time.time(), **data})
            if self.activity.record(kind, data) and self.activity_path:
                atomic_json(self.activity_path, self.activity.snapshot())""")
replace("        if kind in ('model_start', 'tool_start', 'tool_result', 'format_error'):", """        if kind == 'model_output':
            raw = data.get('text', '')
            _, detected = visible_answer(raw)
            self.run.emit('model_done', step=data.get('step'), output_chars=len(raw), reasoning_detected=detected)
        if kind in ('model_start', 'tool_start', 'tool_result', 'format_error'):""")
replace("            self.run.emit('approval_resolved', approval_id=approval['id'], allowed=allowed)", """            reason = 'cancelled' if self.run.stop.is_set() else ('denied' if approval['decision'] is False else 'expired')
            self.run.emit('approval_resolved', approval_id=approval['id'], allowed=allowed, reason='allowed' if allowed else reason)""")
replace("        return self.registry.call(name, arguments)\n\n\nclass WebApp", "        self.run.emit('tool_execute', tool=name)\n        return self.registry.call(name, arguments)\n\n\nclass WebApp")
replace("            run = Run(rid, sid, mid, mode, image_plan=image_plan)", """            run = Run(rid, sid, mid, mode, image_plan=image_plan,
                      activity_path=self.data_dir / 'runs' / (rid + '.activity.json'))
            run.emit('run_started', model=runtime['image_model'] if mode == 'image' else runtime['model'],
                     history_turns=0 if mode == 'image' else len(history) // 2, attachments=len(attachments))""")
replace("                content = backend.complete(messages + history + [{'role': 'user', 'content': task}])\n                if not isinstance(content, str) or not content.strip():", """                raw = backend.complete(messages + history + [{'role': 'user', 'content': task}])
                if not isinstance(raw, str):
                    raise WebError('模型返回了非文本内容。')
                audit.add('model_output', step=1, text=raw)
                content, _ = visible_answer(raw)
                if not content.strip():""")
replace("            with self.lock:\n                s = self.sessions.get(run.session_id)", """            run.emit('run_finished', status=state, error=content if state != 'completed' else None)
            with self.lock:
                s = self.sessions.get(run.session_id)""")
replace("                    trace=copy.deepcopy(run.events), elapsed_seconds=", "                    activity=run.activity.snapshot(), trace=copy.deepcopy(run.events), elapsed_seconds=")
replace("                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),", "                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),\n                      '/activity.js': ('activity.js', 'text/javascript; charset=utf-8'),\n                      '/activity.css': ('activity.css', 'text/css; charset=utf-8'),")
replace("                        value = {'events': copy.deepcopy(run.events[cursor:]), 'cursor': len(run.events), 'status': run.status}", """                        value = {'events': copy.deepcopy(run.events[cursor:]), 'cursor': len(run.events),
                                 'status': run.status, 'activity': run.activity.snapshot()}""")
p.write_text(s)

p=r/'local_agent/webui/app.js';s=p.read_text()
def replace(a,b):
 global s
 assert a in s,a[:70];s=s.replace(a,b,1)
replace("run:null,runSession:null,traces:[],approval:null", "run:null,runSession:null,traces:[],activity:null,approval:null")
replace("function renderMessages(){const scroll=", "function renderMessages(){const activityFocus=window.AgentActivity?.remember($('messages'));const scroll=")
replace(".map(x=>x.dataset.step);const list=$('messages')", ".map(x=>x.dataset.step).filter(Boolean);const list=$('messages')")
replace("      if(traces.some(e=>e.event==='tool_start'||e.event==='format_error'))article.append(traceView(traces));", """      const activity=running?(state.activity||m.activity):m.activity;
      if(activity&&window.AgentActivity)article.append(window.AgentActivity.render(activity,m.id));
      else if(traces.some(e=>e.event==='tool_start'||e.event==='format_error'))article.append(traceView(traces));""")
replace("state.stopping?'正在停止，等待当前操作结束…':m.mode==='image'?", "state.stopping?'正在停止，等待当前操作结束…':activity?.stage?activity.stage:m.mode==='image'?")
replace("  if(near||!messages.length)requestAnimationFrame", "  window.AgentActivity?.restore(list,activityFocus);\n  if(near||!messages.length)requestAnimationFrame")
replace("state.runSession=sid;state.traces=[];state.approval=null;", "state.runSession=sid;state.traces=[];state.activity=null;state.approval=null;")
replace("cursor=result.cursor;for(const e of result.events)", "cursor=result.cursor;if(result.activity)state.activity=result.activity;for(const e of result.events)")
replace("state.approval=null;state.traces=[];state.stopping=false;", "state.approval=null;state.traces=[];state.activity=null;state.stopping=false;")
p.write_text(s)
p=r/'local_agent/webui/index.html';s=p.read_text();s=s.replace('  <script src="/app.js" defer></script>', '  <link rel="stylesheet" href="/activity.css">\n  <script src="/activity.js" defer></script>\n  <script src="/app.js" defer></script>');p.write_text(s)

p=r/'local_agent/web.py';s=p.read_text()
s=s.replace("TERMINAL = {'completed', 'failed', 'cancelled'}", "TERMINAL = {'completed', 'failed', 'cancelled'}\nAPPROVAL_TIMEOUT = 300")
s=s.replace("except (ValueError, OSError):\n                        pass  # A damaged checkpoint", "except (ValueError, OSError, KeyError, TypeError, AttributeError):\n                        pass  # A damaged checkpoint")
s=s.replace('deadline = time.monotonic() + 300','deadline = time.monotonic() + APPROVAL_TIMEOUT');p.write_text(s)
p=r/'.github/workflows/web-ui.yml';s=p.read_text()
s=s.replace('node --check local_agent/webui/app.js','node --check local_agent/webui/app.js\n          node --check local_agent/webui/activity.js')
s=s.replace('      - name: Archive exact source for verification', '      - name: Execution summaries and tool traces (real browser, labelled model fixtures)\n        run: python scripts/browser_activity.py --output reports/browser-activity\n      - name: Archive exact source for verification')
s=s.replace('            reports/browser-images/','            reports/browser-images/\n            reports/browser-activity/');p.write_text(s)
for path,sha in m['after'].items():
 assert blob(r/path)==sha, 'Unexpected source content: '+path
print('SOURCE_HASHES_VERIFIED', json.dumps(m['after']))
