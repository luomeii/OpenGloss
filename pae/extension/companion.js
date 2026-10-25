'use strict';
(() => {
  const $=id=>document.getElementById(id), turns=new Map();
  let locals=[],sequence=0,historyGeneration=0,statsGeneration=0,sessionGeneration=0,busy=false,muted=false,mode='collapsed',night=false;
  const send=message=>new Promise(resolve=>{try{chrome.runtime.sendMessage(message,r=>resolve(chrome.runtime.lastError?{ok:false}:r||{ok:false}));}catch{resolve({ok:false});}});
  function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=String(text);return e;}
  function trusted(id,type,fn){$(id).addEventListener(type,event=>{if(event.isTrusted)fn(event);});}
  function resize(next){mode=next;$('panel').style.display=next==='panel'?'block':'none';$('side').style.display=next==='sidebar'?'flex':'none';send({type:'PAE_UI_SIZE',mode:next});if(next==='collapsed')$('cap').focus();}
  async function hello(){const epoch=sessionGeneration;const r=await send({type:'PAE_CHAR_HELLO'});if(epoch!==sessionGeneration)return;if(r.ok&&r.name){for(const id of ['sidename','who','mwho'])$(id).textContent=r.name;$('sidestate').textContent='本机服务已连接';}else $('sidestate').textContent='服务未就绪，请双击 quick_start.bat';}
  function pct(value){if(typeof value!=='number'||!Number.isFinite(value)||value<=0)return '—';return value<0.01?(value*100).toFixed(1)+'%':Math.round(value*100)+'%';}
  function ring(value,small,label){const v=Number.isFinite(value)?Math.max(0,Math.min(1,value)):0,e=node('div','ring'+(small?' sm':''));e.style.background='conic-gradient(hsl('+(38+v*120)+' 80% 55%) '+Math.round((v>0?Math.max(v,0.03):0)*360)+'deg, var(--pae-line) 0)';e.appendChild(node('span','',label==null?Math.round(v*100):label));return e;}
  function delta(value){return node('span','delta'+(Number.isFinite(value)&&value<0?' neg':''),Number.isFinite(value)?(value>=0?'+':'')+value.toFixed(2):'—');}
  function paintStats(data){
    const metrics=[];for(const m of (data.metrics||[]).slice(0,8)){const row=node('div','row'),word=node('div','w',m.label);word.appendChild(node('span','g',m.note));row.append(ring(m.value,false,pct(m.value)),word,delta(m.delta));metrics.push(row);} $('sidestats').replaceChildren(...metrics);
    const words=[];for(const w of (data.words||[]).slice(0,8)){const row=node('div','row'),word=node('div','w',w.lemma);word.appendChild(node('span','g',w.gloss));row.append(ring(w.s_value,true),word,delta(w.delta));words.push(row);} $('sidewords').replaceChildren(...(words.length?words:[node('div','row empty','还没攒到词——翻几页英文就来')]));
    const stamps=[];for(const s of (data.stamps||[]).slice(0,8)){const e=node('span','stamp '+(['pos','neg'].includes(s.cls)?s.cls:''),s.text);e.setAttribute('data-tag',s.tag||'记忆');stamps.push(e);} $('sidestamps').replaceChildren(...(stamps.length?stamps:[node('span','stamp','它还没记下什么')]));
  }
  function paintHistory(){
    const all=new Map(turns);for(const entry of locals)all.set(entry.turn_id||entry.local_id,entry);
    const entries=[...all.values()].sort((a,b)=>(a.ts||0)-(b.ts||0)).slice(-100),children=[];
    for(const entry of entries){
      const e=node('div','turn '+(entry.role==='user'?'u':'a'),entry.text);
      const label=entry.state==='pending'?'发送中…':entry.state==='failed'?'未确认保存 · 可重试':entry.state==='sync'?'待同步':'';
      if(label)e.appendChild(node('span','t',label));
      if(entry.state==='failed'&&entry.role==='user'){const retry=node('button','','重试');retry.addEventListener('click',event=>{if(event.isTrusted&&!busy)ask(entry.text,entry);});e.appendChild(retry);}
      if(String(entry.text).length>120){e.classList.add('clamp');e.title='点击展开全文';e.addEventListener('click',event=>{if(event.isTrusted)e.classList.toggle('exp');});}children.push(e);
    }
    $('chatbody').replaceChildren(...children);$('chatcount').textContent='显示最近 '+entries.length+' 条（最多100条；历史读取最近60条）';$('chatbody').scrollTop=$('chatbody').scrollHeight;
  }
  async function history(){
    const generation=++historyGeneration,epoch=sessionGeneration;const r=await send({type:'PAE_CHAR_HIST'});
    if(generation!==historyGeneration||epoch!==sessionGeneration)return;
    if(!r.ok){$('sidestate').textContent='历史更新失败，保留当前内容';return;}
    const incoming=new Map();for(const t of (Array.isArray(r.turns)?r.turns:[]).slice(-60))if(typeof t.turn_id==='string'&&typeof t.text==='string')incoming.set(t.turn_id,{...t,state:'saved'});
    turns.clear();for(const [id,t] of incoming)turns.set(id,t);
    locals=locals.filter(t=>!t.turn_id||!incoming.has(t.turn_id)).slice(-40);paintHistory();
  }
  async function stats(){const generation=++statsGeneration,epoch=sessionGeneration;const r=await send({type:'PAE_CHAR_STATS'});if(generation!==statsGeneration||epoch!==sessionGeneration)return;if(r.ok&&r.panel)paintStats(r.panel);else $('sidestate').textContent='统计更新失败，保留上次数据';}
  function refresh(){history();stats();}
  async function ask(text,retry){
    text=String(text||'').trim();if(!text||text.length>4000||busy)return;
    busy=true;const epoch=sessionGeneration;const user=retry||{local_id:'local-'+(++sequence),role:'user',text,ts:Date.now()/1000};user.state='pending';if(!retry)locals.push(user);locals=locals.slice(-40);paintHistory();$('sidesend').disabled=$('send').disabled=true;
    const r=await send({type:'PAE_CHAR_SAY',text});if(epoch!==sessionGeneration)return;
    busy=false;$('sidesend').disabled=$('send').disabled=false;
    if(!r.ok){user.state='failed';paintHistory();$('sidestate').textContent='请求失败，保存状态未确认';return;}
    user.turn_id=r.user_turn_id;user.state=r.user_turn_id?'saved':'sync';
    if(typeof r.reply==='string'&&r.reply)locals.push({local_id:'local-'+(++sequence),turn_id:r.agent_turn_id,role:'agent',text:r.reply,ts:Date.now()/1000,state:r.agent_turn_id?'saved':'sync'});
    locals=locals.slice(-40);paintHistory();history();stats();
  }
  function submit(id){const text=$(id).value;if(!text.trim()||busy)return;if(text.trim().length>4000){$('sidestate').textContent='消息最多4000字，请缩短后重试';return;}$(id).value='';ask(text);}
  trusted('cap','click',()=>{resize(mode==='collapsed'?'sidebar':'collapsed');if(mode!=='collapsed'){hello();refresh();}});
  trusted('sidebtn','click',()=>{resize('sidebar');hello();refresh();$('sidein').focus();});
  trusted('sideclose','click',()=>resize('collapsed'));
  trusted('sidereload','click',refresh);
  trusted('sidesend','click',()=>submit('sidein'));trusted('send','click',()=>submit('in'));
  for(const id of ['sidein','in'])trusted(id,'keydown',event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault?.();submit(id);}});
  trusted('mute','click',async()=>{const epoch=sessionGeneration;const r=await send({type:'PAE_SET_MUTE',muted:!muted});if(epoch!==sessionGeneration)return;if(r.ok){muted=!!r.muted;$('mute').textContent=muted?'取消静音':'静音';}else $('sidestate').textContent='静音设置未保存';});
  trusted('themebtn','click',()=>{night=!night;$('paeroot').classList.toggle('theme-night',night);$('themebtn').textContent=night?'羊皮纸':'暗夜';});
  trusted('chathd','click',()=>$('chatbox').classList.toggle('open'));
  window.addEventListener('message',event=>{if(event.source===window.parent&&event.data?.type==='OpenGloss-open-sidebar'){resize('sidebar');hello();refresh();}});
  chrome.runtime.onMessage.addListener((message,sender)=>{
    if(sender.id!==chrome.runtime.id)return;
    if(message.type==='PAE_OPEN_SIDEBAR'){resize('sidebar');hello();refresh();return;}
    if(message.type!=='PAE_PRIVATE_PUSH'||muted)return;
    const event=message.event||{};if(event.type==='say'){
      // Proactive text stays within the extension-origin frame. Its anchor label
      // is shown here; no private message is copied into the host page.
      $('btext').textContent=String(event.text||'');$('bubble').style.display=mode==='collapsed'?'none':'block';$('sidestate').textContent=event.anchor?'相关段落：'+String(event.anchor).slice(0,80):'有新消息';if(mode==='sidebar')history();
    }else if(event.type==='param_changed'){$('sidestate').textContent='标注参数已更新';if(mode==='sidebar')stats();}
  });
  $('capname').textContent='OpenGloss · 侧栏';$('sidename').textContent='OpenGloss';$('cap').title='打开阅读侧栏';$('cap').setAttribute('aria-label','打开 OpenGloss 阅读侧栏');$('sidever').textContent='v'+chrome.runtime.getManifest().version;
  $('panel').style.display=$('side').style.display='none';paintHistory();
})();
