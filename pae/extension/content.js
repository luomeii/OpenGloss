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
  let paeDisabled = true;  // 等待读取开关后才允许请求，避免关闭状态的首次泄漏


  // 调试快照只保留在 content script 的隔离世界。
  function paeDebug(obj) {
    try {
      window.__PAE_DEBUG = obj;
    } catch (e) { /* swallow */ }
  }

  // 每请求文本上限（约 200 词）：保证每块都能吃满引擎的 8 词预算。
  // 否则整页塞进一个请求 → 整页最多只有 8 个注解（「整页只有几处高亮」的第二个原因）。
  const CHUNK_CHARS = 1400;
  // 每页注解上限：**由引擎的有效参数决定**（修「死旋钮」——AI 提案改了它必须真的生效）
  let annCap = 40;
  function refreshLimits(done) {
    try {
      chrome.runtime.sendMessage({ type: 'PAE_GET_LIMITS' }, function (r) {
        try {
          if (!chrome.runtime.lastError && r && r.limits &&
              Number.isInteger(r.limits.max_ann_per_page) && r.limits.max_ann_per_page > 0)
            annCap = r.limits.max_ann_per_page;
        } catch (e) {} finally { if (done) done(); }
      });
    } catch (e) { if (done) done(); }
  }
  let annTotal = 0;
  let curPageId = '';
  const documentToken = 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2);
  const visibleReported = new Set(); // bounded per document, independent of learning/annotation budgets
  let revision = 0, cycleReady = false, busy = false;
  const annotationQueue = [];
  function pageIdNow() { return String(location.href).split('#')[0].slice(0, 200); }
  function annotationState() {
    return { documentToken: documentToken, revision: revision, pageId: pageIdNow(), enabled: !paeDisabled };
  }
  function beginAnnotationCycle(reextract) {
    const cycle = ++revision;
    resetWordCard();
    cycleReady = false; busy = false; annTotal = 0;
    curPageId = pageIdNow(); annotationQueue.length = 0; BATCH.length = 0; SEEN.clear();
    if (timer) { clearTimeout(timer); timer = null; }
    paeDebug({ state: paeDisabled ? 'disabled' : 'preparing', ann_total: 0, ann_cap: annCap });
    try {
      chrome.runtime.sendMessage(Object.assign({ type: 'PAE_ANNOTATION_STATE', clear: true }, annotationState()), function (r) {
        if (cycle !== revision) return;
        if (chrome.runtime.lastError || !r || !r.ok) {
          paeDebug({ lastError: (r && r.error) || 'annotation-state-unavailable' }); return;
        }
        if (paeDisabled) return;
        refreshLimits(function () {
          if (cycle !== revision || paeDisabled) return;
          cycleReady = true;
          if (reextract) extractNow();
          if (BATCH.length) sendBatch();
          pumpAnnotations();
        });
      });
    } catch (e) { paeDebug({ lastError: String(e) }); }
  }

  function pumpAnnotations() {
    if (annTotal >= annCap) { annotationQueue.length = 0; return; }
    if (paeDisabled || !cycleReady || busy || !annotationQueue.length) return;
    if (pageIdNow() !== curPageId) { beginAnnotationCycle(true); return; }
    const job = annotationQueue.shift(), cycle = revision;
    const remaining = annCap - annTotal;
    busy = true;
    const message = Object.assign({ type: 'PAE_ANNOTATE', text: job.text, sessionId: SESSION_ID,
      maxAnnotations: remaining }, annotationState());
    function completed(resp) {
      if (cycle !== revision) return;
      busy = false;
      if (paeDisabled) return;
      if (pageIdNow() !== curPageId) { beginAnnotationCycle(true); return; }
      try {
        if (chrome.runtime.lastError || !resp || !resp.ok) {
          paeDebug({ lastError: (chrome.runtime.lastError && chrome.runtime.lastError.message) ||
            (resp && resp.error) || 'no-response', ann_total: annTotal, ann_cap: annCap });
        } else {
          const n = Array.isArray(resp.data) ? resp.data.length : -1;
          if (n < 0 || n > remaining) {
            paeDebug({ lastError: 'annotation-budget-contract', ann_total: annTotal, ann_cap: annCap });
          } else {
            annTotal += n;
            paeDebug({ lastResponse: resp.data, annotations: n,
              rendered: resp.rendered || { rendered: -1, reason: 'no-rendered-field' },
              chunk: job.chunk, ann_total: annTotal, ann_cap: annCap });
            try { if (n > 0) mountCharacter(); } catch (e) { /* annotation result is independent */ }
            if (resp.rendered?.added > 0) {
              const nodes = indexWordRanges(resp.data);
              observeDrawnWords(resp.data, resp.rendered.drawn, nodes);
            }
          }
        }
      } catch (e) { paeDebug({ renderError: String(e) }); }
      pumpAnnotations();
    }
    try { chrome.runtime.sendMessage(message, completed); }
    catch (e) { completed({ ok: false, error: String(e) }); }
  }

  function sendChunks(chunks, pageId) {
    if (pageId !== curPageId || paeDisabled) return;
    for (let i = 0; i < chunks.length; i++)
      annotationQueue.push({ text: chunks[i].join('\n'), chunk: (i + 1) + '/' + chunks.length });
    pumpAnnotations();
  }

  function sendBatch() {
    try {
      if (paeDisabled) return;
      if (!BATCH.length) return;
      const pageId = pageIdNow();
      if (pageId !== curPageId) { beginAnnotationCycle(true); return; }
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
      if (paeDisabled) return;
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

  // Character data stays on extension channels. The page only hosts the frame rectangles.
  const characterFrames = new Map();
  let characterHost, characterRoot, characterPage;
  let characterName, characterTheme, characterMuted = false, characterSide = false;
  let characterHistoryPending = false, characterStatsSequence = 0;
  let characterTurns = [];

  const wordRanges = new Map(), wordFeedback = new Map();
  const visibilityWords = new Set();
  let visibilityObserver = null;
  const wordSkip = 'script,style,noscript,code,pre,kbd,samp,textarea,input,select,button,nav,footer,header,aside';
  let wordHost, wordRoot, wordView, wordHideTimer, wordViewId = 0, wordPointerFrame = null;
  let wordListeners;

  function supportsWholeWord(surface) {
    return /^[A-Za-z]+(?:['-][A-Za-z]+)*$/.test(surface);
  }

  function completeWord(text, start, end) {
    const before = text.slice(Math.max(0, start - 4), start);
    const after = text.slice(end, end + 4);
    return !/[\p{L}\p{M}\p{N}_]['’‐‑-]?$/u.test(before) &&
      !/^['’‐‑-]?[\p{L}\p{M}\p{N}_]/u.test(after);
  }

  function validWord(word) {
    return !!word && !paeDisabled && word.revision === revision && word.node.isConnected &&
      word.range.startContainer === word.node && word.range.endContainer === word.node &&
      word.range.toString() === word.original &&
      (!supportsWholeWord(word.surface) ||
        completeWord(word.node.textContent, word.range.startOffset, word.range.endOffset));
  }

  function indexWordRanges(annotations) {
    // Match the public renderer's first occurrence and duplicate handling.
    for (const [node] of wordRanges) if (!node.isConnected) wordRanges.delete(node);
    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
    const nodes = [];
    for (let node; (node = walker.nextNode());) {
      if (node.parentElement && !node.parentElement.closest(wordSkip) && node.textContent.trim()) nodes.push(node);
    }
    const positions = new Map();
    const wanted = new Set();
    for (const annotation of annotations) {
      if (!annotation?.gloss || typeof annotation.surface !== 'string' || annotation.surface.length < 3) continue;
      const surface = annotation.surface;
      if (supportsWholeWord(surface)) {
        wanted.add(surface.toLowerCase());
        continue;
      }
      if (positions.has(surface)) continue;
      for (const node of nodes) {
        const start = node.textContent.indexOf(surface);
        if (start < 0) continue;
        positions.set(surface, { node, start, end: start + surface.length, original: surface });
        break;
      }
    }
    if (wanted.size) {
      const pattern = new RegExp([...wanted].sort((a, b) => b.length - a.length).join('|'), 'gi');
      for (const node of nodes) {
        if (!wanted.size) break;
        const text = node.textContent;
        for (const match of text.matchAll(pattern)) {
          const original = match[0];
          const surface = original.toLowerCase();
          const start = match.index;
          const end = start + original.length;
          if (!wanted.has(surface) || !completeWord(text, start, end)) continue;
          positions.set(surface, { node, start, end, original });
          wanted.delete(surface);
          if (!wanted.size) break;
        }
      }
    }
    for (const annotation of annotations) {
      if (!annotation?.gloss || typeof annotation.surface !== 'string') continue;
      const surface = annotation.surface;
      const position = positions.get(supportsWholeWord(surface) ? surface.toLowerCase() : surface);
      if (!position) continue;
      const { node, start, end, original } = position;
      const key = start + ':' + surface.length;
      const entries = wordRanges.get(node) || new Map();
      if (entries.has(key)) continue;
      const range = document.createRange();
      range.setStart(node, start);
      range.setEnd(node, end);
      entries.set(key, { revision, node, range, surface, original, lemma: annotation.lemma,
        gloss: annotation.gloss, s_value: Number.isFinite(annotation.s_value) ? annotation.s_value : 0 });
      wordRanges.set(node, entries);
    }
    if (wordRanges.size && !wordListeners) {
      wordListeners = new AbortController();
      const signal = wordListeners.signal;
      window.addEventListener('mousemove', queueWordPointer, { capture: true, signal });
      window.addEventListener('scroll', placeWordCard, { capture: true, passive: true, signal });
      window.addEventListener('resize', placeWordCard, { signal });
    }
    return nodes;
  }

  // Observation state remains in this isolated closure. MAIN still paints highlights.
  function observeDrawnWords(annotations, drawn, nodes) {
    if (!Array.isArray(drawn) || !Array.isArray(nodes) || drawn.length > annotations.length) return;
    for (const item of drawn) {
      if (!item || !Number.isInteger(item.index) || !Number.isInteger(item.nodeIndex) ||
          !Number.isInteger(item.start) || !Number.isInteger(item.end)) continue;
      const annotation = annotations[item.index], node = nodes[item.nodeIndex];
      const word = wordRanges.get(node)?.get(item.start + ':' + (item.end - item.start));
      if (annotation && word?.lemma === annotation.lemma && word.original === item.surface &&
          word.range.endOffset === item.end && validWord(word)) visibilityWords.add(word);
    }
    if (!visibilityWords.size) return;
    if (visibilityObserver) { visibilityObserver.wake(); return; }
    const cycle = revision, page = curPageId;
    let stopped = false, pending = null, active = new Map();
    function current() {
      return !paeDisabled && cycleReady && revision === cycle && curPageId === page && pageIdNow() === page;
    }
    function cancel() { if (pending !== null) clearTimeout(pending); pending = null; }
    function reset() { active.clear(); }
    function schedule() {
      if (!stopped && pending === null && visibilityWords.size && visibleReported.size < 80 &&
          document.visibilityState === 'visible') pending = setTimeout(sample, 200);
    }
    function visible(word) {
      try {
        const range = word.range, node = word.node, element = node.parentElement;
        if (!validWord(word) || !/^[a-z][a-z'-]{2,63}$/.test(word.lemma) || visibleReported.has(word.lemma) ||
            range.collapsed || node.nodeType !== Node.TEXT_NODE || !element) return false;
        if (!supportsWholeWord(word.surface)) {
          const part = /[\p{L}\p{N}_'-]/u, text = node.textContent;
          if ((range.startOffset && part.test(text[range.startOffset - 1])) ||
              (range.endOffset < text.length && part.test(text[range.endOffset]))) return false;
        }
        if (element.checkVisibility && !element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
        for (const rect of range.getClientRects()) {
          const left = Math.max(0, rect.left), right = Math.min(innerWidth, rect.right);
          const top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom);
          if (right <= left || bottom <= top) continue;
          const hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2);
          if (hit && (hit === element || element.contains(hit))) return true;
        }
      } catch (e) {}
      return false;
    }
    function sample() {
      pending = null;
      if (stopped) return;
      if (!current()) { dispose(); return; }
      if (document.visibilityState !== 'visible') { reset(); return; }
      for (const word of visibilityWords) if (!word.node.isConnected) visibilityWords.delete(word);
      if (visibilityWords.size > 80) { reset(); return; }
      const now = performance.now(), next = new Map();
      for (const word of visibilityWords) {
        if (!visible(word) || next.has(word.lemma)) continue;
        const old = active.get(word.lemma);
        const segment = old && now - old.last <= 300 ? old : { since: now, last: now };
        segment.last = now;
        if (now - segment.since >= 1000 && visibleReported.size < 80) {
          visibleReported.add(word.lemma);
          try {
            chrome.runtime.sendMessage(Object.assign({ type: 'PAE_VISIBILITY_EVENT', lemma: word.lemma },
              annotationState()), function () { void chrome.runtime.lastError; });
          } catch (e) {}
        } else next.set(word.lemma, segment);
      }
      active = next;
      if (active.size) schedule();
    }
    function wake() { if (!stopped) { reset(); cancel(); schedule(); } }
    function visibility() { reset(); cancel(); if (document.visibilityState === 'visible') schedule(); }
    function dispose() {
      if (stopped) return;
      stopped = true; cancel(); reset(); mutations.disconnect(); visibilityWords.clear();
      window.removeEventListener('scroll', wake, true);
      window.removeEventListener('resize', wake);
      window.removeEventListener('pagehide', dispose);
      window.removeEventListener('popstate', wake);
      document.removeEventListener('visibilitychange', visibility);
      if (visibilityObserver === observer) visibilityObserver = null;
    }
    const mutations = new MutationObserver(wake);
    mutations.observe(document.documentElement, { childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ['style', 'class', 'hidden'] });
    const observer = { wake, dispose };
    visibilityObserver = observer;
    window.addEventListener('scroll', wake, true);
    window.addEventListener('resize', wake);
    window.addEventListener('pagehide', dispose);
    window.addEventListener('popstate', wake);
    document.addEventListener('visibilitychange', visibility);
    schedule();
  }

  function cancelWordHide() {
    clearTimeout(wordHideTimer);
    wordHideTimer = null;
  }

  function hideWordCard() {
    cancelWordHide();
    if (wordPointerFrame !== null) {
      cancelAnimationFrame(wordPointerFrame);
      wordPointerFrame = null;
    }
    wordView = null;
    const slot = characterFrames.get('word');
    if (slot) hideCharacterFrame(slot);
  }

  function resetWordCard() {
    visibilityObserver?.dispose();
    visibilityWords.clear();
    hideWordCard();
    wordListeners?.abort();
    wordListeners = null;
    removeCharacterFrame('word');
    wordHost?.remove();
    wordHost = wordRoot = null;
    wordRanges.clear();
    for (const state of wordFeedback.values()) {
      clearTimeout(state.pending?.timer);
      clearTimeout(state.query?.timer);
    }
    wordFeedback.clear();
  }

  function wordState(word) {
    if (!wordFeedback.has(word.lemma)) wordFeedback.set(word.lemma, {});
    const state = wordFeedback.get(word.lemma);
    if (!(state.validUntil > Date.now() / 1000)) state.recorded = false;
    return state;
  }

  function paintWordCard() {
    if (!wordView || !validWord(wordView.word)) return;
    const word = wordView.word, state = wordState(word), score = state.score ?? word.s_value;
    sendCharacter(characterFrames.get('word'), { type: 'word-view', viewId: wordView.id,
      title: word.surface + (word.lemma !== word.surface ? ' → ' + word.lemma : ''), gloss: word.gloss, score,
      label: state.recorded ? '已记录' : state.pending ? '记录中…' : state.query ? '查询中…' : '认识了',
      disabled: !!(state.recorded || state.pending || state.query), error: state.error || '' });
  }

  function wordMessage(message, done = () => {}) {
    try {
      chrome.runtime.sendMessage(message, (result) => done(chrome.runtime.lastError ? null : result));
    } catch (e) { done(null); }
  }

  function wordEvent(word, type, done) {
    const event = { type, lemma: word.lemma, surface: word.surface,
      s_value: wordState(word).score ?? word.s_value, page_id: pageIdNow() };
    if (type === 'hover') event.sentence = word.node.parentElement?.textContent.trim().slice(0, 300);
    wordMessage({ type: 'PAE_EVENT', sessionId: SESSION_ID, event }, done);
  }

  function applyWordReceipt(word, state, result) {
    const receipt = result?.data?.feedback || result?.data;
    if (result?.ok && result.data?.ok && typeof receipt?.recorded === 'boolean') {
      state.recorded = receipt.recorded;
      state.validUntil = receipt.valid_until;
      state.uncertain = false;
      state.error = '';
      if (Number.isFinite(receipt.s_value) && state.score !== receipt.s_value) {
        state.score = receipt.s_value;
        wordMessage({ type: 'PAE_WORD_TINT', lemma: word.lemma, documentToken, revision,
          band: state.score < 0.2 ? 'new' : state.score < 0.45 ? 'mid' : 'high' });
      }
    } else if (!state.recorded) {
      state.error = result?.error === 'known_click_daily_limit' ? '反馈额度已用完，请稍后再试。' :
        result?.error === 'learning_write_paused' ? '记录服务暂停，请重启引擎后确认。' : '暂时无法确认记录，请重试。';
      if (result?.data?.result_unknown) state.uncertain = true;
    }
    paintWordCard();
  }

  function queryWord(word) {
    const state = wordState(word);
    if (state.recorded || state.pending || state.query) return;
    const query = state.query = { timer: setTimeout(() => finish(null), 4000) };
    function finish(result) {
      if (state.query !== query || word.revision !== revision) return;
      clearTimeout(query.timer);
      state.query = null;
      applyWordReceipt(word, state, result);
    }
    paintWordCard();
    wordMessage({ type: 'PAE_FEEDBACK_STATE', lemma: word.lemma }, finish);
  }

  function recordWord(word) {
    const state = wordState(word);
    if (state.recorded || state.pending || state.query) return;
    if (state.uncertain) { queryWord(word); return; }
    state.error = '';
    const action = state.action = state.pending = { timer: setTimeout(() => {
      if (state.pending !== action || word.revision !== revision) return;
      state.pending = null;
      state.uncertain = true;
      state.error = '结果待确认，正在核对。';
      queryWord(word);
    }, 8000) };
    paintWordCard();
    wordEvent(word, 'known_click', (result) => {
      if (state.action !== action || word.revision !== revision) return;
      clearTimeout(action.timer);
      state.pending = null;
      if (!validWord(word)) {
        state.uncertain = true;
        if (wordView?.word.lemma === word.lemma && validWord(wordView.word)) queryWord(wordView.word);
        return;
      }
      clearTimeout(state.query?.timer);
      state.query = null;
      applyWordReceipt(word, state, result);
      if (result?.ok && result.data?.ok && characterPage === pageIdNow()) refreshCharacterStats();
    });
  }

  function placeWordCard() {
    const slot = characterFrames.get('word');
    if (!wordView || !slot || !wordView.height) return;
    const word = wordView.word;
    if (!validWord(word)) { hideWordCard(); return; }
    const rect = word.range.getBoundingClientRect();
    if (rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth) {
      hideWordCard(); return;
    }
    const width = Math.min(276, Math.max(1, innerWidth - 16));
    const above = rect.top - 16, below = innerHeight - rect.bottom - 18;
    const useAbove = above >= wordView.height || (below < wordView.height && above >= below);
    const available = useAbove ? above : below;
    const height = Math.min(wordView.height, Math.max(1, innerHeight - 16),
      available >= 80 ? available : Math.max(1, innerHeight - 16));
    const preferred = useAbove ? rect.top - height - 8 : rect.bottom + 10;
    const style = slot.frame.style;
    style.width = width + 'px';
    style.height = height + 'px';
    style.left = Math.max(8, Math.min(rect.left - 20, innerWidth - width - 8)) + 'px';
    style.top = Math.max(8, Math.min(preferred, innerHeight - height - 8)) + 'px';
    displayCharacterFrame(slot, true);
    if (!wordView.displayed) {
      wordView.displayed = true;
      wordEvent(word, 'hover');
    }
  }

  function scheduleWordHide() {
    if (wordView && wordHideTimer == null) wordHideTimer = setTimeout(hideWordCard, 240);
  }

  function moveWordPointer(event) {
    const point = document.caretRangeFromPoint(event.clientX, event.clientY);
    const entries = wordRanges.get(point?.startContainer);
    let match;
    if (entries) for (const word of entries.values()) {
      if (validWord(word) && point.startOffset >= word.range.startOffset && point.startOffset <= word.range.endOffset) {
        match = word;
        break;
      }
    }
    if (!match) { scheduleWordHide(); return; }
    cancelWordHide();
    if (wordView?.word === match && wordHost?.isConnected) return;
    if (!wordHost?.isConnected) {
      removeCharacterFrame('word');
      wordHost = document.createElement('div');
      wordHost.style.cssText = 'position:fixed;inset:0;width:0;height:0;overflow:visible;border:0;padding:0;' +
        'margin:0;background:transparent;pointer-events:none;z-index:2147483647';
      wordHost.setAttribute('popover', 'manual');
      wordRoot = wordHost.attachShadow({ mode: 'closed' });
      document.documentElement.appendChild(wordHost);
      createCharacterFrame('word');
      try { wordHost.showPopover(); } catch (e) {}
    }
    wordView = { id: ++wordViewId, word: match };
    hideCharacterFrame(characterFrames.get('word'));
    paintWordCard();
    queryWord(match);
  }

  function queueWordPointer(event) {
    if (!event.isTrusted || paeDisabled || !wordRanges.size) return;
    if (wordPointerFrame !== null) cancelAnimationFrame(wordPointerFrame);
    if (event.target === wordHost) { wordPointerFrame = null; cancelWordHide(); return; }
    wordPointerFrame = requestAnimationFrame(() => {
      wordPointerFrame = null;
      moveWordPointer(event);
    });
  }
  window.addEventListener('pagehide', hideWordCard);

  function liveCharacter(slot) {
    return characterFrames.get(slot.surface) === slot && !!slot.frame.isConnected &&
      (slot.surface === 'word' ? wordHost?.isConnected && !paeDisabled : characterPage === pageIdNow());
  }

  function sendCharacter(slot, data) {
    if (slot && slot.ready && liveCharacter(slot)) slot.port.postMessage(data);
  }

  function updateCharacter(slot, withTurns) {
    if (slot.surface === 'word') return;
    sendCharacter(slot, { type: 'state', name: characterName, muted: characterMuted,
      night: characterTheme, turns: withTurns && slot.surface === 'side' ? characterTurns : null });
  }

  function hideCharacterFrame(slot) {
    slot.frame.style.visibility = 'hidden';
    slot.frame.style.pointerEvents = 'none';
    slot.frame.tabIndex = -1;
  }

  function displayCharacterFrame(slot, interactive) {
    slot.frame.style.visibility = 'visible';
    slot.frame.style.pointerEvents = interactive ? 'auto' : 'none';
    slot.frame.tabIndex = interactive ? 0 : -1;
  }

  function removeCharacterFrame(surface) {
    const slot = characterFrames.get(surface);
    if (!slot) return;
    characterFrames.delete(surface);
    if (slot.noticeTimer) clearTimeout(slot.noticeTimer);
    slot.dot?.remove();
    if (slot.port) slot.port.close();
    slot.frame.remove();
  }

  function resetCharacter() {
    for (const surface of characterFrames.keys()) if (surface !== 'word') removeCharacterFrame(surface);
    characterHost?.remove();
    characterHost = characterRoot = characterPage = null;
    characterTurns = [];
    characterSide = false;
    characterHistoryPending = false;
    characterStatsSequence += 1;
  }

  function requestCharacter(kind, fields, done) {
    const host = characterHost;
    const page = characterPage;
    chrome.runtime.sendMessage(Object.assign({ type: kind }, fields), (result) => {
      const failed = !!chrome.runtime.lastError;
      if (characterHost === host && characterPage === page && page === pageIdNow()) done(failed ? null : result);
    });
  }

  function appendCharacterTurn(role, text) {
    if (!text) return;
    const turn = { role: role, text: text, ts: Date.now() / 1000 };
    characterTurns.push(turn);
    if (characterTurns.length > 300) characterTurns.shift();
    sendCharacter(characterFrames.get('side'), { type: 'turn', turn: turn });
  }

  function refreshCharacterStats() {
    if (!characterSide || !characterFrames.get('side')?.ready) return;
    const seq = ++characterStatsSequence;
    requestCharacter('PAE_CHAR_STATS', null, (result) => {
      if (seq === characterStatsSequence) sendCharacter(characterFrames.get('side'), { type: 'stats', result: result });
    });
  }

  function openCharacterHistory() {
    if (characterTurns.length || characterHistoryPending) return;
    characterHistoryPending = true;
    requestCharacter('PAE_CHAR_HIST', null, (result) => {
      characterHistoryPending = false;
      if (characterTurns.length || !result?.ok || !Array.isArray(result.turns)) return;
      characterTurns = result.turns;
      sendCharacter(characterFrames.get('side'), { type: 'history', turns: characterTurns });
    });
  }

  function characterMarginPosition(anchor) {
    const elements = document.querySelectorAll('p,li,td,dd,blockquote,section,article,div');
    let bestIndex = -1, bestRect, bestScore = -Infinity;
    for (let index = 0; index < elements.length; index++) {
      const element = elements[index];
      const text = element.textContent || '';
      if (text.length < anchor.length || !text.includes(anchor)) continue;
      const rect = element.getBoundingClientRect();
      if (rect.width < 40 || rect.height < 10) continue;
      const score = (rect.top > -50 && rect.top < innerHeight ? 1000 : 0) - text.length / 100;
      if (score > bestScore) {
        bestIndex = index;
        bestRect = rect;
        bestScore = score;
      }
    }
    if (!bestRect) return null;
    const width = 260, height = 70;
    let left = Math.max(8, bestRect.right + 14), top = Math.max(8, bestRect.top + 4);
    if (left + width > innerWidth - 8) {
      left = Math.min(Math.max(8, bestRect.right - width), Math.max(8, innerWidth - width - 8));
      top = bestRect.bottom + 10;
    }
    if (top + height > innerHeight) top = Math.max(8, innerHeight - height - 10);
    return { left: left, top: top, mark: bestIndex, dotLeft: bestRect.right + 2, dotTop: bestRect.top + 12 };
  }

  function notifyCharacter(text, kind, anchor) {
    if (!text || !characterHost || characterPage !== pageIdNow()) return;
    const target = kind === 'say' && anchor ? characterMarginPosition(anchor) : null;
    if (!target && kind === 'say' && characterMuted) return;
    appendCharacterTurn('agent', text);
    const surface = target ? 'margin' : 'bubble';
    removeCharacterFrame(surface);
    const slot = createCharacterFrame(surface);
    slot.notice = text;
    if (target) {
      slot.frame.style.left = target.left + 'px';
      slot.frame.style.top = target.top + 'px';
      const dot = document.createElement('div');
      dot.style.cssText = 'position:fixed;width:8px;height:8px;border-radius:99px;pointer-events:none;display:none';
      dot.style.left = target.dotLeft + 'px';
      dot.style.top = target.dotTop + 'px';
      dot.style.background = characterTheme ? '#f59e0b' : '#b45309';
      dot.style.boxShadow = '0 0 0 4px rgba(245,158,11,.18)';
      characterRoot.appendChild(dot);
      slot.dot = dot;
      requestCharacter('PAE_CHAR_ANCHOR', { elementIndex: target.mark }, () => {});
    }
    slot.noticeTimer = setTimeout(() => removeCharacterFrame(surface), target ? 15000 : 12000);
  }

  function resizeCharacter(slot, data) {
    if (slot.surface === 'side' || !Number.isInteger(data.width) || !Number.isInteger(data.height) ||
        data.width < 1 || data.height < 1 || data.width > 400 || data.height > 1000) return;
    const frame = slot.frame;
    const width = slot.surface === 'quick' && data.open ? Math.min(324, innerWidth - 16) : data.width;
    frame.style.width = width + 'px';
    frame.style.height = data.height + 'px';
    if (slot.surface === 'quick') {
      const validCap = Number.isInteger(data.capWidth) && Number.isInteger(data.capHeight) &&
        data.capWidth > 0 && data.capWidth <= width && data.capHeight > 0 && data.capHeight <= data.height;
      const edge = validCap ? width - data.capWidth : 0;
      const shelf = validCap ? data.height - data.capHeight : data.height;
      frame.style.clipPath = 'polygon(0 0,100% 0,100% 100%,' + edge + 'px 100%,' +
        edge + 'px ' + shelf + 'px,0 ' + shelf + 'px)';
      const bubble = characterFrames.get('bubble');
      if (bubble) bubble.frame.style.bottom = (24 + data.height) + 'px';
    }
    if (slot.dot) slot.dot.style.display = 'block';
    displayCharacterFrame(slot, slot.surface !== 'bubble');
  }

  function useCharacterPort(slot, data) {
    if (!data || !liveCharacter(slot) || !slot.documentId) return;
    const surface = slot.surface;
    if (surface === 'word') {
      if (data.type === 'ready' && !slot.ready) { slot.ready = true; paintWordCard(); return; }
      if (!slot.ready || !wordView || data.viewId !== wordView.id || !validWord(wordView.word)) return;
      if (data.type === 'word-size' && Number.isInteger(data.height) && data.height > 0) {
        wordView.height = data.height;
        placeWordCard();
      } else if (data.type === 'word-enter') cancelWordHide();
      else if (data.type === 'word-leave') scheduleWordHide();
      else if (data.type === 'word-known') recordWord(wordView.word);
      return;
    }
    if (data.type === 'ready' && !slot.ready) {
      slot.ready = true;
      updateCharacter(slot, true);
      if (surface === 'side' && characterSide) {
        displayCharacterFrame(slot, true);
        refreshCharacterStats();
        openCharacterHistory();
      }
      if (slot.notice) sendCharacter(slot, { type: 'notice', text: slot.notice });
      return;
    }
    if (!slot.ready) return;
    if (data.type === 'size') resizeCharacter(slot, data);
    else if (surface === 'quick' && data.type === 'open-side') {
      characterSide = true;
      const side = characterFrames.get('side') || createCharacterFrame('side');
      if (side.ready) {
        displayCharacterFrame(side, true);
        refreshCharacterStats();
        openCharacterHistory();
      }
    } else if (surface === 'side' && data.type === 'close-side') {
      characterSide = false;
      hideCharacterFrame(slot);
    } else if (surface === 'side' && characterSide && data.type === 'refresh') {
      refreshCharacterStats();
    } else if (surface === 'margin' && data.type === 'close-margin') {
      removeCharacterFrame('margin');
    } else if (surface === 'side' && characterSide && data.type === 'theme') {
      characterTheme = !data.night;
      characterFrames.forEach((frame) => updateCharacter(frame, false));
    } else if (surface === 'quick' && data.type === 'mute') {
      characterMuted = !!data.muted;
      characterFrames.forEach((frame) => updateCharacter(frame, false));
      requestCharacter('PAE_SET_MUTE', { muted: characterMuted }, () => {});
    } else if ((surface === 'quick' || (surface === 'side' && characterSide)) &&
        data.type === 'say' && typeof data.text === 'string' && data.text.trim()) {
      const text = data.text.trim();
      appendCharacterTurn('user', text);
      requestCharacter('PAE_CHAR_SAY', { text: text }, (result) => {
        const reply = result ? (result.reply || '（它没说话）' + (result.error || '')) :
          '引擎没在跑，我暂时说不了话。';
        notifyCharacter(reply, 'reply');
      });
    }
  }

  function createCharacterFrame(surface) {
    const characterURL = chrome.runtime.getURL('character-frame.html');
    const characterOrigin = characterURL.slice(0, characterURL.lastIndexOf('/'));
    const frame = document.createElement('iframe');
    const slot = { surface: surface, frame: frame, ready: false, token: null, documentId: null, port: null };
    characterFrames.set(surface, slot);
    frame.title = { quick: '小 P', side: '小 P 侧栏', bubble: '小 P 气泡', margin: '小 P 边注', word: 'OpenGloss 词卡' }[surface];
    frame.style.cssText = 'position:fixed;border:0;background:transparent;max-width:calc(100vw - 16px);padding:0;margin:0';
    if (surface === 'side') frame.style.cssText += ';top:0;right:0;width:min(360px,100vw);height:100vh;max-width:none';
    else if (surface === 'margin') frame.style.cssText += ';width:284px;height:160px';
    else if (surface === 'word') frame.style.cssText += ';width:276px;height:104px';
    else {
      const bottom = surface === 'bubble' ?
        24 + (characterFrames.get('quick')?.frame.getBoundingClientRect().height || 30) : 16;
      frame.style.cssText += ';right:16px;bottom:' + bottom + 'px;width:' +
        (surface === 'quick' ? 324 : 326) + 'px;height:180px';
    }
    hideCharacterFrame(slot);
    frame.addEventListener('load', () => {
      if (!liveCharacter(slot)) return;
      slot.ready = false;
      slot.documentId = null;
      hideCharacterFrame(slot);
      if (slot.port) slot.port.close();
      slot.token = Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('');
      const channel = new MessageChannel();
      slot.port = channel.port1;
      channel.port1.onmessage = ({ data }) => {
        if (slot.port === channel.port1) useCharacterPort(slot, data);
      };
      frame.contentWindow.postMessage({ type: 'pae-character-bind', token: slot.token, surface: surface },
        characterOrigin, [channel.port2]);
    });
    frame.src = characterURL;
    (surface === 'word' ? wordRoot : characterRoot).appendChild(frame);
    return slot;
  }

  function mountCharacter() {
    if (window.top !== window || typeof chrome.runtime.getURL !== 'function') return;
    if (characterHost?.isConnected && characterPage === pageIdNow()) return;
    resetCharacter();
    characterPage = pageIdNow();
    characterHost = document.createElement('div');
    characterHost.id = 'pae-char-host';
    characterHost.style.cssText = 'position:fixed;inset:0;width:0;height:0;overflow:visible;border:0;padding:0;' +
      'background:transparent;pointer-events:none;z-index:2147483646';
    characterHost.setAttribute('popover', 'manual');
    characterRoot = characterHost.attachShadow({ mode: 'closed' });
    document.documentElement.appendChild(characterHost);
    try { characterHost.showPopover(); } catch (e) {}
    createCharacterFrame('quick');
  }

  window.addEventListener('pagehide', resetCharacter);
  window.addEventListener('pageshow', (event) => {
    if (event.persisted && !paeDisabled && annTotal > 0) mountCharacter();
  });

  chrome.storage.onChanged.addListener(function (changes, area) {
    if (area !== 'local' || !('paeEnabled' in changes)) return;
    paeDisabled = changes.paeEnabled.newValue === false;
    beginAnnotationCycle(!paeDisabled);
  });
  chrome.storage.local.get({ paeEnabled: true }, function (s) {
    paeDisabled = !!(s && s.paeEnabled === false);
    beginAnnotationCycle(!paeDisabled);
  });
  chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
    if (msg && msg.type === 'PAE_CHAR_VERIFY_ACTIVE') {
      const slot = characterFrames.get(msg.surface);
      const ok = sender.id === chrome.runtime.id && !!slot && liveCharacter(slot) &&
        slot.token !== null && slot.token === msg.token && !slot.documentId &&
        typeof msg.documentId === 'string';
      if (ok) {
        slot.documentId = msg.documentId;
        slot.token = null;
      }
      reply({ ok: ok });
    }
    if (msg && msg.type === 'PAE_ANNOTATION_PROBE') reply(annotationState());
  });
  // ③ 推送（SW 的 SSE → 本页角色壳）
  chrome.runtime.onMessage.addListener(function (msg) {
    if (!msg || msg.type !== 'PAE_PUSH') return;
    var ev = msg.event || {};
    var text = ev.text || '';
    if (ev.type === 'param_changed') {
      text = '我把参数调了：' + JSON.stringify(ev.after || {});
      // 参数变更后**真重渲染**（验收基准 inpage-agent-prototype 的核心特性；2026-10-02 漂移审计补）
      // 只有影响注解的参数才值得整页重画；引擎侧门槛已即时生效，客户端重画让它立刻可见。
      var _after = ev.after || {};
      var _need = ('annotate_max_s' in _after) || ('max_ann_per_page' in _after) ||
                  ('band_lo' in _after) || ('band_hi' in _after);
      if (_need && !paeDisabled) {
        beginAnnotationCycle(true);
      } else refreshLimits();
    }
    else if (ev.type === 'hint') text = '提示：' + (ev.lemma || '') + (ev.level === 'up' ? ' 多标一点' : ' 少标一点');
    if (text) notifyCharacter(text, ev.type === 'say' ? 'say' : 'system', ev.anchor || '');
  });

  chrome.runtime.sendMessage({ type: 'PAE_CHAR_HELLO' }, (result) => {
    if (chrome.runtime.lastError || !result?.name) return;
    characterName = result.name;
    characterFrames.forEach((slot) => updateCharacter(slot, false));
  });
})();
