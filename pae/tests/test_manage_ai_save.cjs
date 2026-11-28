'use strict';
// Execute the actual management script, with synthetic DOM and transport.
// This suite neither starts a browser nor contacts an engine/provider.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.resolve(__dirname, '../manage.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const flags = ['ai_enabled', 'shadow_ai_enabled', 'proactive_enabled', 'proactive_ai_enabled', 'scheduled_ai_enabled'];
const tick = () => new Promise(setImmediate);

async function fixture() {
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) elements.set(id, {value: '', checked: false, disabled: false,
      textContent: '', className: '', classList: {toggle() {}}, focus() {this.focused = true;}});
    return elements.get(id);
  };
  const state = {engine: 'ready', max_ann_per_page: 40, muted: false,
    ai: Object.fromEntries(flags.map(f => [f, false])), provider: {key_configured: false}};
  Object.assign(state.ai, {proactive_gap_minutes: 30, base: '', model: ''});
  const requests = [];
  let fail = null, wait = null, readWait = null, transportFailure = false;
  const context = vm.createContext({document: {getElementById: el}, fetch: async (url, options) => {
    if (!options || !options.method) {const snapshot=structuredClone(state);if(readWait)await readWait;return {ok: true, json: async () => snapshot};}
    assert(url.startsWith('/v1/manage'), 'save must not call a model');
    const body = JSON.parse(options.body);
    requests.push({url, body});
    if (wait) await wait;
    if (transportFailure) throw new Error('synthetic transport failure');
    if (fail) return {ok: false, json: async () => ({error: 'invalid_or_unwritable_settings', detail: fail})};
    if (url === '/v1/manage/ai') {
      for (const [k, v] of Object.entries(body)) if (k !== 'key' && k !== 'clear_key') state.ai[k] = v;
      if (body.key) state.provider.key_configured = true;
      if (body.clear_key) state.provider.key_configured = false;
    }
    return {ok: true, json: async () => structuredClone({ai: state.ai, provider: state.provider})};
  }});
  vm.runInContext(script, context); await tick();
  return {el, state, requests, fail: text => {fail = text;}, hold: promise => {wait = promise;},
    holdRead: promise => {readWait = promise;}, failTransport: () => {transportFailure = true;}};
}

(async () => {
  let passed = 0;
  async function test(name, work) {await work(await fixture()); passed++; console.log('PASS ' + name);}
  await test('connection save is independent of an invalid background draft', async ({el, requests}) => {
    el('base').value = 'https://example.invalid'; el('model').value = 'synthetic-model';
    el('key').value = 'synthetic-not-a-real-key'; el('ai_enabled').checked = true;
    el('proactive_gap_minutes').value = '15'; el('shadow_ai_enabled').checked = true;
    await el('saveAi').onclick();
    assert.deepEqual(requests[0].body, {base: 'https://example.invalid', model: 'synthetic-model',
      ai_enabled: true, key: 'synthetic-not-a-real-key'});
    assert.equal(el('proactive_gap_minutes').value, '15');
    assert.equal(el('shadow_ai_enabled').checked, true);
    assert.match(el('aiMsg').textContent, /已保存.*未发起模型请求/);
  });
  await test('invalid reminder drafts do not send requests', async ({el, requests}) => {
    assert.equal(typeof el('saveBackground').onclick, 'function');
    for (const value of ['', '15', '29', '30.5', '1441', 'nonsense']) {
      el('proactive_gap_minutes').value = value; await el('saveBackground').onclick();
      assert.match(el('backgroundMsg').textContent, /30–1440/);
    }
    assert.equal(requests.length, 0);
  });
  await test('background save keeps connection and key drafts', async ({el, requests}) => {
    el('base').value = 'https://example.invalid'; el('model').value = 'synthetic-draft';
    el('key').value = 'synthetic-new-draft'; el('ai_enabled').checked = true;
    el('proactive_gap_minutes').value = '60';
    await el('saveBackground').onclick();
    assert.deepEqual(Object.keys(requests[0].body).sort(),
      ['proactive_gap_minutes', 'shadow_ai_enabled', 'proactive_enabled', 'proactive_ai_enabled', 'scheduled_ai_enabled'].sort());
    assert.equal(requests[0].body.proactive_gap_minutes, 60);
    assert.equal(el('model').value, 'synthetic-draft'); assert.equal(el('key').value, 'synthetic-new-draft');
    assert.equal(el('ai_enabled').checked, true);
  });
  await test('server detail is shown and failed save retains every draft', async ({el, requests, fail}) => {
    el('key').value = 'synthetic-not-a-real-key'; el('model').value = 'synthetic-draft';
    fail('设置文件写入失败，本次未保存'); await el('saveAi').onclick();
    assert.equal(requests.length, 1); assert.match(el('aiMsg').textContent, /写入失败.*未保存/);
    assert.equal(el('key').value, 'synthetic-not-a-real-key'); assert.equal(el('model').value, 'synthetic-draft');
    assert.equal(el('saveAi').disabled, false);
  });
  await test('pending saves are serialized and a newly typed key is not erased', async ({el, requests, hold}) => {
    let release; hold(new Promise(resolve => {release = resolve;}));
    el('key').value = 'synthetic-submitted-key'; const pending = el('saveAi').onclick();
    await tick(); assert.equal(el('saveAi').disabled, true); assert.equal(el('saveBackground').disabled, true);
    el('key').value = 'synthetic-new-draft'; await el('saveAi').onclick(); await el('clearKey').onclick();
    assert.equal(requests.length, 1); release(); await pending;
    assert.equal(el('key').value, 'synthetic-new-draft'); assert.equal(el('saveAi').disabled, false);
  });
  await test('clearing stored key preserves unrelated drafts and newly entered key', async ({el, requests}) => {
    el('base').value = 'https://example.invalid'; el('proactive_gap_minutes').value = '60';
    el('key').value = 'synthetic-not-yet-saved'; await el('clearKey').onclick();
    assert.deepEqual(requests[0].body, {clear_key: true});
    assert.equal(el('base').value, 'https://example.invalid');
    assert.equal(el('proactive_gap_minutes').value, '60');
    assert.equal(el('key').value, 'synthetic-not-yet-saved');
  });
  await test('reading settings refresh does not overwrite AI drafts', async ({el}) => {
    el('model').value = 'synthetic-draft'; el('proactive_gap_minutes').value = '60';
    el('cap').value = '20'; await el('saveCap').onclick();
    assert.equal(el('model').value, 'synthetic-draft'); assert.equal(el('proactive_gap_minutes').value, '60');
  });
  await test('saved status remains distinct from an unsaved master switch', async ({el}) => {
    el('ai_enabled').checked = true; el('proactive_gap_minutes').value = '60';
    await el('saveBackground').onclick();
    assert.match(el('aiState').textContent, /总控.*关闭/);
    assert.equal(el('ai_enabled').checked, true);
  });
  await test('missing acknowledgment is not reported as a confirmed rejection', async ({el, failTransport}) => {
    failTransport(); el('model').value = 'synthetic-draft'; await el('saveAi').onclick();
    assert.match(el('aiMsg').textContent, /无法确认.*核对/);
    assert.equal(el('model').value, 'synthetic-draft');
  });
  await test('a delayed reading refresh cannot replace a newer saved AI status', async ({el, holdRead}) => {
    let release; holdRead(new Promise(resolve => {release = resolve;}));
    el('cap').value = '20'; const reading = el('saveCap').onclick(); await tick();
    el('ai_enabled').checked = true; await el('saveAi').onclick();
    assert.match(el('aiState').textContent, /总控.*开启/);
    release(); await reading; assert.match(el('aiState').textContent, /总控.*开启/);
  });
  console.log(JSON.stringify({passed, actual_page_script: true, real_browser: false, real_network: false}));
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
