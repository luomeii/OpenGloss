'use strict';

// PAE content script — visible text extraction + batching (capture only, no rendering).

// 会话 id：存 sessionStorage —— 同一标签页刷新保持同一会话（跨标签独立）。
// fold.py 的会话阻尼 rho 依赖它；没有 session_id 时 rho 永久退化为常数增益。
const SESSION_ID = (function () {
  try {
    let s = sessionStorage.getItem('pae_session');
    if (!s) {
      s = 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      sessionStorage.setItem('pae_session', s);
    }
    return s;
  } catch (e) {
    return 's' + Date.now().toString(36);
  }
})();

(function () {
  const BATCH = [];
  let timer = null;
  const BATCH_MS = 800;
  const MAX_BATCH = 50;

  const SEEN = new Set();
  const SKIP = 'script,style,code,pre,kbd,samp,textarea,input,button,select,nav,footer,header,aside';
  const NODE_MAX = 5000;
  const TOTAL_MAX = 50000;
  let mutTimer = null;
  let paeDisabled = false;  // 一键开关（storage.paeEnabled）


  // 调试通道：content script 在隔离世界，主世界读不到 window.__PAE_DEBUG。
  // 因此同时写一份到 DOM dataset（两个世界都能读），供无头验收读取。
  function paeDebug(obj) {
    try {
      window.__PAE_DEBUG = obj;
      document.documentElement.dataset.paeDebug = JSON.stringify(obj);
    } catch (e) { /* swallow */ }
  }

  // 每请求文本上限（约 200 词）：保证每块都能吃满引擎的 8 词预算。
  // 否则整页塞进一个请求 → 整页最多只有 8 个注解（「整页只有几处高亮」的第二个原因）。
  const CHUNK_CHARS = 1400;
  // 每页注解上限：**由引擎的有效参数决定**（修「死旋钮」——AI 提案改了它必须真的生效）
  let annCap = 40;
  function refreshLimits() {
    try {
      chrome.runtime.sendMessage({ type: 'PAE_GET_LIMITS' }, function (r) {
        try {
          if (chrome.runtime.lastError || !r || !r.limits) return;
          if (r.limits.max_ann_per_page) annCap = r.limits.max_ann_per_page;
        } catch (e) {}
      });
    } catch (e) {}
  }
  refreshLimits();
  let annTotal = 0;
  let curPageId = '';

  function sendChunks(chunks, pageId) {
    let idx = 0;
    const sendNext = () => {
      if (paeDisabled || idx >= chunks.length || annTotal >= annCap) return;
      const text = chunks[idx++].join('\n');
      chrome.runtime.sendMessage({ type: 'PAE_ANNOTATE', text: text, pageId: pageId, sessionId: SESSION_ID }, function (resp) {
        try {
          if (chrome.runtime.lastError || !resp || !resp.ok) {
            paeDebug({
              lastError: (chrome.runtime.lastError && chrome.runtime.lastError.message) || (resp && resp.error) || 'no-response'
            });
          } else {
            const n = Array.isArray(resp.data) ? resp.data.length : -1;
            if (n > 0) annTotal += n;
            paeDebug({
              lastResponse: resp.data,
              annotations: n,
              rendered: resp.rendered || { rendered: -1, reason: 'no-rendered-field' },
              chunk: idx + '/' + chunks.length,
              ann_total: annTotal,
              ann_cap: annCap
            });
          }
        } catch (e2) { paeDebug({ renderError: String(e2 && e2.message || e2) }); }
        sendNext();
      });
    };
    sendNext();
  }

  function sendBatch() {
    try {
      if (paeDisabled) return;
      if (!BATCH.length) return;
      const pageId = String(location.href).split('#')[0].slice(0, 200);
      if (pageId !== curPageId) { curPageId = pageId; annTotal = 0; }
      if (annTotal >= annCap) { BATCH.length = 0; return; }
      const chunks = [];
      let cur = [];
      let curLen = 0;
      for (let i = 0; i < BATCH.length; i++) {
        const p = BATCH[i];
        cur.push(p);
        curLen += p.length + 1;
        if (curLen >= CHUNK_CHARS) { chunks.push(cur); cur = []; curLen = 0; }
      }
      if (cur.length) chunks.push(cur);
      BATCH.length = 0;
      let chars = 0;
      for (let k = 0; k < chunks.length; k++) chars += chunks[k].join('').length;
      paeDebug({ state: 'sending', chunks: chunks.length, chars: chars });
      sendChunks(chunks, pageId);
    } catch (e) {
      // swallow — never bubble to page
    }
  }

  function pushParagraph(t) {
    try {
      if (!t) return;
      if (SEEN.has(t)) return;
      SEEN.add(t);
      BATCH.push(t);
      if (BATCH.length >= MAX_BATCH) {
        if (timer) { clearTimeout(timer); timer = null; }
        sendBatch();
      } else if (!timer) {
        timer = setTimeout(function () { timer = null; sendBatch(); }, BATCH_MS);
      }
    } catch (e) {
      // swallow
    }
  }

  function extractVisibleText() {
    const out = [];
    try {
      const root = document.body || document.documentElement;
      if (!root) return out;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
      let total = 0;
      let node = walker.nextNode();
      while (node) {
        try {
          const el = node.parentElement;
          if (!el || el.closest(SKIP)) { node = walker.nextNode(); continue; }
          let t = (node.textContent || '').trim();
          if (!t) { node = walker.nextNode(); continue; }

          // ---- 提取区域排除（三层；每个文本节点都过）----
          // 2a 链接密度：向上找最近块级祖先（p/div/li/td/table/section/article，最多 4 层），
          //    该祖先内 <a> 数 / 词数 > 0.34 视为导航/菜单（HN 的菜单是 <td> 里的 <a>，标签名 SKIP 抓不到）。
          let block = el;
          for (let up = 0; up < 4 && block && block.parentElement; up++) {
            if (/^(P|DIV|LI|TD|TABLE|SECTION|ARTICLE)$/.test(block.tagName)) break;
            block = block.parentElement;
          }
          if (block) {
            const links = block.querySelectorAll('a').length;
            const words = (block.textContent || '').trim().split(/\s+/).length;
            if (links >= 2 && words > 0 && links / words > 0.34) { node = walker.nextNode(); continue; }
          }
          // 2b 位置排除：文档首 5% 与末 10%（节点相对文档位置，不用视口；提取时算一次）
          const rect = el.getBoundingClientRect();
          const docH = Math.max(document.documentElement.scrollHeight, 1);
          const yTop = rect.top + window.scrollY;
          if (yTop < docH * 0.05 || yTop > docH * 0.90) { node = walker.nextNode(); continue; }
          // 2c 过短节点
          if (t.length < 20) { node = walker.nextNode(); continue; }

          if (t.length > NODE_MAX) t = t.slice(0, NODE_MAX);
          if (total + t.length > TOTAL_MAX) {
            const room = TOTAL_MAX - total;
            if (room <= 0) break;
            t = t.slice(0, room);
          }
          total += t.length;
          out.push(t);
        } catch (e) {
          // swallow per-node
        }
        if (total >= TOTAL_MAX) break;
        node = walker.nextNode();
      }
    } catch (e) {
      // swallow
    }
    return out;
  }

  function extractNow() {
    try {
      const paras = extractVisibleText();
      for (let i = 0; i < paras.length; i++) pushParagraph(paras[i]);
    } catch (e) {
      // swallow
    }
  }

  function start() {
    try {
      extractNow();
      if (!timer) timer = setTimeout(function () { timer = null; sendBatch(); }, BATCH_MS);
      const obs = new MutationObserver(function () {
        try {
          if (mutTimer) clearTimeout(mutTimer);
          mutTimer = setTimeout(function () { mutTimer = null; extractNow(); }, 150);
        } catch (e) {
          // swallow
        }
      });
      obs.observe(document.documentElement || document, { childList: true, subtree: true });
    } catch (e) {
      // swallow
    }
  }

  try {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  } catch (e) {
    // swallow
  }

  chrome.storage.onChanged.addListener(function (changes, area) {
    if (area !== 'local' || !('paeEnabled' in changes)) return;
    paeDisabled = changes.paeEnabled.newValue === false;
    if (paeDisabled) {
      BATCH.length = 0;  // 关闭即丢弃积压批次，防累积
      try { chrome.runtime.sendMessage({ type: 'PAE_CLEAR' }, function () {}); } catch (e) {}
    } else {
      try { SEEN.clear(); extractNow(); } catch (e) {}
    }
  });
  chrome.storage.local.get({ paeEnabled: true }, function (s) {
    if (s && s.paeEnabled === false) paeDisabled = true;
  });
  // 主世界（悬停卡/认识了）→ 隔离世界 → SW → 引擎。跨世界只能用 window.postMessage。
  function charSend(obj) { try { window.postMessage(obj, '*'); } catch (e) {} }

  window.addEventListener('message', function (ev) {
    if (ev.source !== window) return;
    var d = ev.data;
    if (!d || d.source !== 'pae') return;
    // ① 学习事件（曝光/悬停/认识了）→ SW → 引擎
    if (d.type === 'annotation_shown' || d.type === 'hover' || d.type === 'known_click') {
      try {
        chrome.runtime.sendMessage({
          type: 'PAE_EVENT',
          sessionId: SESSION_ID,
          event: { type: d.type, lemma: d.lemma, surface: d.surface, s_value: d.s_value,
                   page_id: d.page_id, lemmas: d.lemmas, page_active_ms: d.page_active_ms,
                   sentence: d.sentence }
        }, function () { void chrome.runtime.lastError; });
      } catch (e) {}
      return;
    }
    // ①c 静音开关 → 引擎（闭环：引擎侧主动说话也必须尊重静音）
    if (d.type === 'char_mute') {
      try { chrome.runtime.sendMessage({ type: 'PAE_SET_MUTE', muted: !!d.muted }, function () {}); } catch (e) {}
      return;
    }
    // ①b-2 侧栏打开 → 拉对话历史（chat_turns 持久表：刷新/换页不丢——2026-10-03 二次反馈）
    if (d.type === 'char_chathist') {
      try {
        chrome.runtime.sendMessage({ type: 'PAE_CHAR_HIST' }, function (r) {
          try {
            void chrome.runtime.lastError;
            charSend({ source: 'pae-char', type: 'chathist', turns: (r && r.turns) || [] });
          } catch (e) {}
        });
      } catch (e) {}
      return;
    }
    // ①b 侧栏「刷新」→ 拉一份人看的统计（同一份状态的另一种表示：双面投影）
    if (d.type === 'char_stats') {
      try {
        chrome.runtime.sendMessage({ type: 'PAE_CHAR_STATS' }, function (r) {
          try {
            void chrome.runtime.lastError;
            charSend({ source: 'pae-char', type: 'stats', html: (r && r.html) || '（引擎没在跑）' });
          } catch (e) {}
        });
      } catch (e) {}
      return;
    }
    // ② 角色对话（主世界壳里输入的一句）→ SW → 引擎 → 回复回壳
    if (d.type === 'char_say') {
      try {
        chrome.runtime.sendMessage({ type: 'PAE_CHAR_SAY', text: d.text }, function (resp) {
          try {
            if (chrome.runtime.lastError || !resp) {
              charSend({ source: 'pae-char', type: 'reply', text: '引擎没在跑，我暂时说不了话。' });
              return;
            }
            charSend({ source: 'pae-char', type: 'reply',
                       text: resp.reply || ('（它没说话）' + (resp.error || '')) });
          } catch (e) {}
        });
      } catch (e) {}
    }
  });

  // ③ 推送（SW 的 SSE → 本页角色壳）
  chrome.runtime.onMessage.addListener(function (msg) {
    if (!msg || msg.type !== 'PAE_PUSH') return;
    var ev = msg.event || {};
    var text = ev.text || '';
    if (ev.type === 'param_changed') {
      text = '我把参数调了：' + JSON.stringify(ev.after || {});
      refreshLimits();
      // 参数变更后**真重渲染**（验收基准 inpage-agent-prototype 的核心特性；2026-10-02 漂移审计补）
      // 只有影响注解的参数才值得整页重画；引擎侧门槛已即时生效，客户端重画让它立刻可见。
      var _after = ev.after || {};
      var _need = ('annotate_max_s' in _after) || ('max_ann_per_page' in _after) ||
                  ('band_lo' in _after) || ('band_hi' in _after);
      if (_need && !paeDisabled) {
        try { chrome.runtime.sendMessage({ type: 'PAE_CLEAR' }, function () {}); } catch (e) {}
        try { SEEN.clear(); extractNow(); } catch (e) {}
      }
    }
    else if (ev.type === 'hint') text = '提示：' + (ev.lemma || '') + (ev.level === 'up' ? ' 多标一点' : ' 少标一点');
    if (text) charSend({ source: 'pae-char', type: ev.type === 'say' ? 'say' : 'system', text: text,
                         anchor: ev.anchor || '' });
  });

  // ④ 告诉 SW：本页有角色，请开始转发推送，并取回人格名
  try {
    chrome.runtime.sendMessage({ type: 'PAE_CHAR_HELLO' }, function (resp) {
      try {
        void chrome.runtime.lastError;
        if (resp && resp.name) charSend({ source: 'pae-char', type: 'name', name: resp.name });
      } catch (e) {}
    });
  } catch (e) {}
})();