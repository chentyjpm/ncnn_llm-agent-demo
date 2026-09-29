'use strict';
// Deterministic event-order tests for the actual UI functions, not model tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../local_agent/webui/app.js'), 'utf8');
function section(start, end) {const a=source.indexOf(start),b=source.indexOf(end,a);assert(a>=0&&b>a);return source.slice(a,b);}
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
async function turn(){await new Promise(r=>setImmediate(r));}
async function testLockUntilFinalRefresh(){
 const gate=deferred();const state={run:null,session:{id:'sid',messages:[]}};
 const node={hidden:false,disabled:false,scrollHeight:0};
 const c=vm.createContext({state,terminal:new Set(['completed']),$:()=>node,toast:()=>{},notice:()=>{},renderMessages:()=>{},controls:()=>{},showFiles:async()=>{},
  refreshSessions:()=>gate.promise,api:async url=>url.includes('/events')?{events:[],cursor:0,status:'completed'}:{id:'sid',messages:[],active_run:null}});
 vm.runInContext(section('async function watchRun(', '\nfunction renderAttachments('),c);
 const running=vm.runInContext("watchRun('run1','sid')",c);await turn();
 assert.equal(state.run,'run1','final refresh must retain the run lock');
 gate.resolve();await running;assert.equal(state.run,null);console.log('PASS final refresh holds run lock');
}
async function testDoNotOverwriteOtherConversation(){
 const gate=deferred();const state={run:null,session:{id:'sid',messages:[]}};
 const node={hidden:true,disabled:false};let requested=false;
 const c=vm.createContext({state,terminal:new Set(['completed']),$:()=>node,toast:()=>{},notice:()=>{},renderMessages:()=>{},controls:()=>{},showFiles:async()=>{},
  refreshSessions:async()=>{},api:async url=>{if(url.includes('/events'))return {events:[],cursor:0,status:'completed'};requested=true;return gate.promise;}});
 vm.runInContext(section('async function watchRun(', '\nfunction renderAttachments('),c);
 const running=vm.runInContext("watchRun('run1','sid')",c);await turn();assert(requested);
 state.session={id:'other',messages:[]};gate.resolve({id:'sid',messages:[]});await running;
 assert.equal(state.session.id,'other');console.log('PASS late response cannot switch conversation');
}
async function testDoubleSubmitSuppressed(){
 const gate=deferred();const state={run:null,sending:false,runtime:{ready:true},mode:'agent',attachments:[],session:{id:'sid'}};
 let posts=0;const input={value:'task',rows:2},node={value:'512',scrollTop:0,scrollHeight:0};
 const c=vm.createContext({state,$:id=>id==='message-input'?input:node,toast:()=>{},controls:()=>{},renderMessages:()=>{},renderAttachments:()=>{},
  ensureSession:()=>gate.promise,refreshSessions:async()=>{},requestAnimationFrame:fn=>fn(),watchRun:rid=>{state.run=rid;},
  api:async()=>{posts++;return {run_id:'run',session:{id:'sid',messages:[]}};}});
 vm.runInContext(section('async function send(', '\nasync function watchRun('),c);
 const first=vm.runInContext('send()',c);await turn();await vm.runInContext('send()',c);
 assert.equal(posts,0);assert.equal(state.sending,true);
 gate.resolve({id:'sid'});await first;assert.equal(posts,1);assert.equal(state.sending,false);
 console.log('PASS pending submission cannot duplicate tool task');
}
(async()=>{await testLockUntilFinalRefresh();await testDoNotOverwriteOtherConversation();await testDoubleSubmitSuppressed();})().catch(e=>{console.error(e);process.exitCode=1;});
