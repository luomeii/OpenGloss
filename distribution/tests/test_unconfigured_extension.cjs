'use strict';
const assert=require('assert');const {environment,element,tick}=require('./test_extension_secure.cjs');
const cases=[],failed=[];async function test(name,fn){try{await fn();cases.push(name)}catch(error){failed.push({name,error:String(error.stack||error)})}}
(async()=>{
 await test('fresh worker annotates without reading a connection secret',async()=>{
  const e=environment('sw.js',{store:{paeToken:'',paeBase:'http://127.0.0.1:1'},fetch:async()=>({ok:true,status:200,json:async()=>[{lemma:'vessel',surface:'vessel',gloss:'synthetic'}]})});
  const r=await e.request({type:'PAE_ANNOTATE',text:'The vessel crosses the ocean.',pageId:'https://fixture.invalid/article',generation:1,max_annotations:8},{id:e.chrome.runtime.id,url:'https://fixture.invalid/article',tab:{id:1},frameId:0});
  assert(r.ok);assert.equal(r.data[0].lemma,'vessel');assert.equal(e.calls.length,1);assert(e.calls[0].url.startsWith('http://127.0.0.1:4815/'));assert(!e.calls[0].init.headers?.Authorization);
 });
 await test('fresh popup works without address or pairing form',async()=>{const e=environment('popup.js',{store:{paeToken:'',paeBase:'http://127.0.0.1:1'}});await tick();assert(e.calls.length);assert(e.calls.every(c=>c.url.startsWith('http://127.0.0.1:4815/')));assert(!e.calls.some(c=>c.init?.headers?.Authorization));assert(/就绪/.test(e.ids.eng.textContent));assert(e.ids['open-manager'].href.endsWith(':4816/manage'));});
 await test('sidebar opens directly from the page capsule',async()=>{const e=environment('companion.js');await tick();e.ids.cap.dispatch('click',{isTrusted:true});await tick();assert(e.messages.some(m=>m.type==='PAE_UI_SIZE'&&m.mode==='sidebar'));});
 await test('temporary annotation failure is retried on the same page',async()=>{
  let attempts=0;const e=environment('content.js',{delay:true,message:(m,cb)=>{if(m.type==='PAE_ANNOTATE')cb(++attempts===1?{ok:false,error:'not ready'}:{ok:true,data:[]});else cb({ok:true,limits:{max_ann_per_page:8}})}});
  const p=element('p');p.textContent='The vessel crosses the synthetic ocean today.';e.nodes.push({nodeType:3,textContent:p.textContent,parentElement:p,isConnected:true});e.release();await e.flush();assert(attempts>=2,'same queued text was lost after failure');
 });
 await test('page scripts cannot request private history',async()=>{const e=environment('sw.js');const r=await e.request({type:'PAE_CHAR_HIST'},{id:e.chrome.runtime.id,url:'https://fixture.invalid/article',tab:{id:1},frameId:0});assert.equal(r.ok,false);assert.equal(e.calls.length,0);});
 console.log(JSON.stringify({ok:failed.length===0,count:cases.length,executed_count:cases.length+failed.length,failed_count:failed.length,cases,failed,real_browser:false}));if(failed.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1});
