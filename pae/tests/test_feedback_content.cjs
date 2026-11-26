'use strict';

// Execute the production content script with a simulated extension frame and clock.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../extension/content.js'), 'utf8');
const end = source.lastIndexOf('})();');
assert.ok(end > 0 && source.slice(end).trim() === '})();');
const instrumented = source.slice(0, end) + `
  globalThis.fixture = {
    attachFrame(slot) {
      paeDisabled = false;
      wordHost = { isConnected: true };
      characterFrames.set('word', slot);
      slot.ready = true;
    },
    show(word, id) {
      word.revision = revision;
      wordView = { id, word };
      paintWordCard();
    },
    receive: useCharacterPort,
  };
` + source.slice(end);

function makeWord(lemma) {
  const node = { isConnected: true, textContent: lemma };
  return {
    lemma, surface: lemma, original: lemma, gloss: '合成释义', s_value: 0.28, node,
    range: { startContainer: node, endContainer: node, startOffset: 0, endOffset: lemma.length,
      toString: () => lemma },
  };
}

function setup() {
  const clicks = [], queries = [], views = [], tints = [];
  const timers = new Map();
  let timerId = 0;
  const runtime = {
    onMessage: { addListener() {} },
    sendMessage(message, reply) {
      if (message.type === 'PAE_EVENT') clicks.push({ message, reply });
      else if (message.type === 'PAE_FEEDBACK_STATE') queries.push({ message, reply });
      else if (message.type === 'PAE_WORD_TINT') tints.push(message);
      else reply({});
    },
  };
  const sandbox = {
    chrome: { runtime, storage: { onChanged: { addListener() {} }, local: { get() {} } } },
    window: { addEventListener() {} },
    document: { readyState: 'loading', addEventListener() {}, documentElement: { dataset: {} } },
    sessionStorage: { getItem: () => 'synthetic-session' },
    location: { href: 'https://reader.example.invalid/reading' },
    setTimeout(callback, delay) {
      const id = ++timerId;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
  };
  vm.runInNewContext(instrumented, sandbox, { filename: 'content.js' });
  const slot = { surface: 'word', documentId: 'synthetic-frame', frame: { isConnected: true },
    port: { postMessage: (message) => views.push(message) } };
  sandbox.fixture.attachFrame(slot);
  const word = makeWord('canopy');
  sandbox.fixture.show(word, 1);
  return {
    clicks, queries, tints, word, runtime,
    view: () => views.at(-1),
    show: (next, id) => sandbox.fixture.show(next, id),
    click: (viewId = 1) => sandbox.fixture.receive(slot, { type: 'word-known', viewId }),
    advance(delay) {
      const entry = [...timers].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, 'expected a scheduled timer for this stage');
      timers.delete(entry[0]);
      entry[1].callback();
    },
  };
}

function receipt() {
  return { ok: true, data: { ok: true, feedback: { recorded: true, s_value: 0.59,
    valid_until: Date.now() / 1000 + 3600 } } };
}

test('the currently displayed word receives one successful committed confirmation', () => {
  const state = setup();
  state.click();
  state.click();
  assert.equal(state.clicks.length, 1);
  assert.equal(state.clicks[0].message.event.type, 'known_click');
  assert.equal(state.clicks[0].message.event.lemma, 'canopy');
  assert.equal(state.view().label, '记录中…');
  assert.equal(state.view().disabled, true);
  state.clicks[0].reply(receipt());
  assert.equal(state.view().label, '已记录');
  assert.equal(state.view().score, 0.59);
  assert.equal(state.view().disabled, true);
  assert.equal(state.tints.at(-1).band, 'high');
  assert.equal('s_value' in state.tints.at(-1), false);
  state.show(state.word, 2);
  state.click(2);
  assert.equal(state.view().label, '已记录');
  assert.equal(state.clicks.length, 1);
});

test('business rejection remains rejected in the active card', () => {
  const state = setup();
  state.click();
  state.clicks[0].reply({ ok: false, data: { ok: false }, error: 'known_click_daily_limit' });
  assert.equal(state.view().label, '认识了');
  assert.equal(state.view().disabled, false);
  assert.match(state.view().error, /额度/);
});

test('extension transport failure cannot display a confirmation', () => {
  const state = setup();
  state.click();
  state.runtime.lastError = { message: 'synthetic transport failure' };
  state.clicks[0].reply(receipt());
  assert.equal(state.view().label, '认识了');
  assert.equal(state.view().disabled, false);
  assert.match(state.view().error, /无法确认/);
});

test('slow response recovers by querying committed status without a second write', () => {
  const state = setup();
  state.click();
  state.advance(8000);
  assert.equal(state.queries.length, 1);
  assert.equal(state.queries[0].message.lemma, 'canopy');
  assert.equal(state.view().label, '查询中…');
  state.click();
  assert.equal(state.clicks.length, 1);
  state.queries[0].reply({ ok: true, data: { ok: true, ...receipt().data.feedback } });
  assert.equal(state.view().label, '已记录');
});

test('late confirmation updates its own word and rejects an outdated view action', () => {
  const state = setup();
  state.click();
  state.show(makeWord('quartz'), 2);
  state.click(1);
  assert.equal(state.clicks.length, 1);
  state.clicks[0].reply(receipt());
  assert.equal(state.view().title, 'quartz');
  assert.equal(state.view().label, '认识了');
  state.show(state.word, 3);
  assert.equal(state.view().label, '已记录');
});
