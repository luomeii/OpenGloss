/* Local-only daily-sheet presentation. No persistence, network or learning writes. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const root = byId('daily-sheet');
  const cards = Array.from(document.querySelectorAll('.word-card'));
  const inputs = cards.map(card => card.querySelector('input[type="checkbox"]'));
  const ids = ['daily-tools', 'pagination', 'prev-page', 'next-page', 'page-status',
    'selection-status', 'suggest-words', 'clear-selection', 'print-selected',
    'print-all', 'print-scope', 'focus-list'];
  if (!root || ids.some(id => !byId(id)) || inputs.some(input => !input)) return;
  const size = 20;
  let page = 0;
  const pages = Math.max(1, Math.ceil(cards.length / size));

  function scope() {
    const count = inputs.filter(input => input.checked).length;
    byId('selection-status').textContent = `已选 ${count} / ${cards.length} 词（跨页保留）`;
    byId('print-selected').disabled = count === 0;
    byId('print-all').disabled = cards.length === 0;
    byId('print-scope').textContent = root.dataset.printMode === 'all'
      ? `打印范围：全部记录 · ${cards.length} 词`
      : count ? `打印范围：所选 · ${count} 词` : '未选择打印词，请先勾选；本次不会打印完整词表。';
    cards.forEach((card, i) => { card.dataset.selected = String(inputs[i].checked); });
  }
  function showPage() {
    cards.forEach((card, i) => { card.hidden = i < page * size || i >= (page + 1) * size; });
    byId('page-status').textContent = `${page + 1} / ${pages} 页 · 每页 ${size} 词 · 共 ${cards.length} 词`;
    byId('prev-page').disabled = page === 0;
    byId('next-page').disabled = page === pages - 1;
  }
  function changed() { root.dataset.printMode = 'selected'; scope(); }
  inputs.forEach(input => input.addEventListener('change', changed));
  byId('prev-page').addEventListener('click', () => { page = Math.max(0, page - 1); showPage(); });
  byId('next-page').addEventListener('click', () => { page = Math.min(pages - 1, page + 1); showPage(); });
  byId('clear-selection').addEventListener('click', () => {
    inputs.forEach(input => { input.checked = false; }); changed();
  });
  byId('suggest-words').addEventListener('click', () => {
    inputs.forEach((input, i) => { input.checked = cards[i].dataset.suggested === 'true'; }); changed();
  });
  document.querySelectorAll('[data-jump]').forEach(link => link.addEventListener('click', event => {
    const index = Number(link.dataset.jump);
    if (!Number.isInteger(index) || index < 0 || index >= cards.length) return;
    event.preventDefault();
    page = Math.floor(index / size); showPage();
    inputs[index].focus(); inputs[index].scrollIntoView({block: 'center'});
  }));
  byId('print-selected').addEventListener('click', () => {
    changed();
    if (!byId('print-selected').disabled) window.print();
  });
  byId('print-all').addEventListener('click', () => {
    if (!cards.length) return;
    root.dataset.printMode = 'all'; scope(); window.print();
  });
  window.addEventListener('beforeprint', scope);
  window.addEventListener('afterprint', () => { showPage(); scope(); });
  root.dataset.printMode = 'selected';
  scope(); showPage();
  root.classList.add('enhanced');
  for (const id of ['daily-tools', 'pagination', 'print-scope', 'focus-list']) byId(id).hidden = false;
})();
