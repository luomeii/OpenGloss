'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = path.join(__dirname, '../daily_sheet.js');
function fixture(count, suggestions = 10) {
  assert.ok(fs.existsSync(source), 'daily selection/pagination implementation is missing');
  const listeners = new Map();
  class Element {
    constructor() { this.hidden = true; this.disabled = false; this.textContent = ''; this.dataset = {}; this.events = {}; }
    addEventListener(name, fn) { this.events[name] = fn; }
    click() { if (!this.disabled && this.events.click) this.events.click({preventDefault(){}}); }
    focus() { this.focused = true; }
    scrollIntoView() {}
  }
  const ids = Object.fromEntries(['daily-sheet','daily-tools','pagination','prev-page','next-page','page-status',
    'selection-status','suggest-words','clear-selection','print-selected','print-all','print-scope','focus-list'].map(k=>[k,new Element()]));
  ids['daily-sheet'].classList = {add: value => { ids['daily-sheet'].enhanced = value; }};
  const cards = Array.from({length:count}, (_, i)=>{
    const card = new Element(); card.hidden = false;
    card.dataset.suggested = i < suggestions ? 'true' : 'false';
    const input = new Element(); input.checked = i < suggestions;
    card.querySelector = () => input; card.input = input; return card;
  });
  const jumps = cards.slice(0,suggestions).map((_,i)=>{const a = new Element();a.dataset.jump=String(i);return a;});
  let prints = 0;
  const context = {document:{getElementById:id=>ids[id],querySelectorAll:sel=>sel==='.word-card'?cards:jumps},
    window:{print:()=>{prints++;},addEventListener:(k,v)=>listeners.set(k,v)}};
  for (const key of ['fetch','XMLHttpRequest','WebSocket','localStorage','sessionStorage'])
    Object.defineProperty(context,key,{get(){throw Error('Forbidden I/O: '+key);}});
  vm.runInNewContext(fs.readFileSync(source,'utf8'),context);
  const change = i => cards[i].input.events.change();
  const selected = () => cards.map((c,i)=>c.dataset.selected==='true'?i:null).filter(i=>i!==null);
  const printable = () => cards.map((c,i)=>ids['daily-sheet'].dataset.printMode==='all'||c.dataset.selected==='true'?i:null).filter(i=>i!==null);
  return {ids,cards,jumps,change,selected,printable,prints:()=>prints,listeners};
}
test('daily printing script is shipped',()=>assert.ok(fs.existsSync(source)));
for (const count of [0,1,10,11,85,300]) test(`pagination keeps ${count} complete records`,()=>{
  const f=fixture(count,Math.min(count,10));
  assert.equal(f.cards.filter(c=>!c.hidden).length,Math.min(count,20));
  assert.match(f.ids['page-status'].textContent,new RegExp(`/ ${Math.max(1,Math.ceil(count/20))} 页`));
  assert.equal(f.ids['prev-page'].disabled,true);
  let visible=0;
  for(let page=0;page<Math.max(1,Math.ceil(count/20));page++) {
    visible+=f.cards.filter(c=>!c.hidden).length;
    f.ids['next-page'].click();
  }
  assert.equal(visible,count); assert.equal(f.ids['next-page'].disabled,true);
});
test('cross-page choices print once regardless of current page',()=>{
  const f=fixture(85);
  f.ids['clear-selection'].click();
  for(const i of [0,1,2,60,61]){f.cards[i].input.checked=true;f.change(i);}
  f.ids['next-page'].click();
  const hiddenBefore=f.cards.map(c=>c.hidden);
  f.ids['print-selected'].click();
  assert.equal(f.prints(),1);assert.deepEqual(f.printable(),[0,1,2,60,61]);
  f.listeners.get('beforeprint')();f.listeners.get('afterprint')();
  assert.deepEqual(f.cards.map(c=>c.hidden),hiddenBefore);
  assert.deepEqual(f.selected(),[0,1,2,60,61]);
});
test('zero selection never silently prints everything',()=>{
  const f=fixture(30,0);
  assert.equal(f.ids['print-selected'].disabled,true);assert.deepEqual(f.printable(),[]);
  f.ids['print-selected'].click();assert.equal(f.prints(),0);
  f.listeners.get('beforeprint')();assert.match(f.ids['print-scope'].textContent,/未选择/);
});
test('explicit all includes off-page records; selection changes return to selected mode',()=>{
  const f=fixture(85);f.ids['print-all'].click();
  assert.equal(f.printable().length,85);assert.equal(f.prints(),1);
  f.cards[0].input.checked=false;f.change(0);
  assert.equal(f.printable().length,9);
});
test('suggestions restore at most ten, not an enforced selection limit',()=>{
  const f=fixture(85);f.ids['clear-selection'].click();f.ids['suggest-words'].click();
  assert.equal(f.selected().length,10);
  f.cards[20].input.checked=true;f.change(20);assert.equal(f.selected().length,11);
});
test('focus jump opens the corresponding page and focuses its only checkbox',()=>{
  const f=fixture(85);f.ids['next-page'].click();f.jumps[0].click();
  assert.equal(f.cards[0].hidden,false);assert.equal(f.cards[0].input.focused,true);
});
test('new page instance resets choices and page; no persistence',()=>{
  const a=fixture(85);a.ids['clear-selection'].click();a.ids['next-page'].click();
  const b=fixture(85);assert.equal(b.selected().length,10);assert.equal(b.cards[0].hidden,false);
});
