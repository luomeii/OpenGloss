'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../extension/sw.js'),'utf8');
function harness(){
  let listener,changed,release;
  const calls=[],requests=[];
  const h={enabled:true,current:{documentToken:'doc-a',revision:1,pageId:'https://fixture.invalid/',enabled:true}};
  const chrome={storage:{local:{get(d,cb){cb({...d,paeEnabled:h.enabled});}},onChanged:{addListener(fn){changed=fn;}}},
    runtime:{getManifest:()=>({version:'fixture'}),onMessage:{addListener(fn){listener=fn;}}},
    tabs:{sendMessage(id,msg,options,cb){if(typeof options==='function')cb=options;cb(h.current);},onRemoved:{addListener(){}}},
    scripting:{async executeScript(options){calls.push(options.func.name);return [{result:{rendered:options.args&&Array.isArray(options.args[0])?options.args[0].length:0}}];}}};
  const sandbox={chrome,URL,console,setTimeout,clearTimeout,fetch:async(url,options)=>{
    if(url.endsWith('/v1/annotate')){requests.push(JSON.parse(options.body));return new Promise(resolve=>{release=data=>resolve({ok:true,json:async()=>data});});}
    return {ok:true,json:async()=>({result:{name:'fixture'}})};
  }};
  vm.runInNewContext(source,sandbox);
  h.send=message=>new Promise(resolve=>{if(listener(message,{tab:{id:7},frameId:0},resolve)!==true)resolve({ok:false,error:'unhandled'});});
  h.state=async()=>{const result=await h.send({type:'PAE_ANNOTATION_STATE',...h.current,clear:true});assert.equal(result.ok,true,'render state handshake required');};
  h.annotate=(limit=8)=>h.send({type:'PAE_ANNOTATE',text:'fixture',pageId:h.current.pageId,
    documentToken:h.current.documentToken,revision:h.current.revision,maxAnnotations:limit});
  h.flush=()=>new Promise(resolve=>setImmediate(resolve));
  h.release=data=>release(data);h.requests=requests;h.calls=calls;
  h.forgetState=()=>sandbox.paeAnnotationStates.clear();
  h.disable=()=>{h.enabled=false;changed({paeEnabled:{newValue:false}},'local');};
  return h;
}
const annotations=n=>Array.from({length:n},(_,i)=>({lemma:'fixture'+i,surface:'fixture'+i,gloss:'test'}));
test('remaining page budget is forwarded before painting',async()=>{
  const h=harness();await h.state();const result=h.annotate(2);await h.flush();
  assert.equal(h.requests[0].max_annotations,2);h.release(annotations(2));assert.equal((await result).ok,true);
  assert.equal(h.calls.filter(name=>name==='paeRegisterHighlights').length,1);
});
test('disabled old response cannot paint after completed clear',async()=>{
  const h=harness();await h.state();const result=h.annotate();await h.flush();
  h.disable();h.current={...h.current,revision:2,enabled:false};await h.state();
  h.release(annotations(8));assert.equal((await result).ok,false);
  assert.equal(h.calls.filter(name=>name==='paeRegisterHighlights').length,0);
});
test('reenabling does not revive an older generation',async()=>{
  const h=harness();await h.state();const result=h.annotate();await h.flush();
  h.disable();h.current={...h.current,revision:2,enabled:false};await h.state();
  h.enabled=true;h.current={...h.current,revision:3,enabled:true};await h.state();
  h.release(annotations(8));assert.equal((await result).ok,false);
  assert.equal(h.calls.filter(name=>name==='paeRegisterHighlights').length,0);
});
test('backend ignoring remaining budget is refused before painting',async()=>{
  const h=harness();await h.state();const result=h.annotate(2);await h.flush();
  h.release(annotations(8));const reply=await result;assert.equal(reply.ok,false);assert.match(reply.error,/budget/);
  assert.equal(h.calls.filter(name=>name==='paeRegisterHighlights').length,0);
});
test('a response from the previous document cannot paint a new document',async()=>{
  const h=harness();await h.state();const result=h.annotate();await h.flush();
  h.current={...h.current,documentToken:'doc-b',revision:1};await h.state();
  h.release(annotations(8));assert.equal((await result).ok,false);
  assert.equal(h.calls.filter(name=>name==='paeRegisterHighlights').length,0);
});
test('lost worker scheduling state is rebuilt only from the current content script',async()=>{
  const h=harness();await h.state();h.forgetState();
  const result=h.annotate(2);await h.flush();h.release(annotations(2));
  assert.equal((await result).ok,true);
  assert.ok(h.calls.includes('paeSetAnnotationGate'));
});
