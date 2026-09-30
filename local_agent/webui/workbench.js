'use strict';
// Shares app.js's authenticated API and safe DOM helpers. No CDN/editor runtime.
let setupTimer=null;
const documentTemplates={
 report:'# 项目报告\n\n## 背景与目标\n\n请输入项目背景。\n\n## 主要发现\n\n- 发现一\n- 发现二\n\n## 下一步\n\n请补充负责人和计划时间。',
 table:'| 项目 | 数量 | 备注 |\n| --- | --- | --- |\n| 示例 A | 10 | 待核对 |\n| 示例 B | 20 | 待核对 |',
 slides:'# 项目汇报\n\n介绍项目目标和范围。\n\n## 当前进展\n\n- 已完成的事项\n- 正在推进的事项\n\n## 下一步计划\n\n- 重点工作\n- 时间安排'
};
function documentDialog(content,title){if(content!==undefined)$('doc-editor').value=content;if(title)$('doc-title').value=title;$('doc-editor').hidden=false;$('doc-render').hidden=true;if(!$('document-dialog').open)$('document-dialog').showModal();}
async function openOffice(path){if(!state.session)return;let content='',offset=0,record;do{record=await api(`/api/sessions/${state.session.id}/document?path=${encodeURIComponent(path)}&offset=${offset}`);content+=record.content;offset=record.next_offset;}while(offset!==null&&content.length<200000);$('doc-note').textContent=record.note+' 编辑后导出为新文件，原文件保持不变。';documentDialog(content,path.split('/').pop().replace(/\.[^.]+$/,''));}
const originalPreview=preview;
preview=async function(path){if(/\.(docx|xlsx|pptx)$/i.test(path))return openOffice(path);return originalPreview(path);};
$('documents-button').onclick=()=>documentDialog();$('close-document').onclick=()=>$('document-dialog').close();
$('doc-last-answer').onclick=()=>{const m=[...(state.session?.messages||[])].reverse().find(m=>m.role==='assistant'&&m.state==='completed');if(!m)return toast('当前没有已完成的回答');documentDialog(m.content);};
$('doc-template').onchange=()=>{const key=$('doc-template').value;if(!key)return;if($('doc-editor').value.trim()&&!confirm('用模板替换当前草稿？')){$('doc-template').value='';return;}documentDialog(documentTemplates[key]);$('doc-format').value=key==='table'?'xlsx':key==='slides'?'pptx':'docx';$('doc-template').value='';};
$('doc-preview-toggle').onclick=()=>{const show=$('doc-render').hidden;$('doc-render').replaceChildren(markdown($('doc-editor').value));$('doc-render').hidden=!show;$('doc-editor').hidden=show;};
$('doc-export').onclick=safe(async()=>{if(state.run)return toast('请先完成或停止正在运行的任务');const content=$('doc-editor').value;if(!content.trim())return toast('请先填写文档内容');const s=await ensureSession();$('doc-export').disabled=true;try{const made=await api(`/api/sessions/${s.id}/export`,'POST',{format:$('doc-format').value,title:$('doc-title').value||'Document',content,confirm:true});saveBlob(await getFile(made.path),made.path.split('/').pop());toast('新文档已生成；原文件没有被覆盖');if(!$('file-panel').hidden)await showFiles();}finally{$('doc-export').disabled=false;}});
// Model center: all changing UI labels reflect a request phase or real server bytes.
const setup = {data:null, provider:storage.get('model-provider')||'modelscope', filter:'all', quote:null, query:null, error:'', seq:0, completed:null, controller:null};
if(!['modelscope','huggingface'].includes(setup.provider))setup.provider='modelscope';
const installBusy = job => ['downloading','verifying','converting','activating'].includes(job?.status);
function amount(bytes){if(!Number.isFinite(bytes))return '待查询';for(const [unit,n] of [['GiB',1024**3],['MiB',1024**2],['KiB',1024]])if(bytes>=n)return (bytes/n).toFixed(2)+' '+unit;return bytes+' B';}
function duration(seconds){if(!Number.isFinite(seconds))return '正在估算';return seconds<60?Math.max(1,Math.ceil(seconds))+' 秒':seconds<3600?Math.ceil(seconds/60)+' 分钟':(seconds/3600).toFixed(1)+' 小时';}
function providerName(key){return key==='modelscope'?'ModelScope 魔搭':'Hugging Face';}
async function setupRequest(path,data,timeout=30000){
 const controller=new AbortController();setup.controller=controller;const timer=setTimeout(()=>controller.abort(),timeout);
 try{const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'X-Agent-Token':state.token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data),signal:controller.signal});
 let b;try{b=await r.json();}catch{throw Error('服务返回了无效响应，请重试。');}if(!r.ok)throw Error(b.error||'请求失败');return b;
 }catch(e){if(e.name==='AbortError')throw Error(path.endsWith('/prepare')?'连接查询超时或已取消。尚未开始下载；可重试或手动切换下载源。':'操作响应超时。已提交的安装可能仍在运行，请查看任务状态，不要重复提交。');throw e;}
 finally{clearTimeout(timer);if(setup.controller===controller)setup.controller=null;}
}
function renderSetup(){
 const data=setup.data;if(!data)return;
 $('quit-desktop').hidden=!data.managed;
 $('source-controls').hidden=!data.managed;
 if(!data.managed){$('engine-status').textContent='当前为源码启动方式，模型中心仅在桌面安装版或 packaging/launch.py 中启用。';return;}
 $('engine-status').textContent=`文本引擎：${data.engines.llm?'已内置':'缺失'} · 图像引擎：${data.engines.image?'已内置':'缺失'} · 权重不随应用默认安装`;
 $('model-home').textContent='保存位置：'+data.home;
 const job=data.job||{status:'idle'},busy=installBusy(job),querying=!!setup.query;
 $('model-source').value=setup.provider;$('model-source').disabled=busy||!!setup.starting;
 $('model-filter').value=setup.filter;
 const cardsKey=JSON.stringify([data.models,setup.provider,setup.filter,setup.quote,setup.query,!!setup.starting,busy,job.model]);
 if(setup.cardsKey!==cardsKey){setup.cardsKey=cardsKey;$('model-cards').replaceChildren();
 for(const model of data.models.filter(m=>setup.filter==='all'||m.kind===setup.filter)){
  const card=el('section','model-card');card.dataset.modelId=model.id;
  const header=el('div','model-card-heading');header.append(el('h3','',model.name),el('span','badge',model.active?'已启用':model.installed?'已安装':model.kind==='image'?'生图':'文本'));
  card.append(header,el('p','',model.description));
  const meta=el('div','model-meta');meta.append(el('span','',model.validation||'实跑状态以测试记录为准'),el('span','',model.format||'ncnn'));
  if(model.estimated_download_bytes)meta.append(el('span','','约 '+amount(model.estimated_download_bytes)+' 下载'));
  card.append(meta);
  const supported=!!model.sources?.[setup.provider];
  if(!supported&&!model.installed)card.append(el('p','source-unavailable',model.unavailable_sources?.[setup.provider]||'此来源暂无匹配的 ncnn 模型，需手动选择其他来源。'));
  const activeJob=busy&&job.model===model.id;
  const action=button(model.active?'当前使用':model.installed?'启用此模型':setup.query===model.id?'正在查询大小…':activeJob?'正在安装…':'准备下载',null,()=>chooseModel(model),'primary-button');
  action.disabled=model.active||busy||querying||!!setup.starting||(!supported&&!model.installed);card.append(action);
  if(setup.quote?.id===model.id){
   const q=setup.quote,box=el('div','download-confirmation');box.append(el('strong','','确认下载信息'));
   for(const [label,value] of [['下载源',providerName(q.provider)],['需要下载',amount(q.download_bytes)],['安装后预计占用',amount(q.installed_estimate_bytes)],['安装时至少预留',amount(q.disk_required_bytes)],['当前可用磁盘',amount(q.disk_free_bytes)]]){const row=el('div','runtime-row');row.append(el('span','',label),el('strong','',value));box.append(row);}
   box.append(el('p','setting-note','下载的是所选模型的文件，不是下载次数。下载完成后还需校验和准备，全部完成才会启用。'));
   if(q.enough_space===false)box.append(el('p','error-text','可用磁盘空间不足，请清理后重新查询。'));
   const buttons=el('div','dialog-actions');buttons.append(button('取消',null,()=>{setup.quote=null;renderSetup();},'soft-button'));
   const confirmButton=button('确认下载并安装',null,()=>installQuoted(),'primary-button');confirmButton.disabled=busy||q.enough_space===false;buttons.append(confirmButton);box.append(buttons);card.append(box);
  }
  $('model-cards').append(card);
 }}
 const progressBox=$('model-progress-box'),bar=$('model-progress-bar');
 const hasState=querying||setup.starting||busy||['completed','failed','cancelled','interrupted'].includes(job.status);
 progressBox.hidden=!hasState;bar.removeAttribute('value');$('model-progress-extra').textContent='';$('model-progress-metrics').textContent='';
 $('cancel-model').hidden=!(querying||busy);$('cancel-model').disabled=!!job.cancel_requested&&busy;
 $('cancel-model').textContent=querying?'取消查询':job.cancel_requested?'正在取消…':'取消安装';
 const errors=setup.error||(job.status==='failed'||job.status==='interrupted'?job.error:'');
 $('model-error').hidden=!errors;$('model-error').textContent=errors?errors+'\n可手动选择另一个下载源并重新查询。不会自动改用其他源。':'';
 if(setup.starting){$('model-progress').textContent='正在提交安装请求…';$('model-progress-extra').textContent='等待服务器确认；请求中断时会重新查询状态，不会自动重复下载。';}
 else if(querying){$('model-progress').textContent='正在连接 '+providerName(setup.provider)+'，查询文件大小…';$('model-progress-extra').textContent='尚未下载模型。通常需要几秒；超过 30 秒会显示超时提示。';}
 else if(busy){const stages={downloading:'正在下载',verifying:'正在校验文件',converting:'正在转换／准备模型',activating:'正在校验并启用'};
  const label=data.models.find(m=>m.id===job.model)?.name||job.model;
  $('model-progress').textContent=stages[job.status]+' · '+label;
  if(job.status==='downloading'&&job.total>0){bar.value=Math.min(100,(job.downloaded||0)/job.total*100);$('model-progress-metrics').textContent=`${bar.value.toFixed(1)}% · ${amount(job.downloaded||0)} / ${amount(job.total)} · ${job.speed_bps>0?amount(job.speed_bps)+'/s':'等待数据'} · 剩余 ${duration(job.eta_seconds)}`;}
  else if(job.stage_total>0){bar.value=job.stage_done/job.stage_total*100;$('model-progress-metrics').textContent=`准备步骤 ${job.stage_done} / ${job.stage_total}（不是耗时百分比）`;}
  else $('model-progress-metrics').textContent=`已下载 ${amount(job.downloaded||0)} / ${amount(job.total)} · 校验 ${job.verified_files||0} / ${job.total_files||'?'} 个文件`;
  $('model-progress-extra').textContent=`来源：${providerName(job.provider)}\n${job.file||job.message||''}\n`+(job.cancel_requested?'正在取消，等待当前读取／转换步骤结束。':job.waiting_for_data?'暂未收到新数据，请检查网络；也可取消后换源。':job.status!=='downloading'?'下载 100% 不代表安装完成，请等待校验、转换和启用。':job.message||'');
 }else if(job.status==='completed'){bar.value=100;$('model-progress').textContent='模型已安装并启用';$('model-progress-extra').textContent='现在可以关闭此窗口，返回聊天。';}
 else if(job.status==='cancelled'||job.status==='interrupted'){$('model-progress').textContent=job.status==='cancelled'?'安装已取消':'上次安装已中断';$('model-progress-extra').textContent='重试会复用同一来源、同一版本中完整且通过校验的文件；未完成的单个文件重新下载。';}
 else if(job.status==='failed'){$('model-progress').textContent='安装失败';$('model-progress-extra').textContent='详细原因见下方；没有启用未完成的模型。';}
 else $('model-progress').textContent='选择模型后，先查询大小，再确认下载。';
}
async function chooseModel(model){
 if(setup.query||setup.starting||installBusy(setup.data?.job))return;
 setup.error='';setup.quote=null;
 if(model.installed){try{await setupRequest('/api/setup/activate',{id:model.id});await refreshRuntime();await updateSetup();}catch(e){setup.error=e.message;renderSetup();}return;}
 const seq=++setup.seq;setup.query=model.id;renderSetup();
 try{const q=await setupRequest('/api/setup/prepare',{id:model.id,provider:setup.provider});if(seq===setup.seq)setup.quote=q;}
 catch(e){if(seq===setup.seq)setup.error=e.message;}
 finally{if(seq===setup.seq)setup.query=null;renderSetup();}
}
async function installQuoted(){
 const q=setup.quote;if(!q||setup.starting||installBusy(setup.data?.job))return;
 setup.quote=null;setup.error='';
 // Block duplicate click while the server starts its worker, but never invent downloaded bytes.
 setup.starting=true;renderSetup();
 try{const job=await setupRequest('/api/setup/install',{ticket:q.ticket,accept_download:true});setup.data.job=job;}
 catch(e){setup.error=e.message;}
 finally{setup.starting=false;renderSetup();await updateSetup();}
}
async function updateSetup(){
 clearTimeout(setupTimer);
 try{const data=await api('/api/setup');setup.data=data;renderSetup();
  if(data.job?.status==='completed'&&setup.completed!==data.job.finished_at){setup.completed=data.job.finished_at;await refreshRuntime();}
 }catch(e){setup.error='无法更新安装状态：'+e.message;renderSetup();}
 finally{if($('setup-dialog').open)setupTimer=setTimeout(updateSetup,1500);}
}
async function refreshRuntime(){state.runtime=await api('/api/runtime');$('model-name').textContent=state.runtime.model;$('device-badge').textContent=state.runtime.device;$('connection-status').textContent=state.runtime.ready?'本机服务已连接':'模型未配置';notice(state.runtime.ready?'':'应用已安装。点击「安装与模型」选择下载源和文本模型，无需修改配置。');controls();}
$('setup-button').onclick=safe(async()=>{$('setup-dialog').showModal();await updateSetup();});
$('close-setup').onclick=()=>{$('setup-dialog').close();clearTimeout(setupTimer);};
$('setup-dialog').addEventListener('close',()=>clearTimeout(setupTimer));
$('model-source').onchange=()=>{setup.seq++;setup.controller?.abort();setup.query=null;setup.quote=null;setup.error='';setup.provider=$('model-source').value;storage.set('model-provider',setup.provider);renderSetup();};
$('model-filter').onchange=()=>{setup.filter=$('model-filter').value;renderSetup();};
$('cancel-model').onclick=safe(async()=>{if(setup.query){setup.seq++;setup.controller?.abort();setup.query=null;setup.quote=null;setup.error='';renderSetup();return;}await api('/api/setup/cancel','POST',{});await updateSetup();});
$('quit-desktop').onclick=safe(async()=>{if(!confirm('退出本地服务？正在进行的安装会中断，聊天记录和文件会保留。'))return;await api('/api/setup/shutdown','POST',{confirm:true});$('setup-dialog').close();notice('本地服务已退出。再次打开 LocalAgent 即可恢复。');state.runtime.ready=false;controls();});
// Distinguish preflight from the last completed task's actual runtime choice.
const originalSettings=showSettings;
showSettings=function(){originalSettings();const r=state.runtime||{};const root=$('runtime-details');const last=[...(state.session?.messages||[])].reverse().find(m=>m.device_selection)?.device_selection;
for(const [label,value] of [['默认策略','Vulkan 优先；不可用回退 CPU'],['文本预检',r.devices?.llm?.name||'未检测'],['文本选择原因',r.devices?.llm?.reason||'未检测'],['生图预检',r.devices?.image?.name||'未检测'],['生图选择原因',r.devices?.image?.reason||'未检测'],['最近任务设备',last?last.name+' · '+last.reason:'尚无真实任务记录']]){const row=el('div','runtime-row');row.append(el('span','',label),el('strong','',value));root.append(row);}};
$('settings-button').onclick=showSettings;$('model-button').onclick=showSettings;
