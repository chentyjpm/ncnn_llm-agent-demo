"""Temporary browser-test assembly; never part of the application release."""
from pathlib import Path

def edit(file,old,new):
    p=Path(file);s=p.read_text();assert s.count(old)==1,(file,old[:80]);p.write_text(s.replace(old,new))

needle="                assert not errors,errors;passed('no browser page errors')"
edit('scripts/browser_images.py',needle,'''                page.set_viewport_size({'width':1440,'height':960})
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
'''+needle)
needle="                page.locator('#model-source').select_option('modelscope')\n                page.locator('#model-filter').select_option('native');"
edit('scripts/browser_model_center.py',needle,'''                turbo_card=page.locator('[data-model-id="qwenimage21-turbo"]')
                expect(turbo_card).to_contain_text('固定 8 步')
                expect(turbo_card.get_by_role('button')).to_have_text('先准备基础模型')
                turbo_card.get_by_role('button').click()
                expect(page.get_by_role('button',name='确认下载并安装')).to_be_visible()
                assert hub.calls[-1]==('qwenimage21','huggingface')
                passed('Turbo dependency guides user to base download instead of copying or guessing files')
                page.screenshot(path=str(a.output/'turbo-base-dependency.png'),full_page=True)
                page.locator('#model-source').select_option('modelscope')
                page.locator('#model-filter').select_option('native');''')
