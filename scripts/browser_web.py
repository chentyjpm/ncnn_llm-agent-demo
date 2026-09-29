#!/usr/bin/env python3
"""Chromium desktop/mobile UI acceptance; model replies are explicit fixtures.

Use --chromium /usr/bin/chromium for an installed browser; in CI run
python -m playwright install --with-deps chromium first. No real weights here.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_agent.web import WebApp,LocalServer


class BrowserFixture:
    label='BROWSER_TEST_FIXTURE_NOT_A_MODEL'
    def __init__(self,record):
        self.turn=0;self.record=record
    def complete(self,messages):
        self.record.append(messages)
        if 'local workflow agent' in messages[0]['content']:
            actions=[{'tool':'files.write','arguments':{'path':'web-demo.txt','content':'WEB_UI_VERIFIED'}},
                     {'tool':'files.read','arguments':{'path':'web-demo.txt'}},{'final':'文件已创建并读取：**WEB_UI_VERIFIED**。'}]
            value=actions[min(self.turn,len(actions)-1)];self.turn+=1
            return json.dumps(value,ensure_ascii=False)
        if messages[-1]['content']=='slow':
            time.sleep(1.5)
        return '这是明确标记的 **UI 测试回复**，不是模型推理。\n\n```python\nprint(42)\n```\n\n<img src=x onerror="window.__injected=true">'
    def release(self):
        pass


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=ROOT/'reports/web-browser')
    ap.add_argument('--chromium')
    a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    from playwright.sync_api import sync_playwright,expect
    cases=[];calls=[];errors=[];external=[]
    started=time.monotonic()
    def passed(name):
        cases.append({'name':name,'status':'passed'});print('PASS',name,flush=True)
    report={'scope':'Actual Chromium + local HTTP/filesystem; model output is a test fixture, not inference. Mobile viewport emulation, not a physical phone.'}
    with tempfile.TemporaryDirectory(prefix='web-browser-') as tmp:
        root=Path(tmp);config={'workspace':str(root/'workspace'),'llm':{'backend':'ncnn_bridge','command':['missing'],'model':str(root/'model'),'vulkan':False},'image':{'enabled':False},'python':{'mode':'disabled'},'mcp_servers':[],'commands':{}}
        app=WebApp(config,root/'data',backend_factory=lambda _:BrowserFixture(calls))
        server=LocalServer(app,0);thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=a.chromium or None,args=['--no-sandbox'])
                report['browser']=browser.version
                ctx=browser.new_context(viewport={'width':1440,'height':960},color_scheme='dark',reduced_motion='reduce')
                page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:external.append(r.url) if not r.url.startswith(base) else None)
                # Delay the real session response to expose final-refresh/next-send races.
                # The HTTP response bytes are unchanged; this is network timing only.
                def delayed_session(route):
                    response=route.fetch();time.sleep(.2);route.fulfill(response=response)
                import re
                page.route(re.compile(re.escape(base)+r'/api/sessions/[0-9a-f]{32}$'), delayed_session)
                page.goto(base);expect(page.locator('#connection-status')).to_have_text('本机服务已连接')
                expect(page.locator('h1')).to_have_text('把想法，变成本地工作流。')
                page.screenshot(path=str(a.output/'desktop-dark.png'),full_page=True);passed('desktop welcome + runtime identity')
                page.locator('#theme-button').click();expect(page.locator('html')).to_have_attribute('data-theme','light')
                page.screenshot(path=str(a.output/'desktop-light.png'),full_page=True)
                page.locator('#theme-button').click();passed('dark/light theme')
                page.locator('#settings-button').click();expect(page.locator('#settings-dialog')).to_be_visible();expect(page.locator('#runtime-details')).to_contain_text('UI 测试后端')
                page.locator('#token-limit').select_option('128');page.locator('#close-settings').click();passed('runtime settings + token limit')
                page.locator('.suggestion').first.click();expect(page.locator('#message-input')).to_have_value('帮我梳理一份技术方案的结构，先给出提纲和需要补充的信息。')
                passed('suggestion prefills without execution')
                page.locator('#message-input').fill('你好，测试本地聊天');page.locator('#send-button').click()
                expect(page.locator('#messages .assistant .code-block')).to_have_count(1,timeout=10000)
                expect(page.locator('#stop-button')).to_be_hidden(timeout=10000);passed('HTTP chat + safe Markdown code block')
                assert page.evaluate('window.__injected') is None
                expect(page.locator('#messages img')).to_have_count(0);passed('model HTML cannot execute or load remote images')
                page.locator('#message-input').fill('继续刚才的问题');page.locator('#message-input').press('Enter')
                expect(page.locator('#messages .assistant .code-block')).to_have_count(2);expect(page.locator('#stop-button')).to_be_hidden(timeout=10000)
                assert any(len(m)==4 for m in calls);passed('multi-turn history reaches backend')
                page.locator('#file-input').set_input_files({'name':'measurements.csv','mimeType':'text/csv','buffer':b'distance\n232\n234\n236'})
                expect(page.locator('#attachments')).to_contain_text('measurements.csv')
                page.locator('#message-input').fill('分析附件');page.locator('#send-button').click();expect(page.locator('#messages .assistant .code-block')).to_have_count(3);expect(page.locator('#stop-button')).to_be_hidden(timeout=10000)
                assert any('UNTRUSTED FILE DATA' in m[-1]['content'] for m in calls);passed('upload + text attachment context')
                page.locator('#mode-agent').click();expect(page.locator('#mode-agent')).to_have_attribute('aria-pressed','true');page.locator('#message-input').fill('创建并读取 web-demo.txt');page.locator('#send-button').click()
                expect(page.locator('.approval-card')).to_be_visible(timeout=10000)
                sid=app.sessions.list()[0]['id'];assert not (app.workspace/sid/'web-demo.txt').exists()
                page.screenshot(path=str(a.output/'tool-approval.png'),full_page=True);passed('write pauses for human approval')
                page.get_by_role('button',name='允许这一次',exact=True).click()
                expect(page.locator('#stop-button')).to_be_hidden(timeout=10000)
                expect(page.locator('#messages .assistant').last).to_contain_text('WEB_UI_VERIFIED')
                assert (app.workspace/sid/'web-demo.txt').read_text()=='WEB_UI_VERIFIED'
                expect(page.locator('.tool-status')).to_have_count(2);passed('approved real write/read + live tool trace')
                page.locator('#files-button').click();expect(page.locator('#file-list')).to_contain_text('web-demo.txt')
                page.get_by_role('button',name='web-demo.txt',exact=True).click();expect(page.locator('#file-preview')).to_have_text('WEB_UI_VERIFIED')
                page.screenshot(path=str(a.output/'desktop-chat-files.png'),full_page=True)
                with page.expect_download() as download:page.get_by_role('button',name='下载 web-demo.txt').click()
                assert download.value.suggested_filename=='web-demo.txt';passed('workspace text preview + download')
                page.locator('#close-files').click()
                page.get_by_role('button',name='重命名对话').first.click();page.locator('#rename-input').fill('H5 联调记录');page.get_by_role('button',name='保存',exact=True).click()
                expect(page.locator('.session-open')).to_contain_text('H5 联调记录');passed('rename conversation')
                with page.expect_download() as download:page.locator('#export-chat').click()
                assert download.value.suggested_filename.endswith('.md');passed('export Markdown transcript')
                page.reload();expect(page.locator('#messages .assistant')).to_have_count(4);passed('reload restores conversation and tool traces')
                page.locator('#new-chat').click();expect(page.locator('#welcome')).to_be_visible()
                page.locator('#mode-chat').click();page.locator('#message-input').fill('slow');page.locator('#send-button').click()
                expect(page.locator('#stop-button')).to_be_visible();page.locator('#stop-button').click()
                expect(page.locator('#messages')).to_contain_text('已停止',timeout=10000);expect(page.locator('#stop-button')).to_be_hidden();passed('stop + cancelled message persisted')
                page.locator('#new-chat').click();page.locator('#session-search').fill('H5 联调');expect(page.locator('.session-row')).to_have_count(1);page.locator('#session-search').fill('');passed('new conversation + history search')
                # Delete the cancelled conversation, not the proof workspace.
                page.locator('.session-row').first.hover();page.get_by_role('button',name='删除对话').first.click();page.locator('#confirm-delete').click();expect(page.locator('.session-row')).to_have_count(1);passed('confirmed history deletion')
                page.set_viewport_size({'width':390,'height':844});expect(page.locator('#welcome')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(a.output/'mobile-dark.png'),full_page=True)
                page.locator('#toggle-sidebar').click();expect(page.locator('#mobile-scrim')).to_be_visible();page.locator('.session-open').first.click()
                expect(page.locator('#mobile-scrim')).to_be_hidden();expect(page.locator('#messages .assistant')).to_have_count(4)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(a.output/'mobile-chat.png'),full_page=True);passed('390px mobile layout + sidebar navigation')
                assert not errors,errors;assert not external,external;passed('no page errors or external network requests')
                browser.close()
        except Exception as exc:
            cases.append({'name':'browser execution','status':'failed','error':f'{type(exc).__name__}: {exc}'})
            print(cases[-1],file=sys.stderr)
            report['failure_sessions']=[app.sessions.get(s['id']) for s in app.sessions.list()]
            report['failure_runs']=[{'id':r.id,'status':r.status,'events':r.events} for r in app.runs.values()]
            report['fixture_inputs']=calls
        finally:
            for rid in list(app.runs):app.cancel(rid)
            deadline=time.monotonic()+3
            while app.active and time.monotonic()<deadline:time.sleep(.01)
            server.shutdown();server.server_close();thread.join(2)
    report.update(tests_run=len(cases),passed=sum(x['status']=='passed' for x in cases),tests=cases,
                  page_errors=errors,external_requests=external,elapsed_seconds=round(time.monotonic()-started,3))
    (a.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if all(x['status']=='passed' for x in cases) else 1


if __name__=='__main__':raise SystemExit(main())
