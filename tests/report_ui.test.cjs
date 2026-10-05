// Exercise offline report behavior using a small DOM adapter, without a browser or dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.events = {};
    this.className = '';
    this.value = '';
    this._text = '';
    this._open = false;
    this.style = { setProperty() {} };
  }
  get childNodes() { return this.children; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
  set textContent(value) { this._text = String(value); this.children = []; }
  append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
  replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'class') this.className = String(value);
  }
  addEventListener(name, callback) { (this.events[name] ||= []).push(callback); }
  get open() { return this._open; }
  set open(value) {
    if (this._open === value) return;
    this._open = value;
    for (const callback of this.events.toggle || []) callback();
  }
  contains(target) { return target === this || this.children.some(child => child.contains(target)); }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map(value => value.trim());
    const matches = element => selectors.some(value => value.startsWith('.') ? element.className.split(' ').includes(value.slice(1)) : element.tagName === value);
    return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  focus() {}
}

const html = fs.readFileSync(process.argv[2], 'utf8');
const elements = new Map();
for (const match of html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) elements.set(match[2], new Element(match[1]));
elements.get('report-data').textContent = html.match(/<script id="report-data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const document = {
  getElementById: id => { assert(elements.has(id), 'Missing HTML element: ' + id); return elements.get(id); },
  createElement: tag => new Element(tag),
  createElementNS: (_, tag) => new Element(tag),
  addEventListener() {},
};
const context = { document };
vm.runInNewContext(fs.readFileSync(process.argv[3], 'utf8'), context);
const get = id => elements.get(id);

assert.equal(context.outcome([{ checks: [], response_status: 200 }]), 'neutral');
assert.equal(context.outcome([{ checks: [{ status: 'success' }], response_status: null }]), 'neutral');
assert.equal(context.outcome([{ checks: [{ status: 'failure', exception: { reason: 'accepted' } }], response_status: 200 }]), 'accepted');

assert.equal(get('selected-run').textContent, 'newer');
assert.equal(get('selected-status').textContent, 'FAIL');
assert.equal(get('newer-run').disabled, true);
assert.equal(get('older-run').disabled, false);
assert(get('run-stats').textContent.includes('1 unexplained failures'));
assert(get('operations').textContent.includes('Inventory'));
let operation = get('operations').querySelectorAll('.operation')[0];
assert.equal(operation.open, false);
assert.equal(operation.querySelectorAll('.phase').length, 0);
operation.open = true;
assert.equal(operation.querySelectorAll('.phase').length, 2);
const phases = operation.querySelectorAll('.phase');
const examples = phases.find(phase => phase.textContent.includes('examples'));
assert(examples.textContent.includes('Skipped'));
examples.open = true;
assert(examples.textContent.includes('no examples'));
const fuzzing = phases.find(phase => phase.textContent.includes('fuzzing'));
assert.equal(fuzzing.open, false);
fuzzing.open = true;
const item = fuzzing.querySelectorAll('.case')[0];
assert.equal(item.open, false);
item.open = true;
assert(item.textContent.includes('status_code_conformance'));
assert.equal(item.querySelectorAll('.exchange').length, 1);

get('collapse-all').onclick();
assert(get('operations').querySelectorAll('details').every(details => !details.open));
get('expand-operations').onclick();
assert(get('operations').querySelectorAll('.entity, .operation').every(details => details.open));
assert(get('operations').querySelectorAll('.phase').every(details => !details.open));

get('older-run').onclick();
assert.equal(get('selected-run').textContent, 'older');
assert.equal(get('selected-status').textContent, 'PASS');
assert.equal(get('older-run').disabled, true);
assert(get('run-stats').textContent.includes('0 unexplained failures'));
assert(get('operations').textContent.includes('Items'));
assert(!get('operations').textContent.includes('Inventory'));
get('operation-filters').children.find(button => button.textContent.startsWith('Failed')).onclick();
assert(get('operations').textContent.includes('No operations match'));
get('newer-run').onclick();
assert.equal(get('operations').querySelectorAll('.operation').length, 1);
get('operation-search').value = 'absent';
get('operation-search').oninput();
assert.equal(get('operations').querySelectorAll('.operation').length, 0);
get('operation-search').value = 'inventory';
get('operation-search').oninput();
assert.equal(get('operations').querySelectorAll('.operation').length, 1);

get('run-search').value = 'older';
get('run-search').oninput();
assert.equal(get('run-options').children.length, 1);
get('run-options').children[0].onclick();
assert.equal(get('selected-run').textContent, 'older');
get('tab-trends').onclick();
assert.equal(get('report').hidden, true);
assert.equal(get('trends').hidden, false);
assert.equal(get('trend-content').querySelectorAll('svg').length, 2);
// The SVG is scaled and offset, as it is in responsive panels with letterboxing.
const matrix = { a: .5, d: .5, e: 120, f: 40, inverse() { return { a: 2, d: 2, e: -240, f: -80 }; } };
function exerciseHover(container, expectedMetric) {
  const svg = container.querySelectorAll('svg')[0];
  const tooltip = container.querySelectorAll('.trend-tooltip')[0];
  const guide = svg.querySelectorAll('.trend-guide')[0];
  const dots = svg.querySelectorAll('circle');
  svg.getScreenCTM = () => matrix;
  svg.createSVGPoint = () => ({ x: 0, y: 0, matrixTransform(m) { return { x: this.x * m.a + m.e, y: this.y * m.d + m.f }; } });
  container.getBoundingClientRect = () => ({ left: 100, width: 320 });
  tooltip.offsetWidth = 220;
  const move = x => svg.events.pointermove[0]({ clientX: x * .5 + 120, clientY: 100 });
  assert.equal(tooltip.hidden, true);
  svg.events.pointerenter[0]({ clientX: 140, clientY: 100 });
  assert.equal(tooltip.hidden, false);
  assert(tooltip.textContent.includes('older'));
  assert(tooltip.textContent.includes('PASS'));
  assert(tooltip.textContent.includes('0 failures · 0 accepted · 1 cases'));
  assert(!tooltip.textContent.includes('Target:'));
  assert(tooltip.textContent.includes(expectedMetric));
  assert.equal(guide.attributes.x1, dots[0].attributes.cx);
  assert.equal(guide.attributes.x2, dots[0].attributes.cx);
  assert.equal(dots[0].attributes.r, '6');
  move(280);
  assert(tooltip.textContent.includes('older'));
  move(284);
  assert(tooltip.textContent.includes('newer'));
  assert(tooltip.textContent.includes('FAIL'));
  assert.equal(guide.attributes.x1, dots[1].attributes.cx);
  assert.equal(dots[0].attributes.r, '4');
  assert.equal(dots[1].attributes.r, '6');
  assert.equal(tooltip.style.left, '100px'); // Keep the compact card within the panel.
  move(600);
  assert(tooltip.textContent.includes('newer'));
  move(-20);
  assert(tooltip.textContent.includes('older'));
  svg.events.pointerleave[0]();
  assert.equal(guide.attributes.visibility, 'hidden');
  assert.equal(tooltip.hidden, true);
  assert.equal(dots[0].attributes.r, '4');
  move(525);
  svg.events.pointercancel[0]();
  assert.equal(tooltip.hidden, true);
}
const charts = get('trend-content').querySelectorAll('.trend-chart-container');
exerciseHover(charts[0], 'Unexplained failed checks: 0');
exerciseHover(charts[1], 'Observed cases: 1');
const singleRunChart = context.chart([JSON.parse(elements.get('report-data').textContent).runs[0]], [1], '#94bfff', 'Observed cases');
const singleSvg = singleRunChart.querySelectorAll('svg')[0];
singleSvg.getScreenCTM = () => matrix;
singleSvg.createSVGPoint = () => ({ x: 0, y: 0, matrixTransform(m) { return { x: this.x * m.a + m.e, y: this.y * m.d + m.f }; } });
singleRunChart.getBoundingClientRect = () => ({ left: 100, width: 320 });
singleRunChart.querySelectorAll('.trend-tooltip')[0].offsetWidth = 280;
singleSvg.events.pointermove[0]({ clientX: 140, clientY: 100 });
assert.equal(singleSvg.querySelectorAll('.trend-guide')[0].attributes.x1, '283');
assert(singleRunChart.querySelectorAll('.trend-tooltip')[0].textContent.includes('newer'));
get('trend-content').querySelectorAll('button')[0].onclick();
assert.equal(get('report').hidden, false);
assert.equal(get('selected-run').textContent, 'newer');
