'use strict';
// Actual worker listener and actual chat display callback; all I/O is synthetic.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sw = fs.readFileSync(path.join(__dirname, '../extension/sw.js'), 'utf8');
const content = fs.readFileSync(path.join(__dirname, '../extension/content.js'), 'utf8');
const marker = "requestCharacter('PAE_CHAR_SAY', { text: text }, (result) => {";
const callbackStart = content.indexOf(marker);
const callbackEnd = content.indexOf('      });', callbackStart);
assert(callbackStart >= 0 && callbackEnd > callbackStart);
const callback = content.slice(callbackStart + marker.length, callbackEnd);
const disabled = 'AI总控未开启，请到管理页开启后再试。';
const missingKey = '尚未配置AI密钥，请到管理页填写并保存连接设置后再试。';
const failed = '这次聊天未完成，请检查引擎和管理页的AI连接设置后再试。';
const empty = '这次没有收到可显示的回复，请再试一次。';
const sender = { id: 'synthetic-extension', tab: { id: 7 }, frameId: 0,
  documentLifecycle: 'active', documentId: 'synthetic-document' };

function display(result) {
  const shown = [];
  vm.runInNewContext(callback, { result, notifyCharacter: (text, kind) => shown.push({ text, kind }) });
  assert.equal(shown.length, 1);
  assert.equal(shown[0].kind, 'reply');
  return shown[0].text;
}

async function respond(item, from = sender) {
  let listener;
  const requests = [];
  const chrome = {
    storage: {
      local: { get: (_defaults, done) => done({ paeBase: 'https://engine.example.invalid' }) },
      onChanged: { addListener() {} },
    },
    runtime: { id: sender.id, onMessage: { addListener(fn) { listener = fn; } } },
  };
  vm.runInNewContext(sw, {
    chrome, URL, setTimeout, clearTimeout,
    async fetch(url, options) {
      requests.push({ url, options });
      if (item.fetchError) throw new Error('synthetic private provider diagnostic');
      const status = item.status ?? 200;
      return { ok: status >= 200 && status < 300, status, json: async () => {
        if (item.jsonError) throw new SyntaxError('synthetic private provider body');
        return item.body;
      } };
    },
  }, { filename: 'sw.js' });
  const result = await new Promise((resolve, reject) => {
    let answered = false;
    const accepted = listener({ type: 'PAE_CHAR_SAY', text: '合成问题' }, from, reply => {
      answered = true;
      resolve(reply);
    });
    if (accepted !== true && !answered) reject(new Error('No response'));
  });
  return { result, requests };
}

const failures = [
  ['disabled', { status: 500, body: { ok: false, error: 'RuntimeError: AI call disabled for this action' } }, disabled],
  ['missing key', { status: 500, body: { ok: false, error: 'RuntimeError: no LLM key configured' } }, missingKey],
  ['HTTP failure with misleading success body', { status: 503, body: { ok: true, result: { reply: 'incorrect success' } } }, failed],
  ['business rejection', { body: { ok: false, error: 'synthetic private provider diagnostic' } }, failed],
  ['truthy nonboolean business ok', { body: { ok: 1, result: { reply: 'incorrect success' } } }, failed],
  ['missing business ok', { body: { result: { reply: 'incorrect success' } } }, failed],
  ['fetch failure', { fetchError: true }, failed],
  ['invalid JSON', { jsonError: true }, failed],
  ['null JSON', { body: null }, failed],
  ['array JSON', { body: [] }, failed],
  ['string JSON', { body: 'synthetic private provider diagnostic' }, failed],
  ['unrecognized error containing disabled words', { status: 500,
    body: { ok: false, error: 'provider says AI call disabled for this action and private diagnostic' } }, failed],
];
for (const [name, item, hint] of failures) {
  test(name + ' is a readable failure, with one request and no raw error', async () => {
    const { result, requests } = await respond(item);
    assert.equal(result.ok, false);
    assert.equal(result.error, hint);
    assert.equal(display(result), hint);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, 'https://engine.example.invalid/v1/cap/pae.persona.say');
    assert.equal(requests[0].options.method, 'POST');
    assert.deepEqual(JSON.parse(requests[0].options.body), { stimulus: '合成问题' });
  });
}
for (const reply of [undefined, null, '', ' \n\t', 7, {}, []]) {
  test('invalid/empty reply ' + JSON.stringify(reply) + ' is not success', async () => {
    const { result, requests } = await respond({ body: { ok: true, result: { reply } } });
    assert.equal(result.ok, false);
    assert.equal(result.error, empty);
    assert.equal(display(result), empty);
    assert.equal(requests.length, 1);
  });
}
test('valid reply, including confirmed memory text, is preserved', async () => {
  for (const reply of ['合成回答。', '记下了。', '  合成回答。\n']) {
    const { result, requests } = await respond({ body: { ok: true, result: { reply } } });
    assert.equal(result.ok, true);
    assert.equal(result.reply, reply);
    assert.equal(display(result), reply);
    assert.equal(requests.length, 1);
  }
});
test('unavailable callback is a readable failure', () => {
  assert.equal(display(null), failed);
  assert.equal(display(undefined), failed);
});
test('a failed callback never displays a supplied success reply', () => {
  assert.equal(display({ ok: false, reply: 'incorrect success', error: failed }), failed);
  assert.equal(display({ ok: true, reply: '' }), failed);
});
test('untrusted document still makes no request', async () => {
  const { result, requests } = await respond({}, { ...sender, id: 'another-extension' });
  assert.equal(result.ok, false);
  assert.equal(requests.length, 0);
});
