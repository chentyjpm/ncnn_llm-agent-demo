#!/usr/bin/env python3
"""Actual Chromium + HTTP/files/consent. All model output is labelled fixture data."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_agent.web import WebApp, LocalServer

PRIVATE='INTERNAL_REASONING_FIXTURE_MUST_NOT_REACH_BROWSER'
CHAT_RELEASE=threading.Event()
PAYLOAD='<img src="https://invalid.example/track" onerror="window.injected=true">'

class Fixture:
    label='ACTIVITY_BROWSER_FIXTURE_NOT_A_MODEL'
    def __init__(self):self.turn=0
    def complete(self,messages):
        task=next(m['content'] for m in reversed(messages) if m['role']=='user' and m['content'].startswith('ACTIVITY_'))
        time.sleep(.3)
        if 'ACTIVITY_TIMEOUT' in task:raise TimeoutError('Intentional fixture timeout')
        if 'local workflow agent' in messages[0]['content']:
            path='denied.txt' if 'DENY' in task else 'cancelled.txt' if 'CANCEL' in task else 'trace-proof.txt'
            actions=([{'tool':'files.read','arguments':{'path':'missing.txt'}},{'final':'已记录读取失败。'}] if 'FAIL' in task else [
                {'tool':'files.write','arguments':{'path':path,'content':PAYLOAD}},
                {'tool':'files.read','arguments':{'path':path}}, {'final':'文件操作结果已返回。'}])
            if ('DENY' in task or 'CANCEL' in task) and self.turn:result={'final':'本次不再调用工具。'}
            else:result=actions[min(self.turn,len(actions)-1)]
            self.turn+=1
            return '<think>'+PRIVATE+'</think>\n'+json.dumps(result,ensure_ascii=False)
        if task == 'ACTIVITY_CHAT': CHAT_RELEASE.wait(20)
        return '<think>'+PRIVATE+'</think>这是 **公开答案**。'
    def release(self):pass


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,default=ROOT/'reports/browser-activity');ap.add_argument('--chromium');a=ap.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    from playwright.sync_api import sync_playwright,expect
    cases=[];errors=[];external=[];started=time.monotonic()
    report={'scope':'Real Chromium and HTTP/file operations. Every model response is a labelled fixture; no inference or OS-installation claim.'}
    def passed(name):cases.append({'name':name,'status':'passed'});print('PASS',name,flush=True)
    with tempfile.TemporaryDirectory(prefix='activity-browser-') as tmp:
        root=Path(tmp);cfg={'workspace':str(root/'workspace'),'llm':{'backend':'ncnn_bridge','command':['missing'],'model':str(root/'model'),'vulkan':False},'image':{'enabled':False},'python':{'mode':'disabled'},'commands':{},'mcp_servers':[]}
        app=WebApp(cfg,root/'state',backend_factory=lambda _:Fixture());server=LocalServer(app,0)
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=a.chromium or None,args=['--no-sandbox'])
                report['browser']=browser.version
                page=browser.new_page(viewport={'width':1440,'height':1040},reduced_motion='reduce')
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:external.append(r.url) if not r.url.startswith(base) else None)
                page.goto(base);expect(page.locator('#connection-status')).to_have_text('本机服务已连接')
                def send(task,mode='chat'):
                    page.locator('#mode-'+mode).click();page.locator('#message-input').fill(task);page.locator('#send-button').click();expect(page.locator('#stop-button')).to_be_visible(timeout=10000)
                def done():expect(page.locator('#stop-button')).to_be_hidden(timeout=15000)
                def latest():return page.locator('.message.assistant').last
                send('ACTIVITY_CHAT')
                expect(latest().locator('.activity-live')).to_contain_text('模型正在生成回复',timeout=10000)
                latest().locator('.activity-overview > summary').click()
                expect(latest().locator('.activity-overview')).to_have_attribute('open','')
                CHAT_RELEASE.set()
                done();expect(latest().locator('.message-content')).to_contain_text('公开答案')
                expect(latest().locator('.activity-overview')).to_have_attribute('open','')
                assert PRIVATE not in page.locator('body').inner_text()
                expect(latest().locator('.activity-body').first).to_contain_text('不是模型内部思维链')
                passed('live summary, safe answer split and open state survives updates')
                page.reload();expect(page.locator('.activity-panel')).to_have_count(1)
                latest().locator('.activity-overview > summary').click()
                expect(latest().locator('.activity-overview')).to_contain_text('第 1 轮')
                passed('completed summary restored from persisted session')
                with page.expect_download() as d:latest().locator('.activity-export').click()
                data=json.loads(Path(d.value.path()).read_text())
                assert data['source']=='runtime_events' and data['tool_calls']==0 and PRIVATE not in json.dumps(data)
                passed('export actual bounded summary and trace JSON')
                send('ACTIVITY_WRITE','agent')
                expect(page.locator('.approval-card')).to_be_visible(timeout=10000)
                call=latest().locator('.activity-call').first;expect(call.locator('.tool-status')).to_have_text('等待确认')
                call.locator('summary').click();expect(call).to_contain_text('尚未执行工具')
                sid=app.sessions.list()[0]['id'];assert not (app.workspace/sid/'trace-proof.txt').exists()
                page.screenshot(path=str(a.output/'activity-approval.png'),full_page=True)
                passed('approval is waiting, not execution; no file written before consent')
                page.reload();expect(page.locator('.approval-card')).to_be_visible(timeout=10000)
                expect(latest().locator('.activity-call')).to_have_count(1)
                passed('reload resumes same pending approval without duplicate trace')
                page.get_by_role('button',name='允许这一次',exact=True).click();done()
                expect(latest().locator('.activity-call')).to_have_count(2)
                expect(latest().locator('.tool-status').first).to_have_text('已完成')
                assert (app.workspace/sid/'trace-proof.txt').read_text()==PAYLOAD
                latest().locator('.activity-overview > summary').click()
                latest().locator('.activity-call').last.locator('summary').click()
                expect(latest().locator('.activity-call').last).to_contain_text('工具执行')
                assert not page.locator('.activity-panel img').count();assert page.evaluate('window.injected') is None
                page.screenshot(path=str(a.output/'activity-completed-dark.png'),full_page=True)
                passed('real write/read, execution durations, trace result and HTML isolation')
                page.locator('#theme-button').click();page.screenshot(path=str(a.output/'activity-completed-light.png'),full_page=True)
                passed('light and dark themes')
                send('ACTIVITY_DENY','agent');expect(page.locator('.approval-card')).to_be_visible(timeout=10000)
                page.get_by_role('button',name='拒绝',exact=True).click();done()
                expect(latest().locator('.tool-status')).to_have_text('已拒绝');assert not (app.workspace/sid/'denied.txt').exists()
                passed('denial preserved with no side effect')
                send('ACTIVITY_FAIL','agent');done();expect(latest().locator('.tool-status')).to_have_text('失败')
                passed('tool failure stays visible even when final answer completes')
                send('ACTIVITY_CANCEL','agent');expect(page.locator('.approval-card')).to_be_visible(timeout=10000)
                page.locator('#stop-button').click();done();expect(latest().locator('.tool-status')).to_have_text('已停止')
                assert not (app.workspace/sid/'cancelled.txt').exists()
                passed('cancel closes pending trace, does not replay')
                send('ACTIVITY_TIMEOUT');done();expect(latest().locator('.activity-live')).to_contain_text('本轮失败')
                passed('model exception closes active summary')
                page.set_viewport_size({'width':390,'height':844})
                latest().locator('.activity-overview > summary').click()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(a.output/'activity-mobile.png'),full_page=True)
                passed('390px viewport fits without horizontal overflow')
                # Keyboard operation remains native details semantics.
                summary=latest().locator('.activity-overview > summary');summary.focus();page.keyboard.press('Enter')
                expect(latest().locator('.activity-overview')).not_to_have_attribute('open','')
                passed('keyboard expands/collapses summary')
                assert not errors,errors;assert not external,external;passed('no page errors or external requests')
                browser.close()
        except Exception as e:
            cases.append({'name':'browser execution','status':'failed','error':f'{type(e).__name__}: {e}'});print(cases[-1],file=sys.stderr)
        finally:
            CHAT_RELEASE.set()
            for rid in list(app.runs):app.cancel(rid)
            deadline=time.monotonic()+3
            while app.active and time.monotonic()<deadline:time.sleep(.01)
            server.shutdown();server.server_close();thread.join(2)
    report.update(cases=cases,tests_run=len(cases),passed=sum(x['status']=='passed' for x in cases),page_errors=errors,external_requests=external,elapsed_seconds=round(time.monotonic()-started,3))
    (a.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if cases and all(x['status']=='passed' for x in cases) else 1

if __name__=='__main__':raise SystemExit(main())
