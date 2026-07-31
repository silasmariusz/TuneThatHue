/*
  Run the panel's script against a stand-in for the browser.

  Not a substitute for looking at it, but it catches the failure that has bitten twice:
  the script throwing on load, which takes out everything after the throw and leaves a
  page that looks fine and does nothing. Here a throw is a failed exit code.
*/
const fs = require('fs');
const path = process.argv[2];
const html = fs.readFileSync(path, 'utf8');

// ── the ids and classes the page actually declares ──────────────────────────
const ids = new Set([...html.matchAll(/\bid="([\w-]+)"/g)].map(m => m[1]));

const listeners = [];
function makeEl(id) {
  const el = {
    id,
    tagName: 'DIV',
    style: {},
    dataset: {},
    children: [],
    attributes: {},
    classList: {
      _s: new Set(),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    get lastChild() { return this._last || (this._last = { textContent: '' }); },
    get firstChild() { return this.lastChild; },
    appendChild(c) { this.children.push(c); return c; },
    removeChild() {},
    insertBefore(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k] ?? null; },
    removeAttribute(k) { delete this.attributes[k]; },
    addEventListener(t, f) { listeners.push([id, t, f]); },
    querySelector() { return makeEl(id + ':q'); },
    querySelectorAll(sel) {
      // the IPv4 groups hold four inputs; everything else can be empty
      if (sel === 'input') return [0, 1, 2, 3].map(i => makeEl(id + ':in' + i));
      return [];
    },
    getContext() {
      return new Proxy({}, { get: () => (() => ({ addColorStop() {} })) });
    },
    getBoundingClientRect() { return { width: 300, height: 120, top: 0, left: 0 }; },
    focus() {}, blur() {}, select() {}, click() {}, remove() {},
    textContent: '', innerHTML: '', value: '', checked: false, disabled: false,
    width: 300, height: 120,
  };
  return el;
}

const cache = new Map();
const el = id => {
  if (!cache.has(id)) cache.set(id, makeEl(id));
  return cache.get(id);
};

const missing = new Set();
global.document = {
  getElementById(id) {
    if (!ids.has(id) && !id.includes(':')) missing.add(id);
    return el(id);
  },
  querySelector: () => el('q'),
  querySelectorAll: () => [],
  createElement: t => { const e = makeEl('new-' + t); e.tagName = String(t).toUpperCase(); return e; },
  addEventListener() {},
  body: el('body'),
  documentElement: el('html'),
  head: el('head'),
  hidden: false,
};
global.window = {
  addEventListener() {}, matchMedia: () => ({ matches: false, addEventListener() {} }),
  AudioContext: function () {
    return { currentTime: 0, resume() {}, destination: {},
      createOscillator: () => ({ frequency: {}, connect: () => ({ connect() {} }), start() {}, stop() {} }),
      createGain: () => ({ gain: { setValueAtTime() {}, exponentialRampToValueAtTime() {} },
        connect: () => ({ connect() {} }) }) };
  },
  devicePixelRatio: 1,
  location: { href: 'http://x/', origin: 'http://x', pathname: '/', search: '', hash: '' },
};
global.location = global.window.location;
global.matchMedia = global.window.matchMedia;
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.getComputedStyle = () => ({ getPropertyValue: () => '' });
global.clearInterval = () => {};
global.clearTimeout = () => {};
global.URL = URL;
global.Image = function () { return { set src(v) {}, addEventListener() {} }; };
global.localStorage = { _d: {}, getItem(k) { return this._d[k] ?? null; },
  setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; } };
global.performance = { now: () => Date.now() };
// Answer each endpoint with the shape the daemon really sends, so the code that reads
// the answers runs too rather than bailing at the first await.
const REPLIES = {
  'api/status': {
    t: 1, packets: 10, bytes: 2048, spectra: 4, peaks: 1, beats: 8, bpm: 120.0,
    renders: 40, lit: 12, format: '48000 Hz / 2ch / int16', spectrum: new Array(17).fill(900),
    output: 'hue', streaming: true, area: 'Biuro', receiving: true,
    level_l: 0.4, level_r: 0.35, driving: 'vban',
    section: 'rise', beat_in_bar: 3, bar_phase: 0.5, intensity: 0.7,
    sendspin: { name: 'TuneThatHue', connected: true, streaming: true, server: 'ma' },
    snapcast: { host: '10.0.0.5', connected: true, codec: 'pcm', codec_supported: true,
                format: '48000:16:2', chunks: 22 },
    slimproto: { connected: true, playing: true, format: 'flac', server: '10.0.0.5',
                 server_name: 'MA', error: '' },
    dlna: { name: 'TuneThatHue', playing: false, format: '', controller: '', error: '' },
    wled: { enabled: true, host: '10.0.0.9', pixels: 60, name: 'WLED', frames: 5, error: '' },
  },
  'api/config': {
    paired: true, host: '10.100.200.200', area: 'Biuro', areas: ['Biuro', 'Salon'],
    output: 'hue', lights: [{ name: 'L1' }, { name: 'L2' }],
    palette_colors: [{ name: 'Disco', colors: ['#f00', '#0f0', '#00f'] }],
    schema: [{ key: 'hue_latency_ms', type: 'integer', range: [0, 500], default: 100,
               label: 'Light latency (ms)', advanced: false }],
    settings: { hue_latency_ms: 120 },
  },
  'api/downloads': [{ id: 'winamp', name: 'Winamp plug-in' }],
};
global.fetch = async (url) => {
  const key = Object.keys(REPLIES).find(k => String(url).includes(k));
  const body = key ? REPLIES[key] : {};
  return { ok: true, status: 200, json: async () => body,
           text: async () => JSON.stringify(body) };
};
global.EventSource = function () { return { addEventListener() {}, close() {}, onmessage: null }; };
global.requestAnimationFrame = () => 0;
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.Option = function (t, v) { return { text: t, value: v }; };
global.navigator = { userAgent: 'smoke' };

const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
try {
  new Function(script)();
} catch (err) {
  console.log('THREW ON LOAD: ' + err.message);
  console.log(err.stack.split('\n').slice(0, 4).join('\n'));
  process.exit(1);
}
if (missing.size) {
  console.log('asked for elements that do not exist: ' + [...missing].join(', '));
  process.exit(1);
}

process.on('unhandledRejection', err => {
  console.log('THREW WHILE LOADING DATA: ' + err.message);
  process.exit(1);
});

// Let loadConfig() and poll() finish, then check the keys ended up where the answers
// above say they should be. This is the new rule under test: the key is the state.
setImmediate(() => setImmediate(() => {
  const pressed = id => el(id).getAttribute('aria-pressed');
  const want = {
    pairBtn: 'true',       // config says paired
    outBtn: 'true',        // status says output is hue
    ssDevice: 'true',      // sendspin running
    slimDevice: 'true',    // squeezebox registered
    dlnaDevice: 'true',    // renderer announced
    snapBtn: 'true',       // joined a snapserver
    wledBtn: 'true',       // strip enabled
    metroBtn: null,        // never pressed
  };
  let bad = 0;
  for (const [id, expect] of Object.entries(want)) {
    const got = pressed(id);
    const ok = expect === null ? (got === 'false' || got === null) : got === expect;
    if (!ok) { console.log(`  ${id}: expected ${expect}, got ${got}`); bad++; }
  }
  const lamps = ['ledBeat', 'ledBar', 'ledRise', 'ledDrop', 'ledBreak', 'ledVban'];
  const lit = lamps.filter(id => el(id).classList.contains('on'));
  // status says section 'rise' and vban receiving, so those two must be lit and the
  // ones for other sections must not be
  if (!lit.includes('ledRise')) { console.log('  ledRise should be lit for section=rise'); bad++; }
  if (!lit.includes('ledVban')) { console.log('  ledVban should be lit while receiving'); bad++; }
  if (lit.includes('ledBreak')) { console.log('  ledBreak lit during a rise'); bad++; }

  const ipShown = [...el('hostGroup').querySelectorAll('input')].length;
  if (ipShown !== 4) { console.log('  the address field is not four octets'); bad++; }

  if (bad) { console.log(`${bad} problem(s)`); process.exit(1); }
  console.log(`script ran clean; keys reflect the daemon; lamps lit: ${lit.join(', ')}`);
}));
