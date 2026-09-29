'use strict';
const $ = id => document.getElementById(id);
const state = {token:'',runtime:null,sessions:[],session:null,attachments:[],mode:'chat',
  run:null,runSession:null,traces:[],approval:null,folder:'.',previewURL:null,uploading:false};
const terminal = new Set(['completed','failed','cancelled']);
const storage = {get(k){try{return localStorage.getItem(k);}catch{return null;}},set(k,v){try{localStorage.setItem(k,v);}catch{}}};
function el(tag, cls, value) {const n=document.createElement(tag); if(cls)n.className=cls; if(value!==undefined)n.textContent=value; return n;}
function icon(name){const n=document.createElementNS('http://www.w3.org/2000/svg','svg');n.classList.add('icon');n.setAttribute('aria-hidden','true');const u=document.createElementNS(n.namespaceURI,'use');u.setAttribute('href','#i-'+name);n.append(u);return n;}
function button(label, name, fn, cls='icon-button'){const b=el('button',cls);b.type='button';b.setAttribute('aria-label',label);b.title=label;if(name)b.append(icon(name));else b.textContent=label;b.addEventListener('click',fn);return b;}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,4200);}
function notice(message){$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path,method='GET',data){const r=await fetch(path,{method,headers:{'X-Agent-Token':state.token,...(data!==undefined?{'Content-Type':'application/json'}:{})},body:data!==undefined?JSON.stringify(data):undefined});let body;try{body=await r.json();}catch{throw Error('服务返回了无法识别的数据，请检查终端日志。');}if(!r.ok)throw Error(body.error||`请求失败 (${r.status})`);return body;}
function safe(fn){return (...args)=>Promise.resolve().then(()=>fn(...args)).catch(e=>toast(e.message));}
function setMode(mode){state.mode=mode;for(const v of ['chat','agent']){$('mode-'+v).classList.toggle('active',v===mode);$('mode-'+v).setAttribute('aria-pressed',String(v===mode));}$('mode-hint').textContent=mode==='chat'?'直接回答，不调用工具':'写入与执行前需要确认';}
function controls(){const busy=!!state.run||!!state.sending;$('send-button').hidden=!!state.run;$('stop-button').hidden=!state.run;$('send-button').disabled=busy||!state.runtime?.ready||!$('message-input').value.trim()||state.uploading;$('upload-button').disabled=busy||state.uploading;$('mode-chat').disabled=busy;$('mode-agent').disabled=busy;}
function closeSidebar() {document.body.classList.remove('sidebar-open');$('mobile-scrim').hidden=true;}
function toggleSidebar(){if(innerWidth<=800){const open=document.body.classList.toggle('sidebar-open');$('mobile-scrim').hidden=!open;}else document.body.classList.toggle('sidebar-collapsed');}
function setTheme(theme){document.documentElement.dataset.theme=theme;storage.set('local-agent-theme',theme);}
setTheme(storage.get('local-agent-theme')||(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'));

function renderSessions(){const list=$('session-list');list.replaceChildren();const filter=$('session-search').value.toLowerCase();$('session-count').textContent=state.sessions.length;
  const visible=state.sessions.filter(s=>s.title.toLowerCase().includes(filter));
  if(!visible.length)list.append(el('div','session-empty',filter?'没有找到匹配的对话':'从一个问题开始，历史对话将保存在本机。'));
  for(const s of visible){const row=el('div','session-row'+(state.session?.id===s.id?' active':''));const open=button(s.title,'chat',safe(()=>loadSession(s.id)),'session-open');open.append(el('span','',s.title));row.append(open);const menu=el('div','session-menu');menu.append(button('重命名对话','edit',()=>renameDialog(s)),button('删除对话','trash',()=>deleteDialog(s)));row.append(menu);list.append(row);}}
async function refreshSessions(){state.sessions=(await api('/api/sessions')).sessions;renderSessions();}
async function ensureSession(){if(!state.session){state.session=await api('/api/sessions','POST',{});storage.set('local-agent-session',state.session.id);await refreshSessions();}return state.session;}
async function newChat(){state.session=null;state.attachments=[];state.folder='.';storage.set('local-agent-session','');$('message-input').value='';$('message-input').rows=2;renderMessages();renderSessions();renderAttachments();closeSidebar();if(!$('file-panel').hidden)await showFiles();$('message-input').focus();controls();}
async function loadSession(sid){state.session=await api('/api/sessions/'+sid);state.attachments=[];state.folder='.';storage.set('local-agent-session',sid);renderSessions();renderMessages();renderAttachments();closeSidebar();if(!$('file-panel').hidden)await showFiles();if(state.session.active_run&&!state.run)watchRun(state.session.active_run,sid);}
function renameDialog(s){$('rename-dialog').dataset.sid=s.id;$('rename-input').value=s.title;$('rename-dialog').showModal();$('rename-input').focus();}
function deleteDialog(s){$('delete-dialog').dataset.sid=s.id;$('delete-dialog').showModal();}

// Deliberately small Markdown renderer. Every model/file string becomes a text
// node; raw HTML, remote images and scripts are never inserted or executed.
function inline(parent,source){const pattern=/(\*\*([^*\n]+)\*\*|`([^`\n]+)`)/g;let m,offset=0;while((m=pattern.exec(source))){parent.append(document.createTextNode(source.slice(offset,m.index)));parent.append(el(m[2]?'strong':'code','',m[2]||m[3]));offset=pattern.lastIndex;}parent.append(document.createTextNode(source.slice(offset)));}
function markdown(source){const root=el('div');const lines=source.split('\n');let i=0;while(i<lines.length){const line=lines[i];
    if(/^```/.test(line)){const language=line.slice(3).trim().slice(0,30)||'code';let code=[];i++;while(i<lines.length&&!/^```/.test(lines[i]))code.push(lines[i++]);if(i<lines.length)i++;const block=el('div','code-block'),bar=el('div','code-toolbar');bar.append(el('span','',language),button('复制代码',null,safe(async()=>{await navigator.clipboard.writeText(code.join('\n'));toast('代码已复制');}),''));const pre=el('pre');pre.append(el('code','',code.join('\n')));block.append(bar,pre);root.append(block);continue;}
    if(/^#{1,4}\s/.test(line)){const title=el(/^#{1,2}\s/.test(line)?'h2':'h3');inline(title,line.replace(/^#{1,4}\s/,''));root.append(title);i++;continue;}
    if(/^\s*[-*]\s/.test(line)){const ul=el('ul');while(i<lines.length&&/^\s*[-*]\s/.test(lines[i])){const li=el('li');inline(li,lines[i++].replace(/^\s*[-*]\s/,''));ul.append(li);}root.append(ul);continue;}
    if(!line.trim()){i++;continue;}
    const paragraph=[];while(i<lines.length&&lines[i].trim()&&!/^(```|#{1,4}\s|\s*[-*]\s)/.test(lines[i]))paragraph.push(lines[i++]);const p=el('p');inline(p,paragraph.join('\n'));root.append(p);
  }return root;}
function traceView(events){const root=el('div','trace');root.append(el('div','trace-heading','AGENT 执行记录'));
  const starts=events.filter(e=>e.event==='tool_start');for(const e of starts){const result=events.find(r=>r.event==='tool_result'&&r.step===e.step);const item=el('details','tool-event');item.dataset.step=e.step;const summary=el('summary');summary.append(icon(result?'check':'tool'),el('span','',e.tool),el('span','tool-status'+(result&&!result.result.ok?' bad':''),result?(result.result.ok?'已完成':'失败'):'进行中'));item.append(summary,el('pre','',JSON.stringify({arguments:e.arguments,...(result?{result:result.result}:{})},null,2)));root.append(item);}
  for(const e of events.filter(e=>e.event==='format_error'))root.append(el('div','tool-event error-text','模型动作格式错误，正在请求修正。'));
  return root;}
function approvalView(){const a=state.approval;const card=el('div','approval-card');card.append(el('strong','','需要你确认 · '+a.tool),el('p','','请核对下面的参数。仅允许这一次调用；拒绝或超时将阻止执行。'),el('pre','',JSON.stringify(a.arguments,null,2)));const actions=el('div','approval-actions');const decide=allow=>safe(async()=>{await api(`/api/runs/${state.run}/approve`,'POST',{approval_id:a.id,allow});state.approval=null;renderMessages();});actions.append(button('拒绝',null,decide(false),'soft-button'),button('允许这一次',null,decide(true),'primary-button'));card.append(actions);return card;}
function renderMessages(){const scroll=$('scroll-area');const near=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<130;const opened=[...$('messages').querySelectorAll('details[open]')].map(x=>x.dataset.step);const list=$('messages');list.replaceChildren();const messages=state.session?.messages||[];$('welcome').hidden=messages.length>0;
  for(const m of messages){const article=el('article','message '+m.role);article.dataset.messageId=m.id;const content=el('div','message-content');if(m.role==='user'){content.textContent=m.content;article.append(content);if(m.attachments?.length){const links=el('div','attachments');for(const p of m.attachments)links.append(button(p.split('/').pop(),'file',safe(()=>openFilesAt(p)),'attachment-chip'));article.append(links);}}
    else {const label=el('div','message-label');label.append(icon('spark'),el('span','','Local Agent'));if(m.mode==='agent')label.append(el('span','badge','AGENT'));article.append(label);
      const running=state.run&&state.runSession===state.session.id&&m.run_id===state.run;
      const traces=running?state.traces:m.trace||[];
      if(traces.some(e=>e.event==='tool_start'||e.event==='format_error'))article.append(traceView(traces));
      if(running&&state.approval)article.append(approvalView());
      if((running||m.state==='running')&&!m.content){const pending=el('div','pending-text');pending.append(el('span','spinner'),el('span','',state.approval?'等待你的确认':state.stopping?'正在停止，等待当前操作结束…':'正在处理 · 模型完成本轮后显示回答'));content.append(pending);}
      else {if(m.state==='failed'||m.state==='cancelled')content.classList.add('error-text');content.append(markdown(m.content));}
      article.append(content);if(m.content){const actions=el('div','message-actions');actions.append(button('复制回答','copy',safe(async()=>{await navigator.clipboard.writeText(m.content);toast('回答已复制');})));if(m.state==='failed'){actions.append(button('重试最后一条消息',null,()=>{const last=[...messages].reverse().find(x=>x.role==='user');if(last){$('message-input').value=last.content;controls();$('message-input').focus();}},'soft-button'));}actions.append(el('span','',m.elapsed_seconds?`${m.elapsed_seconds} 秒`:'本地回复'));article.append(actions);}
    }list.append(article);
  }
  for(const d of list.querySelectorAll('details'))if(opened.includes(d.dataset.step))d.open=true;
  if(near||!messages.length)requestAnimationFrame(()=>scroll.scrollTop=scroll.scrollHeight);
}
async function send(){
  if(state.run||state.sending)return toast('请先完成或停止当前任务。');
  const message=$('message-input').value.trim();if(!message)return;if(!state.runtime?.ready)return toast('请先配置本地模型。');
  const payload={message,mode:state.mode,max_new_tokens:Number($('token-limit').value),attachments:state.attachments.map(x=>x.path)};
  state.sending=true;controls();
  try{const s=await ensureSession();const submitted=await api(`/api/sessions/${s.id}/messages`,'POST',payload);
    if(state.session?.id===s.id){state.session=submitted.session;state.attachments=[];$('message-input').value='';$('message-input').rows=2;renderAttachments();renderMessages();}
    // Start observing immediately, before any asynchronous sidebar refresh.
    watchRun(submitted.run_id,s.id);await refreshSessions();
    requestAnimationFrame(()=>$('scroll-area').scrollTop=$('scroll-area').scrollHeight);
  }finally{state.sending=false;controls();}
}
async function watchRun(rid,sid){if(state.run)return;state.run=rid;state.runSession=sid;state.traces=[];state.approval=null;state.stopping=false;$('stop-button').disabled=false;controls();let cursor=0;
  try {while(state.run===rid){const result=await api(`/api/runs/${rid}/events?after=${cursor}`);cursor=result.cursor;for(const e of result.events){if(e.event==='approval_required')state.approval=e.approval;if(e.event==='approval_resolved')state.approval=null;if(e.event==='stopping')state.stopping=true;if(e.event!=='done')state.traces.push(e);if(e.event==='done'&&state.session?.id===sid){const m=state.session.messages.find(m=>m.id===e.message.id);if(m)Object.assign(m,e.message);state.session.active_run=null;}}
      if(state.session?.id===sid)renderMessages();if(terminal.has(result.status))break;
    }}catch(e){notice('与任务的连接中断：'+e.message+'。刷新页面可重新查询任务状态。');}
  finally{
    // Keep the current run locked until its final session refresh completes.
    // Otherwise a late response can erase the next run's assistant/approval card.
    try{await refreshSessions();if(state.session?.id===sid){const session=await api('/api/sessions/'+sid);if(state.session?.id===sid){state.session=session;renderMessages();if(!$('file-panel').hidden)await showFiles();}}}catch(e){toast(e.message);}
    finally{if(state.run===rid){state.run=null;state.runSession=null;state.approval=null;state.traces=[];state.stopping=false;}controls();renderMessages();}
  }
}
function renderAttachments(){const root=$('attachments');root.replaceChildren();for(const item of state.attachments){const chip=el('div','attachment-chip');chip.append(icon('file'),el('span','',item.name),button('移除附件','close',()=>{state.attachments=state.attachments.filter(x=>x!==item);renderAttachments();}));root.append(chip);}}
async function upload(files){state.uploading=true;controls();try{const s=await ensureSession();for(const file of files){if(state.attachments.length>=10)throw Error('最多附加 10 个文件。');if(!file.size||file.size>8*1024*1024)throw Error('单个文件必须为 1 字节至 8 MiB。');const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));const result=await api(`/api/sessions/${s.id}/upload`,'POST',{name:file.name,data:btoa(binary)});state.attachments.push(result);renderAttachments();}toast('文件已上传到当前对话工作区');if(!$('file-panel').hidden)await showFiles();}finally{state.uploading=false;$('file-input').value='';controls();}}
function formatBytes(n){return n===null?'文件夹':n>=1048576?(n/1048576).toFixed(1)+' MB':n>=1024?(n/1024).toFixed(1)+' KB':n+' B';}
async function getFile(path){const r=await fetch(`/api/sessions/${state.session.id}/download?path=${encodeURIComponent(path)}`,{headers:{'X-Agent-Token':state.token}});if(!r.ok){const e=await r.json();throw Error(e.error);}return r.blob();}
function saveBlob(blob,name){const u=URL.createObjectURL(blob),a=el('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),3000);}
async function preview(path){const blob=await getFile(path);$('file-preview').hidden=true;$('image-preview').hidden=true;if(state.previewURL)URL.revokeObjectURL(state.previewURL);
  const head=new Uint8Array(await blob.slice(0,12).arrayBuffer());let mime='';if(head[0]===137&&head[1]===80&&head[2]===78&&head[3]===71)mime='image/png';else if(head[0]===255&&head[1]===216&&head[2]===255)mime='image/jpeg';else if(String.fromCharCode(...head.slice(0,4))==='RIFF'&&String.fromCharCode(...head.slice(8,12))==='WEBP')mime='image/webp';
  if(mime){state.previewURL=URL.createObjectURL(new Blob([blob],{type:mime}));$('image-preview').src=state.previewURL;$('image-preview').hidden=false;}
  else if(/\.(txt|md|json|csv|log|py|js|css|html|yml|yaml|cpp|h|ini|toml)$/i.test(path)&&blob.size<=1048576){$('file-preview').textContent=await blob.text();$('file-preview').hidden=false;}
  else saveBlob(blob,path.split('/').pop());
}
async function openFilesAt(path){state.folder=path.includes('/')?path.split('/').slice(0,-1).join('/'):'.';$('file-panel').hidden=false;await showFiles();await preview(path);}
async function showFiles(){$('file-panel').hidden=false;const list=$('file-list');list.replaceChildren();$('file-preview').hidden=true;$('image-preview').hidden=true;$('folder-path').textContent=state.folder==='.'?'/':state.folder;$('parent-folder').hidden=state.folder==='.';
  if(!state.session){list.append(el('p','panel-note','发送一条消息或上传文件后，这里会显示独立工作区。'));return;}
  const result=await api(`/api/sessions/${state.session.id}/files?path=${encodeURIComponent(state.folder)}`);if(!result.items.length)list.append(el('p','panel-note','还没有文件。你可以上传资料，或让 Agent 创建文件。'));
  for(const f of result.items){const row=el('div','file-item');row.append(icon(f.type==='directory'?'folder':'file'));const info=el('div','file-info');const title=button(f.name,null,safe(async()=>{if(f.type==='directory'){state.folder=f.path;await showFiles();}else if(f.type==='file')await preview(f.path);}), 'file-name');info.append(title,el('small','',f.type==='blocked'||f.type==='symlink-blocked'?'禁止访问':formatBytes(f.bytes)));row.append(info);if(f.type==='file'){row.append(button('附加到下一条消息','clip',()=>{if(state.attachments.length>=10)return toast('最多 10 个附件');if(!state.attachments.some(x=>x.path===f.path))state.attachments.push({path:f.path,name:f.name});renderAttachments();toast('已附加到下一条消息');}),button('下载 '+f.name,'down',safe(async()=>saveBlob(await getFile(f.path),f.name))));}list.append(row);}
}
function showSettings(){const r=state.runtime||{};const root=$('runtime-details');root.replaceChildren();for(const [label,value] of [['当前模型',r.model||'未连接'],['推理后端',r.llm_backend||'未配置'],['计算设备',r.device||'CPU'],['模型状态',r.test_fixture?'UI 测试后端（非真实模型）':r.ready?'可执行文件与模型配置已找到':'未就绪'],['Python',r.features?.python||'disabled'],['系统命令',r.features?.commands?'启动时已授权':'关闭'],['外部 MCP',r.features?.mcp?'启动时已授权':'关闭'],['Qwen Image',r.features?.images?'配置已启用，实跑状态需核验':'关闭'],['工作区',r.workspace||'—']]){const row=el('div','runtime-row');row.append(el('span','',label),el('strong','',value));root.append(row);}$('settings-dialog').showModal();}
async function boot(){try{const r=await api('/api/bootstrap');state.token=r.token;state.runtime=r.runtime;$('model-name').textContent=state.runtime.model;$('device-badge').textContent=state.runtime.device;$('connection-status').textContent=state.runtime.ready?'本机服务已连接':'模型未配置';if(state.runtime.test_fixture)notice('UI 自动化测试模式：这里的回复来自明确标记的测试后端，不是真实模型。');else if(!state.runtime.ready)notice('界面已就绪，模型尚未配置。打开「运行设置」查看启动方式。');await refreshSessions();const last=storage.get('local-agent-session');if(last&&state.sessions.some(s=>s.id===last))await loadSession(last);const active=state.sessions.find(s=>s.active_run);if(active&&!state.run)watchRun(active.active_run,active.id);}catch(e){notice('无法连接本地服务：'+e.message);$('connection-status').textContent='服务未连接';}controls();}
$('new-chat').onclick=safe(newChat);$('toggle-sidebar').onclick=toggleSidebar;$('close-sidebar').onclick=closeSidebar;$('mobile-scrim').onclick=closeSidebar;$('session-search').oninput=renderSessions;
$('theme-button').onclick=()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');$('settings-button').onclick=showSettings;$('model-button').onclick=showSettings;$('close-settings').onclick=()=>$('settings-dialog').close();
$('mode-chat').onclick=()=>setMode('chat');$('mode-agent').onclick=()=>setMode('agent');
$('message-input').oninput=()=>{$('message-input').rows=Math.min(7,Math.max(2,$('message-input').value.split('\n').length));controls();};
$('message-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();safe(send)();}});$('composer').onsubmit=e=>{e.preventDefault();safe(send)();};
$('stop-button').onclick=safe(async()=>{await api(`/api/runs/${state.run}/cancel`,'POST',{});state.stopping=true;$('stop-button').disabled=true;renderMessages();});
$('upload-button').onclick=()=>$('file-input').click();$('file-input').onchange=safe(()=>upload([...$('file-input').files]));
for(const b of document.querySelectorAll('.suggestion'))b.onclick=()=>{if(b.dataset.mode)setMode(b.dataset.mode);$('message-input').value=b.dataset.prompt;$('message-input').focus();controls();};
$('files-button').onclick=safe(async()=>{if($('file-panel').hidden)await showFiles();else $('file-panel').hidden=true;});$('close-files').onclick=()=>$('file-panel').hidden=true;$('parent-folder').onclick=safe(async()=>{state.folder=state.folder.includes('/')?state.folder.split('/').slice(0,-1).join('/'):'.';await showFiles();});
$('rename-form').onsubmit=e=>{e.preventDefault();safe(async()=>{const sid=$('rename-dialog').dataset.sid;const s=await api('/api/sessions/'+sid,'PATCH',{title:$('rename-input').value});if(state.session?.id===sid)state.session=s;$('rename-dialog').close();await refreshSessions();})();};$('cancel-rename').onclick=()=>$('rename-dialog').close();
$('cancel-delete').onclick=()=>$('delete-dialog').close();$('confirm-delete').onclick=safe(async()=>{const sid=$('delete-dialog').dataset.sid;await api('/api/sessions/'+sid,'DELETE');$('delete-dialog').close();if(state.session?.id===sid)await newChat();await refreshSessions();toast('对话记录已删除，工作文件保留。');});
$('export-chat').onclick=()=>{if(!state.session)return toast('当前没有可导出的对话。');const s=state.session;const contents='# '+s.title+'\n\n'+s.messages.map(m=>'## '+(m.role==='user'?'你':'Local Agent')+'\n\n'+(m.content||'[任务未完成]')).join('\n\n');saveBlob(new Blob([contents],{type:'text/markdown;charset=utf-8'}),'conversation-'+s.id.slice(0,8)+'.md');};
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();safe(newChat)();}if(e.key==='Escape'){closeSidebar();$('file-panel').hidden=true;}});
window.addEventListener('resize',()=>{if(innerWidth>800)closeSidebar();});
boot();
