'use strict';
// Synthetic DOM, extension transport and clock only. No browser, socket or AI.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = process.argv[2] || path.resolve(__dirname, '../..');
const workerSource = fs.readFileSync(path.join(root, 'pae/extension/sw.js'), 'utf8');
const contentSource = fs.readFileSync(path.join(root, 'pae/extension/content.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const words = n => Array.from({length:n}, (_,i) => 'fixture' + String.fromCharCode(97+Math.floor(i/26)) + String.fromCharCode(97+i%26));
const rows = names => names.map(lemma => ({lemma, surface:lemma, gloss:'合成释义', s_value:0.1}));
const state = () => ({documentToken:'dfixture000000000', revision:1, pageId:'https://fixture.invalid/reading', enabled:true});
const sender = () => ({id:'a'.repeat(32),tab:{id:7},frameId:0,documentId:'synthetic-document',documentLifecycle:'active'});

function dom(names=words(1)) {
  const handlers=new Map(), documentHandlers=new Map(), mutations=[];
  const add=(map,type,fn)=>{if(!map.has(type))map.set(type,new Set());map.get(type).add(fn);};
  const remove=(map,type,fn)=>map.get(type)?.delete(fn);
  const h={geometry:0, visible:true, hit:true, rect:{left:10,right:500,top:500,bottom:520}};
  const element={tagName:'P',closest(){return null;},querySelectorAll(){return [];},
    getBoundingClientRect(){return h.rect;},checkVisibility(){return h.visible;},contains(){return false;}};
  const node={nodeType:3,isConnected:true,textContent:'A synthetic paragraph contains '+names.join(' ')+' for testing.',parentElement:element};
  const nodes=[node];
  const document={readyState:'complete',visibilityState:'visible',documentElement:{dataset:{},scrollHeight:5000},body:{},
    getElementById(){return {};},querySelectorAll(){return [];},
    createTreeWalker(){let i=0;return{nextNode(){return nodes[i++]||null;}};},
    createRange(){return{setStart(n,i){this.startContainer=n;this.startOffset=i;},setEnd(n,i){this.endContainer=n;this.endOffset=i;},
      get collapsed(){return this.startOffset===this.endOffset;},toString(){return this.startContainer.textContent.slice(this.startOffset,this.endOffset);},
      getClientRects(){h.geometry++;return[h.rect];}};},
    elementFromPoint(){return h.hit?element:null;},
    addEventListener(type,fn){add(documentHandlers,type,fn);},removeEventListener(type,fn){remove(documentHandlers,type,fn);}};
  const window={scrollY:0,addEventListener(type,fn){add(handlers,type,fn);},removeEventListener(type,fn){remove(handlers,type,fn);}};
  element.textContent=node.textContent;
  return Object.assign(h,{node,nodes,document,window,mutations,
    fire(type,data={}){for(const fn of [...(handlers.get(type)||[])])fn(data);},
    docFire(type){for(const fn of [...(documentHandlers.get(type)||[])])fn();},
    mutate(){for(const item of [...mutations])if(item.active)item.fn();},
    MutationObserver:class{constructor(fn){this.fn=fn;this.active=false;mutations.push(this);}observe(){this.active=true;}disconnect(){this.active=false;}}});
}

function renderer(d=dom(), gate=state()) {
  const posted=[], css={highlights:new Map()};
  class Highlight extends Set {constructor(...ranges){super(ranges);}}
  const window={CSS:css,Highlight,__paeAnnotationGate:gate,postMessage(message){posted.push(message);}};
  const ctx=vm.createContext({window,document:d.document,CSS:css,Highlight,NodeFilter:{SHOW_TEXT:4},Node:{TEXT_NODE:3},
    location:{href:gate.pageId},performance:{now:()=>500000}});
  const source=workerSource.match(/^function paeRegisterHighlights\(annotations, gate\) \{[\s\S]*?^\}/m)[0];
  return {posted,window,run(data){return plain(vm.runInContext('('+source+')('+JSON.stringify(data.map(a=>({lemma:a.lemma,surface:a.surface,drawable:!!a.gloss,band:'new'})))+','+JSON.stringify(gate)+')',ctx));}};
}

function content(names=words(1)) {
  const d=dom(names), messages=[], annotations=[], timers=new Map(), runtimeHandlers=[];
  let now=0,next=1,changed;
  const runtime={id:'a'.repeat(32),onMessage:{addListener(fn){runtimeHandlers.push(fn);}},sendMessage(message,reply){
    messages.push(plain(message));
    if(message.type==='PAE_ANNOTATE')annotations.push({message,reply});
    else if(message.type==='PAE_GET_LIMITS')reply({ok:true,limits:{max_ann_per_page:80}});
    else reply({ok:true});
  }};
  const location={href:state().pageId};
  vm.runInNewContext(contentSource,{window:d.window,document:d.document,location,chrome:{runtime,
    storage:{local:{get(defaults,reply){reply(defaults);}},onChanged:{addListener(fn){changed=fn;}}}},
    sessionStorage:{getItem:()=> 'synthetic-session'},NodeFilter:{SHOW_TEXT:4},Node:{TEXT_NODE:3},
    MutationObserver:d.MutationObserver,AbortController,innerWidth:1000,innerHeight:1000,
    performance:{now:()=>now},setTimeout(fn,ms){const id=next++;timers.set(id,{at:now+ms,fn,ms});return id;},
    clearTimeout(id){timers.delete(id);}});
  function advance(ms){const end=now+ms;let guard=0;while(true){const due=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
    if(!due)break;assert.ok(++guard<10000,'bounded synthetic timer work');timers.delete(due[0]);now=due[1].at;due[1].fn();}now=end;}
  function current(){let result;for(const fn of runtimeHandlers)fn({type:'PAE_ANNOTATION_PROBE'},sender(),v=>{result=v;});return result;}
  const h=Object.assign(d,{messages,annotations,advance,location,
    suspend(ms){now+=ms;for(const timer of timers.values())if(timer.at<now)timer.at=now;advance(0);},
    current,events:()=>messages.filter(m=>m.type==='PAE_VISIBILITY_EVENT'),
    observationTimers:()=>[...timers.values()].filter(t=>t.ms===200).length,
    toggle(enabled){changed({paeEnabled:{newValue:enabled}},'local');},
    post(data){d.fire('message',{source:d.window,data});},
    reply(data=rows(names),receipt){const job=annotations.at(-1);assert.ok(job,'annotation request exists');
      job.reply({ok:true,data,rendered:receipt||renderer(d,plain(job.message)).run(data)});},
    push(after){for(const fn of runtimeHandlers)fn({type:'PAE_PUSH',event:{type:'param_changed',after}},sender(),()=>{});}});
  advance(1000);
  return h;
}

function worker(names=words(3)) {
  const d=dom(names), requests=[], h={enabled:true,current:state(),data:rows(names),failEvents:false,drawOverride:null};
  let listener,changed;
  const render=renderer(d,h.current);
  const chrome={runtime:{id:'a'.repeat(32),onMessage:{addListener(fn){listener=fn;}}},
    storage:{local:{get(defaults,reply){reply({...defaults,paeEnabled:h.enabled});}},onChanged:{addListener(fn){changed=fn;}}},
    tabs:{onRemoved:{addListener(){}},sendMessage(id,msg,options,reply){reply(h.current);}},
    scripting:{async executeScript(spec){if(spec.func.name==='paeRegisterHighlights')return[{result:h.drawOverride||render.run(h.data)}];return[{result:{ok:true}}];}}};
  const ctx={chrome,URL,setTimeout(){},clearTimeout(){},fetch:async(url,options)=>{
    if(url.endsWith('/v1/annotate'))return{ok:true,json:async()=>h.data};
    assert.ok(url.endsWith('/v1/event'),'no unrelated request');
    requests.push(JSON.parse(options.body));
    if(h.failEvents)throw new Error('synthetic event failure');
    return{ok:true,json:async()=>({ok:true})};}};
  vm.runInNewContext(workerSource,ctx);
  return Object.assign(h,{requests,render,
    send(message,who=sender()){return new Promise(resolve=>{if(listener(message,who,resolve)!==true)resolve({ok:false});});},
    annotate(){return h.send({type:'PAE_ANNOTATE',...h.current,text:'synthetic',sessionId:'synthetic-session',maxAnnotations:80});},
    visible(lemma=names[0],extra={}){return h.send({type:'PAE_VISIBILITY_EVENT',...h.current,lemma,...extra});},
    disable(){h.enabled=false;changed({paeEnabled:{newValue:false}},'local');},
    forget(){ctx.paeAnnotationStates.clear();},
    async drain(){for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));}});
}

test('renderer returns only newly registered input locations and never posts observations',()=>{
  const h=renderer();const data=[...rows(words(1)),...rows(['fixturemissing']),{lemma:'fixtureglossless',surface:'fixtureglossless',gloss:''}];
  const result=h.run(data);assert.equal(result.added,1);assert.equal(h.posted.length,0);
  assert.ok(Array.isArray(result.drawn));assert.deepEqual(result.drawn.map(r=>r.index),[0]);
  assert.equal(result.drawn[0].nodeIndex,0);assert.equal(result.drawn[0].surface,words(1)[0]);
});
test('zero or duplicate drawing produces no new receipt',()=>{
  const h=renderer();assert.deepEqual(h.run(rows(['fixturemissing'])).drawn,[]);
  h.run(rows(words(1)));const second=h.run(rows(words(1)));assert.equal(second.added,0);assert.deepEqual(second.drawn,[]);
});
test('public page messages cannot create either observation event',()=>{
  const h=content();const gate=h.current();
  h.post({source:'pae',type:'visible_opportunity',...gate,lemma:words(1)[0],visible_ms:1000,observation_version:1});
  h.post({source:'pae',type:'annotation_shown',lemmas:words(1),page_id:'forged'});
  assert.equal(h.events().length,0);assert.equal(h.messages.filter(m=>m.type==='PAE_EVENT').length,0);
});
test('private visible sampling preserves the one second continuous interval and document dedupe',()=>{
  const h=content();h.reply();h.advance(1000);assert.equal(h.events().length,0);
  h.advance(200);assert.equal(h.events().length,1);assert.equal(h.events()[0].lemma,words(1)[0]);
  h.fire('scroll');h.advance(2000);assert.equal(h.events().length,1);
});
test('selected but not drawn words never acquire visible opportunities',()=>{
  const h=content();const data=[...rows(words(1)),...rows(['fixturemissing'])];h.reply(data);h.advance(1400);
  assert.deepEqual(h.events().map(e=>e.lemma),words(1));assert.equal(h.window.__PAE_DEBUG.ann_total,2,'selected page allowance is unchanged');
});
test('a moved text offset fails closed without changing the original word index',()=>{
  const h=content();const data=rows(words(1)),receipt=renderer(h,h.current()).run(data);
  h.node.textContent='prefix '+h.node.textContent;h.reply(data,receipt);h.advance(1600);assert.equal(h.events().length,0);
});

test('non-ASCII surfaces preserve the existing adjacent letter and number boundary check',()=>{
  for(const adjacent of ['é','8','']){
    const h=content(['mémoire']);
    h.node.textContent=h.node.textContent.replace('mémoire',adjacent+'mémoire'+adjacent);
    h.reply([{...rows(['fixturecanopy'])[0],surface:'mémoire'}]);h.advance(1200);
    assert.equal(h.events().length,adjacent?0:1,adjacent||'whole word');
  }
});
test('hidden page interrupts continuity and resumes with a fresh interval',()=>{
  const h=content();h.reply();h.advance(600);h.document.visibilityState='hidden';h.docFire('visibilitychange');
  h.advance(2000);assert.equal(h.events().length,0);assert.equal(h.observationTimers(),0);
  h.document.visibilityState='visible';h.docFire('visibilitychange');h.advance(1000);assert.equal(h.events().length,0);
  h.advance(200);assert.equal(h.events().length,1);
});
test('scrolling, occlusion, disconnection and page navigation do not count stale dwell',()=>{
  for(const kind of ['scroll','occlusion','disconnection','navigation']){
    const h=content();h.reply();h.advance(800);
    if(kind==='scroll')h.fire('scroll');
    if(kind==='occlusion')h.hit=false;
    if(kind==='disconnection')h.node.isConnected=false;
    if(kind==='navigation')h.location.href='https://fixture.invalid/next';
    h.advance(600);assert.equal(h.events().length,0,kind);
  }
});
test('a delayed sample cannot infer dwell through an unsampled gap',()=>{
  const h=content();h.reply();h.advance(800);h.suspend(600);h.advance(800);
  assert.equal(h.events().length,0);h.advance(200);assert.equal(h.events().length,1);
});
test('disable, new revision and pagehide dispose pending observations',()=>{
  for(const kind of ['disable','revision','pagehide']){
    const h=content();h.reply();h.advance(600);
    if(kind==='disable')h.toggle(false);
    if(kind==='revision')h.push({max_ann_per_page:40});
    if(kind==='pagehide')h.fire('pagehide');
    h.advance(1600);assert.equal(h.events().length,0,kind);assert.equal(h.observationTimers(),0,kind);
  }
});
test('reopening starts new sampling but never repeats a reported word in one document',()=>{
  const h=content();h.reply();h.advance(1200);assert.equal(h.events().length,1);
  h.toggle(false);h.toggle(true);h.advance(1000);h.reply();h.advance(1600);assert.equal(h.events().length,1);
});
test('sampling is bounded for 0, 40 and 80 ranges and repeated DOM mutations',()=>{
  for(const n of [0,40,80]){
    const names=words(n),h=content(names);h.reply(rows(names));
    for(let i=0;i<10;i++){h.mutate();h.advance(200);assert.ok(h.observationTimers()<=1);}
    assert.equal(h.events().length,0);h.advance(1200);assert.equal(h.events().length,n);
    assert.ok(h.geometry<=n*18,'bounded geometry checks');assert.equal(h.observationTimers(),0);
  }
});
test('worker records only newly drawn words and keeps the original annotate response',async()=>{
  const h=worker(words(1));h.data.push(...rows(['fixturemissing']));
  const reply=await h.annotate();assert.equal(reply.ok,true);assert.equal(reply.data.length,2);await h.drain();
  const events=h.requests.filter(r=>r.type==='annotation_shown');assert.deepEqual(events.map(e=>e.lemma),words(1));
  assert.equal(events[0].page_active_ms,500000);assert.equal(events[0].session_id,'synthetic-session');
  assert.equal((await h.annotate()).ok,true);await h.drain();assert.equal(h.requests.length,1);
});
test('malformed or duplicated renderer receipts cannot inflate shown observations',async()=>{
  for(const drawn of [[{index:500}], [{index:0},{index:0}], [{index:0,nodeIndex:-1,start:0,end:9,surface:words(1)[0]}]]){
    const h=worker(words(1));h.drawOverride={added:drawn.length,rendered:1,drawn};
    assert.equal((await h.annotate()).ok,true);await h.drain();assert.equal(h.requests.length,0);
  }
});
test('observation failure does not turn a successful annotation into failure',async()=>{
  const h=worker(words(1));h.failEvents=true;const result=await h.annotate();assert.equal(result.ok,true);
  await h.drain();assert.equal(h.requests.length,1);assert.equal(h.requests[0].type,'annotation_shown');
});
test('generic event forwarding refuses observations, preserves real learning feedback',async()=>{
  const h=worker();for(const type of ['annotation_shown','visible_opportunity']){
    const result=await h.send({type:'PAE_EVENT',event:{type,lemma:words(1)[0]}});assert.equal(result.ok,false);
  }
  assert.equal(h.requests.length,0);
  for(const type of ['hover','known_click'])assert.equal((await h.send({type:'PAE_EVENT',event:{type,lemma:words(1)[0]}})).ok,true);
  assert.deepEqual(h.requests.map(e=>e.type),['hover','known_click']);
});
test('visible observations require an active top document of this extension',async()=>{
  for(const patch of [{id:'other'},{frameId:1},{documentLifecycle:'prerender'},{documentId:undefined},{tab:undefined}]){
    const h=worker();const result=await h.send({type:'PAE_VISIBILITY_EVENT',...h.current,lemma:words(1)[0]}, {...sender(),...patch});
    assert.equal(result.ok,false);assert.equal(h.requests.length,0);
  }
});
test('visible worker state recovers after restart and rejects old revision or disabled state',async()=>{
  const h=worker();h.forget();assert.equal((await h.visible()).ok,true);
  assert.equal((await h.visible(words(1)[0],{revision:0})).ok,false);h.disable();assert.equal((await h.visible()).ok,false);
  assert.equal(h.requests.length,1);
});
