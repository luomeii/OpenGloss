'use strict';
const PAE_DEFAULT_BASE = 'http://127.0.0.1:4815';
const PAE_BASE = PAE_DEFAULT_BASE;
let PAE_PERSONA_NAME = null;
const PAE_STAT_PREV = {}, PAE_STAT_PREV_S = {}, PAE_PAGES = new Map();
let PAE_CONTROLLER = new AbortController();
const PAE_STREAM = {controller:null, retry:0, wanted:false};
const PAE_READY = Promise.resolve();
async function paeReadJson(response) {
  if (!response.ok) throw Error('HTTP ' + response.status);
  const j = await response.json();
  if (j?.ok === false || j?.result?.ok === false) { if (j.error === 'known_click_daily_limit') throw Object.assign(Error('今日认识了限额已到'),{code:'known_click_daily_limit'}); throw Error('请求被拒绝'); }
  return j;
}
async function paeFetch(url, init = {}) {
  await PAE_READY;
  // Callers pass only engine-relative paths; no redirect is followed.
  if (!url.startsWith('/v1/')) throw Error('拒绝非引擎请求');
  const response = await fetch(PAE_BASE + url,{...init,signal:init.signal || PAE_CONTROLLER.signal,credentials:'omit',redirect:'error'});
  return response;
}
async function paePost(path, body = {}) { return paeReadJson(await paeFetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})); }
function paePublicPage(value) {
  try { const u = new URL(value); return /^https?:$/.test(u.protocol) && !/^(localhost|127(?:\.\d+){3}|\[::1\])$/i.test(u.hostname); } catch { return false; }
}
function paeSenderKind(sender) {
  if (sender?.id !== chrome.runtime.id) return '';
  if (sender.url === chrome.runtime.getURL('companion.html')) return 'companion';
  if (sender.url === chrome.runtime.getURL('popup.html')) return 'popup';
  if (sender.tab && sender.frameId === 0 && paePublicPage(sender.url)) return 'content';
  return '';
}
function paeText(value, max, required = true) { if (typeof value !== 'string' || value.length > max || (required && !value.trim())) throw Error('文本字段无效'); return value; }
async function paeStartStream() {
  PAE_STREAM.wanted = true;
  await PAE_READY;
  if (PAE_STREAM.controller) return;
  const controller = new AbortController();
  PAE_STREAM.controller = controller;
  try {
    const response = await paeFetch('/v1/ext/stream',{signal:controller.signal});
    if (!response.ok || !response.body) throw Error('推送连接失败');
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
    PAE_STREAM.retry = 0;
    while (!controller.signal.aborted) {
      const part = await reader.read(); if (part.done) break;
      buffer += decoder.decode(part.value,{stream:true});
      if (buffer.length > 262144) throw Error('推送数据过长');
      const frames = buffer.replace(/\r\n/g,'\n').split('\n\n'); buffer = frames.pop();
      for (const frame of frames) for (const line of frame.split('\n')) if (line.startsWith('data:')) {
        try { const event = JSON.parse(line.slice(5)); paeBroadcast(event); } catch {}
      }
    }
  } catch {} finally {
    if (PAE_STREAM.controller === controller) {
      PAE_STREAM.controller = null;
      if (!controller.signal.aborted) chrome.alarms.create('pae-stream-retry',{delayInMinutes:Math.min(5,0.5 * 2 ** Math.min(4,PAE_STREAM.retry++))});
    }
  }
}
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'pae-stream-retry') paeStartStream(); });
function paeBroadcast(event) {
  if (!event || !['say','hint','param_changed'].includes(event.type)) return;
  chrome.runtime.sendMessage({type:'PAE_PRIVATE_PUSH',event}, () => { void chrome.runtime.lastError; });
  if (event.type === 'param_changed') chrome.tabs.query({}, tabs => {
    for (const tab of tabs) if (tab.id != null) chrome.tabs.sendMessage(tab.id,{type:'PAE_PARAMS_CHANGED',after:event.after || {}},()=>{void chrome.runtime.lastError;});
  });
}
async function paePersonaName() {
  if (PAE_PERSONA_NAME) return PAE_PERSONA_NAME;
  const j = await paePost('/v1/cap/pae.persona');
  PAE_PERSONA_NAME = j.result?.params?.name || '小P'; return PAE_PERSONA_NAME;
}
function paeFmtPct(v) {
  if (typeof v !== 'number' || !isFinite(v) || v <= 0) return '—';
  var p = v * 100;
  return (p >= 1) ? (Math.round(p) + '%') : ((Math.round(p * 10) / 10).toFixed(1) + '%');
}

// 反推样本分子：引擎把比率 round 到 3 位小数（metrics.py: round(k/n, 3)），
// 只有当 round(rate × n) 是**唯一整数解**（|k/n − rate| ≤ 0.0005）时才采信，
// 否则返回 null，调用方退回只报分母——宁可少显示，也不编造分子。
function paeSampleOf(rate, n) {
  if (typeof rate !== 'number' || !isFinite(rate) || !(n > 0)) return null;
  var k = Math.round(rate * n);
  return (Math.abs(k / n - rate) <= 0.0005) ? { k: k, n: n } : null;
}

async function paeStats() {
var o = {}, f = {}, ctx = {}, received = 0;
        try {
          var r1 = await paeFetch('/v1/cap/pae.outcomes', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: '{"evaluate_decisions":false}' });
          o = ((await paeReadJson(r1)).result) || {};
          received++;
        } catch (e) {}
        try {
          var r2 = await paeFetch('/v1/cap/pae.familiarity', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: '{"recompute":false}' });
          f = ((await paeReadJson(r2)).result) || {};
          received++;
        } catch (e) {}
        // 侧栏「最近在啃」+ 圆环指标：pae.context 一次拿 words（带 S 值）与 metrics（速率）
        try {
          var r3 = await paeFetch('/v1/cap/pae.context', { method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{"include":["words","metrics"],"top_n":8}' });
          ctx = ((await paeReadJson(r3)).result) || {};
          received++;
        } catch (e) {}
        // 印章卡数据：长期记忆 facts（真事实才显示）。
        // 2026-10-03 二次反馈修复：以前 facts 为空时拿「最近 4 条对话」冒充记忆——
        // 用户看到「它记下的」永远是 4 条、且每次聊天后被新对话顶掉，误以为数据丢失。
        // 现在没有事实就老老实实显示「它还没记下什么」；事实由聊天里的 [[记住]] 协议真写入。
        var facts = [];
        try {
          var r4 = await paeFetch('/v1/cap/pae.memory.search', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: '{"query":"","k":8}' });
          facts = ((await paeReadJson(r4)).result) || [];
          received++;
        } catch (e) {}
        if (received !== 4) throw new Error("引擎暂时不可用，统计未更新");
              var mm = ctx.metrics || {};
        var cohortN = Number(o.cohort_size || 0) || 0;
        var convN = Number(o.converted || 0) || 0;
        var engN = Number(o.engaged_not_converted || 0) || 0;
        // 样本量（分子/分母）：曝光数是引擎真值；分子由 round(rate × exposure) 反推，
        // 只有唯一整数解才采信（paeSampleOf 内做往返一致性校验）——宁可只报分母，不编造分子。
        var expN = Number(mm.annotation_exposure || 0) || 0;
        var hoverSample = paeSampleOf(mm.hover_rate, expN);
        var clickSample = paeSampleOf(mm.click_rate, expN);
        function sampleTxt(s, unit) {   // 形如「4 次点击 / 962 次曝光」
          if (s) return s.k + ' 次' + unit + ' / ' + s.n + ' 次曝光';
          return expN > 0 ? ('曝光 ' + expN + ' 次') : '还没有曝光';
        }
        var cohortShort = cohortN > 0 ? (cohortN + ' 个成熟词')
                                      : ('首遇满 7 天的词还不够（当前 ' + cohortN + ' 个成熟词）');
        // ---- 圆环指标：value（0..1）决定 conic-gradient 比例；delta 是上一次刷新的真实差 ----
        // note 里带上当前样本量（分子/分母），且样本量放**最前**：侧栏注文只有 ~200px 宽，
        // 放后面会被 ellipsis 截掉，用户就看不到分子分母了。
        var metrics = [
          { label: '转化率', value: o.conversion_rate,
            note: (cohortN > 0 ? (convN + ' 词 / ' + cohortN + ' 个成熟词') : cohortShort) +
                  ' · 成熟词里点过「认识了」的比例' },
          { label: '参与率', value: o.engagement_rate,
            note: (cohortN > 0 ? ((convN + engN) + ' 词 / ' + cohortN + ' 个成熟词') : cohortShort) +
                  ' · 悬停或点击过的比例' },
          { label: '悬停率', value: mm.hover_rate, note: sampleTxt(hoverSample, '悬停') + ' · 悬停事件/曝光事件，不代表读懂或记住' },
          { label: '点击率', value: mm.click_rate, note: sampleTxt(clickSample, '点击') + ' · 认识了点击事件/曝光事件' }
        ];
        for (var mi = 0; mi < metrics.length; mi++) {
          var mkey = metrics[mi].label, cur = metrics[mi].value;
          metrics[mi].delta = (typeof cur === 'number' && typeof PAE_STAT_PREV[mkey] === 'number')
            ? (cur - PAE_STAT_PREV[mkey]) : null;
          if (typeof cur === 'number') PAE_STAT_PREV[mkey] = cur;
        }
        // ---- 词行：最近在啃（top 词 + 各自 S 值 + delta）----
        var words = [];
        var arr = Array.isArray(ctx.words) ? ctx.words : [];
        for (var wi = 0; wi < arr.length; wi++) {
          var w = arr[wi] || {};
          var sv = (typeof w.s_value === 'number') ? w.s_value : null;
          var dv = (typeof sv === 'number' && typeof PAE_STAT_PREV_S[w.lemma] === 'number')
            ? (sv - PAE_STAT_PREV_S[w.lemma]) : null;
          if (typeof sv === 'number') PAE_STAT_PREV_S[w.lemma] = sv;
          words.push({ lemma: w.lemma || '', gloss: w.gloss || '', s_value: sv, delta: dv });
        }
        // ---- 印章卡：它记下的 ----
        var stamps = [];
        for (var fi = 0; fi < facts.length && fi < 8; fi++) {
          var fx = facts[fi] || {};
          var txt = String(fx.text || '').slice(0, 40);
          if (!txt) continue;
          var conf = (typeof fx.confidence === 'number') ? fx.confidence : 0.6;
          stamps.push({ text: txt, tag: String(fx.kind || '记忆').slice(0, 6),
                        cls: conf >= 0.7 ? 'pos' : (conf <= 0.35 ? 'neg' : '') });
        }
        // 主世界壳拿不到结构化对象（content.js 只转发 html 字符串，T25 起就是这么接的），
        // 所以把 panel 数据编进一个隐藏节点的属性里，壳侧解析后自己上样式。
        var payload = { metrics: metrics, words: words, stamps: stamps };
        return {ok:true,panel:payload};
}

// This serialized MAIN function only paints public annotation data. No messages,
// credentials, private history, inputs, or privileged callbacks enter this world.
function paeRegisterHighlights(annotations, generation) {
  if (!window.Highlight || !window.CSS?.highlights) return {added:0,rendered:0,reason:'no-highlight-api'};
  let state = window.__paeHL;
  if (!state || state.generation !== generation) {
    if (state && state.generation > generation) return {added:0,rendered:0,reason:'stale'};
    state = window.__paeHL = {generation,entries:[],nodeIds:new WeakMap(),nextId:1};
  }
  state.entries = state.entries.filter(e=>e.range.startContainer.isConnected);
  const nodes=[], walker=document.createTreeWalker(document.body || document.documentElement,NodeFilter.SHOW_TEXT);
  let node, scanned=0;
  while ((node=walker.nextNode()) && scanned++ < 20000) if(node.parentElement && !node.parentElement.closest('script,style,noscript,code,pre,kbd,samp,textarea,input,select,button,nav,footer,header,aside')) nodes.push(node);
  const have=new Set(state.entries.map(e=>e.key)); let added=0; const added_indices=[];
  for (const [annotationIndex,a] of annotations.slice(0,400).entries()) {
    if (!a || typeof a.surface !== 'string' || !a.surface || typeof a.gloss !== 'string' || !a.gloss) continue;
    for (const n of nodes) {
      const text=n.textContent || ''; let offset=-1, cursor=0;
      while ((offset=text.indexOf(a.surface,cursor))>=0) {
        const left=text.slice(0,offset).slice(-1), right=text[offset+a.surface.length] || '';
        if (!/[\p{L}\p{N}_]/u.test(left) && !/[\p{L}\p{N}_]/u.test(right)) break;
        cursor=offset+1;
      }
      if(offset<0)continue;
      if(!state.nodeIds.has(n))state.nodeIds.set(n,state.nextId++);
      const key=state.nodeIds.get(n)+':'+offset+':'+a.surface.length;
      if(have.has(key))break;
      const range=document.createRange();range.setStart(n,offset);range.setEnd(n,offset+a.surface.length);
      state.entries.push({key,range,band:['new','mid','high'].includes(a.band)?a.band:'new'});have.add(key);added++;added_indices.push(annotationIndex);break;
    }
  }
  state.entries=state.entries.slice(-400);
  if (!document.getElementById('pae-highlight-style')) {
    const style=document.createElement('style');style.id='pae-highlight-style';
    style.textContent='::highlight(pae-word){text-decoration:underline wavy rgba(217,119,6,.7);text-underline-offset:4px}::highlight(pae-word-new){background:rgba(255,214,102,.38)}::highlight(pae-word-mid){background:rgba(96,165,250,.32)}::highlight(pae-word-high){background:rgba(45,212,191,.30)}';
    (document.head || document.documentElement).appendChild(style);
  }
  const ranges=state.entries.map(e=>e.range);CSS.highlights.set('pae-word',new Highlight(...ranges));
  for(const name of ['new','mid','high'])CSS.highlights.set('pae-word-'+name,new Highlight(...state.entries.filter(e=>e.band===name).map(e=>e.range)));
  return {added,rendered:ranges.length,added_indices};
}
function paeClearHighlights(generation) {
  if(window.__paeHL && window.__paeHL.generation > generation)return;
  window.__paeHL={generation,entries:[],nodeIds:new WeakMap(),nextId:1};
  for(const name of ['pae-word','pae-word-new','pae-word-mid','pae-word-high'])window.CSS?.highlights?.delete(name);
  document.getElementById('pae-highlight-style')?.remove();
}
function paeTarget(sender) { return sender.documentId ? {tabId:sender.tab.id,documentIds:[sender.documentId]} : {tabId:sender.tab.id,frameIds:[0]}; }
function paePageKey(sender) { return sender.tab.id+':'+(sender.documentId || sender.url); }
async function paeHandle(msg,sender,kind) {
  await PAE_READY;
  if (msg.type === 'PAE_STATUS') { await paePost('/v1/cap/pae.config'); return {ok:true,base:PAE_BASE}; }
  if (msg.type === 'PAE_OPEN_SIDEBAR') {
    return new Promise(resolve=>chrome.tabs.query({active:true,currentWindow:true},tabs=>{
      const tab=tabs[0];if(!tab?.id||!paePublicPage(tab.url))return resolve({ok:false,error:'请在普通英文网页打开侧栏'});
      chrome.tabs.sendMessage(tab.id,{type:'PAE_OPEN_SIDEBAR'},()=>resolve(chrome.runtime.lastError?{ok:false,error:'请刷新当前网页后再打开侧栏'}:{ok:true}));
    }));
  }
  if (msg.type === 'PAE_UI_SIZE') {
    if(!sender.tab || !['collapsed','panel','sidebar'].includes(msg.mode))throw Error('界面状态无效');
    chrome.tabs.sendMessage(sender.tab.id,{type:'PAE_UI_SIZE',mode:msg.mode},{frameId:0},()=>{void chrome.runtime.lastError;});return {ok:true};
  }
  if (msg.type === 'PAE_CHAR_HELLO') { paeStartStream();return {ok:true,name:await paePersonaName()}; }
  if (msg.type === 'PAE_CHAR_STATS') return paeStats();
  if (msg.type === 'PAE_CHAR_HIST') { const j=await paePost('/v1/cap/pae.memory.recent',{n:60});return {ok:true,turns:Array.isArray(j.result)?j.result:[]}; }
  if (msg.type === 'PAE_CHAR_SAY') { const j=await paePost('/v1/cap/pae.persona.say',{stimulus:paeText(msg.text,4000)});return {ok:true,...j.result}; }
  if (msg.type === 'PAE_SET_MUTE') { if(typeof msg.muted!=='boolean')throw Error('静音字段无效'); const j=await paePost('/v1/cap/pae.mute',{muted:msg.muted});return {ok:true,muted:j.result?.muted}; }
  if (msg.type === 'PAE_GET_LIMITS') { const j=await paePost('/v1/cap/pae.params');return {ok:true,limits:j.result?.effective || {}}; }
  if (msg.type === 'PAE_EVENT') {
    const e=msg.event || {};
    if(!['annotation_shown','hover','known_click'].includes(e.type))throw Error('事件类型无效');
    const list=Array.isArray(e.lemmas)?e.lemmas:[e.lemma];if(!list.length || list.length>400)throw Error('词列表无效');
    if(e.page_active_ms!=null&&(!Number.isFinite(e.page_active_ms)||e.page_active_ms<0))throw Error('可见时长无效');
    let data;
    for(const lemma of [...new Set(list)])data=await paePost('/v1/event',{type:e.type,lemma:paeText(lemma,100),surface:e.surface?paeText(e.surface,100):lemma,page_id:typeof e.page_id==='string'?e.page_id.slice(0,200):'',session_id:typeof msg.sessionId==='string'?msg.sessionId.slice(0,100):'',page_active_ms:e.page_active_ms,sentence:typeof e.sentence==='string'?e.sentence.slice(0,300):undefined,s_value:Number.isFinite(e.s_value)?e.s_value:undefined});
    return {ok:true,data};
  }
  const key=paePageKey(sender);
  if (msg.type === 'PAE_CLEAR') {
    const generation=Number.isSafeInteger(msg.generation)?msg.generation:0;PAE_PAGES.set(key,{generation,annotations:[],nextRenderId:0});
    await chrome.scripting.executeScript({target:paeTarget(sender),world:'MAIN',func:paeClearHighlights,args:[generation]});return {ok:true};
  }
  if (msg.type === 'PAE_ANNOTATE') {
    const text=paeText(msg.text,50000),cap=msg.max_annotations;
    if(!Number.isSafeInteger(cap)||cap<0||cap>400)throw Error('注解余额必须为0到400的整数');
    const generation=Number.isSafeInteger(msg.generation)?msg.generation:0;
    const page=PAE_PAGES.get(key);
    if(page&&generation<page.generation)throw Error('旧页面请求已取消');
    const state=page&&page.generation===generation?page:{generation,annotations:[],nextRenderId:0};PAE_PAGES.set(key,state);
    const result=await paePost('/v1/annotate',{text,page_id:paeText(msg.pageId,200),session_id:typeof msg.sessionId==='string'?msg.sessionId.slice(0,100):null,max_annotations:cap});
    if(PAE_PAGES.get(key)!==state)throw Error('旧页面结果已取消');
    if(!Array.isArray(result))throw Error('注解响应格式无效');
    const data=result.slice(0,cap).filter(a=>a&&typeof a.surface==='string'&&typeof a.lemma==='string'&&typeof a.gloss==='string').map(a=>({surface:a.surface.slice(0,100),lemma:a.lemma.slice(0,100),gloss:a.gloss.slice(0,2000),s_value:Number.isFinite(a.s_value)?a.s_value:0,render_id:state.nextRenderId++}));
    state.annotations.push(...data);state.annotations=state.annotations.slice(-400);
    return {ok:true,data};
  }
  if (msg.type === 'PAE_RENDER') {
    const state=PAE_PAGES.get(key);if(!state||msg.generation!==state.generation)throw Error('旧页面绘制已取消');
    const requested=Array.isArray(msg.render_ids)?new Set(msg.render_ids.filter(Number.isSafeInteger)):new Set();
    const data=state.annotations.filter(a=>requested.has(a.render_id));
    const publicData=data.map(a=>({surface:a.surface,gloss:a.gloss,band:a.s_value<0.2?'new':a.s_value<0.45?'mid':'high'}));
    const output=await chrome.scripting.executeScript({target:paeTarget(sender),world:'MAIN',func:paeRegisterHighlights,args:[publicData,state.generation]});
    if(PAE_PAGES.get(key)!==state)throw Error('旧页面绘制已取消');
    const raw=output?.[0]?.result || {};const indices=Array.isArray(raw.added_indices)?raw.added_indices:[];const added_indices=[...new Set(indices.filter(i=>Number.isSafeInteger(i)&&i>=0&&i<data.length))];return {ok:true,rendered:{added:added_indices.length,rendered:Number.isSafeInteger(raw.rendered)?raw.rendered:0,added_indices}};
  }
  throw Error('不支持的消息');
}
chrome.runtime.onMessage.addListener((msg,sender,respond)=>{
  const kind=paeSenderKind(sender);
  const contentTypes=['PAE_ANNOTATE','PAE_GET_LIMITS','PAE_EVENT','PAE_CLEAR','PAE_RENDER'];
  const companionTypes=['PAE_CHAR_HELLO','PAE_CHAR_HIST','PAE_CHAR_SAY','PAE_CHAR_STATS','PAE_SET_MUTE','PAE_EVENT','PAE_UI_SIZE'];
  if(!msg || typeof msg.type!=='string' || !(kind==='content'?contentTypes.includes(msg.type):kind==='companion'?companionTypes.includes(msg.type):kind==='popup'&&['PAE_STATUS','PAE_OPEN_SIDEBAR'].includes(msg.type))) { respond({ok:false,error:'消息来源或类型被拒绝'});return false; }
  paeHandle(msg,sender,kind).then(respond,error=>respond({ok:false,code:error.code==='known_click_daily_limit'?'known_click_daily_limit':'unavailable',error:error.code==='known_click_daily_limit'?'今日认识了限额已到':/429|daily_cap/.test(error.message)?'今日限额已到':'本机服务暂未就绪，请双击 quick_start.bat；网页会自动重试'}));return true;
});
chrome.tabs.onRemoved.addListener(tabId=>{for(const key of PAE_PAGES.keys())if(key.startsWith(tabId+':'))PAE_PAGES.delete(key);});
