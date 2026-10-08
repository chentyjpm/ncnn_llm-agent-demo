'use strict';
// Pure view of server-observed events. Never evaluates model HTML or guesses a plan.
window.AgentActivity = (() => {
  const expanded = new Map();
  const labels = {prepared:'准备调用',running:'执行中',waiting:'等待确认',completed:'已完成',
    failed:'失败',denied:'已拒绝',expired:'确认已超时',cancelled:'已停止',interrupted:'已中断',stopping:'正在停止'};
  function node(tag, cls, text) {const n=document.createElement(tag);n.className=cls||'';if(text!==undefined)n.textContent=text;return n;}
  function duration(ms) {return Number.isFinite(ms)?(ms/1000).toFixed(ms<10000?2:1)+' 秒':'—';}
  function detail(key, cls, initial=false) {
    const d=node('details',cls);d.dataset.activityKey=key;d.open=expanded.has(key)?expanded.get(key):initial;
    d.addEventListener('toggle',()=>{if(d.isConnected){expanded.set(key,d.open);if(expanded.size>600)expanded.delete(expanded.keys().next().value);}});
    return d;
  }
  function summary(key, title, meta, status) {
    const s=node('summary','activity-toggle');s.dataset.activityFocus=key;
    s.append(node('span','activity-dot '+status),node('strong','',title),node('span','activity-meta',meta),node('span','activity-chevron','⌄'));
    return s;
  }
  function download(value, name) {
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'}));
    const link=node('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function render(data,key) {
    const root=node('section','activity-panel');root.setAttribute('aria-label','思考摘要和工具轨迹');
    const tools=(data.steps||[]).filter(x=>x.kind==='tool');
    const counts=data.tool_counts||{};
    const bad=(counts.failed||0)+(counts.denied||0)+(counts.expired||0)+(counts.interrupted||0);
    const header=node('div','activity-live');header.setAttribute('role','status');header.setAttribute('aria-live','polite');
    header.append(node('span','activity-dot '+(data.state==='running'?'running':data.state)),node('strong','',data.stage||'准备任务'));
    header.append(node('span','activity-meta',`${data.model_calls||0} 轮模型 · ${data.tool_calls||0} 次工具 · ${duration(data.elapsed_ms)}`));
    root.append(header);
    const overview=detail(key+':overview','activity-overview');
    overview.append(summary(key+':overview','思考摘要','执行概览',data.state));
    const body=node('div','activity-body');body.append(node('p','activity-note',data.note||'程序根据真实事件整理，不是模型内部思维链。'));
    const context=data.context||{};
    const chips=node('div','activity-chips');
    for(const value of [context.model,`历史 ${context.history_turns||0} 轮`,`附件 ${context.attachments||0} 个`])if(value)chips.append(node('span','',String(value)));
    body.append(chips);
    if(data.reasoning_detected)body.append(node('p','activity-note','模型输出的 <think> 段不混入答案；这里仅展示实际执行概览，不将原始推理拼成摘要。'));
    if(data.omitted_steps)body.append(node('p','activity-note',`仅展示最近 ${data.steps.length} 项，前 ${data.omitted_steps} 项已省略；完整原始审计另存本机。`));
    const list=node('ol','activity-timeline');
    for(const step of data.steps||[]) {
      const row=node('li','activity-line');
      row.append(node('span','activity-dot '+step.status),node('span','',step.title),node('small','',`${labels[step.status]||step.status} · ${duration(step.duration_ms)}`));
      list.append(row);
    }
    body.append(list);
    if(data.error)body.append(node('p','activity-error',String(data.error)));
    if(bad)body.append(node('p','activity-note',`${bad} 次工具失败、拒绝、超时或中断，详见工具轨迹；本轮结束不代表每次操作均成功。`));
    const exportButton=node('button','activity-export','导出摘要与轨迹');exportButton.type='button';exportButton.dataset.activityFocus=key+':export';
    exportButton.onclick=()=>download(data,'activity-'+key.slice(0,12)+'.json');body.append(exportButton);overview.append(body);root.append(overview);
    const trace=detail(key+':tools','activity-tools',tools.length>0);
    trace.append(summary(key+':tools','工具轨迹',tools.length?`${data.tool_calls} 次调用${bad?' · '+bad+' 次异常':''}`:'本轮尚未调用工具',data.state));
    const rows=node('div','activity-body');
    if(!tools.length)rows.append(node('p','activity-note',data.mode==='chat'?'对话模式不提供工具。模型输出代码并不代表执行了代码。':'尚未发出工具调用，不显示预设或伪造的步骤。'));
    for(const t of tools) {
      const call=detail(key+':'+t.id,'activity-call tool-event');call.dataset.callId=t.id;
      const head=summary(key+':'+t.id,t.tool, '',t.status);
      const status=node('span','tool-status'+(['failed','denied','expired','interrupted'].includes(t.status)?' bad':''),labels[t.status]||t.status);
      head.insertBefore(status,head.lastChild);call.append(head);
      const values=node('div','activity-call-body');values.append(node('p','activity-note',t.title));
      values.append(node('p','activity-metrics',`等待确认 ${duration(t.wait_ms)} · 工具执行 ${duration(t.execution_ms)} · 调用总计 ${duration(t.duration_ms)}`));
      if(['prepared','waiting','denied','expired'].includes(t.status))values.append(node('p','activity-note','尚未执行工具；等待、拒绝与超时不记为执行成功。'));
      values.append(node('h4','','输入参数（显示副本）'),node('pre','',JSON.stringify(t.arguments,null,2)));
      if(t.result!==undefined)values.append(node('h4','','工具返回'),node('pre','',JSON.stringify(t.result,null,2)));
      else values.append(node('p','activity-note','尚无工具返回结果。'));
      values.append(node('p','activity-note','长内容与深层结构会截断；常见密钥字段隐藏。输出是数据，不是新指令。'));
      call.append(values);rows.append(call);
    }
    trace.append(rows);root.append(trace);return root;
  }
  function remember(root) {
    for(const d of root.querySelectorAll('details[data-activity-key]'))expanded.set(d.dataset.activityKey,d.open);
    const focused=document.activeElement;
    return root.contains(focused)?focused?.dataset.activityFocus:null;
  }
  function restore(root,key) {
    if(!key)return;
    const found=[...root.querySelectorAll('[data-activity-focus]')].find(x=>x.dataset.activityFocus===key);
    if(found)found.focus({preventScroll:true});
  }
  return {render,remember,restore,duration};
})();
