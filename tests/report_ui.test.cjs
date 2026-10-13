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
    this.classList = { toggle: name => {
      const classes = new Set(this.className.split(' ').filter(Boolean));
      const added = !classes.has(name);
      if (added) classes.add(name); else classes.delete(name);
      this.className = [...classes].join(' ');
      return added;
    } };
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
const context = { document, matchMedia: () => ({ matches: false, addEventListener() {} }) };
vm.runInNewContext(fs.readFileSync(process.argv[3], 'utf8'), context);
const get = id => elements.get(id);

assert.equal(context.outcome([{ checks: [], response_status: 200 }]), 'neutral');
const unexecuted = { case_id: 'generated', checks: [], response_status: null, request: null, response: null };
assert.equal(context.outcome([unexecuted]), 'untested');
const unexecutedDetails = context.renderCase(unexecuted);
unexecutedDetails.open = true;
assert(unexecutedDetails.textContent.includes('Not executed'));
assert(unexecutedDetails.textContent.includes('earns no pass or coverage credit'));
assert(!unexecutedDetails.textContent.includes('Incomplete evidence'));
assert.equal(context.outcome([{ ...unexecuted, request: {} }]), 'neutral');
assert.equal(context.outcome([{ ...unexecuted, response: {} }]), 'neutral');
assert.equal(context.outcome([{ ...unexecuted, execution_status: 'executed' }]), 'neutral');
assert.equal(context.outcome([{ ...unexecuted, execution_status: 'unknown' }]), 'neutral');
const authChild = { case_id: 'auth-child', checks: [], response_status: 401,
  verification: { parent_case_id: 'parent', check: 'ignored_auth', status: 'success' } };
assert.equal(context.outcome([authChild]), 'pass');
assert.equal(context.outcome([authChild, unexecuted]), 'pass');
assert.equal(context.outcome([unexecuted], true, [{ status: 'error' }]), 'neutral');
assert.equal(context.outcome([{ ...authChild, response_status: null }]), 'neutral');
const authDetails = context.renderCase(authChild);
authDetails.open = true;
assert(authDetails.textContent.includes('Verified by ignored_auth on parent case parent.'));
assert(!authDetails.textContent.includes('No check results recorded.'));
assert.equal(context.outcome([authChild, { checks: [], response_status: 401 }]), 'neutral');
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
assert.equal(item.querySelectorAll('.http-details').length, 0);
assert.equal(item.querySelectorAll('.exchange-panel').length, 2);
assert(item.querySelectorAll('.exchange-headers').every(details => !details.open));
assert(item.querySelectorAll('.passed-checks').every(details => !details.open));
assert.equal(get('run-notices').querySelectorAll('.issues').length, 0);
assert(get('run-stats').textContent.includes('Failures accepted by an exception'));
get('run-stats').children[3].onclick();
assert.equal(get('operation-filters').children.find(button => button.textContent.startsWith('Failed')).attributes['aria-pressed'], 'true');

const truncated = context.renderExchange('Response', { status: 200,
  body: { status: 'truncated', text: '{"partial":"' + 'x'.repeat(700) } });
assert(truncated.textContent.includes('truncated'));
const payload = truncated.querySelectorAll('.payload')[0];
assert(payload.textContent.includes('partial'));
assert.equal(payload.attributes['aria-label'], 'Response body, scrollable');
const expandBody = truncated.querySelectorAll('.payload-expand')[0];
expandBody.onclick();
assert.equal(expandBody.attributes['aria-expanded'], 'true');
expandBody.onclick();
assert.equal(expandBody.attributes['aria-expanded'], 'false');

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

// Status filters apply at every level, including mixed cases within a phase.
const statusCase = (case_id, phase, status, exception) => ({
  case_id, phase, response_status: 200, checks: [{ name: 'check', status, exception }],
});
const mixedCases = [
  statusCase('failed', 'fuzzing', 'failure'),
  statusCase('passed-with-failure', 'fuzzing', 'success'),
  statusCase('accepted-with-failure', 'fuzzing', 'failure', { reason: 'allowed' }),
  { ...unexecuted, case_id: 'unexecuted-with-failure', phase: 'fuzzing' },
  statusCase('accepted', 'coverage', 'failure', { reason: 'allowed' }),
  statusCase('passed-with-accepted', 'coverage', 'success'),
  statusCase('passed', 'stateful', 'success'),
  { ...unexecuted, phase: 'Unknown' },
];
const filterOperation = (path, cases) => ({
  entity: 'Filters', method: 'GET', path, expected: true,
  tested: cases.some(item => !context.notExecuted(item)), cases,
  scenarios: [{ phase: 'examples', status: 'skip', skip_reason: 'no examples' }],
});
context.filterOperations = [
  filterOperation('/failed', mixedCases),
  filterOperation('/accepted', mixedCases.filter(item => item.phase !== 'fuzzing')),
  filterOperation('/untested', [{ ...unexecuted, phase: 'coverage' }]),
  filterOperation('/passed', [statusCase('passed', 'stateful', 'success')]),
];
vm.runInNewContext('DATA.runs[0].operations = filterOperations; selectRun(0);', context);
const selectFilter = label => get('operation-filters').children.find(button => button.textContent.startsWith(label)).onclick();
const expandEvidence = () => {
  for (const op of get('operations').querySelectorAll('.operation')) op.open = true;
  for (const phase of get('operations').querySelectorAll('.phase')) phase.open = true;
};
const visibleCaseIds = () => get('operations').querySelectorAll('.case-id').map(item => item.textContent);
selectFilter('Failed');
assert.equal(get('operations').querySelectorAll('.operation').length, 1);
expandEvidence();
assert.equal(get('operations').querySelectorAll('.phase').length, 1);
assert(get('operations').querySelectorAll('.phase-name')[0].textContent === 'fuzzing');
assert.deepEqual(visibleCaseIds(), ['Case failed']);
selectFilter('Accepted');
assert.equal(get('operations').querySelectorAll('.operation').length, 1);
expandEvidence();
assert.equal(get('operations').querySelectorAll('.phase').length, 1);
assert(get('operations').querySelectorAll('.phase-name')[0].textContent === 'coverage');
assert.deepEqual(visibleCaseIds(), ['Case accepted']);
selectFilter('Not tested');
assert.equal(get('operations').querySelectorAll('.operation').length, 1);
expandEvidence();
assert.equal(get('operations').querySelectorAll('.phase').length, 2);
assert.deepEqual(visibleCaseIds(), ['Case generated']);
assert(get('operations').textContent.includes('Not executed'));
selectFilter('All');
assert.equal(get('operations').querySelectorAll('.operation').length, 4);
expandEvidence();
assert.equal(get('operations').querySelectorAll('.case').length, 14);
assert(get('operations').textContent.includes('Skipped'));

// Entity shortcuts narrow the list and reset when changing the run.
get('entity-navigation').children.find(button => button.tagName === 'button' && button.textContent.startsWith('Filters')).onclick();
assert.equal(get('operations').querySelectorAll('.entity').length, 1);
get('older-run').onclick();
assert(get('operations').textContent.includes('Items'));

context.filterOperations = [filterOperation('/incomplete', [
  { case_id: 'missing-checks', phase: 'coverage', response_status: 200, checks: [] },
  statusCase('successful', 'coverage', 'success'),
])];
vm.runInNewContext('DATA.runs[0].operations = filterOperations; selectRun(0);', context);
selectFilter('Incomplete');
expandEvidence();
assert.deepEqual(visibleCaseIds(), ['Case missing-checks']);
