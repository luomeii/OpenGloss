'use strict';
(() => {
  const url = new URL(location.href);
  if (!/^https?:$/.test(url.protocol) || /^(localhost|127(?:\.\d+){3}|\[::1\])$/i.test(url.hostname) || window.top !== window) return;
  const SKIP='script,style,noscript,code,pre,kbd,samp,textarea,input,button,select,nav,footer,header,aside';
  const session=(()=>{try{let id=sessionStorage.getItem('pae_session');if(!id){id='s'+Date.now().toString(36)+Math.random().toString(36).slice(2);sessionStorage.setItem('pae_session',id);}return id;}catch{return 's'+Date.now().toString(36)+Math.random().toString(36).slice(2);}})();
  const seen=new Set(), queue=[], rendered=[], counted=new Set(), exposed=new Set(), dirty=new Set();
  let enabled=false, ready=false, generation=0, cap=40, total=0, runningGeneration=null, timer=null, route=pageId();
  let visibleStart=document.visibilityState==='visible'?Date.now():null, visibleTotal=0;
  let shell,frame,hoverRoot,card,hoverWord,hoverGloss,known,status,current=null;
  const send=message=>new Promise(resolve=>{
    try{chrome.runtime.sendMessage(message,response=>resolve(chrome.runtime.lastError?{ok:false,error:'扩展连接中断'}:response || {ok:false,error:'引擎未响应'}));}
    catch{resolve({ok:false,error:'扩展连接中断'});}
  });
  function pageId(){const u=new URL(location.href);return (u.origin+u.pathname+u.search+(/^#!?\//.test(u.hash)?u.hash:'')).slice(0,200);}
  function activeMs(){return Math.max(0,Math.round(visibleTotal+(visibleStart===null?0:Date.now()-visibleStart)));}
  document.addEventListener('visibilitychange',()=>{
    if(visibleStart!==null)visibleTotal+=Date.now()-visibleStart;
    visibleStart=document.visibilityState==='visible'?Date.now():null;
  });
  function shellInit(){
    shell=document.createElement('div');shell.style.cssText='position:fixed;right:18px;bottom:18px;width:180px;height:52px;z-index:2147483646;border:0;';
    const shadow=shell.attachShadow({mode:'closed'});frame=document.createElement('iframe');frame.src=chrome.runtime.getURL('companion.html');frame.title='OpenGloss 私密对话';frame.style.cssText='width:100%;height:100%;border:0;background:transparent;color-scheme:normal';shadow.appendChild(frame);document.documentElement.appendChild(shell);
    const host=document.createElement('div');host.style.cssText='position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647';hoverRoot=host.attachShadow({mode:'closed'});card=document.createElement('div');card.style.cssText='display:none;position:fixed;width:250px;padding:12px;background:#fff9e9;color:#332818;border:1px solid #cdb584;border-radius:11px;box-shadow:0 8px 24px #4322;font:13px/1.5 system-ui';
    hoverWord=document.createElement('strong');hoverGloss=document.createElement('p');known=document.createElement('button');known.textContent='认识了';status=document.createElement('span');card.append(hoverWord,hoverGloss,known,status);hoverRoot.appendChild(card);document.documentElement.appendChild(host);
    known.addEventListener('click',async event=>{
      if(!event.isTrusted||known.disabled||!current)return;
      const selected=current,token=generation;known.disabled=true;status.textContent='正在确认…';
      const response=await eventSend('known_click',selected);
      if(token!==generation||selected!==current)return;
      const data=response.data?.result || response.data || {};
      if(response.ok&&data.counted===true){status.textContent='已记下';}
      else if(response.ok&&data.reason==='duplicate_bucket'){status.textContent='本时段已记录';}
      else if(response.ok&&data.counted===false){status.textContent='本次未计入：'+(data.reason==='daily_cap'?'今日限额':'资格条件未满足');known.disabled=false;}
      else {status.textContent=response.code==='known_click_daily_limit'||/429|限额|daily_cap/.test(response.error||'')?'今日限额已到':'未确认，点击重试';known.disabled=false;}
    });
    document.addEventListener('mousemove',event=>{
      if(!event.isTrusted||!enabled)return;
      let point;
      try{point=document.caretRangeFromPoint?.(event.clientX,event.clientY);if(!point&&document.caretPositionFromPoint){const p=document.caretPositionFromPoint(event.clientX,event.clientY);if(p)point={startContainer:p.offsetNode,startOffset:p.offset};}}catch{return;}
      const found=point&&rendered.find(entry=>entry.range.startContainer===point.startContainer&&point.startOffset>=entry.range.startOffset&&point.startOffset<entry.range.endOffset);
      if(!found)return;
      if(current===found)return;current=found;known.disabled=false;status.textContent='';hoverWord.textContent=found.surface;hoverGloss.textContent=found.gloss;
      const rect=found.range.getBoundingClientRect();card.style.left=Math.max(8,Math.min(rect.left,innerWidth-280))+'px';card.style.top=Math.max(8,Math.min(rect.bottom+8,innerHeight-150))+'px';card.style.display='block';eventSend('hover',found);
    },true);
    card.addEventListener('mouseleave',event=>{if(event.isTrusted){card.style.display='none';current=null;}});
  }
  function eventSend(type,item){return send({type:'PAE_EVENT',sessionId:session,event:{type,lemma:item.lemma,surface:item.surface,s_value:item.s_value,page_id:route,page_active_ms:activeMs(),sentence:item.range?.startContainer.parentElement?.textContent?.slice(0,300)}});}
  function blockOf(element){let block=element;for(let i=0;i<4&&block?.parentElement;i++){if(/^(P|DIV|LI|TD|TABLE|SECTION|ARTICLE)$/.test(block.tagName))break;block=block.parentElement;}return block;}
  function collect(){
    if(!ready||!enabled)return;
    checkRoute();if(total>=cap)return;
    const blocks=new Map(),root=document.body||document.documentElement,walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    let node,visited=0,chars=0;
    while((node=walker.nextNode())&&visited++<20000){
      const el=node.parentElement;if(!el||el.closest(SKIP))continue;
      const block=blockOf(el);if(!block)continue;
      if(dirty.size&&!dirty.has(block))continue;
      const text=(node.textContent||'').trim();if(!text)continue;
      if(!blocks.has(block))blocks.set(block,[]);blocks.get(block).push(text);chars+=text.length;if(chars>=50000)break;
    }
    dirty.clear();
    const docHeight=Math.max(document.documentElement.scrollHeight,1);
    for(const [block,parts] of blocks){
      const text=parts.join(' ').slice(0,5000);if(text.length<20||seen.has(text))continue;
      const words=text.split(/\s+/).length,links=block.querySelectorAll('a').length;
      if(links>=2&&links/words>0.34)continue;
      const rect=block.getBoundingClientRect(),top=rect.top+scrollY;
      if(top<docHeight*0.05||top>docHeight*0.9||!rect.width||!rect.height)continue;
      seen.add(text);for(let offset=0;offset<text.length;offset+=1400)queue.push(text.slice(offset,offset+1400));
    }
    drain();
  }
  function findRange(annotation){
    const walker=document.createTreeWalker(document.body||document.documentElement,NodeFilter.SHOW_TEXT);let node,visited=0;
    while((node=walker.nextNode())&&visited++<20000){
      if(!node.parentElement||node.parentElement.closest(SKIP))continue;
      const text=node.textContent||'';let offset=-1,cursor=0;
      while((offset=text.indexOf(annotation.surface,cursor))>=0){
        if(!/[\p{L}\p{N}_]/u.test(text.slice(0,offset).slice(-1))&&!/[\p{L}\p{N}_]/u.test(text[offset+annotation.surface.length]||''))break;cursor=offset+1;
      }
      if(offset<0)continue;
      const range=document.createRange();range.setStart(node,offset);range.setEnd(node,offset+annotation.surface.length);return range;
    }
    return null;
  }
  async function drain(){
    if(runningGeneration===generation||!enabled)return;const token=generation;runningGeneration=token;
    try{while(enabled&&token===generation&&queue.length&&total<cap){
      const remaining=cap-total,text=queue.shift();
      const response=await send({type:'PAE_ANNOTATE',text,pageId:route,sessionId:session,max_annotations:remaining,generation:token});
      if(token!==generation||!enabled||pageId()!==route)break;
      if(!response.ok)continue;
      const entries=[];
      for(const annotation of (Array.isArray(response.data)?response.data:[]).slice(0,remaining)){
        if(counted.has(annotation.lemma))continue;
        const range=findRange(annotation);if(range)entries.push({...annotation,range});
      }
      if(!entries.length)continue;
      const draw=await send({type:'PAE_RENDER',generation:token,render_ids:entries.map(e=>e.render_id)});
      if(token!==generation||!enabled||pageId()!==route)break;
      const added=new Set(draw.ok&&Array.isArray(draw.rendered?.added_indices)?draw.rendered.added_indices.filter(i=>Number.isSafeInteger(i)&&i>=0&&i<entries.length):[]);
      const visible=entries.filter((entry,index)=>added.has(index));rendered.push(...visible);total+=visible.length;
      for(const entry of visible)counted.add(entry.lemma);
      // Visible document time is only a page-level proxy, not gaze/element dwell.
      if(document.visibilityState==='visible'){const fresh=visible.filter(entry=>!exposed.has(entry.lemma));if(fresh.length){for(const entry of fresh)exposed.add(entry.lemma);send({type:'PAE_EVENT',sessionId:session,event:{type:'annotation_shown',lemmas:fresh.map(e=>e.lemma),page_id:route,page_active_ms:activeMs()}});}}
    }}finally{if(runningGeneration===token){runningGeneration=null;if(enabled&&token===generation&&queue.length&&total<cap)schedule();}}
  }
  function schedule(){if(timer!==null)return;timer=setTimeout(()=>{timer=null;collect();},150);}
  async function reset(){
    const token=++generation;runningGeneration=null;queue.length=0;seen.clear();counted.clear();rendered.length=0;dirty.clear();total=0;current=null;if(card)card.style.display='none';
    await send({type:'PAE_CLEAR',generation:token});if(token!==generation)return;
    if(enabled){const limits=await send({type:'PAE_GET_LIMITS'});if(token!==generation)return;if(limits.ok&&Number.isSafeInteger(limits.limits?.max_ann_per_page))cap=Math.max(0,Math.min(400,limits.limits.max_ann_per_page));schedule();}
  }
  function checkRoute(){const next=pageId();if(next!==route){route=next;exposed.clear();visibleTotal=0;visibleStart=document.visibilityState==='visible'?Date.now():null;reset();}}
  chrome.storage.onChanged.addListener((changes,area)=>{if(area==='local'&&ready&&(changes.paeEnabled||changes.paeBase||changes.paeToken)){if(changes.paeEnabled)enabled=changes.paeEnabled.newValue!==false;if(changes.paeBase||changes.paeToken)exposed.clear();reset();}});
  chrome.runtime.onMessage.addListener((message,sender)=>{
    if(sender.id!==chrome.runtime.id)return;
    if(message.type==='PAE_PARAMS_CHANGED'&&enabled)reset();
    if(message.type==='PAE_UI_SIZE'&&shell){const sizes={collapsed:['180px','52px'],panel:['320px','220px'],sidebar:['min(420px, calc(100vw - 24px))','min(760px, calc(100vh - 36px))']};const size=sizes[message.mode];if(size){shell.style.width=size[0];shell.style.height=size[1];}}
  });
  chrome.storage.local.get({paeEnabled:true},async settings=>{
    if(chrome.runtime.lastError)return;ready=true;enabled=settings.paeEnabled!==false;shellInit();
    const observer=new MutationObserver(records=>{for(const record of records){const block=blockOf(record.target?.nodeType===3?record.target.parentElement:record.target);if(block)dirty.add(block);if(record.type==='childList')dirty.clear();}schedule();});
    observer.observe(document.documentElement,{childList:true,subtree:true,characterData:true});
    window.addEventListener('popstate',checkRoute);window.addEventListener('hashchange',checkRoute);setInterval(checkRoute,700);
    if(enabled)await reset();
  });
})();