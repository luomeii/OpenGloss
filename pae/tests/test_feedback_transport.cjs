'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../extension/sw.js'), 'utf8');
const extensionId = 'synthetic-extension';
const sender = {
  id: extensionId, tab: { id: 7 }, frameId: 0, documentLifecycle: 'active', documentId: 'synthetic-document',
};
const click = {
  type: 'PAE_EVENT', sessionId: 'synthetic-session',
  event: { type: 'known_click', lemma: 'canopy', page_id: 'synthetic-page' },
};
const query = { type: 'PAE_FEEDBACK_STATE', lemma: 'canopy' };

async function respond(message, status, data, from = sender) {
  let listener;
  const requests = [];
  const chrome = {
    storage: {
      local: { get: (_defaults, callback) => callback({ paeBase: 'https://engine.example.invalid' }) },
      onChanged: { addListener() {} },
    },
    runtime: { id: extensionId, onMessage: { addListener(callback) { listener = callback; } } },
  };
  vm.runInNewContext(source, {
    chrome, URL, setTimeout, clearTimeout,
    async fetch(address, options) {
      assert.equal(new URL(address).hostname, 'engine.example.invalid');
      requests.push([new URL(address).pathname, new URL(address).search, options?.method || 'GET']);
      return { status, ok: status >= 200 && status < 300, json: async () => data };
    },
  }, { filename: 'sw.js' });
  const reply = await new Promise((resolve, reject) => {
    let answered = false;
    const accepted = listener(message, from, (result) => {
      answered = true;
      resolve(result);
    });
    if (accepted !== true && !answered) reject(new Error('message received no response'));
  });
  return { reply, requests };
}

test('HTTP refusal reaches a legitimate document without a success receipt', async () => {
  const { reply, requests } = await respond(click, 503, { ok: true });
  assert.deepEqual(requests, [['/v1/event', '', 'POST']]);
  assert.equal(reply.ok, false);
  assert.equal(reply.error, 'HTTP 503');
});

test('successful HTTP transport preserves business refusal', async () => {
  const { reply, requests } = await respond(click, 200, { ok: false, error: 'known_click_daily_limit' });
  assert.deepEqual(requests, [['/v1/event', '', 'POST']]);
  assert.equal(reply.ok, false);
  assert.equal(reply.error, 'known_click_daily_limit');
});

test('already committed duplicate still returns a successful receipt without counting', async () => {
  const { reply, requests } = await respond(click, 200, { ok: true, counted: false, reason: 'duplicate_bucket' });
  assert.deepEqual(requests, [['/v1/event', '', 'POST']]);
  assert.equal(reply.ok, true);
  assert.equal(reply.data.counted, false);
});

test('state query returns the committed feedback', async () => {
  const state = { ok: true, recorded: true, valid_until: 1700006400, s_value: 0.59 };
  const { reply, requests } = await respond(query, 200, state);
  assert.deepEqual(requests, [['/v1/feedback', '?lemma=canopy', 'GET']]);
  assert.equal(reply.ok, true);
  assert.deepEqual(reply.data, state);
});

test('state query reads HTTP failure as unavailable', async () => {
  const { reply, requests } = await respond(query, 503, { ok: true });
  assert.deepEqual(requests, [['/v1/feedback', '?lemma=canopy', 'GET']]);
  assert.equal(reply.ok, false);
  assert.equal(reply.error, 'HTTP 503');
});

test('missing active extension document metadata prevents a request', async () => {
  const { reply, requests } = await respond(query, 200, { ok: true }, {});
  assert.equal(requests.length, 0);
  assert.equal(reply.ok, false);
});
