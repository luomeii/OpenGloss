'use strict';

// ---------------------------------------------------------------------------
// 引擎地址：可在扩展弹窗里改（chrome.storage.local.paeBase），默认本机 4815。
// 换端口/换机器不用改代码（P7 工程收尾）。
// ---------------------------------------------------------------------------
var PAE_BASE = 'http://127.0.0.1:4815';
// Only annotation scheduling state; neither authentication nor learning history.
var paeAnnotationStates = new Map();

function paeNormBase(v) {
  var s = String(v || '');
  while (s.length && s.charAt(s.length - 1) === '/') { s = s.slice(0, -1); }
  return s || 'http://127.0.0.1:4815';
}

try {
  chrome.storage.local.get({ paeBase: 'http://127.0.0.1:4815' }, function (s) {
    if (s && s.paeBase) { PAE_BASE = paeNormBase(s.paeBase); }
  });
  chrome.storage.onChanged.addListener(function (ch, area) {
    if (area === 'local' && ch.paeEnabled && ch.paeEnabled.newValue === false)
      paeAnnotationStates.forEach(function (entry) { entry.enabled = false; });
    if (area === 'local' && ch.paeBase && ch.paeBase.newValue) {
      PAE_BASE = paeNormBase(ch.paeBase.newValue);
    }
  });
} catch (e) {}

// ---------------------------------------------------------------------------
// This function is INJECTED INTO THE MAIN WORLD via chrome.scripting.
// R1 experiment (verified, reproducible): the CSS Custom Highlight registry is
// per-JS-world. Registration from a content script (isolated world) paints
// NOTHING; the same registration from the main world paints 1563 pixels on the
// fixture page. Hence the injection.
// ---------------------------------------------------------------------------
function paeSetAnnotationGate(gate) {
  if (window.__paeVisibility) window.__paeVisibility.dispose();
  window.__paeAnnotationGate = gate;
  return { ok: true };
}
function paeAnnotationKey(sender) {
  return sender && sender.tab ? sender.tab.id + ':' + (sender.frameId || 0) : null;
}
function paeAnnotationMatches(entry, state) {
  return entry && entry.documentToken === state.documentToken && entry.revision === state.revision &&
    entry.pageId === state.pageId && entry.enabled === state.enabled;
}
function paeAnnotationMeta(message) {
  return { documentToken: message.documentToken, revision: message.revision,
    pageId: message.pageId, enabled: message.enabled !== false };
}
function paeAnnotationEnabled() {
  return new Promise(function (resolve) {
    chrome.storage.local.get({ paeEnabled: true }, function (s) {
      resolve(!chrome.runtime.lastError && s && s.paeEnabled !== false);
    });
  });
}
function paeProbeAnnotation(sender) {
  return new Promise(function (resolve) {
    try {
      chrome.tabs.sendMessage(sender.tab.id, { type: 'PAE_ANNOTATION_PROBE' },
        { frameId: sender.frameId || 0 }, function (state) {
          resolve(chrome.runtime.lastError ? null : state);
        });
    } catch (e) { resolve(null); }
  });
}
function paeAnnotationEnqueue(entry, operation) {
  var result = entry.tail.catch(function () {}).then(operation);
  entry.tail = result.catch(function () {});
  return result;
}
async function paeAnnotationCurrent(entry, state, sender, requireEnabled) {
  if (!paeAnnotationMatches(entry, state)) return false;
  if (requireEnabled && (!entry.enabled || !await paeAnnotationEnabled())) return false;
  var actual = await paeProbeAnnotation(sender);
  return paeAnnotationMatches(entry, state) && paeAnnotationMatches(actual, state);
}
async function paeApplyAnnotationState(message, sender, clear) {
  var state = paeAnnotationMeta(message), key = paeAnnotationKey(sender);
  if (key === null || typeof state.documentToken !== 'string' || !state.documentToken ||
      !Number.isInteger(state.revision) || state.revision < 0 || typeof state.pageId !== 'string')
    throw new Error('invalid-annotation-state');
  var actual = await paeProbeAnnotation(sender);
  if (!paeAnnotationMatches(actual, state)) throw new Error('stale-annotation-state');
  var entry = paeAnnotationStates.get(key);
  if (!entry) { entry = { tail: Promise.resolve() }; paeAnnotationStates.set(key, entry); }
  Object.assign(entry, state);
  return paeAnnotationEnqueue(entry, async function () {
    if (!await paeAnnotationCurrent(entry, state, sender, false)) throw new Error('stale-annotation-state');
    var result = await chrome.scripting.executeScript({
      target: { tabId: sender.tab.id, frameIds: [sender.frameId || 0] }, world: 'MAIN',
      func: clear ? paeClearHighlights : paeSetAnnotationGate, args: [state]
    });
    return { ok: true, cleared: result && result[0] && result[0].result };
  });
}
async function paeAnnotationOperation(entry, state, sender, operation) {
  return paeAnnotationEnqueue(entry, async function () {
    if (!await paeAnnotationCurrent(entry, state, sender, true)) return null;
    return operation();
  });
}
try {
  if (chrome.tabs && chrome.tabs.onRemoved) chrome.tabs.onRemoved.addListener(function (id) {
    paeAnnotationStates.forEach(function (entry, key) {
      if (key.indexOf(id + ':') === 0) paeAnnotationStates.delete(key);
    });
  });
} catch (e) {}

function paeDrawingBand(value) {
  if (!Number.isFinite(value) || value < 0.2) return 'new';
  return value < 0.45 ? 'mid' : 'high';
}

function paeTintWord(lemma, band, token, revision) {
  const gate = window.__paeAnnotationGate;
  const highlights = window.__paeHL;
  if (!gate?.enabled || gate.documentToken !== token || gate.revision !== revision || !highlights) return;
  const buckets = { new: [], mid: [], high: [] };
  for (const entry of highlights.entries) {
    const word = highlights.meta[entry.key];
    if (!word) continue;
    if (word.lemma === lemma) word.band = band;
    buckets[word.band].push(entry.range);
  }
  for (const [name, ranges] of Object.entries(buckets)) {
    CSS.highlights.set('pae-word-' + name, new Highlight(...ranges));
  }
}

function paeRegisterHighlights(annotations, gate) {
  try {
    if (gate) {
      var current = window.__paeAnnotationGate;
      if (!current || !current.enabled || current.documentToken !== gate.documentToken ||
          current.revision !== gate.revision ||
          String(location.href).split('#')[0].slice(0, 200) !== gate.pageId)
        return { rendered: 0, stale: true };
    }
    if (!window.Highlight || !window.CSS || !CSS.highlights) {
      return { rendered: 0, reason: 'no-highlight-api' };
    }
    // 累积表常驻主世界 window（隔离世界注册不绘制，见 R1 实验）
    var S = window.__paeHL;
    if (!S) {
      S = window.__paeHL = { entries: [], meta: {}, nodeIds: new WeakMap(), nextId: 1 };
    }
    var STYLE_ID = 'pae-highlight-style';
    if (!document.getElementById(STYLE_ID)) {
      var st = document.createElement('style');
      st.id = STYLE_ID;
      // 三档分色（按 annotation 的 s_value）：新词琥珀 / 带中蓝 / 快出带青。
      // pae-word 这个名字保留（多处测试与悬停卡按它取 range），它现在只画波浪下划线，
      // 底色交给三条档位规则；每条 range 恒属于且只属于一档，故旧观感（新词琥珀）不变。
      st.textContent = '::highlight(pae-word) {'
        + ' text-decoration: underline wavy rgba(217,119,6,0.7);'
        + ' text-underline-offset: 4px; }'
        + '::highlight(pae-word-new) { background-color: rgba(255,214,102,0.38); }'
        + '::highlight(pae-word-mid) { background-color: rgba(96,165,250,0.32); }'
        + '::highlight(pae-word-high) { background-color: rgba(45,212,191,0.30); }';
      (document.head || document.documentElement).appendChild(st);
    }
    var SKIP = 'script,style,noscript,code,pre,kbd,samp,textarea,input,select,button,nav,footer,header,aside';
    var root = document.body || document.documentElement;
    if (!root) return { rendered: 0, reason: 'no-root' };
    // 剔除已被移除的节点（SPA 换页 / 虚拟滚动）——这是累积表不膨胀的保证
    S.entries = S.entries.filter(function (e) {
      try { return e.range.startContainer.isConnected; } catch (err) { return false; }
    });
    var have = {};
    for (var k = 0; k < S.entries.length; k++) have[S.entries[k].key] = 1;
    var nodes = [];
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = walker.nextNode())) {
      var el = n.parentElement;
      if (!el || el.closest(SKIP)) continue;
      var txt = n.textContent || '';
      if (txt.trim()) nodes.push(n);
    }
    // This function is injected by value. Keep these checks aligned with the isolated word card.
    function supportsWholeWord(surface) {
      return /^[A-Za-z]+(?:['-][A-Za-z]+)*$/.test(surface);
    }
    function completeWord(text, start, end) {
      const before = text.slice(Math.max(0, start - 4), start);
      const after = text.slice(end, end + 4);
      return !/[\p{L}\p{M}\p{N}_]['’‐‑-]?$/u.test(before) &&
        !/^['’‐‑-]?[\p{L}\p{M}\p{N}_]/u.test(after);
    }
    const positions = new Map();
    const wanted = new Set();
    for (const annotation of annotations) {
      if (!annotation?.drawable || typeof annotation.surface !== 'string' || annotation.surface.length < 3) continue;
      const surface = annotation.surface;
      if (supportsWholeWord(surface)) {
        wanted.add(surface.toLowerCase());
        continue;
      }
      // Dictionary forms such as abdomino- retain their original exact lookup.
      if (positions.has(surface)) continue;
      for (const [nodeIndex, node] of nodes.entries()) {
        const start = node.textContent.indexOf(surface);
        if (start < 0) continue;
        positions.set(surface, { node, nodeIndex, start, end: start + surface.length, original: surface });
        break;
      }
    }
    if (wanted.size) {
      // Longer alternatives win when the shorter word is a prefix of a requested compound.
      const pattern = new RegExp([...wanted].sort((a, b) => b.length - a.length).join('|'), 'gi');
      for (const [nodeIndex, node] of nodes.entries()) {
        if (!wanted.size) break;
        const text = node.textContent;
        for (const match of text.matchAll(pattern)) {
          const original = match[0];
          const surface = original.toLowerCase();
          const start = match.index;
          const end = start + original.length;
          if (!wanted.has(surface) || !completeWord(text, start, end)) continue;
          positions.set(surface, { node, nodeIndex, start, end, original });
          wanted.delete(surface);
          if (!wanted.size) break;
        }
      }
    }
    var added = 0, dup = 0, drawn = [];
    for (var i = 0; i < annotations.length; i++) {
      var a = annotations[i];
      if (!a || typeof a.surface !== 'string' || a.surface.length < 3) continue;
      if (!a.drawable) continue;  // 无释义仍不标注
      const position = positions.get(supportsWholeWord(a.surface) ? a.surface.toLowerCase() : a.surface);
      if (!position) continue;
      var nid = S.nodeIds.get(position.node);
      if (!nid) { nid = S.nextId++; S.nodeIds.set(position.node, nid); }
      var key = nid + ':' + position.start + ':' + a.surface.length;
      if (have[key]) { dup++; continue; }
      var r = document.createRange();
      r.setStart(position.node, position.start);
      r.setEnd(position.node, position.end);
      S.entries.push({ key: key, range: r });
      S.meta[key] = { lemma: a.lemma, band: a.band, surface: position.original };
      have[key] = 1;
      added++;
      drawn.push({ index: i, nodeIndex: position.nodeIndex, start: position.start,
        end: position.end, surface: position.original });
    }
    // 按档位分桶；用累积表重算，所以老条目也会跟着 S 变档
    var bucket = { 'new': [], mid: [], high: [] };
    for (var b = 0; b < S.entries.length; b++) {
      var ent = S.entries[b];
      bucket[S.meta[ent.key].band].push(ent.range);
    }
    var all = bucket['new'].concat(bucket.mid, bucket.high);
    if (all.length > 4000) all = all.slice(all.length - 4000);  // 参数上限保护
    if (all.length) CSS.highlights.set('pae-word', new Highlight(...all));
    // 三个档名恒注册（空档也注册），外部一眼能核对三档是否就位
    CSS.highlights.set('pae-word-new', new Highlight(...bucket['new']));
    CSS.highlights.set('pae-word-mid', new Highlight(...bucket.mid));
    CSS.highlights.set('pae-word-high', new Highlight(...bucket.high));
    return { rendered: all.length, added: added, dup: dup, total: annotations.length,
             drawn: drawn, page_active_ms: Math.round(performance.now()),
             bands: { 'new': bucket['new'].length, mid: bucket.mid.length, high: bucket.high.length } };
  } catch (e) {
    return { rendered: -1, error: String((e && e.message) || e) };
  }
}
// Transient receipts travel through scripting/runtime, never through page messages.
function paeDrawnReceipts(data, rendered) {
  var drawn = rendered && rendered.drawn;
  if (!Array.isArray(drawn) || drawn.length > data.length || rendered.added !== drawn.length) return [];
  var seen = new Set();
  for (var item of drawn) {
    if (!item || !Number.isInteger(item.index) || item.index < 0 || item.index >= data.length || seen.has(item.index) ||
        !Number.isInteger(item.nodeIndex) || item.nodeIndex < 0 ||
        !Number.isInteger(item.start) || item.start < 0 || !Number.isInteger(item.end) ||
        typeof item.surface !== 'string' || item.end - item.start !== item.surface.length) return [];
    var word = data[item.index];
    if (!word || !word.gloss || typeof word.surface !== 'string' ||
        word.surface.length < 3 || word.surface.toLowerCase() !== item.surface.toLowerCase()) return [];
    seen.add(item.index);
  }
  return drawn;
}
async function paeRecordShown(entry, state, sender, data, drawn, sessionId, elapsed) {
  for (var item of drawn) {
    if (!await paeAnnotationCurrent(entry, state, sender, true)) return;
    var word = data[item.index];
    var response = await fetch(PAE_BASE + '/v1/event', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'annotation_shown', lemma: word.lemma,
        surface: word.surface, page_id: state.pageId, session_id: sessionId || '',
        // Legacy elapsed-page value, not time spent actively reading.
        page_active_ms: Number.isFinite(elapsed) ? elapsed : undefined }) });
    var result = await response.json();
    if (!response.ok || !result || result.ok !== true) return;
  }
}
function paeClearHighlights(gate) {
  try {
    if (window.__paeVisibility) window.__paeVisibility.dispose();
    if (gate) window.__paeAnnotationGate = gate;
    var cleared = 0;
    if (window.CSS && CSS.highlights) {
      var names = ['pae-word', 'pae-word-new', 'pae-word-mid', 'pae-word-high'];
      for (var q = 0; q < names.length; q++) { if (CSS.highlights.delete(names[q])) cleared++; }
    }
    var previous = window.__paeHL;
    if (previous && previous.feedbackDispose) previous.feedbackDispose();
    window.__paeHL = null;  // 清累积表
    var hh = document.getElementById('pae-hover-host');
    if (hh && hh.parentNode) { hh.parentNode.removeChild(hh); cleared++; }
    var st = document.getElementById('pae-highlight-style');
    if (st) { st.remove(); cleared++; }
    var titled = document.querySelectorAll('[data-pae-title]');
    for (var i = 0; i < titled.length; i++) {
      titled[i].removeAttribute('title');
      delete titled[i].dataset.paeTitle;
    }
    return { cleared: cleared, titles_removed: titled.length };
  } catch (e) {
    return { cleared: -1, error: String((e && e.message) || e) };
  }
}

// ---------------------------------------------------------------------------
// 推送通道：SW 持 SSE 长连接（扩展源，不受页面 CSP 限制），收到事件转发给所有标签页
// ---------------------------------------------------------------------------
var PAE_STREAM = { running: false };
var PAE_PERSONA_NAME = null;
// 上一次统计快照：给圆环 / delta 一个**真实测量**的差值（不是编出来的演示数字）
var PAE_STAT_PREV = {};
var PAE_STAT_PREV_S = {};

// ---- L2 指标显示（2026-10-02）：比例类指标的显示规则。只改显示，不动引擎算出的值 ----
// ≥1% 显示整数百分比；<1% 且 >0 显示一位小数（0.004 → 「0.4%」，不再被 round 成 0%）；
// 0 / null / 非数 → 「—」（真·空值不装成 0.0%）。
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

function paeStartStream() {
  if (PAE_STREAM.running) return;
  PAE_STREAM.running = true;
  (async function () {
    try {
      var res = await fetch(PAE_BASE + '/v1/ext/stream');
      var reader = res.body.getReader();
      var dec = new TextDecoder();
      var buf = '';
      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buf += dec.decode(chunk.value, { stream: true });
        var parts = buf.split('\n\n');
        buf = parts.pop();
        for (var i = 0; i < parts.length; i++) {
          var lines = parts[i].split('\n');
          for (var j = 0; j < lines.length; j++) {
            if (lines[j].indexOf('data:') !== 0) continue;
            try { paeBroadcast(JSON.parse(lines[j].slice(5).trim())); } catch (e) {}
          }
        }
      }
    } catch (e) {
      // 引擎离线：静默降级，下次请求再试
    } finally {
      PAE_STREAM.running = false;
    }
  })();
}

function paeBroadcast(ev) {
  if (!ev || !ev.type) return;
  chrome.tabs.query({}, function (tabs) {
    for (var i = 0; i < tabs.length; i++) {
      if (tabs[i].id == null) continue;
      chrome.tabs.sendMessage(tabs[i].id, { type: 'PAE_PUSH', event: ev }, function () {
        void chrome.runtime.lastError;
      });
    }
  });
}

async function paePersonaName() {
  if (PAE_PERSONA_NAME) return PAE_PERSONA_NAME;
  try {
    var r = await fetch(PAE_BASE + '/v1/cap/pae.persona', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: '{}' });
    var j = await r.json();
    PAE_PERSONA_NAME = (((j.result || {}).params || {}).name) || '小P';
  } catch (e) {
    PAE_PERSONA_NAME = '小P';
  }
  return PAE_PERSONA_NAME;
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (msg && (msg.type === 'PAE_WORD_TINT' || msg.type === 'PAE_FEEDBACK_STATE' || msg.type === 'PAE_VISIBILITY_EVENT' ||
      (msg.type === 'PAE_EVENT' && ['hover', 'known_click'].includes(msg.event?.type)))) {
    if (sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0 ||
        sender.documentLifecycle !== 'active' || typeof sender.documentId !== 'string') {
      sendResponse({ ok: false });
      return;
    }
  }
  if (msg?.type === 'PAE_WORD_TINT') {
    if (!['new', 'mid', 'high'].includes(msg.band)) return;
    chrome.scripting.executeScript({
      target: { tabId: sender.tab.id, documentIds: [sender.documentId] },
      world: 'MAIN', func: paeTintWord,
      args: [msg.lemma, msg.band, msg.documentToken, msg.revision]
    }).then(() => sendResponse({ ok: true }), () => sendResponse({ ok: false }));
    return true;
  }
  if (msg && msg.type === 'PAE_CHAR_VERIFY') {
    const frameURL = chrome.runtime.getURL('character-frame.html');
    if (sender.id !== chrome.runtime.id || !sender.tab || !(sender.frameId > 0) ||
        typeof sender.documentId !== 'string' || sender.documentLifecycle !== 'active' ||
        sender.url !== frameURL || sender.origin !== frameURL.slice(0, frameURL.lastIndexOf('/')) ||
        typeof msg.token !== 'string' || !/^[0-9a-f]{32}$/.test(msg.token)) {
      sendResponse({ ok: false });
      return;
    }
    chrome.tabs.sendMessage(sender.tab.id, { type: 'PAE_CHAR_VERIFY_ACTIVE', token: msg.token,
      surface: msg.surface, documentId: sender.documentId }, { frameId: 0 }, (result) => {
      const failed = !!chrome.runtime.lastError;
      sendResponse({ ok: !failed && result?.ok === true });
    });
    return true;
  }
  if (msg && ['PAE_SET_MUTE', 'PAE_CHAR_STATS', 'PAE_CHAR_HELLO', 'PAE_CHAR_SAY',
    'PAE_CHAR_HIST', 'PAE_CHAR_ANCHOR'].includes(msg.type)) {
    if (sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0 ||
        sender.documentLifecycle !== 'active' || typeof sender.documentId !== 'string') {
      sendResponse({ ok: false });
      return;
    }
  }
  if (msg && msg.type === 'PAE_CHAR_ANCHOR') {
    if (!Number.isInteger(msg.elementIndex) || msg.elementIndex < 0) return;
    chrome.scripting.executeScript({
      target: { tabId: sender.tab.id, documentIds: [sender.documentId] },
      world: 'MAIN', func: paeCharacterAnchor, args: [msg.elementIndex],
    }).then(() => sendResponse({ ok: true }), () => sendResponse({ ok: false }));
    return true;
  }
  if (msg && msg.type === 'PAE_SET_MUTE') {
    // 静音闭环：让引擎侧的主动说话也安静（此前只挡页面气泡，引擎照发）
    (async function () {
      try {
        // H1 修复（评审 2026-10-03）：此前引擎侧 pae.mute 因漏传 conn 永远 500，
        // 而这里不检查 res.ok 就报成功——静音按钮看着成功、引擎照说不误。
        var res = await fetch(PAE_BASE + '/v1/cap/pae.mute', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ muted: !!msg.muted })
        });
        var j = null;
        try { j = await res.json(); } catch (e2) {}
        sendResponse({ ok: res.ok && !!(j && j.ok), muted: (j && j.result && j.result.muted) });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
    })();
    return true;
  }
  if (msg && msg.type === 'PAE_CHAR_STATS') {
    (async function () {
      try {
        var o = {}, f = {}, ctx = {};
        try {
          var r1 = await fetch(PAE_BASE + '/v1/cap/pae.outcomes', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: '{"evaluate_decisions":false}' });
          o = ((await r1.json()).result) || {};
        } catch (e) {}
        try {
          var r2 = await fetch(PAE_BASE + '/v1/cap/pae.familiarity', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: '{"recompute":false}' });
          f = ((await r2.json()).result) || {};
        } catch (e) {}
        // 侧栏「最近在啃」+ 圆环指标：pae.context 一次拿 words（带 S 值）与 metrics（速率）
        try {
          var r3 = await fetch(PAE_BASE + '/v1/cap/pae.context', { method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{"include":["words","metrics"],"top_n":8}' });
          ctx = ((await r3.json()).result) || {};
        } catch (e) {}
        // 印章卡数据：长期记忆 facts（真事实才显示）。
        // 2026-10-03 二次反馈修复：以前 facts 为空时拿「最近 4 条对话」冒充记忆——
        // 用户看到「它记下的」永远是 4 条、且每次聊天后被新对话顶掉，误以为数据丢失。
        // 现在没有事实就老老实实显示「它还没记下什么」；事实由聊天里的 [[记住]] 协议真写入。
        var facts = [];
        try {
          var r4 = await fetch(PAE_BASE + '/v1/cap/pae.memory.search', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: '{"query":"","k":8}' });
          facts = ((await r4.json()).result) || [];
        } catch (e) {}
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
          { label: '悬停率', value: mm.hover_rate, note: sampleTxt(hoverSample, '悬停') + ' · 注解被看过的比例' },
          { label: '点击率', value: mm.click_rate, note: sampleTxt(clickSample, '点击') + ' · 点了「认识了」的比例' }
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
          var hasLearning = w.has_learning_state !== false;
          var sv = (hasLearning && typeof w.s_value === 'number') ? w.s_value : null;
          var dv = (typeof sv === 'number' && typeof PAE_STAT_PREV_S[w.lemma] === 'number')
            ? (sv - PAE_STAT_PREV_S[w.lemma]) : null;
          if (typeof sv === 'number') PAE_STAT_PREV_S[w.lemma] = sv;
          if (!hasLearning) delete PAE_STAT_PREV_S[w.lemma];
          words.push({ lemma: w.lemma || '', gloss: w.gloss || '', s_value: sv, delta: dv,
                       has_learning_state: hasLearning });
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
        var crTxt = (o.conversion_rate === null || o.conversion_rate === undefined)
          ? ('— · ' + cohortShort)
          : (paeFmtPct(o.conversion_rate) + '（' + cohortN + ' 个成熟词里 ' + convN + ' 个真点过认识了）');
        var summary = [
          { label: '结果侧', text: '转化率 ' + crTxt },
          { label: '关注度', text: '参与率 ' + paeFmtPct(o.engagement_rate) +
            ' · 完全无视 ' + (o.ignored || 0) + ' 词' },
          { label: '熟悉度', text: '模式 ' + (f.mode || 'shadow') + '：若启用会豁免 ' +
            ((f.would_exempt || []).length) + ' 个词' }
        ];
        sendResponse({ ok: true, summary: summary, panel: { metrics: metrics, words: words, stamps: stamps } });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
    })();
    return true;
  }
  if (msg && msg.type === 'PAE_GET_LIMITS') {
    (async function () {
      try {
        var r = await fetch(PAE_BASE + '/v1/cap/pae.params', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: '{}' });
        var j = await r.json();
        var eff = (j.result || {}).effective || {};
        sendResponse({ ok: true, limits: { max_ann_per_page: eff.max_ann_per_page,
                                           annotate_max_s: eff.annotate_max_s,
                                           daily_say_cap: eff.daily_say_cap } });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
    })();
    return true;
  }
  if (msg && msg.type === 'PAE_CHAR_HELLO') {
    paeStartStream();
    paePersonaName().then(function (n) { sendResponse({ ok: true, name: n }); });
    return true;
  }
  if (msg && msg.type === 'PAE_CHAR_SAY') {
    (async function () {
      var failedHint = '这次聊天未完成，请检查引擎和管理页的AI连接设置后再试。';
      try {
        // 2026-10-03 二次反馈修复：以前这里自己拼一份 metrics 数字当 context_block 传上去，
        // 引擎的自动装配（「我记得」事实卡 + 学习带 + 样本不足护栏）在生产路径上是死代码。
        // 现在只传 stimulus，让引擎装配完整上下文——单一事实来源，扩展不再越俎代庖。
        var r2 = await fetch(PAE_BASE + '/v1/cap/pae.persona.say', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ stimulus: msg.text || '（用户点了你一下）' })
        });
        var j2 = await r2.json();
        if (!r2.ok || !j2 || j2.ok !== true) {
          var hint = failedHint;
          if (j2 && j2.error === 'RuntimeError: AI call disabled for this action')
            hint = 'AI总控未开启，请到管理页开启后再试。';
          else if (j2 && j2.error === 'RuntimeError: no LLM key configured')
            hint = '尚未配置AI密钥，请到管理页填写并保存连接设置后再试。';
          sendResponse({ ok: false, error: hint });
          return;
        }
        var reply = j2.result && j2.result.reply;
        if (typeof reply !== 'string' || !reply.trim()) {
          sendResponse({ ok: false, error: '这次没有收到可显示的回复，请再试一次。' });
          return;
        }
        sendResponse({ ok: true, reply: reply });
      } catch (e) {
        sendResponse({ ok: false, error: failedHint });
      }
    })();
    return true;
  }
  if (msg && msg.type === 'PAE_CHAR_HIST') {
    // 侧栏打开时拉对话历史（engine 的 chat_turns 是持久表：刷新页面/换页面都不丢）
    (async function () {
      try {
        var r = await fetch(PAE_BASE + '/v1/cap/pae.memory.recent', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: '{"n":60}' });
        var j = await r.json();
        sendResponse({ ok: true, turns: (j.result || []) });
      } catch (e) {
        sendResponse({ ok: false, turns: [] });
      }
    })();
    return true;
  }
  if (msg && msg.type === 'PAE_FEEDBACK_STATE') {
    (async function () {
      try {
        var r = await fetch(PAE_BASE + '/v1/feedback?lemma=' + encodeURIComponent(msg.lemma || ''));
        if (!r.ok) throw new Error('HTTP ' + r.status);
        var data = await r.json();
        sendResponse({ ok: !!(data && data.ok), data: data, error: data && data.error });
      } catch (e) { sendResponse({ ok: false, error: String((e && e.message) || e) }); }
    })();
    return true;
  }
  if (msg && msg.type === 'PAE_VISIBILITY_EVENT') {
    (async function () {
      try {
        var state = paeAnnotationMeta(msg), key = paeAnnotationKey(sender);
        var entry = paeAnnotationStates.get(key);
        if (!entry) {
          await paeApplyAnnotationState(msg, sender, false);
          entry = paeAnnotationStates.get(key);
        }
        if (!/^[a-z][a-z'-]{2,63}$/.test(msg.lemma || '') ||
            !await paeAnnotationCurrent(entry, state, sender, true)) throw new Error('stale-observation');
        var response = await fetch(PAE_BASE + '/v1/event', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ type: 'visible_opportunity', lemma: msg.lemma,
            page_id: state.documentToken, visible_ms: 1000, observation_version: 1 }) });
        var data = await response.json();
        sendResponse({ ok: response.ok && !!data.ok });
      } catch (e) { sendResponse({ ok: false }); }
    })();
    return true;
  }
  if (msg && msg.type === 'PAE_EVENT') {
    if (['annotation_shown', 'visible_opportunity'].includes(msg.event?.type)) {
      sendResponse({ ok: false, error: 'extension-observation-required' });
      return;
    }
    (async function () {
      try {
        var d = msg.event || {};
        var list = (Array.isArray(d.lemmas) && d.lemmas.length) ? d.lemmas : [d.lemma];
        var last = null;
        for (var i = 0; i < list.length; i++) {
          if (!list[i]) continue;
          var res = await fetch(PAE_BASE + '/v1/event', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              type: d.type,
              lemma: list[i],
              surface: d.surface || list[i],
              page_id: d.page_id || '',
              session_id: msg.sessionId || '',
              page_active_ms: (typeof d.page_active_ms === 'number') ? d.page_active_ms : undefined,
              sentence: (typeof d.sentence === 'string') ? d.sentence.slice(0, 300) : undefined,
              s_value: (i === 0 && typeof d.s_value === 'number') ? d.s_value : undefined
            })
          });
          if (!res.ok) throw new Error('HTTP ' + res.status);
          last = await res.json();
          if (!last || last.ok !== true) {
            sendResponse({ ok: false, data: last, error: (last && last.error) || 'event_rejected' });
            return;
          }
        }
        sendResponse({ ok: true, data: last });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
    })();
    return true;
  }
  if (msg && msg.type === 'PAE_ANNOTATION_STATE') {
    paeApplyAnnotationState(msg, sender, msg.clear !== false).then(sendResponse,
      function (error) { sendResponse({ ok: false, error: String(error.message || error) }); });
    return true;
  }
  if (msg && msg.type === 'PAE_CLEAR') {
    (async function () {
      try {
        var tabId = sender && sender.tab && sender.tab.id;
        var res = null;
        if (tabId != null) {
          var out = await chrome.scripting.executeScript({
            target: { tabId: tabId },
            world: 'MAIN',
            func: paeClearHighlights
          });
          res = out && out[0] && out[0].result;
        }
        sendResponse({ ok: true, cleared: res });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
    })();
    return true;
  }
  if (!msg || msg.type !== 'PAE_ANNOTATE') return;
  (async function () {
    try {
      var state = paeAnnotationMeta(msg), key = paeAnnotationKey(sender);
      if (key === null || !Number.isInteger(msg.maxAnnotations) || msg.maxAnnotations < 0)
        throw new Error('invalid-annotation-budget');
      var entry = paeAnnotationStates.get(key);
      // A restarted worker reconstructs only the state confirmed by the live
      // isolated content script; it never revives a superseded response.
      if (!entry) {
        await paeApplyAnnotationState(msg, sender, false);
        entry = paeAnnotationStates.get(key);
      }
      if (!await paeAnnotationCurrent(entry, state, sender, true)) throw new Error('stale-annotation-state');
      var r = await fetch(PAE_BASE + '/v1/annotate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: msg.text, page_id: msg.pageId, session_id: msg.sessionId || null,
          max_annotations: msg.maxAnnotations })
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var data = await r.json();
      if (!Array.isArray(data) || data.length > msg.maxAnnotations) throw new Error('annotation-budget-contract');
      var rendered = { rendered: 0 };
      var tabId = sender && sender.tab && sender.tab.id;
      // ① 关键路径：只做「把注解画出来」，其余一律不阻塞响应
      try {
        if (tabId != null && Array.isArray(data) && data.length) {
          var drawing = data.map(a => a && ({ lemma: a.lemma, surface: a.surface,
            drawable: !!a.gloss, band: paeDrawingBand(a.s_value) }));
          var res = await paeAnnotationOperation(entry, state, sender, function () { return chrome.scripting.executeScript({
            target: { tabId: tabId, frameIds: [sender.frameId || 0] },
            world: 'MAIN',
            func: paeRegisterHighlights,
            args: [drawing, state]
          }); });
          if (!res || (res[0] && res[0].result && res[0].result.stale)) throw new Error('stale-annotation-state');
          rendered = (res && res[0] && res[0].result) || { rendered: 0 };
        }
      } catch (e2) {
        throw e2;
      }
      if (!await paeAnnotationCurrent(entry, state, sender, true)) throw new Error('stale-annotation-state');
      var drawn = paeDrawnReceipts(data, rendered);
      rendered = Object.assign({}, rendered, { drawn: drawn });
      sendResponse({ ok: true, data: data, rendered: rendered });
      // No page bridge and no blocking of the annotation/feedback response.
      if (drawn.length) paeRecordShown(entry, state, sender, data, drawn,
        msg.sessionId, rendered.page_active_ms).catch(function () {});
    } catch (e) {
      sendResponse({ ok: false, error: String((e && e.message) || e) });
    }
  })();
  return true;
});

// The public page only receives the position of its own paragraph.
function paeCharacterAnchor(index) {
  if (!window.Highlight || !CSS.highlights) return;
  const element = document.querySelectorAll('p,li,td,dd,blockquote,section,article,div')[index];
  if (!element) return;
  if (!document.getElementById('pae-anchor-style')) {
    const style = document.createElement('style');
    style.id = 'pae-anchor-style';
    style.textContent = '::highlight(pae-anchor) { background-color: rgba(245,158,11,0.20); }';
    (document.head || document.documentElement).appendChild(style);
  }
  const range = document.createRange();
  range.selectNodeContents(element);
  const highlight = new Highlight(range);
  CSS.highlights.set('pae-anchor', highlight);
  setTimeout(() => {
    if (CSS.highlights.get('pae-anchor') === highlight) CSS.highlights.delete('pae-anchor');
  }, 15000);
}
