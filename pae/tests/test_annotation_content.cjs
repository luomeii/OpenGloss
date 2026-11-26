'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../extension/content.js'),'utf8');
function harness(cap=10){
  let changed,mutate,now=0,next=1;
  const timers=new Map(),calls=[],paragraphs=['First isolated reading paragraph with eight dictionary words.'];
  const document={readyState:'complete',documentElement:{dataset:{},scrollHeight:5000},body:{},
    createTreeWalker(){let i=0;return {nextNode(){if(i>=paragraphs.length)return null;const text=paragraphs[i++];return {textContent:text,parentElement:{tagName:'P',textContent:text,
      closest(){return null;},querySelectorAll(){return [];},getBoundingClientRect(){return {top:500};}}};}};}};
  const runtime={onMessage:{addListener(){}},sendMessage(message,cb){
    if(message.type==='PAE_ANNOTATE')calls.push({message,cb});
    else if(message.type==='PAE_GET_LIMITS')cb({limits:{max_ann_per_page:cap}});
    else cb({ok:true});
  }};
  const window={scrollY:0,addEventListener(){},postMessage(){}};
  const context={window,document,chrome:{runtime,storage:{onChanged:{addListener(fn){changed=fn;}},local:{get(d,cb){cb(d);}}}},
    sessionStorage:{getItem(){return 's0';}},location:{href:'https://fixture.invalid/'},NodeFilter:{SHOW_TEXT:4},
    MutationObserver:class{constructor(fn){mutate=fn;}observe(){}},
    setTimeout(fn,ms){let id=next++;timers.set(id,{at:now+ms,fn});return id;},clearTimeout(id){timers.delete(id);},console};
  vm.runInNewContext(source,context);
  function advance(ms){const end=now+ms;while(true){const due=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;timers.delete(due[0]);now=due[1].at;due[1].fn();}now=end;}
  return {calls,advance,document,window,add(){paragraphs.push('Second isolated paragraph with different dictionary words.');mutate();},toggle(enabled){changed({paeEnabled:{newValue:enabled}},'local');},
    reply(index,n){calls[index].cb({ok:true,data:Array.from({length:n},()=>({lemma:'fixture'})),rendered:{rendered:n}});}};
}
test('dynamic batches share one queue and send only remaining slots',()=>{
  const h=harness();h.advance(1000);assert.equal(h.calls.length,1);
  h.add();h.advance(1100);assert.equal(h.calls.length,1,'second batch must wait for current response');
  h.reply(0,8);assert.equal(h.calls.length,2);assert.equal(h.calls[1].message.maxAnnotations,2);
  h.reply(1,2);assert.equal(h.window.__PAE_DEBUG.ann_total,10);
});
test('old generation callback cannot consume the new page allowance',()=>{
  const h=harness(8);h.advance(1000);h.toggle(false);h.toggle(true);h.advance(1000);
  assert.equal(h.calls.length,2);h.reply(1,8);h.reply(0,8);
  assert.equal(h.window.__PAE_DEBUG.ann_total,8);
});
test('short page is allowed to finish below its configurable cap',()=>{
  const h=harness(40);h.advance(1000);h.reply(0,3);h.advance(1000);
  assert.equal(h.calls.length,1);assert.equal(h.window.__PAE_DEBUG.ann_total,3);
});
test('annotation diagnostics stay in the isolated script after a successful response',()=>{
  const h=harness();
  const dataset=h.document.documentElement.dataset;
  assert.equal(Object.hasOwn(dataset,'paeDebug'),false,'preparing state must not reach the DOM');
  h.advance(1000);
  h.reply(0,1);
  assert.equal(h.window.__PAE_DEBUG.lastResponse[0].lemma,'fixture');
  assert.equal(Object.hasOwn(dataset,'paeDebug'),false,'the response must not reach the DOM');
});
