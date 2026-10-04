var PAE_POPUP_BASE = 'http://127.0.0.1:4815';
function paeNorm(v) {
  var s = String(v || '');
  while (s.length && s.charAt(s.length - 1) === '/') { s = s.slice(0, -1); }
  return s || 'http://127.0.0.1:4815';
}
try {
  chrome.storage.local.get({ paeBase: 'http://127.0.0.1:4815' }, function (s) {
    if (s && s.paeBase) { PAE_POPUP_BASE = paeNorm(s.paeBase); }
  });
} catch (e) {}
'use strict';
function setUI(enabled) {
  const btn = document.getElementById('pae-toggle');
  const state = document.getElementById('state');
  btn.textContent = enabled ? 'ON — annotating' : 'OFF';
  btn.className = enabled ? 'on' : 'off';
  state.textContent = 'PAE ' + (enabled ? 'enabled' : 'disabled');
}
document.getElementById('pae-toggle').addEventListener('click', () => {
  chrome.storage.local.get({ paeEnabled: true }, (s) => {
    const next = !s.paeEnabled;
    chrome.storage.local.set({ paeEnabled: next });
    setUI(next);
  });
});
chrome.storage.local.get({ paeEnabled: true }, (s) => setUI(s.paeEnabled));
fetch(PAE_POPUP_BASE + '/v1/health', { method: 'GET' })
  .then(r => { document.getElementById('eng').textContent = r.ok ? 'running' : 'error ' + r.status; })
  .catch(() => { document.getElementById('eng').textContent = 'down'; });