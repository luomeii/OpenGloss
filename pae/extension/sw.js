'use strict';

// ---------------------------------------------------------------------------
// 引擎地址：可在扩展弹窗里改（chrome.storage.local.paeBase），默认本机 4815。
// 换端口/换机器不用改代码（P7 工程收尾）。
// ---------------------------------------------------------------------------
var PAE_BASE = 'http://127.0.0.1:4815';

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
    if (area === 'local' && ch.paeBase && ch.paeBase.newValue) {
      PAE_BASE = paeNormBase(ch.paeBase.newValue);
    }
  });
} catch (e) {}

// ---------------------------------------------------------------------------
// 扩展版本号（2026-10-03 二次反馈：Chrome 侧栏停留在旧版却无人发现——解压加载的
// 扩展不会自动更新）。侧栏头部显示 vN.N.N，两个浏览器各看一眼就能核对一致性。
// ---------------------------------------------------------------------------
var PAE_EXT_VERSION = '';
try { PAE_EXT_VERSION = chrome.runtime.getManifest().version; } catch (e) {}

// ---------------------------------------------------------------------------
// This function is INJECTED INTO THE MAIN WORLD via chrome.scripting.
// R1 experiment (verified, reproducible): the CSS Custom Highlight registry is
// per-JS-world. Registration from a content script (isolated world) paints
// NOTHING; the same registration from the main world paints 1563 pixels on the
// fixture page. Hence the injection.
// ---------------------------------------------------------------------------
function paeRegisterHighlights(annotations) {
  try {
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
    // 档位判定：S<0.2 新词（琥珀）/ 0.2≤S<0.45 带中（蓝）/ S≥0.45 快出带（青）。
    // 没有 s_value 的历史注解按新词档，保持旧观感。
    function paeBandOf(v) {
      if (typeof v !== 'number' || !isFinite(v)) return 'new';
      if (v < 0.2) return 'new';
      if (v < 0.45) return 'mid';
      return 'high';
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
    var added = 0, dup = 0;
    for (var i = 0; i < annotations.length; i++) {
      var a = annotations[i];
      if (!a || typeof a.surface !== 'string' || a.surface.length < 3) continue;
      if (!a.gloss) continue;  // 无释义不注（修 P1-5）
      for (var j = 0; j < nodes.length; j++) {
        var idx = nodes[j].textContent.indexOf(a.surface);
        if (idx < 0) continue;
        var nid = S.nodeIds.get(nodes[j]);
        if (!nid) { nid = S.nextId++; S.nodeIds.set(nodes[j], nid); }
        var key = nid + ':' + idx + ':' + a.surface.length;
        if (have[key]) { dup++; break; }
        var r = document.createRange();
        r.setStart(nodes[j], idx);
        r.setEnd(nodes[j], idx + a.surface.length);
        S.entries.push({ key: key, range: r });
        S.meta[key] = { lemma: a.lemma, gloss: a.gloss, s_value: a.s_value, surface: a.surface };
        have[key] = 1;
        added++;
        break;
      }
    }
    // 按档位分桶；用累积表重算，所以老条目也会跟着 S 变档
    var bucket = { 'new': [], mid: [], high: [] };
    for (var b = 0; b < S.entries.length; b++) {
      var ent = S.entries[b];
      bucket[paeBandOf((S.meta[ent.key] || {}).s_value)].push(ent.range);
    }
    var all = bucket['new'].concat(bucket.mid, bucket.high);
    if (all.length > 4000) all = all.slice(all.length - 4000);  // 参数上限保护
    if (all.length) CSS.highlights.set('pae-word', new Highlight(...all));
    // 三个档名恒注册（空档也注册），外部一眼能核对三档是否就位
    CSS.highlights.set('pae-word-new', new Highlight(...bucket['new']));
    CSS.highlights.set('pae-word-mid', new Highlight(...bucket.mid));
    CSS.highlights.set('pae-word-high', new Highlight(...bucket.high));
    // 注意：注入函数不能引用 SW 作用域的其他函数（只序列化自身源码）——
    // 悬停卡由 SW 另行独立注入 paeHoverInitMain()。
    // 曝光上报（annotation_shown：纯观察，fold 的 _OBS_ONLY，不影响 S）
    try {
      var lemmas = [];
      for (var q = 0; q < annotations.length; q++) {
        if (annotations[q] && annotations[q].lemma) lemmas.push(annotations[q].lemma);
      }
      if (lemmas.length) {
        window.postMessage({ source: 'pae', type: 'annotation_shown', lemma: lemmas[0], lemmas: lemmas,
                             page_id: String(location.href).split('#')[0].slice(0, 200),
                             page_active_ms: Math.round(performance.now()) }, '*');
      }
    } catch (e3) {}
    return { rendered: all.length, added: added, dup: dup, total: annotations.length,
             bands: { 'new': bucket['new'].length, mid: bucket.mid.length, high: bucket.high.length } };
  } catch (e) {
    return { rendered: -1, error: String((e && e.message) || e) };
  }
}
function paeClearHighlights() {
  try {
    var cleared = 0;
    if (window.CSS && CSS.highlights) {
      var names = ['pae-word', 'pae-word-new', 'pae-word-mid', 'pae-word-high'];
      for (var q = 0; q < names.length; q++) { if (CSS.highlights.delete(names[q])) cleared++; }
    }
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
        var attr = JSON.stringify(payload).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
        // 转化率文案：cohort=0 时说清「差在哪」——不是含糊的「数据还不够」
        var crTxt = (o.conversion_rate === null || o.conversion_rate === undefined)
          ? ('— · ' + cohortShort)
          : (paeFmtPct(o.conversion_rate) + '（' + cohortN + ' 个成熟词里 ' + convN + ' 个真点过认识了）');
        var html = '<div class="sum"><b>结果侧</b> 转化率 ' + crTxt + '<br>' +
                   '<b>关注度</b> 参与率 ' + paeFmtPct(o.engagement_rate) +
                   ' · 完全无视 ' + (o.ignored || 0) + ' 词<br>' +
                   '<b>熟悉度</b> 模式 ' + (f.mode || 'shadow') + '：若启用会豁免 ' + ((f.would_exempt || []).length) + ' 个词</div>' +
                   '<div class="rows" id="sidemetrics"></div>' +
                   '<div data-pae-panel="' + attr + '" style="display:none"></div>';
        sendResponse({ ok: true, html: html, panel: payload });
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
      try {
        // 2026-10-03 二次反馈修复：以前这里自己拼一份 metrics 数字当 context_block 传上去，
        // 引擎的自动装配（「我记得」事实卡 + 学习带 + 样本不足护栏）在生产路径上是死代码。
        // 现在只传 stimulus，让引擎装配完整上下文——单一事实来源，扩展不再越俎代庖。
        var r2 = await fetch(PAE_BASE + '/v1/cap/pae.persona.say', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ stimulus: msg.text || '（用户点了你一下）' })
        });
        var j2 = await r2.json();
        sendResponse({ ok: true, reply: ((j2.result || {}).reply) || '', error: j2.error });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
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
  if (msg && msg.type === 'PAE_EVENT') {
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
          last = await res.json();
        }
        sendResponse({ ok: true, data: last });
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
    })();
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
      var r = await fetch(PAE_BASE + '/v1/annotate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: msg.text, page_id: msg.pageId, session_id: msg.sessionId || null })
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var data = await r.json();
      var rendered = { rendered: 0 };
      var tabId = sender && sender.tab && sender.tab.id;
      // ① 关键路径：只做「把注解画出来」，其余一律不阻塞响应
      try {
        if (tabId != null && Array.isArray(data) && data.length) {
          var res = await chrome.scripting.executeScript({
            target: { tabId: tabId },
            world: 'MAIN',
            func: paeRegisterHighlights,
            args: [data]
          });
          rendered = (res && res[0] && res[0].result) || { rendered: 0 };
        }
      } catch (e2) {
        rendered = { rendered: -1, error: String((e2 && e2.message) || e2) };
      }
      // ② 装饰性工作（悬停卡 / 角色形态 / 推送流）**放在响应之后**。
      //    它们要额外跟引擎往返（取人格名），在冷启动时会和注解请求抢时间——
      //    曾导致 t4 在串行回归里偶发失败。注解响应不该等化妆品。
      if (tabId != null && Array.isArray(data) && data.length) {
        try {
          // H2（评审 2026-10-03）：postMessage 桥的 token 分发。
          // 背景：页面脚本能伪造 source:'pae' 消息（驱动角色说话/读回复/投毒词库）。
          // 现状：token 在 SW 生成并走两条 extension 内部通道分发
          //   （① executeScript args → 主世界壳；② tabs.sendMessage → content.js），
          //   但**校验尚未在 content.js 落地**——这层桥目前仍可被伪造，
          //   实际防线是引擎侧日配额（persona.say 120 / known_click 60）。
          // 彻底修法：把角色壳迁到扩展隔离世界（见 README「已知边界」与路线图）。
          var charToken = 'pae_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
          chrome.scripting.executeScript({ target: { tabId: tabId }, world: 'MAIN',
                                           func: paeHoverInitMain })
            .then(function () { return paePersonaName(); })
            .then(function (pname) {
              return chrome.scripting.executeScript({ target: { tabId: tabId }, world: 'MAIN',
                                                      func: paeCharacterInit,
                                                      args: [{ name: pname, ver: PAE_EXT_VERSION, token: charToken }] })
                .then(function () {
                  try {
                    chrome.tabs.sendMessage(tabId, { type: 'PAE_CHAR_TOKEN', token: charToken },
                                            function () { void chrome.runtime.lastError; });
                  } catch (e9) {}
                });
            })
            .then(function () { paeStartStream(); })
            .catch(function () {});
        } catch (e5) {}
      }
      sendResponse({ ok: true, data: data, rendered: rendered });
    } catch (e) {
      sendResponse({ ok: false, error: String((e && e.message) || e) });
    }
  })();
  return true;
});

// ---------------------------------------------------------------------------
// 词级悬停卡（主世界）：点谁显示谁的释义，不再是「8 个词共用 2 条段落提示」。
// Shadow DOM 隔离，页面 DOM 零改动。样式走中标注档（药丸/纸卡）。
// 「认识了」→ window.postMessage → content script → SW → /v1/event（known_click）
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// 内嵌角色形态（页面胶囊 / 边注气泡）——用户最初要的「网页内嵌智能体角色」本体。
// 形态与人格分离：这个壳显示谁的话都行；人格由引擎的 pae.persona 决定。
// 零页面 DOM 污染（Shadow DOM），推送走 SW 的 SSE 转发（不受页面 CSP 限制）。
// ---------------------------------------------------------------------------
// ---- 角色形态（胶囊 / 侧栏 / 边注）---- 覆盖式外壳：全部在 Shadow DOM 内，页面 DOM 零改动
// 机制思路参考（覆盖式外壳、统一 Shell、Top Layer、双面投影、三段卡），实现为本项目自写
function paeCharacterInit(opts) {
  opts = opts || {};
  if (window.__paeChar) {
    if (opts.name) { try { window.__paeChar.setName(opts.name); } catch (e) {} }
    if (opts.token) { try { window.__paeChar.token = opts.token; } catch (e) {} }
    return { ok: true, existing: true };
  }
  // H2 token：SW 注入的闭包值（预留给「content.js 强制校验」那一步；当前仅持有，未强制校验）。
  var CHAR_TOKEN = opts.token || '';
  var S = { muted: false, name: opts.name || '小P', open: false, seen: 0, log: [], token: CHAR_TOKEN };
  window.__paeChar = S;

  var host = document.createElement('div');
  host.id = 'pae-char-host';
  host.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483646;';
  host.setAttribute('popover', 'manual');   // Top Layer：不与页面 z-index 打架
  var root = host.attachShadow({ mode: 'open' });
  root.innerHTML =
    '<style>' +
    ':host{all:initial}' +
    /* ===== 令牌层（照抄基准 :root / .theme-night）：系统偏好=初始值，换肤按钮=手动覆盖 ===== */
    '.pae-root{' +
    '--pae-paper:#fdfaf3;--pae-paper-warm:#f3e7cf;--pae-ink:#2c2417;--pae-ink-soft:#7a6a4f;' +
    '--pae-line:rgba(150,120,70,.32);--pae-line-strong:rgba(140,105,55,.55);' +
    '--pae-accent:#8a5a1e;--pae-accent-soft:rgba(196,146,70,.18);' +
    '--pae-shadow:0 6px 26px rgba(120,90,40,.16);--pae-radius:10px;--pae-ease:180ms;--pae-noise-op:.035;' +
    '--pae-edge:rgba(217,119,6,.34);--pae-edge-soft:rgba(217,119,6,.2);--pae-accent-ink:#a1550a;' +
    '--pae-surface:linear-gradient(165deg,rgba(255,253,246,.99),rgba(255,247,231,.99));' +
    '--pae-side-bg:linear-gradient(180deg,#fffdf7,#fff8ec);' +
    '--pae-field:#fff;--pae-hatch:rgba(255,214,102,.35);--pae-btnhover:#fff7e6;' +
    '--pae-capbg:linear-gradient(135deg,#f59e0b,#b45309);--pae-dot:#f59e0b;--pae-dot-soft:rgba(245,158,11,.22);' +
    'font:13px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--pae-ink)}' +
    '.pae-root.theme-night{' +
    '--pae-paper:#12161c;--pae-paper-warm:#1a2029;--pae-ink:#dfe6ee;--pae-ink-soft:#8792a2;' +
    '--pae-line:rgba(150,180,220,.22);--pae-line-strong:rgba(170,200,240,.4);' +
    '--pae-accent:#e0b060;--pae-accent-soft:rgba(224,176,96,.14);' +
    '--pae-shadow:0 6px 26px rgba(0,0,0,.5);--pae-noise-op:.05;' +
    '--pae-edge:rgba(245,158,11,.35);--pae-edge-soft:rgba(245,158,11,.22);--pae-accent-ink:#f0b45a;' +
    '--pae-surface:linear-gradient(165deg,rgba(43,38,32,.98),rgba(35,31,26,.98));' +
    '--pae-side-bg:linear-gradient(180deg,#2b2620,#221e19);' +
    '--pae-field:#1e1a16;--pae-hatch:rgba(245,158,11,.18);--pae-btnhover:#33291c}' +
    /* ===== 纸纹 + 双线框（照抄基准 .paper::before / .paper::after，纯 SVG 零图片） ===== */
    '.paper{position:relative;background:var(--pae-paper);border:1px solid var(--pae-line);' +
    'border-radius:var(--pae-radius);box-shadow:var(--pae-shadow);overflow:hidden}' +
    '.paper::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:var(--pae-noise-op);' +
    'background-image:url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'140\' height=\'140\'%3E%3Cfilter id=\'n\'%3E%3CfeTurbulence type=\'fractalNoise\' baseFrequency=\'.8\' numOctaves=\'3\'/%3E%3C/filter%3E%3Crect width=\'140\' height=\'140\' filter=\'url(%23n)\'/%3E%3C/svg%3E")}' +
    '.paper::after{content:"";position:absolute;inset:7px;border:1px double var(--pae-line-strong);' +
    'border-radius:calc(var(--pae-radius) - 3px);pointer-events:none;opacity:.75}' +
    '.paper>*{position:relative}' +
    /* ===== 三段卡（照抄基准 .card/.ch/.cb/.ca/.chev） ===== */
    '.card{margin:0 0 9px;border:1px solid var(--pae-line);border-radius:9px;background:var(--pae-paper);overflow:hidden}' +
    '.card .ch{display:flex;align-items:center;gap:7px;padding:8px 11px;cursor:pointer;user-select:none;background:var(--pae-accent-soft)}' +
    '.card .ch .t{font-size:13px;font-weight:600}' +
    '.card .ch .ts{margin-left:auto;font-size:10.5px;font-weight:400;color:var(--pae-ink-soft)}' +
    '.card .ch .chev{margin-left:2px;transition:transform var(--pae-ease);color:var(--pae-ink-soft)}' +
    '.card.open .ch .chev{transform:rotate(90deg)}' +
    '.card .cb{display:none;padding:10px 12px;font-size:12.8px;line-height:1.75;border-top:1px solid var(--pae-line)}' +
    '.card.open .cb{display:block}' +
    '.card .ca{display:none;gap:6px;padding:7px 11px;border-top:1px solid var(--pae-line)}' +
    '.card.open .ca{display:flex}' +
    '.card .ca button{font:11.5px system-ui;border:1px solid var(--pae-line);background:transparent;' +
    'color:var(--pae-ink-soft);border-radius:13px;padding:2px 10px;cursor:pointer}' +
    '.card .ca button:hover{border-color:var(--pae-accent);color:var(--pae-accent)}' +
    /* ===== 缎带分隔（照抄基准 .ribbon） ===== */
    '.ribbon{display:flex;align-items:center;gap:8px;margin:11px 14px 8px;color:var(--pae-ink-soft);' +
    'font-size:11px;letter-spacing:1.4px;text-transform:uppercase}' +
    '.ribbon::before,.ribbon::after{content:"";flex:1;height:0;border-top:1px solid var(--pae-line)}' +
    '.ribbon i{font-style:normal;padding:2px 8px;border:1px solid var(--pae-line);border-radius:20px;' +
    'background:var(--pae-accent-soft);color:var(--pae-accent)}' +
    /* ===== 掌握度圆环（照抄基准 .ring；conic-gradient 按 S 值比例） ===== */
    '.ring{width:40px;height:40px;border-radius:50%;display:flex;align-items:center;justify-content:center;' +
    'font:11px ui-monospace,monospace;position:relative;flex:none}' +
    '.ring::before{content:"";position:absolute;inset:4px;border-radius:50%;background:var(--pae-paper)}' +
    '.ring span{position:relative}' +
    '.ring.sm{width:28px;height:28px;font-size:9.5px}' +
    '.ring.sm::before{inset:3px}' +
    /* ===== 词行列表（照抄基准 .rows/.row/.w/.g/.delta；限定在 .rows 下，不动胶囊的 .row 输入行） ===== */
    '.rows{padding:2px 14px 10px}' +
    '.rows .row{display:flex;align-items:center;gap:9px;padding:6px 0;border-bottom:1px dashed var(--pae-line)}' +
    '.rows .row:last-child{border-bottom:none}' +
    '.rows .row .w{flex:1;min-width:0;font-size:12.5px}' +
    '.rows .row .g{display:block;font-size:10.5px;color:var(--pae-ink-soft);overflow:hidden;' +
    'text-overflow:ellipsis;white-space:nowrap}' +
    '.rows .row.empty{display:block;font-size:12px;color:var(--pae-ink-soft);border-bottom:none}' +
    '.delta{flex:none;font:10.5px ui-monospace,monospace;padding:1px 6px;border-radius:9px;' +
    'background:var(--pae-accent-soft);color:var(--pae-accent)}' +
    '.delta.neg{background:rgba(180,71,47,.16);color:#b4472f}' +
    /* ===== 印章卡（照抄基准 .stamp：rotate(-3deg) + ::before 读 data-tag） ===== */
    '.stamp{display:inline-block;position:relative;border:1.5px solid var(--pae-line-strong);border-radius:5px;' +
    'padding:6px 10px;margin:3px 6px 3px 0;transform:rotate(-3deg);font-size:11.5px;' +
    'background:var(--pae-paper);box-shadow:0 2px 8px rgba(120,90,40,.12)}' +
    '.stamp::before{content:attr(data-tag);position:absolute;top:-8px;left:6px;font-size:9px;letter-spacing:.6px;' +
    'background:var(--pae-accent);color:var(--pae-paper);padding:0 5px;border-radius:3px}' +
    '.stamp.pos{border-color:#3f8f4f}' +
    '.stamp.pos::before{background:#3f8f4f}' +
    '.stamp.neg{border-color:#b4472f}' +
    '.stamp.neg::before{background:#b4472f}' +
    /* ===== 书页对折分割线（照抄基准 .fold） ===== */
    '.fold{height:1px;margin:0 14px;position:relative;background:linear-gradient(90deg,transparent,' +
    'var(--pae-line-strong) 18%,var(--pae-line-strong) 82%,transparent)}' +
    '.fold::after{content:"";position:absolute;left:50%;top:-3px;width:7px;height:7px;margin-left:-3.5px;' +
    'transform:rotate(45deg);background:var(--pae-paper);border:1px solid var(--pae-line-strong)}' +
    /* ---- 胶囊 / 气泡 / 面板：颜色全部由令牌驱动 ---- */
    '.wrap{position:fixed;right:16px;bottom:16px;display:flex;flex-direction:column;align-items:flex-end;gap:8px}' +
    '.bubble{max-width:300px;padding:9px 12px;border-radius:12px;display:none;background:var(--pae-surface);' +
    'border:1px solid var(--pae-edge);box-shadow:var(--pae-shadow)}' +
    '.bubble .who{font-size:11px;color:var(--pae-accent-ink);font-weight:600;margin-bottom:3px}' +
    '.panel{display:none;width:290px;padding:10px 11px;border-radius:12px;background:var(--pae-surface);' +
    'border:1px solid var(--pae-edge);box-shadow:var(--pae-shadow)}' +
    '.row{display:flex;gap:6px}' +
    '.panel input,.chatbox .ca input{flex:1;font:inherit;padding:5px 8px;border:1px solid var(--pae-edge);' +
    'border-radius:7px;background:var(--pae-field);color:inherit}' +
    'button{cursor:pointer;border:1px solid var(--pae-edge);background:var(--pae-field);color:var(--pae-accent-ink);' +
    'border-radius:7px;padding:4px 9px;font:12px system-ui}' +
    'button:hover{background:var(--pae-btnhover)}' +
    '.row2{margin-top:7px;display:flex;gap:6px;justify-content:flex-end}' +
    '.cap{display:flex;align-items:center;gap:7px;cursor:pointer;border:none;background:var(--pae-capbg);' +
    'color:#fff;border-radius:99px;padding:7px 14px;box-shadow:0 6px 18px rgba(180,90,10,.32);font:13px system-ui;font-weight:600}' +
    '.dot{width:7px;height:7px;border-radius:99px;background:#fff;opacity:.85}' +
    '.muted{opacity:.55}' +
    /* ---- 侧栏（覆盖式外壳，占右侧，不进页面布局） ---- */
    '.side{position:fixed;top:0;right:0;height:100vh;max-height:100vh;box-sizing:border-box;width:360px;' +
    'display:none;flex-direction:column;overflow:hidden;' +
    'background:var(--pae-side-bg);border-left:1px solid var(--pae-edge-soft);box-shadow:var(--pae-shadow);color:var(--pae-ink)}' +
    '.side .hd{display:flex;align-items:center;gap:8px;padding:11px 14px}' +
    '.side .hd b{font-size:14px}' +
    '.side .hd .sp{margin-left:auto}' +
    /* 三段布局中间段：唯一滚动容器；对话流/词行/印章全在它里面 */
    '.side .bd{flex:1 1 auto;min-height:0;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;' +
    'display:flex;flex-direction:column;gap:0;padding:0}' +
    /* 滚动段的子项不许被压缩（否则 flex 收缩会把 .stats/词行压成几像素） */
    '.side .bd>*{flex:none}' +
    '.turn{padding:8px 11px;border-radius:11px;max-width:88%;white-space:pre-wrap;word-break:break-word}' +
    '.turn.a{background:var(--pae-hatch);border:1px solid var(--pae-edge-soft);align-self:flex-start}' +
    '.turn.u{background:var(--pae-field);border:1px solid var(--pae-edge);align-self:flex-end}' +
    '.turn .t{display:block;font-size:10.5px;opacity:.6;margin-top:3px}' +
    /* ---- 对话窗（2026-10-03 二次反馈重构）：独立小窗自带滚轮 ----
       旧版把每句聊天当独立卡片堆在 .bd 顶部，聊 30 轮就把掌握度/最近在啃/它记下的
       全挤到看不见。现在数据区独占 .bd，对话固定在底部小窗里自己滚，互不侵占。 */
    '.chatbox{flex:none;margin:0;border-top:1px solid var(--pae-edge-strong);background:var(--pae-side-bg)}' +
    '.chatbox .ch{padding:8px 14px}' +
    '.chatbox .ch .t{font-size:12px}' +
    '.card.open.chatbox .cb{display:flex;flex-direction:column;gap:7px;max-height:30vh;overflow-y:auto;padding:10px 14px}' +
    '.chatbox .ca{gap:6px;align-items:center}' +
    '.chatbox .ca input{flex:1;min-width:0}' +
    '.chatbox .ca button{flex:none}' +
    '.chatbox .turn.clamp{max-height:76px;overflow:hidden;cursor:pointer}' +
    '.chatbox .turn.clamp.exp{max-height:none;cursor:default}' +
    '.stats{margin:0 14px 10px;padding:9px 11px;border-radius:11px;background:var(--pae-field);' +
    'border:1px solid var(--pae-edge-soft);font-size:12px;max-height:34vh;overflow-y:auto;box-sizing:border-box}' +
    '.stats b{color:var(--pae-accent-ink)}' +
    '.stats .rows{padding:6px 0 0}' +
    /* ---- 边注（指着某段说） ---- */
    '.margin{position:fixed;max-width:260px;padding:8px 11px;border-radius:11px;display:none;' +
    'background:var(--pae-surface);border:1px solid var(--pae-edge);box-shadow:var(--pae-shadow);font-size:12.5px}' +
    '.margin .who{font-size:10.5px;color:var(--pae-accent-ink);font-weight:600;margin-bottom:2px}' +
    '.margin .dot2{position:fixed;width:8px;height:8px;border-radius:99px;background:var(--pae-dot);' +
    'box-shadow:0 0 0 4px var(--pae-dot-soft);display:none}' +
    /* 换肤按钮（基准 #theme 的挂载点） */
    '.theme-btn{font-size:11.5px}' +
    '</style>' +
    '<div class="pae-root" id="paeroot">' +
    '  <div class="wrap">' +
    '    <div class="bubble" id="bubble"><div class="who" id="who"></div><div id="btext"></div></div>' +
    '    <div class="panel" id="panel">' +
    '      <div class="row"><input id="in" placeholder="跟它说一句…"><button id="send">发</button></div>' +
    '      <div class="row2"><button id="sidebtn">展开侧栏</button><button id="mute">静音</button></div>' +
    '    </div>' +
    '    <button class="cap" id="cap"><span class="dot"></span><span id="capname"></span></button>' +
    '  </div>' +
    '  <div class="margin" id="margin"><div class="who" id="mwho"></div><div id="mtext"></div></div>' +
    '  <div class="dot2" id="mdot"></div>' +
    '  <div class="side paper" id="side">' +
    '    <div class="hd"><b id="sidename"></b><span id="sidestate" style="font-size:11px;opacity:.65"></span>' +
    '      <span id="sidever" style="font-size:10px;opacity:.5"></span>' +
    '      <span class="sp"></span>' +
    '      <button class="theme-btn" id="themebtn" title="切换 暗夜 / 羊皮纸">换肤</button>' +
    '      <button id="sidereload">刷新</button><button id="sideclose">收起</button></div>' +
    '    <div class="fold"></div>' +
    '    <div class="bd" id="sidebd">' +
    '      <div class="ribbon"><i>掌握度</i></div>' +
    '      <div class="stats" id="sidestats">…</div>' +
    '      <div class="ribbon"><i>最近在啃</i></div>' +
    '      <div class="rows" id="sidewords"></div>' +
    '      <div class="ribbon"><i>它记下的</i></div>' +
    '      <div class="rows" id="sidestamps"></div>' +
    '    </div>' +
    '    <div class="card open chatbox" id="chatbox">' +
    '      <div class="ch" id="chathd" title="点标题展开/收起对话窗"><span class="t">对话</span>' +
    '        <span class="ts" id="chatcount"></span><span class="chev">›</span></div>' +
    '      <div class="cb" id="chatbody"></div>' +
    '      <div class="ca"><input id="sidein" placeholder="跟它说一句…"><button id="sidesend">发</button></div>' +
    '    </div>' +
    '  </div>' +
    '</div>';
  (document.documentElement || document.body).appendChild(host);
  try { host.showPopover(); } catch (e) {}

  var bubble = root.getElementById('bubble');
  var btext = root.getElementById('btext');
  var who = root.getElementById('who');
  var panel = root.getElementById('panel');
  var capname = root.getElementById('capname');
  var cap = root.getElementById('cap');
  var side = root.getElementById('side');
  var sidebd = root.getElementById('sidebd');
  var chatbox = root.getElementById('chatbox');
  var chatbody = root.getElementById('chatbody');
  var chatcount = root.getElementById('chatcount');
  var margin = root.getElementById('margin');
  var mtext = root.getElementById('mtext');
  var mwho = root.getElementById('mwho');
  var mdot = root.getElementById('mdot');
  var bubbleTimer = null, marginTimer = null;
  var paeroot = root.getElementById('paeroot');
  var sidewords = root.getElementById('sidewords');
  var sidestamps = root.getElementById('sidestamps');

  // ---- 换肤（基准 #theme 的做法）：.theme-night 一开一关；系统偏好只是初始值 ----
  // 两套令牌都在 :root / .theme-night（见上面的样式表），这里只负责开关这个类。
  var mq = (window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null);
  var themeForced = false;
  function applyTheme(night) {
    S.night = !!night;
    paeroot.classList.toggle('theme-night', S.night);
    var tb = root.getElementById('themebtn');
    if (tb) {
      tb.textContent = S.night ? '羊皮纸' : '暗夜';
      tb.title = S.night ? '切到羊皮纸主题' : '切到暗夜主题';
    }
  }
  S.applyTheme = applyTheme;
  applyTheme(mq ? mq.matches : false);          // 初始值 = 系统偏好
  if (mq) {
    var onMq = function (e) { if (!themeForced) applyTheme(e.matches); };
    if (mq.addEventListener) mq.addEventListener('change', onMq);
    else if (mq.addListener) mq.addListener(onMq);
  }
  root.getElementById('themebtn').addEventListener('click', function () {
    themeForced = true;                         // 手动覆盖：此后不再跟随系统偏好
    applyTheme(!S.night);
  });

  // ---- 面板渲染：把 SW 塞进 html 里的 panel 数据铺成基准的结构 ----
  // （content.js 只转发 html 字符串；数据藏在 [data-pae-panel] 属性里，壳侧解析——不改 content.js）
  function esc(v) {
    return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  // L2 比例显示规则（与 SW 侧 paeFmtPct 同规则；主世界函数不能引用 SW 作用域，故此处重写一份）：
  // ≥1% 显示整数百分比；<1% 且 >0 显示一位小数（0.004 → 「0.4%」）；0 / null / 非数 → 「—」。
  function fmtPct(v) {
    if (typeof v !== 'number' || !isFinite(v) || v <= 0) return '—';
    var p = v * 100;
    return (p >= 1) ? (Math.round(p) + '%') : ((Math.round(p * 10) / 10).toFixed(1) + '%');
  }
  // 圆环：弧长按**真实值**；真实值 >0 但小于 0.03 时给一个最小可视弧（0.03 → 10.8°），
  // 只是「别让 0.4% 看起来像空环」的可视化下限——环内文字 / 悬浮说明永远是精确值，不假造数据。
  // text 省略时（词行 S 值）沿用旧的整数读数（0..100，不是百分比）。
  function ringHTML(v, sm, text) {
    var x = (typeof v === 'number' && isFinite(v)) ? Math.max(0, Math.min(1, v)) : 0;
    var arc = x > 0 ? Math.max(x, 0.03) : 0;
    var hue = 38 + x * 120;
    var label = (text == null) ? String(Math.round(x * 100)) : String(text);
    return '<div class="ring' + (sm ? ' sm' : '') + '" style="background:conic-gradient(hsla(' + hue +
      ',80%,55%,.9) ' + Math.round(arc * 360) + 'deg, var(--pae-line) 0)"><span>' + esc(label) + '</span></div>';
  }
  function deltaHTML(d) {
    var neg = (typeof d === 'number' && d < 0);
    var txt = (typeof d === 'number' && isFinite(d)) ? (d >= 0 ? '+' : '') + d.toFixed(2) : '—';
    return '<span class="delta' + (neg ? ' neg' : '') + '">' + txt + '</span>';
  }
  function renderPanel(data) {
    var box = root.getElementById('sidemetrics');
    if (box) {
      var ms = (data && data.metrics) || [], mh = '';
      for (var i = 0; i < ms.length; i++) {
        var note = String(ms[i].note || '');   // note 里已带样本量（如「4 次点击 / 962 次曝光」）
        mh += '<div class="row"' + (note ? ' title="' + esc(note) + '"' : '') + '>' +
          ringHTML(ms[i].value, false, fmtPct(ms[i].value)) +
          '<div class="w">' + esc(ms[i].label) + '<span class="g">' + esc(note) + '</span></div>' +
          deltaHTML(ms[i].delta) + '</div>';
      }
      box.innerHTML = mh;
    }
    var ws = (data && data.words) || [], wh = '';
    for (var j = 0; j < ws.length; j++) {
      var g = String(ws[j].gloss || '');
      if (g.length > 16) g = g.slice(0, 16) + '…';   // 释义截断
      wh += '<div class="row">' + ringHTML(ws[j].s_value, true) +
        '<div class="w">' + esc(ws[j].lemma) + '<span class="g">' + esc(g) + '</span></div>' +
        deltaHTML(ws[j].delta) + '</div>';
    }
    if (!wh) wh = '<div class="row empty">还没攒到词——翻几页英文就来</div>';
    sidewords.innerHTML = wh;
    var ss = (data && data.stamps) || [], sh = '';
    for (var k = 0; k < ss.length; k++) {
      sh += '<span class="stamp ' + esc(ss[k].cls || '') + '" data-tag="' + esc(ss[k].tag || '记忆') + '">' +
        esc(ss[k].text || '') + '</span>';
    }
    if (!sh) sh = '<span class="stamp" data-tag="状态">它还没记下什么</span>';
    sidestamps.innerHTML = sh;
  }
  S.renderPanel = renderPanel;
  function paintStats(html) {
    var st2 = root.getElementById('sidestats');
    st2.innerHTML = html || '…';
    var node = st2.querySelector('[data-pae-panel]');
    var data = null;
    if (node) { try { data = JSON.parse(node.getAttribute('data-pae-panel') || 'null'); } catch (e) {} }
    renderPanel(data);
  }
  S.paintStats = paintStats;

  function setName(n) {
    S.name = n || '小P';
    capname.textContent = S.name;
    who.textContent = S.name;
    mwho.textContent = S.name;
    root.getElementById('sidename').textContent = S.name;
  }
  S.setName = setName;

  // 对话窗：一条 = 一个气泡（.turn.a 她 / .turn.u 你），不再是一张张三段卡
  // （旧版卡片堆在 .bd 顶部，聊 30 轮就把掌握度/最近在啃/它记下的全挤掉——2026-10-03 二次反馈）。
  function pushTurn(role, text, ts) {
    text = String(text == null ? '' : text);
    if (!text) return;
    var d = document.createElement('div');
    d.className = 'turn ' + (role === 'user' ? 'u' : 'a');
    d.textContent = text;
    var t = document.createElement('span');
    t.className = 't';
    var when = (typeof ts === 'number' && ts > 0) ? new Date(ts * 1000) : new Date();
    t.textContent = when.toLocaleTimeString().slice(0, 5);
    d.appendChild(t);
    if (text.length > 120) {          // 长回复先夹起：点击展开/收起
      d.classList.add('clamp');
      d.title = '点击展开全文';
      d.addEventListener('click', function () { d.classList.toggle('exp'); });
    }
    chatbody.appendChild(d);
    while (chatbody.children.length > 300) chatbody.removeChild(chatbody.firstChild);
    chatcount.textContent = chatbody.children.length ? ('共 ' + chatbody.children.length + ' 条') : '';
    chatbody.scrollTop = chatbody.scrollHeight;
    S.log.push({ role: role, text: text, ts: Date.now() });
    if (S.log.length > 400) S.log.shift();
  }
  S.pushTurn = pushTurn;
  // 打开侧栏时从引擎拉对话历史（chat_turns 是持久表：刷新/换页/换浏览器都不丢）
  function renderHistory(turns) {
    chatbody.innerHTML = '';
    chatcount.textContent = '';
    var arr = Array.isArray(turns) ? turns : [];
    for (var i = 0; i < arr.length; i++) {
      var tr = arr[i] || {};
      pushTurn(tr.role === 'user' ? 'user' : 'agent', tr.text, tr.ts);
    }
  }
  S.renderHistory = renderHistory;

  function show(text, kind) {
    if (!text) return;
    if (kind === 'say' && S.muted) return;
    btext.textContent = text;
    bubble.style.display = 'block';
    S.seen++;
    pushTurn('agent', text);
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(function () { bubble.style.display = 'none'; }, 12000);
  }
  S.show = show;

  // ---- 边注：指着某段说（不碰页面 DOM，用文档坐标覆盖层）----
  function findTarget(anchor) {
    var a = String(anchor || '').trim();
    if (!a) return null;
    var els = document.querySelectorAll('p,li,td,dd,blockquote,section,article,div');
    var best = null, bestScore = -1;
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var txt = el.textContent || '';
      if (txt.indexOf(a) < 0) continue;
      var r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 10) continue;
      // 偏好贴近视口上方、且文本较短的块（更精确）
      var score = (r.top > -50 && r.top < window.innerHeight ? 1000 : 0) - txt.length / 100;
      if (score > bestScore) { bestScore = score; best = el; }
    }
    return best;
  }
  function marginSay(anchor, text) {
    var el = findTarget(anchor);
    if (!el || !text) return false;
    var r = el.getBoundingClientRect();
    var w = 260, h = 70;
    var left = Math.max(8, r.right + 14);
    var top = Math.max(8, r.top + 4);
    if (left + w > window.innerWidth - 8) {
      // 右侧放不下（段落几乎占满整宽）→ 放到段落**下方右侧**，不盖住正文
      left = Math.min(Math.max(8, r.right - w), Math.max(8, window.innerWidth - w - 8));
      top = r.bottom + 10;
    }
    if (top + h > window.innerHeight) { top = Math.max(8, window.innerHeight - h - 10); }
    margin.style.left = left + 'px';
    margin.style.top = top + 'px';
    mtext.textContent = text;
    margin.style.display = 'block';
    mdot.style.left = (r.right + 2) + 'px';
    mdot.style.top = (r.top + 12) + 'px';
    mdot.style.display = 'block';
    pushTurn('agent', text);
    clearTimeout(marginTimer);
    marginTimer = setTimeout(function () { margin.style.display = 'none'; mdot.style.display = 'none'; }, 15000);
    // 目标段落短暂高亮（独立注册名 pae-anchor，不动 DOM；15 秒后随边注一起撤）
    // —— 这里曾是一段空壳死代码（2026-10-02 需求漂移审计抓到），现在补上真实现
    try {
      if (window.Highlight && CSS.highlights) {
        var _sid = 'pae-anchor-style';
        if (!document.getElementById(_sid)) {
          var _st = document.createElement('style');
          _st.id = _sid;
          _st.textContent = '::highlight(pae-anchor) { background-color: rgba(245,158,11,0.20); }';
          (document.head || document.documentElement).appendChild(_st);
        }
        var _rr = document.createRange();
        _rr.selectNodeContents(el);
        CSS.highlights.set('pae-anchor', new Highlight(_rr));
        clearTimeout(S._anchorT);
        S._anchorT = setTimeout(function () {
          try { CSS.highlights.delete('pae-anchor'); } catch (e4) {}
        }, 15000);
      }
    } catch (e5) {}
    return true;
  }
  S.marginSay = marginSay;

  function ask(text) {
    if (!text) return;
    // 2026-10-03 二次反馈修复：旧代码先 show(text)（把用户的话当**她的**气泡显示并
    // 记成 agent），再 pushLog('user')——侧栏里每句话出现两次、第一句角色颠倒
    // （用户实测「在吗」先显示成小P说的）。现在只记用户一条；她的回复到达再记一条。
    pushTurn('user', text);
    try {
      window.postMessage({ source: 'pae', type: 'char_say', text: text,
                           page_id: String(location.href).split('#')[0].slice(0, 200) }, '*');
    } catch (e) {}
  }
  S.ask = ask;

  cap.addEventListener('click', function () { S.open = !S.open; panel.style.display = S.open ? 'block' : 'none'; });
  root.getElementById('send').addEventListener('click', function () {
    var el = root.getElementById('in');
    if (el.value.trim()) { ask(el.value.trim()); el.value = ''; }
  });
  root.getElementById('in').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && this.value.trim()) { ask(this.value.trim()); this.value = ''; }
  });
  root.getElementById('mute').addEventListener('click', function () {
    S.muted = !S.muted;
    this.textContent = S.muted ? '取消静音' : '静音';
    cap.className = S.muted ? 'cap muted' : 'cap';
    try { window.postMessage({ source: 'pae', type: 'char_mute', muted: S.muted }, '*'); } catch (e) {}
  });
  function openSide(v) {
    side.style.display = v ? 'flex' : 'none';
    if (v) {
      // 打开就自动拉一次：圆环 / 词行 / 印章一开就有（原来是手动点「刷新」）；
      // 对话历史也从引擎拉全量再渲染（不再显示「(侧栏已打开)」假条目——用户吐槽它像她说的）。
      // 已有本地对话（当前会话进行中）就不重拉——避免把还没落库的当前对话冲掉。
      try { window.postMessage({ source: 'pae', type: 'char_stats' }, '*'); } catch (e) {}
      if (!chatbody.children.length) {
        try { window.postMessage({ source: 'pae', type: 'char_chathist' }, '*'); } catch (e) {}
      }
    }
    S.sideOpen = v;
  }
  S.openSide = openSide;
  root.getElementById('sidebtn').addEventListener('click', function () { openSide(true); });
  root.getElementById('sideclose').addEventListener('click', function () { openSide(false); });
  root.getElementById('sidesend').addEventListener('click', function () {
    var el = root.getElementById('sidein');
    if (el.value.trim()) { ask(el.value.trim()); el.value = ''; }
  });
  root.getElementById('sidein').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && this.value.trim()) { ask(this.value.trim()); this.value = ''; }
  });
  root.getElementById('sidereload').addEventListener('click', function () {
    try { window.postMessage({ source: 'pae', type: 'char_stats' }, '*'); } catch (e) {}
  });
  root.getElementById('chathd').addEventListener('click', function () {
    chatbox.classList.toggle('open');   // 点「对话」标题条 = 展开/收起整个对话窗
  });
  margin.addEventListener('click', function () { margin.style.display = 'none'; mdot.style.display = 'none'; });

  window.addEventListener('message', function (ev) {
    if (ev.source !== window) return;
    var d = ev.data;
    if (!d || d.source !== 'pae-char') return;
    if (d.type === 'say') {
      if (d.anchor) { if (!marginSay(d.anchor, d.text)) show(d.text, 'say'); }
      else show(d.text, 'say');
    } else if (d.type === 'reply') show(d.text, 'reply');
    else if (d.type === 'system') show(d.text, 'say');
    else if (d.type === 'name') setName(d.name);
    else if (d.type === 'stats') paintStats(d.html);
    else if (d.type === 'chathist') renderHistory(d.turns);
  });

  setName(S.name);
  try { root.getElementById('sidever').textContent = (opts.ver ? ('v' + opts.ver) : ''); } catch (e) {}
  renderPanel(null);   // 先把「最近在啃 / 它记下的」的空态铺上，有数据时再由 paintStats 覆盖
  openSide(false);
  return { ok: true, name: S.name };
}

function paeHoverInitMain() {
  var S = window.__paeHL;
  if (!S || S.hoverBound) return;
  S.hoverBound = true;
  var host = document.createElement('div');
  host.id = 'pae-hover-host';
  host.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none;';
  var root = host.attachShadow({ mode: 'open' });
  root.innerHTML =
    '<style>' +
    '.c{position:absolute;pointer-events:auto;width:250px;padding:9px 12px;' +
    'background:linear-gradient(160deg,rgba(255,253,246,.99),rgba(255,247,231,.99));' +
    'border:1px solid rgba(217,119,6,.34);border-radius:11px;' +
    'box-shadow:0 6px 22px rgba(120,80,10,.16), inset 0 1px 0 rgba(255,255,255,.9);' +
    'font:13px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#2b2117;display:none}' +
    '.w{font-weight:650;font-size:14px;letter-spacing:.01em}' +
    '.g{color:#5c4a33;margin-top:3px}' +
    '.m{margin-top:7px;display:flex;align-items:center;gap:7px;color:#8a7355;font-size:11.5px}' +
    '.pill{padding:1px 7px;border-radius:99px;background:rgba(255,214,102,.6);color:#7c4a03;font-weight:600}' +
    '.b{margin-left:auto;cursor:pointer;border:1px solid rgba(217,119,6,.45);background:#fff;' +
    'color:#a1550a;border-radius:7px;padding:2px 9px;font:11.5px system-ui}' +
    '.b:hover{background:#fff7e6}' +
    '</style><div class="c" id="c"></div>';
  (document.documentElement || document.body).appendChild(host);
  var card = root.getElementById('c');
  var shownKey = null, hideTimer = null, lastMove = 0;

  function hide() { card.style.display = 'none'; shownKey = null; }
  function bandName(s) { return s < 0.10 ? '新词' : (s <= 0.55 ? '学习带' : '熟悉'); }
  function place(rect) {
    var w = 250, h = 104;
    var left = Math.min(Math.max(8, rect.left - 20), window.innerWidth - w - 8);
    var top = rect.top - h - 8;
    if (top < 8) top = rect.bottom + 10;
    card.style.left = left + 'px';
    card.style.top = top + 'px';
  }
  function pageId() { return String(location.href).split('#')[0].slice(0, 200); }

  host.addEventListener('mouseenter', function () { if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; } });
  host.addEventListener('mouseleave', function () { hideTimer = setTimeout(hide, 240); });

  card.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t || t.className !== 'b') return;
    var m = S.meta[shownKey] || {};
    try {
      window.postMessage({ source: 'pae', type: 'known_click', lemma: m.lemma || '',
                           surface: m.surface || '', s_value: m.s_value, page_id: pageId() }, '*');
    } catch (err) {}
    t.textContent = '记下了';
    t.style.background = '#fff7e6';
  });

  function hit(x, y) {
    var r = null;
    try {
      if (document.caretRangeFromPoint) {
        r = document.caretRangeFromPoint(x, y);
      } else if (document.caretPositionFromPoint) {
        var p = document.caretPositionFromPoint(x, y);
        if (p) { r = document.createRange(); r.setStart(p.offsetNode, p.offset); }
      }
    } catch (e) { return null; }
    if (!r || !r.startContainer || r.startContainer.nodeType !== 3) return null;
    var nid = S.nodeIds.get(r.startContainer);
    if (!nid) return null;
    var off = r.startOffset;
    for (var i = 0; i < S.entries.length; i++) {
      var parts = String(S.entries[i].key).split(':');
      if (+parts[0] !== nid) continue;
      var st = +parts[1], en = st + +parts[2];
      if (off >= st && off <= en) return S.entries[i];
    }
    return null;
  }

  document.addEventListener('mousemove', function (e) {
    if (card.style.display === 'block' && e.target === host) return;  // 鼠标在卡上，别关
    var now = Date.now();
    if (now - lastMove < 70) return;
    lastMove = now;
    var ent = hit(e.clientX, e.clientY);
    if (!ent) {
      if (card.style.display === 'block' && !hideTimer) hideTimer = setTimeout(hide, 200);
      return;
    }
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    if (ent.key === shownKey && card.style.display === 'block') return;
    var m = S.meta[ent.key] || {};
    var s = (typeof m.s_value === 'number') ? m.s_value : 0;
    card.innerHTML = '<div class="w">' + (m.surface || '') +
      (m.lemma && m.lemma !== m.surface ? ' → ' + m.lemma : '') + '</div>' +
      '<div class="g">' + (m.gloss || '（无释义）') + '</div>' +
      '<div class="m"><span class="pill">' + bandName(s) + '</span><span>S ' + s.toFixed(2) + '</span>' +
      '<button class="b">认识了</button></div>';
    place(ent.range.getBoundingClientRect());
    card.style.display = 'block';
    shownKey = ent.key;
    try {
      var sent = '';
      try {
        var pe2 = ent.range.startContainer.parentElement;
        if (pe2 && pe2.textContent) sent = pe2.textContent.trim().slice(0, 300);
      } catch (e3) {}
      window.postMessage({ source: 'pae', type: 'hover', lemma: m.lemma || '',
                           surface: m.surface || '', s_value: s, page_id: pageId(), sentence: sent }, '*');
    } catch (err2) {}
  }, true);
}