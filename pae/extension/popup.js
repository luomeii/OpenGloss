'use strict';
const DEFAULT_BASE = 'http://127.0.0.1:4815';
const ui = id => document.getElementById(id);
let enabled = true, initialized = false, statusGeneration = 0;
function localBase(value) {
  const s = String(value || '').trim() || DEFAULT_BASE;
  const u = new URL(s);
  if (u.protocol !== 'http:' || u.hostname !== '127.0.0.1' || (!u.port || Number(u.port)<1 || Number(u.port)>65535) || u.username || u.password || u.search || u.hash || u.pathname !== '/' || !/^http:\/\/127\.0\.0\.1:\d+\/?$/.test(s)) throw Error('地址只能为 http://127.0.0.1:合法端口');
  return u.origin;
}
function paintToggle() {
  ui('pae-toggle').textContent = enabled ? '标注：开启' : '标注：关闭';
  ui('state').textContent = enabled ? '阅读时显示词义标注' : '已关闭标注；对话侧栏仍可使用';
}
async function status() {
  const generation = ++statusGeneration;
  let base;
  try {
    base = localBase(ui('pae-base').value);
    const token = ui('pae-token').value.trim();
    if (!token) { ui('eng').textContent = '未配对 · ' + base + ' · 请在引擎管理页复制本实例配对口令'; return; }
    ui('eng').textContent = '正在验证配对 · ' + base;
    const response = await fetch(base + '/v1/cap/pae.config', {method:'POST', headers:{'Content-Type':'application/json',Authorization:'Bearer ' + token},body:'{}',credentials:'omit',redirect:'error'});
    if (generation !== statusGeneration) return;
    if (response.status === 401) throw Error('401：配对口令无效，请重新配对');
    if (!response.ok) throw Error('HTTP ' + response.status);
    const data = await response.json();
    if (generation !== statusGeneration) return;
    if (data.ok === false || data.result?.ok === false) throw Error('配对验证失败');
    ui('eng').textContent = '运行中 · 已配对 · ' + base;
  } catch (error) {
    if (generation === statusGeneration) ui('eng').textContent = (base ? base + ' · ' : '') + '未连接/验证失败：' + (/401/.test(error.message) ? '401：配对口令无效，请重新配对' : '引擎关闭、地址无效或响应不可用，请检查设置');
  }
}
ui('pae-save').addEventListener('click', event => {
  if (!event.isTrusted || !initialized) return;
  let base, token;
  try { base = localBase(ui('pae-base').value); token = ui('pae-token').value.trim(); if (/[\r\n]/.test(token) || token.length > 4096) throw Error('访问口令格式无效'); }
  catch (error) { ui('eng').textContent = error.message; return; }
  chrome.storage.local.set({paeBase:base,paeToken:token}, () => {
    if (chrome.runtime.lastError) { ui('eng').textContent = '设置保存失败，请重新打开扩展弹窗'; return; }
    ui('pae-base').value = base; status();
  });
});
ui('pae-toggle').addEventListener('click', event => {
  if (!event.isTrusted || !initialized) return;
  chrome.storage.local.set({paeEnabled:!enabled}, () => {
    if (chrome.runtime.lastError) { ui('eng').textContent = '开关保存失败，请重试'; return; }
    enabled = !enabled; paintToggle();
  });
});
chrome.storage.local.get({paeBase:DEFAULT_BASE,paeToken:'',paeEnabled:true}, settings => {
  if (chrome.runtime.lastError) { ui('eng').textContent = '设置读取失败，请重新打开扩展弹窗'; return; }
  ui('pae-base').value = settings.paeBase;
  ui('pae-token').value = settings.paeToken;
  enabled = settings.paeEnabled !== false; initialized = true; paintToggle(); status();
});