#!/usr/bin/env python3
"""Real Chromium+HTTP progress UI test; small controlled transport, NOT a model.
No external requests. Never claims fixture bytes are real model inference.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from local_agent.web import LocalServer,WebApp
from local_agent.desktop import automatic_config
from local_agent.model_hub import ModelHub
from local_agent.model_catalog import CATALOG
from local_agent.native_catalog import NATIVE_MODELS


class DisplayFixture(ModelHub):
    """Controls ONLY network-job states for visible UI assertions; no weights."""
    def __init__(self,*a,**k):
        super().__init__(*a,**k);self.calls=[];self.query_error=False
    def prepare(self,model_id,provider='huggingface',*,download_route_id='direct'):
        self.calls.append((model_id,provider));self.route=download_route_id;time.sleep(.6)
        if self.query_error:raise OSError('TEST_NETWORK_FAILURE')
        return {'id':model_id,'provider':provider,'download_route':download_route_id,'third_party_mirror':download_route_id=='hf_mirror', 'metadata_final_host':'hf-mirror.com' if download_route_id=='hf_mirror' else 'huggingface.co','ticket':'UI_FIXTURE_ONLY', 'repository':'Qwen/Fixture', 'revision':'0'*40,
                'download_bytes':1024**3,'disk_required_bytes':4*1024**3,'disk_free_bytes':8*1024**3,
                'installed_estimate_bytes':2*1024**3,'enough_space':True}
    def start(self,ticket,consent):
        if ticket!='UI_FIXTURE_ONLY' or consent is not True:raise ValueError('No consent')
        self.job={'status':'downloading','model':'qwen05','provider':self.calls[-1][1],'download_route':self.route,'last_download_host':'cdn-lfs.hf.co', 'downloaded':256*1024**2,
                  'total':1024**3,'file':'fixture.bin','speed_bps':2*1024**2,'started_at':time.time(),
                  'last_data_at':time.time(),'message':'UI fixture, no model downloaded'}
        return copy.deepcopy(self.job)
    def stop(self):
        if self.job['status']!='idle':self.job['status']='cancelled'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'reports/model-center-browser');p.add_argument('--chromium');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    from playwright.sync_api import sync_playwright,expect
    result={'scope':__doc__,'cases':[]};errors=[];requests=[]
    def passed(name):result['cases'].append({'name':name,'passed':True});print('PASS',name,flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);hub=DisplayFixture(root,{'llm':str(root/'missing'),'image':str(root/'missing-image')})
        app=WebApp(automatic_config(root,hub),root/'state');app.model_hub=hub;server=LocalServer(app,0)
        thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.02),daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=a.chromium or None)
                page=browser.new_page(viewport={'width':1200,'height':900},color_scheme='light');page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url) if not r.url.startswith(base) else None)
                page.goto(base);page.locator('#setup-button').click();expect(page.locator('.model-card')).to_have_count(len(CATALOG))
                expect(page.locator('#model-source')).to_have_value('modelscope');assert not hub.calls
                passed('three explicit sources, curated native catalog, no automatic download')
                expect(page.locator('[data-model-id="qwenimage21"] button')).to_be_disabled()
                expect(page.locator('[data-model-id="qwenimage21"]')).to_contain_text('尚未核实')
                passed('unsupported image source explicitly disabled, no fallback')
                page.locator('#model-source').select_option('huggingface');expect(page.locator('[data-model-id="qwenimage21"] button')).to_be_enabled()
                turbo_card=page.locator('[data-model-id="qwenimage21-turbo"]')
                expect(turbo_card).to_contain_text('固定 8 步')
                expect(turbo_card.get_by_role('button')).to_have_text('先准备基础模型')
                turbo_card.get_by_role('button').click()
                expect(page.get_by_role('button',name='确认下载并安装')).to_be_visible()
                assert hub.calls[-1]==('qwenimage21','huggingface')
                passed('Turbo dependency guides user to base download instead of copying or guessing files')
                page.screenshot(path=str(a.output/'turbo-base-dependency.png'),full_page=True)
                page.locator('#model-source').select_option('modelscope')
                page.locator('#model-filter').select_option('native');expect(page.locator('.model-card')).to_have_count(len(NATIVE_MODELS))
                page.locator('#model-source').select_option('sdu')
                first=next(iter(NATIVE_MODELS));expect(page.locator(f'[data-model-id="{first}"] button')).to_be_enabled()
                page.locator('#model-search').fill('Youtu');expect(page.locator('.model-card')).to_have_count(2)
                page.screenshot(path=str(a.output/'native-catalog.png'),full_page=True)
                passed('native source, family search and installed-model selectors')
                page.locator('#model-search').fill('');page.locator('#model-filter').select_option('pending')
                for b in page.locator('.model-card button').all():expect(b).to_be_disabled()
                passed('specialized non-chat models cannot be activated')
                page.locator('#model-filter').select_option('all');page.locator('#model-source').select_option('modelscope')
                page.locator('[data-model-id="qwen05"] button').click();expect(page.locator('#model-progress')).to_contain_text('正在连接 ModelScope')
                assert page.locator('#model-progress-bar').get_attribute('value') is None
                expect(page.get_by_role('button',name='确认下载并安装')).to_be_visible();assert hub.calls[-1]==('qwen05','modelscope');assert hub.job['status']=='idle'
                expect(page.locator('.download-confirmation')).to_contain_text('需要下载');expect(page.locator('.download-confirmation')).to_contain_text('1.00 GiB')
                page.screenshot(path=str(a.output/'source-and-confirmation.png'),full_page=True);passed('query visibly indeterminate, source bound, size shown before consent')
                page.locator('#model-source').select_option('huggingface');expect(page.locator('.download-confirmation')).to_have_count(0);assert hub.job['status']=='idle'
                passed('changing source invalidates old confirmation without starting')
                hub.query_error=True;page.locator('[data-model-id="qwen05"] button').click();expect(page.locator('#model-error')).to_contain_text('TEST_NETWORK_FAILURE')
                expect(page.locator('[data-model-id="qwen05"] button')).to_be_enabled();page.screenshot(path=str(a.output/'query-error.png'),full_page=True);passed('network failure stays visible, retry button restored')
                hub.query_error=False;page.locator('[data-model-id="qwen05"] button').click();page.get_by_role('button',name='确认下载并安装').click()
                expect(page.locator('#model-progress-metrics')).to_contain_text('25.0%');expect(page.locator('#model-progress-metrics')).to_contain_text('256.00 MiB')
                expect(page.locator('#model-source')).to_be_disabled();page.screenshot(path=str(a.output/'download-progress.png'),full_page=True);passed('real server job bytes map to percent/sizes/speed, source locked')
                page.locator('#close-setup').click();page.locator('#setup-button').click();expect(page.locator('#model-progress-metrics')).to_contain_text('25.0%');passed('reopen restores ongoing job')
                hub.job.update(status='verifying',downloaded=1024**3,total_files=5,verified_files=4)
                expect(page.locator('#model-progress')).to_contain_text('校验',timeout=6000);assert page.locator('#model-progress-bar').get_attribute('value') is None
                expect(page.locator('#model-progress-extra')).to_contain_text('100% 不代表安装完成');passed('download complete is not installed; hash stage indeterminate')
                hub.job.update(status='converting',stage_done=12,stage_total=28)
                expect(page.locator('#model-progress-metrics')).to_contain_text('12 / 28',timeout=6000);passed('conversion steps separated from download bytes')
                page.locator('#cancel-model').click();expect(page.locator('#model-progress')).to_have_text('安装已取消');expect(page.locator('#model-source')).to_be_enabled();passed('cancelled is not completed and permits source change')
                page.reload();page.locator('#setup-button').click();expect(page.locator('#model-source')).to_have_value('huggingface');expect(page.locator('#model-progress')).to_have_text('安装已取消');passed('reload retains source choice and server job state')
                page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(a.output/'mobile-models.png'),full_page=True);passed('mobile dialog has no horizontal overflow')
                page.set_viewport_size({'width':1200,'height':900})
                page.locator('#model-filter').select_option('image')
                expect(page.locator('#hf-download-route')).to_have_value('direct')
                page.locator('#hf-download-route').select_option('hf_mirror')
                expect(page.locator('#hf-route-note')).to_contain_text('第三方')
                passed('mirror is explicit optional HF route with third-party disclosure')
                page.locator('[data-model-id="qwenimage21"] button').click()
                expect(page.locator('#model-progress')).to_contain_text('HF-Mirror')
                expect(page.locator('.download-confirmation')).to_contain_text('HF-Mirror')
                expect(page.locator('.download-confirmation')).to_contain_text('清单响应主机')
                assert hub.route=='hf_mirror'
                page.screenshot(path=str(a.output/'hf-mirror-confirmation.png'),full_page=True)
                passed('image mirror metadata query reaches backend and quote identifies route/host')
                page.locator('#hf-download-route').select_option('direct')
                expect(page.locator('.download-confirmation')).to_have_count(0)
                passed('route change invalidates old download confirmation')
                page.locator('[data-model-id="qwenimage21"] button').click()
                page.locator('#hf-download-route').select_option('hf_mirror')
                page.wait_for_timeout(900)
                expect(page.locator('.download-confirmation')).to_have_count(0)
                passed('cancelled slow query cannot redisplay a stale route quote')
                hub.query_error=True;page.locator('[data-model-id="qwenimage21"] button').click()
                expect(page.locator('#model-error')).to_contain_text('HF-Mirror')
                expect(page.locator('#hf-download-route')).to_have_value('hf_mirror')
                passed('mirror failure stays on chosen route without hidden origin retry')
                hub.query_error=False;page.locator('[data-model-id="qwenimage21"] button').click()
                page.get_by_role('button',name='确认下载并安装').click()
                expect(page.locator('#hf-download-route')).to_be_disabled()
                expect(page.locator('#model-progress-extra')).to_contain_text('HF-Mirror')
                expect(page.locator('#model-progress-extra')).to_contain_text('cdn-lfs.hf.co')
                page.screenshot(path=str(a.output/'hf-mirror-progress.png'),full_page=True)
                page.reload();page.locator('#setup-button').click()
                expect(page.locator('#hf-download-route')).to_have_value('hf_mirror')
                expect(page.locator('#hf-download-route')).to_be_disabled()
                passed('ongoing mirror download restores actual route, progress and final host')
                page.locator('#cancel-model').click();expect(page.locator('#hf-download-route')).to_be_enabled()
                page.locator('#model-source').select_option('modelscope')
                expect(page.locator('#hf-route-control')).to_be_hidden()
                page.locator('#model-source').select_option('sdu')
                expect(page.locator('#hf-route-control')).to_be_hidden()
                page.locator('#model-source').select_option('huggingface')
                expect(page.locator('#hf-download-route')).to_have_value('hf_mirror')
                passed('mirror route hidden for MS/SDU and HF preference retained')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(a.output/'hf-mirror-mobile.png'),full_page=True)
                passed('mirror selector on narrow screens has no horizontal overflow')
                assert not errors,errors;assert not requests,requests;passed('no page errors or browser external requests');browser.close()
        except Exception as exc:
            result['cases'].append({'name':'browser execution','passed':False,'error':f'{type(exc).__name__}: {exc}'})
        finally:
            hub.stop();server.shutdown();server.server_close();thread.join(2)
    result.update(passed=sum(t['passed'] for t in result['cases']),failed=sum(not t['passed'] for t in result['cases']),page_errors=errors)
    (a.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
    return int(bool(result['failed']))


if __name__=='__main__':raise SystemExit(main())
