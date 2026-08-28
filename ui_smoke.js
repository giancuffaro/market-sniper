/* ui_smoke.js — RUN the page's JavaScript, don't just parse it.
 *
 * `node --check` proves the brackets balance. It cannot tell you that
 * paintLiveNote() reads a variable named `position` that was never declared -
 * that is a ReferenceError at CALL time, and it threw one line before the
 * settings modal was shown, so SETTINGS silently stopped opening.
 *
 * This builds a throwaway DOM out of the real ids in the file, evaluates the
 * page's script for real, then calls the handlers a user can reach. Anything
 * that throws is a bug the user would have hit.
 *
 *   node ui_smoke.js index.html
 */
const fs = require('fs'), vm = require('vm');

const file = process.argv[2] || 'index.html';
const html = fs.readFileSync(file, 'utf8');
const ids  = [...html.matchAll(/id="([\w-]+)"/g)].map(m => m[1]);

function mkEl(id) {
  const set = new Set();
  const el = {
    id, tagName:'DIV', value:'', checked:false, hidden:false, disabled:false,
    textContent:'', innerHTML:'', className:'', href:'', src:'',
    dataset:{}, style:new Proxy({},{get:()=> '',set:()=>true}),
    classList:{ add:(...c)=>c.forEach(x=>set.add(x)), remove:(...c)=>c.forEach(x=>set.delete(x)),
                toggle:(c,f)=>{ (f===undefined? (set.has(c)?set.delete(c):set.add(c)) : (f?set.add(c):set.delete(c))); },
                contains:c=>set.has(c) },
    appendChild:x=>x, removeChild:x=>x, remove(){}, focus(){}, blur(){}, click(){},
    setAttribute(){}, removeAttribute(){}, getAttribute(){return null;},
    addEventListener(){}, removeEventListener(){}, scrollIntoView(){},
    getBoundingClientRect:()=>({top:0,left:0,width:0,height:0,bottom:0,right:0}),
    querySelector:()=>mkEl('q'), querySelectorAll:()=>[],
    closest:()=>null, insertAdjacentHTML(){},
  };
  return el;
}
const cache = {};
const $id = id => (cache[id] ||= mkEl(id));
ids.forEach($id);

const doc = {
  getElementById: id => cache[id] || null,          // null for unknown, like a browser
  querySelector: () => mkEl('sel'),
  querySelectorAll: () => [],
  createElement: t => { const e = mkEl('new'); e.tagName = String(t).toUpperCase(); return e; },
  addEventListener(){}, removeEventListener(){},
  body: mkEl('body'), documentElement: mkEl('html'),
  head: mkEl('head'), title:'', hidden:false, visibilityState:'visible',
  cookie:'',
};
const store = {};
const localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k,v) => { store[k] = String(v); },
  removeItem: k => { delete store[k]; }, clear: () => { for (const k in store) delete store[k]; },
};

const calls = [];
function fakeFetch(url, opt) {
  calls.push({url:String(url), method:(opt&&opt.method)||'GET'});
  // Enough of a state payload that the render paths have something to chew on.
  const body = {ok:true, connected:false, settings:{}, strategies:[], profiles:[],
                position:null, blotter:[], accounts:[], sessions:{}, levels:[],
                velocity:{state:'closed'}, day_pct:0, day_wins:0, day_losses:0};
  return Promise.resolve({ ok:true, status:200,
    json:()=>Promise.resolve(body), text:()=>Promise.resolve(JSON.stringify(body)) });
}

const win = {
  document: doc, localStorage, sessionStorage: localStorage, fetch: fakeFetch,
  setTimeout: (f,t)=>setTimeout(()=>{},0), clearTimeout, setInterval: ()=>0, clearInterval,
  requestAnimationFrame: ()=>0, cancelAnimationFrame: ()=>{},
  console, JSON, Math, Date, Number, String, Boolean, Array, Object, Promise, RegExp,
  Error, TypeError, isNaN, parseFloat, parseInt, encodeURIComponent, decodeURIComponent,
  addEventListener(){}, removeEventListener(){}, matchMedia:()=>({matches:false,addListener(){},addEventListener(){}}),
  location:{href:'http://127.0.0.1:8000/', reload(){}, replace(){}, origin:'http://127.0.0.1:8000'},
  navigator:{userAgent:'node', clipboard:{writeText:()=>Promise.resolve()}},
  alert(){}, confirm:()=>true, prompt:()=>null, open:()=>null, close(){}, print(){},
  BroadcastChannel: class { constructor(){} postMessage(){} close(){} addEventListener(){} set onmessage(_){} },
  performance:{now:()=>0},
  // Enough of Web Audio that the sound code runs for real in here. If it ever
  // throws, that is a genuine bug and not a missing stub.
  AudioContext: class {
    constructor(){ this.currentTime = 0; this.state = 'running'; this.destination = {}; }
    resume(){}
    createOscillator(){ return {frequency:{setValueAtTime(){}}, type:'sine',
      connect(){}, start(){}, stop(){}}; }
    createGain(){ return {gain:{setValueAtTime(){}, exponentialRampToValueAtTime(){}},
      connect(){}}; }
  },
};
win.window = win; win.self = win; win.globalThis = win; win.top = win; win.parent = win;

const ctx = vm.createContext(win);
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
let fails = 0;
const bad = (what, e) => { fails++; console.log(`  FAIL  ${what}\n        ${e && e.message}`); };

scripts.forEach((src, i) => {
  try { vm.runInContext(src, ctx, {filename:`${file}#script${i}`}); }
  catch (e) { bad(`script block ${i} threw while loading`, e); }
});

// `const EZ = ...` at the top level of a script is a LEXICAL binding: it never
// becomes a property of the context object, so ctx.EZ reads undefined. Ask the
// context for it by name instead.
// Each page exports under its own name - EZ on the options screen, FZ on
// futures. Ask the context for whichever is there rather than hardcoding one,
// so this harness covers both pages instead of silently passing on one.
const APP = vm.runInContext(
  '(typeof EZ !== "undefined" && EZ) || (typeof FZ !== "undefined" && FZ) || null', ctx);
const EZ = APP;
if (!EZ) { console.log('  FAIL  neither EZ nor FZ was defined'); process.exit(1); }

// The handlers a user can actually reach from the settings screen.
const steps = [
  ['openSettings()',            () => EZ.openSettings && EZ.openSettings()],
  ["showTab('config')",         () => EZ.showTab && EZ.showTab('config')],
  ["showTab('strat')",          () => EZ.showTab && EZ.showTab('strat')],
  ["showTab('mirror')",         () => EZ.showTab && EZ.showTab('mirror')],
  ["setStrikeMode('ATM1')",     () => EZ.setStrikeMode && EZ.setStrikeMode('ATM1')],
  ["setStrikeMode('ITM1')",     () => EZ.setStrikeMode && EZ.setStrikeMode('ITM1')],
  ["setStrikeMode('ITM2')",     () => EZ.setStrikeMode && EZ.setStrikeMode('ITM2')],
  ['applySettings()',           () => EZ.applySettings && EZ.applySettings()],
  ['openSettings() again',      () => EZ.openSettings && EZ.openSettings()],
  ['closeSettings()',           () => EZ.closeSettings && EZ.closeSettings()],
  ['toggleSound() off',         () => EZ.toggleSound && EZ.toggleSound()],
  ['toggleSound() on',          () => EZ.toggleSound && EZ.toggleSound()],
];
for (const [name, fn] of steps) {
  try { const r = fn(); if (r && r.catch) r.catch(e => bad(name + ' (async)', e)); }
  catch (e) { bad(name, e); }
}

// The whole point: the modal must actually be showing.
try {
  if (EZ.openSettings) EZ.openSettings();
  if (cache.setScrim && !cache.setScrim.classList.contains('show'))
    bad('openSettings()', new Error('setScrim never got class "show" - the window would not appear'));
} catch (e) { bad('openSettings() visibility', e); }

if (!fails) console.log(`  OK    ${scripts.length} script block(s) ran; ${steps.length} handlers called clean`);
process.exit(fails ? 1 : 0);
