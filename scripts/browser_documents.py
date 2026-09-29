#!/usr/bin/env python3
"""Real Chromium Office workbench/onboarding test without any model or fake replies."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_agent.web import WebApp,LocalServer
from local_agent.desktop import automatic_config
from local_agent.model_hub import ModelHub
from local_agent.documents import DocumentTools


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports/web-documents')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    from playwright.sync_api import sync_playwright,expect
    cases=[];errors=[];external=[]
    report={'scope':'Actual browser, HTTP, Office export/read. No model, no capability fixture, no weight downloads.'}
    def passed(name):
        cases.append({'name':name,'passed':True});print('PASS',name,flush=True)
    with tempfile.TemporaryDirectory(prefix='document-browser-') as tmp:
        root=Path(tmp)
        hub=ModelHub(root,{'llm':str(root/'missing-bridge'),'image':str(root/'missing-image')})
        app=WebApp(automatic_config(root,hub),root/'state');app.model_hub=hub
        server=LocalServer(app,0)
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        browser=None
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                context=browser.new_context(viewport={'width':1440,'height':960},color_scheme='dark',reduced_motion='reduce')
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:external.append(r.url) if not r.url.startswith(base) else None)
                page.goto(base);expect(page.locator('#connection-status')).to_have_text('模型未配置')
                expect(page.locator('#send-button')).to_be_disabled()
                passed('no model required for workbench; no invented response')
                page.locator('#documents-button').click();expect(page.locator('#document-dialog')).to_be_visible()
                page.locator('#doc-title').fill('文档回归验证')
                page.locator('#doc-editor').fill('# 检测报告\n\n| 项目 | 数值 |\n| --- | --- |\n| CHECK | 42 |')
                page.locator('#doc-preview-toggle').click();expect(page.locator('#doc-render')).to_contain_text('CHECK')
                page.locator('#doc-preview-toggle').click()
                page.screenshot(path=str(args.output/'document-workbench.png'),full_page=True)
                downloaded={}
                for fmt in ('docx','xlsx','pptx','md'):
                    page.locator('#doc-format').select_option(fmt)
                    with page.expect_download() as event: page.locator('#doc-export').click()
                    target=root/('export.'+fmt);event.value.save_as(str(target));downloaded[fmt]=target
                    if target.stat().st_size==0: raise AssertionError('empty export')
                    sid=app.sessions.list()[0]['id'];docs=DocumentTools(app.ws(sid))
                    relative=next(p.relative_to(app.workspace/sid).as_posix() for p in (app.workspace/sid/'exports').glob('*.'+fmt))
                    if 'CHECK' not in docs.read(relative)['content']: raise AssertionError('export round trip lost content')
                    passed('browser '+fmt+' export and actual content round trip')
                page.locator('#doc-editor').fill('<img src=x onerror="window.__doc_xss=1">')
                page.locator('#doc-preview-toggle').click();expect(page.locator('#doc-render img')).to_have_count(0)
                if page.evaluate('window.__doc_xss') is not None: raise AssertionError('document HTML executed')
                passed('document preview renders untrusted HTML as text')
                page.locator('#close-document').click()
                source=downloaded['xlsx'].read_bytes();original=hashlib.sha256(source).hexdigest()
                page.locator('#file-input').set_input_files({'name':'source.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':source})
                expect(page.locator('#attachments')).to_contain_text('source.xlsx')
                page.locator('#files-button').click()
                page.get_by_role('button',name='uploads',exact=True).click()
                page.locator('.file-name').filter(has_text='source.xlsx').click()
                expect(page.locator('#document-dialog')).to_be_visible();expect(page.locator('#doc-editor')).to_contain_text('')
                if 'CHECK' not in page.locator('#doc-editor').input_value(): raise AssertionError('Office file not extracted')
                uploaded=next((app.workspace/sid/'uploads').glob('*source.xlsx'))
                if hashlib.sha256(uploaded.read_bytes()).hexdigest()!=original: raise AssertionError('original file changed')
                passed('uploaded Excel opens as editable text; original bytes preserved')
                page.locator('#close-document').click();page.locator('#close-files').click()
                page.locator('#settings-button').click();expect(page.locator('#runtime-details')).to_contain_text('Vulkan 优先')
                expect(page.locator('#runtime-details')).to_contain_text('engine_missing')
                page.locator('#close-settings').click();passed('separate Vulkan-first preflight and fallback reason displayed')
                page.locator('#setup-button').click();expect(page.locator('#setup-dialog')).to_be_visible()
                expect(page.locator('.model-card')).to_have_count(2)
                if hub.status()['job']['status']!='idle' or list(hub.models.iterdir()): raise AssertionError('unconsented model download')
                page.screenshot(path=str(args.output/'model-center.png'),full_page=True);passed('model center does not download without consent')
                page.locator('#close-setup').click();page.set_viewport_size({'width':390,'height':844})
                if not page.evaluate('document.documentElement.scrollWidth <= innerWidth'): raise AssertionError('mobile overflow')
                page.locator('#documents-button').click();expect(page.locator('#document-dialog')).to_be_visible()
                page.screenshot(path=str(args.output/'mobile-documents.png'),full_page=True);passed('mobile document workbench remains accessible')
                if errors or external: raise AssertionError({'page_errors':errors,'external_requests':external})
                passed('no page errors or external requests')
                browser.close();browser=None
        except Exception as exc:
            cases.append({'name':'browser documents','passed':False,'error':f'{type(exc).__name__}: {exc}'})
        finally:
            hub.stop();server.shutdown();server.server_close();thread.join(3)
    report.update(tests=cases,passed=sum(c['passed'] for c in cases),failed=sum(not c['passed'] for c in cases),
                  page_errors=errors,external_requests=external)
    args.output.joinpath('result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if not report['failed'] else 1


if __name__=='__main__': raise SystemExit(main())
