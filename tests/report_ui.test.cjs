// Exercise actual compressed bundles, async navigation and charts without npm dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attributes = {}; this.events = {};
    this.className = ''; this.value = ''; this._text = ''; this._open = false;
    this.style = { setProperty() {} };
    this.classList = { toggle: name => {
      const classes = new Set(this.className.split(' ').filter(Boolean)), added = !classes.has(name);
      if (added) classes.add(name); else classes.delete(name);
      this.className = [...classes].join(' '); return added;
    } };
  }
  get childNodes() { return this.children; }
  get isConnected() { return this._root || !!this.parent?.isConnected; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { for (const child of this.children) child.parent = null; this.children = []; this._text = ''; this.append(...children); }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; } }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'class') this.className = String(value); }
  addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
  get open() { return this._open; }
  set open(value) { if (this._open !== value) { this._open = value; for (const callback of this.events.toggle || []) callback(); } }
  contains(target) { return target === this || this.children.some(child => child.contains(target)); }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map(value => value.trim());
    const matches = element => selectors.some(value => value.startsWith('.') ? element.className.split(' ').includes(value.slice(1)) : element.tagName === value);
    return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  focus() { this.focused = true; }
}
class ChartAdapter {
  static instances = [];
  constructor(canvas, config) {
    this.canvas = canvas; this.config = config; this.width = 320; this.height = 180; this.active = [];
    this.tooltip = { setActiveElements: (active, position) => { this.tooltipActive = active; this.tooltipPosition = position; } };
    ChartAdapter.instances.push(this);
  }
  destroy() { this.destroyed = true; this.canvas = null; }
  getDatasetMeta() { return { data: this.config.data.datasets[0].data.map((_, i) => ({ x: i * 20, y: 40 })) }; }
  isDatasetVisible() { return true; }
  setActiveElements(active) { this.active = active; }
  getActiveElements() { return this.active; }
  update() {}
}

async function main() {
  const htmlPath = process.argv[2], html = fs.readFileSync(htmlPath, 'utf8'), elements = new Map();
  for (const match of html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const element = new Element(match[1]); element._root = true; elements.set(match[2], element);
  }
  elements.get('report-data').textContent = html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
  const manifest = JSON.parse(elements.get('report-data').textContent), requested = [], failures = new Set(), corruptions = new Set();
  const head = new Element('head'); head._root = true;
  let context;
  const document = {
    head, getElementById: id => { assert(elements.has(id), 'Missing HTML element: ' + id); return elements.get(id); },
    createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), addEventListener() {},
  };
  head.append = script => {
    Element.prototype.append.call(head, script); requested.push(script.src);
    setImmediate(() => {
      if (failures.has(script.src)) { script.onerror(); return; }
      if (corruptions.has(script.src)) { context.reportChunk(script.src, 5, 'bm90LWd6aXA='); script.onload(); return; }
      try {
        vm.runInContext(fs.readFileSync(path.join(path.dirname(htmlPath), decodeURIComponent(script.src)), 'utf8'), context);
        script.onload();
      } catch { script.onerror(); }
    });
  };
  context = vm.createContext({ document, Chart: ChartAdapter, matchMedia: () => ({ matches: false, addEventListener() {} }),
    setTimeout, clearTimeout, queueMicrotask, atob, Uint8Array, Blob, Response, DecompressionStream, TextDecoder });
  context.window = context;
  const evaluate = code => vm.runInContext(code, context), get = id => elements.get(id);
  const settle = async predicate => {
    for (let attempt = 0; attempt < 200; attempt++) {
      if (predicate()) return;
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    assert(predicate(), 'Async rendering did not settle');
  };
  vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), context);
  await context.reportReady;
  assert.equal(get('selected-run').textContent, manifest.combined ? 'All scenarios' : 'newer');
  assert(requested.every(ref => (manifest.combined || manifest.runs[0]).operations_ref.includes(ref)), 'Startup reads only selected operation summaries');
  assert.equal(head.children.length, 0, 'Loaded script elements must be released');
  if (manifest.combined) await context.selectRun(0);

  assert.equal(evaluate("fmtDate('2026-10-13T15:48:17')"), '13/10/2026, 15:48:17');
  assert.equal(evaluate("fmtDate('2026-12-31')"), '31/12/2026');
  const coverage = context.documentedResponseCoverage({ operations: [{ method: 'GET', path: '/items', method_probe: true,
    response_coverage: [{ status: '200', hits: 1 }, { status: '405', hits: 0 }] }] });
  assert.equal(coverage.covered, 1); assert.equal(coverage.total, 2); assert.equal(coverage.uncovered[0].methodProbe, true);
  const exchange = context.renderExchange('Response', { status: 200, headers: {}, body: { status: 'truncated', text: '{"partial":"' + 'x'.repeat(700) } });
  assert(exchange.textContent.includes('truncated')); assert(exchange.textContent.includes('partial'));
  const expand = exchange.querySelectorAll('.payload-expand')[0]; expand.onclick();
  assert.equal(expand.attributes['aria-expanded'], 'true');
  assert.equal(get('older-run').disabled, false); assert.equal(get('newer-run').disabled, !manifest.combined);
  assert(get('run-stats').textContent.includes('1 unexplained failures'));
  assert.equal(get('operations').querySelectorAll('.operation').length, 100);
  assert(get('operation-result-count').textContent.startsWith('105 of 105 operations'));
  get('operation-pagination').querySelectorAll('button')[1].onclick();
  assert.equal(get('operations').querySelectorAll('.operation').length, 5);
  get('operation-search').value = '/items'; get('operation-search').oninput({ type: 'input' });
  assert(get('operation-pagination').textContent.includes('Page 1 of 2'));
  get('operation-search').value = '/items/paged-103'; get('operation-search').oninput();
  assert.equal(get('operations').querySelectorAll('.operation').length, 1);
  get('operation-search').value = ''; get('operation-search').oninput();

  const beforeExpand = requested.length;
  get('expand-operations').onclick();
  assert.equal(requested.length, beforeExpand, 'Bulk expansion must not load cases or evidence');
  const phase = get('operations').querySelectorAll('.phase').find(item => item.textContent.includes('fuzzing'));
  phase.open = true;
  await settle(() => phase.querySelectorAll('.case').length === 50);
  assert(!phase.textContent.includes('HTTP exchange'));
  const firstCase = phase.querySelectorAll('.case')[0]; firstCase.open = true;
  await settle(() => firstCase.textContent.includes('HTTP exchange'));
  assert(firstCase.textContent.includes('status_code_conformance'));
  assert(firstCase.textContent.includes('bad'));
  const evidenceRequests = requested.length;
  phase.querySelectorAll('.pagination')[0].querySelectorAll('button')[1].onclick();
  await settle(() => phase.querySelectorAll('.case')[0]?.textContent.includes('copy-50'));
  assert.equal(phase.querySelectorAll('.case').length, 50);
  assert.equal(firstCase.isConnected, false, 'Pagination must release rendered evidence');
  assert.equal(requested.length, evidenceRequests, 'Paging a cached index must not reload evidence');

  get('failure-type-select').value = 'BadResponse'; get('failure-type-select').onchange();
  assert.equal(get('operations').querySelectorAll('.operation').length, 1);
  assert.equal(get('operations').querySelectorAll('.case').length, 0);
  const failurePhase = get('operations').querySelectorAll('.phase')[0]; failurePhase.open = true;
  await settle(() => failurePhase.querySelectorAll('.case').length === 2);
  assert(failurePhase.textContent.includes('copy-120'), 'Failures beyond the first case page remain filterable');
  get('run-stats').children[3].onclick();
  const filteredPhase = get('operations').querySelectorAll('.phase')[0]; filteredPhase.open = true;
  await settle(() => filteredPhase.querySelectorAll('.case').length === 1);
  assert(!filteredPhase.textContent.includes('copy-120'));

  await get('older-run').onclick();
  assert.equal(get('selected-run').textContent, 'older'); assert.equal(get('older-run').disabled, true);
  assert.equal(get('failure-type-select').value, '');
  const trendRequests = requested.length; get('tab-trends').onclick();
  assert.equal(requested.length, trendRequests, 'Trends must not load evidence or other run summaries');
  const instances = ChartAdapter.instances.filter(instance => !instance.destroyed);
  assert.equal(instances.length, 3);
  assert.deepEqual(Array.from(instances[0].config.data.datasets[0].data), [0, 1]);
  assert.deepEqual(Array.from(instances[0].config.data.datasets[1].data), [0, 1]);
  assert.deepEqual(Array.from(instances[0].config.data.datasets[2].data), [0, 2]);
  assert.deepEqual(Array.from(instances[1].config.data.datasets[0].data), [1, 0]);
  assert.deepEqual(Array.from(instances[1].config.data.datasets[1].data), [0, 1]);
  for (const instance of instances.slice(0, 2)) {
    assert.equal(instance.config.type, 'line');
    assert(instance.config.data.datasets.every(dataset => dataset.tension === 0.4 && dataset.cubicInterpolationMode === 'default'));
    const container = get('trend-content').querySelectorAll('.trend-chart-container')[instances.indexOf(instance)];
    const tooltip = container.querySelectorAll('.trend-tooltip')[0]; tooltip.offsetWidth = 220; tooltip.offsetHeight = 120;
    instance.config.options.plugins.tooltip.external({ chart: instance, tooltip: { opacity: 1, dataPoints: [{ dataIndex: 0 }], caretX: 100, caretY: 90 } });
    assert(tooltip.textContent.includes('older'));
    assert.equal(tooltip.querySelectorAll('.trend-tooltip-series').length, 3);
    const canvas = container.querySelectorAll('canvas')[0];
    canvas.events.keydown[0]({ key: 'End', preventDefault() {} }); assert.equal(instance.tooltipActive[0].index, 1);
    canvas.events.keydown[0]({ key: 'Escape', preventDefault() {} }); assert.equal(instance.tooltipActive.length, 0);
  }
  assert.equal(context.passedChange({ passed: 2, observed: 3 }, { passed: 1, observed: 3 }).symbol, '▲');
  assert.equal(context.passedChange({ passed: 1, observed: 3 }, { passed: 2, observed: 3 }).symbol, '▼');
  assert.equal(context.tooltipDate('2026-10-13T15:48:17'), '13 Oct 2026 - 15:48:17');
  instances[2].config.options.onClick({}, [{ index: 1, datasetIndex: 0 }]);
  await settle(() => get('failure-type-select').value === 'BadResponse' && get('selected-run').textContent === 'newer');
  assert.equal(get('report').hidden, false);

  const ref = manifest.runs[1].operations_ref[0];
  evaluate('chunkCache.clear(); cacheBytes = 0;'); failures.add(ref);
  await context.selectRun(1);
  assert(get('operations').textContent.includes('Keep the HTML and its .data folder together'));
  failures.delete(ref); await get('operations').querySelectorAll('button')[0].onclick();
  assert.equal(get('operations').querySelectorAll('.operation').length, 1);
  evaluate('chunkCache.clear(); cacheBytes = 0;'); corruptions.add(ref);
  await context.selectRun(1);
  assert(get('operations').textContent.includes('Keep the HTML and its .data folder together'));
  corruptions.delete(ref);
  evaluate('chunkCache.clear(); cacheBytes = 0;');
  await Promise.all([context.selectRun(0), context.selectRun(1)]);
  assert.equal(get('selected-run').textContent, 'older'); assert.equal(get('operations').querySelectorAll('.operation').length, 1);
  evaluate("rememberChunk('large', [], CACHE_BYTES - 10); rememberChunk('recent', [], 20);");
  assert.equal(evaluate("chunkCache.has('large')"), false); assert.equal(evaluate("chunkCache.has('recent')"), true);
  assert(evaluate('cacheBytes <= CACHE_BYTES'));
  const available = context.DecompressionStream; context.DecompressionStream = undefined;
  evaluate('chunkCache.clear(); cacheBytes = 0;'); await context.selectRun(0);
  assert(get('operations').textContent.includes('Update Chrome or Edge'));
  context.DecompressionStream = available;
  await context.selectRun(manifest.combined ? -1 : 0);
  if (manifest.combined) {
    assert.equal(get('selected-run').textContent, 'All scenarios'); assert.equal(evaluate('currentRun().case_count'), 122);
    const combinedOp = get('operations').querySelectorAll('.operation')[0]; combinedOp.open = true;
    const combinedPhase = combinedOp.querySelectorAll('.phase').find(item => item.textContent.includes('fuzzing')); combinedPhase.open = true;
    await settle(() => combinedPhase.querySelectorAll('.case').length === 50);
    const summary = combinedPhase.querySelectorAll('.case')[0]; summary.open = true;
    await settle(() => summary.textContent.includes('Source: staging · newer'));
  }
  assert.equal(head.children.length, 0);
  console.log('Report bundle UI checks passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
