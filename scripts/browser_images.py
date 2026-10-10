#!/usr/bin/env python3
"""Actual browser + HTTP image route, labelled synthetic engine fixture.
Not a real-model benchmark; validates approvals, cards, downloads and reload.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from local_agent.web import WebApp,LocalServer
from local_agent.images import ImageRunner
from tests.test_web import TextFixture,test_config
from tests.test_image_tasks import ImageHttpTests


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--chromium');a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    from playwright.sync_api import sync_playwright,expect
    report={'scope':__doc__,'cases':[]};errors=[]
    def passed(name):report['cases'].append({'name':name,'passed':True});print('PASS',name,flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);cfg=test_config(root);(root/'image-model').mkdir()
        cfg['image']={'enabled':True,'command':[sys.executable],'model':str(root/'image-model'),'device':'cpu'}
        text=TextFixture();app=WebApp(cfg,root/'state',backend_factory=lambda _:text)
        server=LocalServer(app,0);thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start()
        try:
            with patch.object(ImageRunner,'run',autospec=True,side_effect=ImageHttpTests.engine_fixture) as engine, sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=a.chromium or None)
                page=browser.new_page(viewport={'width':1440,'height':960},color_scheme='dark')
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                expect(page.locator('#notice')).to_contain_text('测试后端')
                page.locator('#mode-image').click();expect(page.locator('#image-options')).to_be_visible()
                page.locator('#image-width').select_option('256');page.locator('#image-height').select_option('256')
                page.locator('#image-steps').fill('2');page.locator('#image-seed').fill('42')
                page.locator('#message-input').fill('TEST FIXTURE: a red square');page.locator('#send-button').click()
                expect(page.locator('.approval-card')).to_be_visible();engine.assert_not_called();assert not text.calls
                expect(page.locator('.approval-card')).to_contain_text('images.generate');page.screenshot(path=str(a.output/'image-approval.png'),full_page=True)
                passed('explicit image route does not use text model; awaits approval')
                page.get_by_role('button',name='允许这一次',exact=True).click()
                expect(page.locator('#stop-button')).to_be_hidden(timeout=10000)
                expect(page.locator('.generated-image img')).to_be_visible()
                assert page.locator('.generated-image img').evaluate('(n)=>n.naturalWidth')==256
                assert engine.call_count==1 and not text.calls
                page.screenshot(path=str(a.output/'image-card-fixture.png'),full_page=True)
                passed('successful real file produces authenticated image card')
                with page.expect_download() as event:page.get_by_role('button',name='下载图片',exact=True).click()
                assert event.value.suggested_filename.endswith('.png');passed('download generated artifact')
                page.reload();expect(page.locator('.generated-image img')).to_be_visible();passed('reload restores stored image card')
                page.locator('#mode-chat').click();page.locator('#message-input').fill('/image second fixture')
                page.locator('#send-button').click();expect(page.locator('.approval-card')).to_be_visible()
                page.get_by_role('button',name='拒绝',exact=True).click();expect(page.locator('#stop-button')).to_be_hidden(timeout=10000)
                assert engine.call_count==1;passed('literal image command routes but denial blocks execution')
                page.set_viewport_size({'width':390,'height':844});page.locator('#mode-image').click()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(a.output/'image-mobile-fixture.png'),full_page=True);passed('mobile image controls fit viewport')
                page.set_viewport_size({'width':1440,'height':960})
                turbo=root/'qwenimage21-turbo';turbo.mkdir()
                app.config['image']['model']=str(turbo)
                page.reload();page.locator('#mode-image').click()
                expect(page.locator('#image-steps')).to_have_value('8')
                expect(page.locator('#image-steps')).to_be_disabled()
                expect(page.locator('#image-mode-note')).to_contain_text('Turbo')
                passed('active Turbo selects eight steps and disables incompatible step edits')
                page.locator('#message-input').fill('TEST FIXTURE ONLY: Turbo illustration')
                page.locator('#send-button').click();expect(page.locator('.approval-card')).to_be_visible()
                expect(page.locator('.approval-card')).to_contain_text('"steps": 8')
                page.screenshot(path=str(a.output/'turbo-approval.png'),full_page=True)
                page.reload();expect(page.locator('.approval-card')).to_be_visible()
                expect(page.locator('#image-steps')).to_have_value('8')
                passed('Turbo approval and refresh retain eight steps without bypassing consent')
                page.get_by_role('button',name='允许这一次',exact=True).click()
                expect(page.locator('#stop-button')).to_be_hidden(timeout=10000)
                assert engine.call_count==2 and engine.call_args.kwargs['steps']==8
                assert not text.calls
                page.screenshot(path=str(a.output/'turbo-result-fixture.png'),full_page=True)
                passed('approved Turbo route invokes image tool with eight steps and no text model')
                app.config['image']['model']=str(root/'image-model')
                page.reload();page.locator('#mode-image').click()
                expect(page.locator('#image-steps')).to_have_value('40')
                expect(page.locator('#image-steps')).to_be_enabled()
                passed('switching to standard model restores editable forty-step default')
                # Exercise the user-facing natural-language route with no text
                # model at all; fixture pixels prove plumbing, not inference.
                app.factory=None
                page.reload();page.locator('#mode-chat').click()
                page.locator('#message-input').fill('帮我画一只猫')
                expect(page.locator('#send-button')).to_be_enabled()
                page.locator('#send-button').click()
                expect(page.locator('.approval-card')).to_be_visible()
                assert engine.call_count==2
                expect(page.locator('.approval-card')).to_contain_text('帮我画一只猫')
                page.reload();expect(page.locator('.approval-card')).to_be_visible()
                page.get_by_role('button',name='允许这一次',exact=True).click()
                expect(page.locator('#stop-button')).to_be_hidden(timeout=10000)
                assert engine.call_count==3 and not text.calls
                expect(page.locator('.generated-image img').last).to_be_visible()
                page.screenshot(path=str(a.output/'automatic-image-fixture.png'),full_page=True)
                passed('natural drawing request in chat works without text model; reload retains approval')
                page.locator('#message-input').fill('把背景改成蓝色')
                page.locator('#send-button').click();expect(page.locator('.approval-card')).to_be_visible()
                expect(page.locator('.approval-card')).to_contain_text('images/')
                page.get_by_role('button',name='拒绝',exact=True).click()
                expect(page.locator('#stop-button')).to_be_hidden(timeout=10000)
                assert engine.call_count==3
                passed('verified-image followup asks again and denial does not generate')
                assert not errors,errors;passed('no browser page errors')
                browser.close()
        except Exception as e:report['cases'].append({'name':'browser image route','passed':False,'error':f'{type(e).__name__}: {e}'})
        finally:
            for rid in list(app.runs):app.cancel(rid)
            end=time.monotonic()+5
            while app.active and time.monotonic()<end:time.sleep(.05)
            server.shutdown();server.server_close();thread.join(3)
    report['ok']=all(c['passed'] for c in report['cases']);report['page_errors']=errors
    (a.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
