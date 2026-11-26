'use strict';

(() => {
  if (window.parent === window) return;
  let attached = false;
  window.addEventListener('message', function bind(event) {
    const offer = event.data;
    if (attached || event.source !== window.parent || offer?.type !== 'pae-character-bind' ||
        typeof offer.token !== 'string' || !/^[a-f0-9]{32}$/.test(offer.token) ||
        !['quick', 'side', 'bubble', 'margin', 'word'].includes(offer.surface) || event.ports.length !== 1) return;
    const port = event.ports[0];
    chrome.runtime.sendMessage({ type: 'PAE_CHAR_VERIFY', token: offer.token, surface: offer.surface }, (result) => {
      if (chrome.runtime.lastError || !result?.ok || attached) {
        port.close();
        return;
      }
      attached = true;
      window.removeEventListener('message', bind);
      if (offer.surface === 'word') renderWordCard(port);
      else renderCharacter(offer.surface, port);
    });
  });
})();

function renderWordCard(port) {
  const card = document.createElement('div');
  card.innerHTML = `<style>
    html,body{margin:0;background:transparent;overflow:hidden}
    .word-card{box-sizing:border-box;width:100%;max-height:100vh;display:flex;flex-direction:column;
      padding:9px 12px;background:linear-gradient(160deg,rgba(255,253,246,.99),rgba(255,247,231,.99));
      border:1px solid rgba(217,119,6,.34);border-radius:11px;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.9);
      color:#2b2117;font:13px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
    .word-title{flex:none;font-weight:650;font-size:14px;letter-spacing:.01em;overflow-wrap:anywhere}
    .word-gloss{min-height:0;overflow:auto;overflow-wrap:anywhere;color:#5c4a33;margin-top:3px}
    .word-meta{flex:none;margin-top:7px;display:flex;align-items:center;gap:7px;flex-wrap:wrap;color:#8a7355;font-size:11.5px}
    .word-badge{padding:1px 7px;border-radius:99px;background:rgba(255,214,102,.6);color:#7c4a03;font-weight:600}
    .word-button{margin-left:auto;cursor:pointer;border:1px solid rgba(217,119,6,.45);background:#fff;
      color:#a1550a;border-radius:7px;padding:2px 9px;font:11.5px system-ui}
    .word-button:hover{background:#fff7e6}
    .word-button:disabled{cursor:default;opacity:.75}
    .word-notice{flex:none;font-size:11px;color:#8a7355;margin-top:5px}
  </style><div class="word-card">
    <div class="word-title"></div><div class="word-gloss"></div>
    <div class="word-meta"><span class="word-badge"></span><span class="word-score"></span>
      <button class="word-button" type="button" disabled>认识了</button></div>
    <div class="word-notice" role="status"></div>
  </div>`;
  document.body.appendChild(card);
  const box = card.querySelector('.word-card');
  const title = card.querySelector('.word-title'), gloss = card.querySelector('.word-gloss');
  const badge = card.querySelector('.word-badge'), score = card.querySelector('.word-score');
  const button = card.querySelector('button'), notice = card.querySelector('.word-notice');
  let viewId, lastHeight;
  function reportSize() {
    if (viewId == null) return;
    const height = Math.ceil(box.getBoundingClientRect().height +
      Math.max(0, gloss.scrollHeight - gloss.clientHeight));
    if (height <= 0 || height === lastHeight) return;
    lastHeight = height;
    port.postMessage({ type: 'word-size', viewId, height });
  }
  box.addEventListener('pointerenter', () => port.postMessage({ type: 'word-enter', viewId }));
  box.addEventListener('pointerleave', () => port.postMessage({ type: 'word-leave', viewId }));
  button.addEventListener('click', (event) => {
    if (event.isTrusted && !button.disabled && viewId != null) port.postMessage({ type: 'word-known', viewId });
  });
  const observer = new ResizeObserver(reportSize);
  observer.observe(box);
  port.onmessage = ({ data }) => {
    if (data?.type !== 'word-view') return;
    if (data.viewId !== viewId) lastHeight = null;
    viewId = data.viewId;
    title.textContent = data.title;
    gloss.textContent = data.gloss;
    badge.textContent = data.score < 0.10 ? '新词' : data.score <= 0.55 ? '学习带' : '熟悉';
    score.textContent = 'S ' + data.score.toFixed(2);
    button.textContent = data.label;
    button.disabled = data.disabled;
    notice.textContent = data.error;
    reportSize();
  };
  port.postMessage({ type: 'ready' });
}

function renderCharacter(surface, port) {
  var S = { muted: false, name: '小P', open: false };
  var host = document.createElement('div');
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
  document.body.appendChild(host);
  var bubble = root.getElementById('bubble');
  var btext = root.getElementById('btext');
  var who = root.getElementById('who');
  var panel = root.getElementById('panel');
  var capname = root.getElementById('capname');
  var cap = root.getElementById('cap');
  var side = root.getElementById('side');
  var chatbox = root.getElementById('chatbox');
  var chatbody = root.getElementById('chatbody');
  var chatcount = root.getElementById('chatcount');
  var margin = root.getElementById('margin');
  var mtext = root.getElementById('mtext');
  var mwho = root.getElementById('mwho');
  var paeroot = root.getElementById('paeroot');
  var sidewords = root.getElementById('sidewords');
  var sidestamps = root.getElementById('sidestamps');

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
  applyTheme(mq ? mq.matches : false);          // 初始值 = 系统偏好
  if (mq) {
    var onMq = function (e) { if (!themeForced) applyTheme(e.matches); };
    if (mq.addEventListener) mq.addEventListener('change', onMq);
    else if (mq.addListener) mq.addListener(onMq);
  }
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
      var unscored = ws[j].has_learning_state === false;
      wh += '<div class="row">' + (unscored ? '' : ringHTML(ws[j].s_value, true)) +
        '<div class="w">' + esc(ws[j].lemma) + '<span class="g">' + esc(g) + '</span>' +
        (unscored ? '<span class="g">尚无学习评分</span>' : '') + '</div>' +
        (unscored ? '' : deltaHTML(ws[j].delta)) + '</div>';
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
  function paintStats(result) {
    const summary = root.getElementById('sidestats');
    summary.replaceChildren();
    if (!result?.summary) summary.textContent = '（引擎没在跑）';
    for (const row of result?.summary || []) {
      const label = document.createElement('b');
      label.textContent = row.label;
      if (summary.childNodes.length) summary.append(document.createElement('br'));
      summary.append(label, ' ' + row.text);
    }
    renderPanel(result?.panel);
  }

  function setName(n) {
    S.name = n || '小P';
    capname.textContent = S.name;
    who.textContent = S.name;
    mwho.textContent = S.name;
    root.getElementById('sidename').textContent = S.name;
  }

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
  }
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


  const wrap = root.querySelector('.wrap');
  const selected = { quick: wrap, side: side, bubble: bubble, margin: margin }[surface];
  wrap.style.cssText = 'right:0;bottom:0';
  if (surface === 'side' || surface === 'margin') wrap.style.display = 'none';
  if (surface === 'side') {
    side.style.display = 'flex';
    side.style.width = '100%';
  }
  if (surface === 'margin') margin.style.cssText = 'top:0;left:0;display:block;overflow-wrap:anywhere';
  if (surface === 'bubble') {
    cap.style.display = 'none';
    bubble.style.cssText = 'display:block;overflow-wrap:anywhere';
  }
  if (surface === 'quick') {
    panel.style.maxWidth = 'calc(100vw - 24px)';
    root.getElementById('in').style.minWidth = '0';
  }

  function emit(type, fields) { port.postMessage(Object.assign({ type: type }, fields)); }
  let noticeReady = false;
  function measure() {
    if (surface === 'side' || ((surface === 'bubble' || surface === 'margin') && !noticeReady)) return;
    const rect = selected.getBoundingClientRect();
    const capRect = cap.getBoundingClientRect();
    emit('size', {
      width: Math.ceil(rect.width), height: Math.ceil(rect.height),
      capWidth: Math.ceil(capRect.width), capHeight: Math.ceil(capRect.height),
      open: S.open,
    });
  }

  function setMuted(muted) {
    S.muted = !!muted;
    root.getElementById('mute').textContent = S.muted ? '取消静音' : '静音';
    cap.className = S.muted ? 'cap muted' : 'cap';
  }

  cap.addEventListener('click', () => {
    S.open = !S.open;
    panel.style.display = S.open ? 'block' : 'none';
    measure();
  });
  root.getElementById('mute').addEventListener('click', () => emit('mute', { muted: !S.muted }));
  root.getElementById('sidebtn').addEventListener('click', () => emit('open-side'));
  root.getElementById('sideclose').addEventListener('click', () => emit('close-side'));
  root.getElementById('sidereload').addEventListener('click', () => emit('refresh'));
  root.getElementById('chathd').addEventListener('click', () => chatbox.classList.toggle('open'));
  margin.addEventListener('click', () => emit('close-margin'));
  root.getElementById('themebtn').addEventListener('click', () => emit('theme', { night: S.night }));

  function bindInput(fieldId, buttonId) {
    const field = root.getElementById(fieldId);
    function send() {
      if (!field.value.trim()) return;
      emit('say', { text: field.value.trim() });
      field.value = '';
    }
    root.getElementById(buttonId).addEventListener('click', send);
    field.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) send();
    });
  }
  bindInput('in', 'send');
  bindInput('sidein', 'sidesend');

  port.onmessage = ({ data }) => {
    if (data.type === 'state') {
      setName(data.name);
      setMuted(data.muted);
      if (typeof data.night === 'boolean') {
        themeForced = true;
        applyTheme(data.night);
      }
      root.getElementById('sidever').textContent = 'v' + chrome.runtime.getManifest().version;
      if (surface === 'side' && data.turns) renderHistory(data.turns);
      measure();
    } else if (data.type === 'stats' && surface === 'side') {
      paintStats(data.result);
    } else if (data.type === 'history' && surface === 'side') {
      renderHistory(data.turns);
    } else if (data.type === 'turn' && surface === 'side') {
      pushTurn(data.turn.role, data.turn.text, data.turn.ts);
    } else if (data.type === 'notice' && (surface === 'bubble' || surface === 'margin')) {
      noticeReady = true;
      (surface === 'bubble' ? btext : mtext).textContent = data.text;
      measure();
    }
  };
  renderPanel(null);
  const observer = new ResizeObserver(measure);
  observer.observe(selected);
  emit('ready');
}
