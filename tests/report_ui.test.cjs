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
class ChartAdapter {
  static instances = [];
  constructor(canvas, config) {
    this.canvas = canvas; this.config = config; this.width = 320; this.active = [];
    this.tooltip = { setActiveElements: (active, position) => {
      this.tooltipActive = active; this.tooltipPosition = position;
    } };
    ChartAdapter.instances.push(this);
  }
  destroy() { this.destroyed = true; this.canvas = null; }
  getDatasetMeta() { return { data: this.config.data.datasets[0].data.map((_, i) => ({ x: i * 20, y: 40 })) }; }
  isDatasetVisible() { return true; }
  setActiveElements(active) { this.active = active; }
  update() {}
}
const context = { document, Chart: ChartAdapter, matchMedia: () => ({ matches: false, addEventListener() {} }) };
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
assert.equal(get('trend-content').querySelectorAll('canvas').length, 2);
assert.equal(get('trend-content').querySelectorAll('.history-table').length, 1);
const historyTable = get('trend-content').querySelectorAll('.history-table')[0];
assert.deepEqual(historyTable.querySelectorAll('th').map(cell => cell.textContent),
  ['Run / date', 'Result', 'Operations', 'Unexplained failures', 'Cases']);
assert.deepEqual(historyTable.querySelectorAll('.history-operations').map(cell => cell.textContent), ['▼ 0/1', '1/1']);
assert.equal(historyTable.querySelectorAll('.history-operations')[0].children[0].className, 'operation-change down');
assert.equal(historyTable.querySelectorAll('.operation-change')[0].className, 'operation-change down');
assert(historyTable.querySelectorAll('.history-operations')[0].attributes['aria-label'].includes('decreased by 100 percentage points'));
const ratio = (passed, observed) => ({ passed, observed });
assert.equal(context.passedChange(ratio(2, 6), ratio(1, 6)).symbol, '▲');
assert.equal(context.passedChange(ratio(2, 10), ratio(1, 2)).kind, 'down');
assert.equal(context.passedChange(ratio(1, 2), ratio(1, 4)).kind, 'up');
assert.equal(context.passedChange(ratio(1, 3), ratio(2, 6)).symbol, '—');
assert.equal(context.passedChange(ratio(2, 12), ratio(1, 6)).kind, 'unchanged');
assert.equal(context.passedChange(ratio(2, 6), null).symbol, '');
assert.equal(context.passedChange(ratio(0, 0), ratio(1, 6)).symbol, '');
assert.equal(context.passedChange(ratio(1, 6), ratio(0, 0)).symbol, '');
assert.equal(get('trend-content').querySelectorAll('.selected-history').length, 1);
assert(get('trend-content').textContent.includes('vs previous'));
// Check our Chart.js configuration and rich tooltip; the real canvas
// renderer and responsive sizing are exercised in a browser.
const charts = get('trend-content').querySelectorAll('.trend-chart-container');
const instances = ChartAdapter.instances.slice(-2);
assert.equal(instances[0].config.data.datasets.length, 3);
assert.equal(instances[0].config.options.plugins.legend.display, true);
assert.equal(instances[1].config.data.datasets.length, 3);
const failureData = JSON.parse(elements.get('report-data').textContent).runs.slice().reverse();
assert.deepEqual(Array.from(instances[0].config.data.datasets[0].data), failureData.map(run => run.failure_count - run.accepted_count));
assert.deepEqual(Array.from(instances[0].config.data.datasets[1].data), failureData.map(run => run.accepted_count));
assert.equal(instances[0].config.data.datasets[0].label, 'Unexplained failures');
assert.equal(instances[0].config.data.datasets[1].label, 'Accepted failures');
assert.equal(instances[0].config.data.datasets[0].borderColor, '#ff9299');
assert.equal(instances[0].config.data.datasets[1].borderColor, '#efc06c');
assert.equal(instances[0].config.data.datasets[2].label, 'Total failed checks');
assert.deepEqual(Array.from(instances[0].config.data.datasets[2].data), failureData.map(run => run.failure_count));
assert(instances[0].config.data.datasets.every(dataset => dataset.borderDash.length === 0));
assert.equal(instances[1].config.data.datasets[0].label, 'Passed or accepted operations');
assert.equal(instances[1].config.data.datasets[1].label, 'Failed operations');
assert.equal(instances[1].config.data.datasets[2].label, 'Observed operations');
assert.deepEqual(Array.from(instances[1].config.data.datasets[0].data), [1, 0]);
assert.deepEqual(Array.from(instances[1].config.data.datasets[1].data), [0, 1]);
assert.deepEqual(Array.from(instances[1].config.data.datasets[2].data), [1, 1]);
assert.equal(instances[1].config.data.datasets[1].borderColor, '#ff9299');
const countedOperation = (checks, response_status = 200, tested = true, scenarios = []) => ({
  tested, scenarios, cases: [{ checks, response_status }],
});
const counts = context.operationCounts({ operations: [
  countedOperation([{ status: 'success' }]),
  countedOperation([{ status: 'failure', exception: { reason: 'accepted' } }]),
  countedOperation([{ status: 'failure' }]),
  countedOperation([{ status: 'success' }], null),
  countedOperation([{ status: 'failure', exception: { reason: 'accepted' } }], null),
  countedOperation([], 200),
  countedOperation([], null, false),
  countedOperation([{ status: 'unknown' }]),
] });
assert.equal(counts.observed, 7);
assert.equal(counts.passed, 2);
assert.equal(counts.failed, 1);
instances.forEach((instance, i) => {
  assert.equal(instance.config.type, 'line');
  const dataset = instance.config.data.datasets[0];
  assert.equal(dataset.tension, 0.4);
  assert.equal(dataset.cubicInterpolationMode, 'default');
  assert.equal(instance.config.options.interaction.intersect, false);
  assert.equal(instance.config.options.maintainAspectRatio, false);
  const tooltip = charts[i].querySelectorAll('.trend-tooltip')[0];
  tooltip.offsetWidth = 220; tooltip.offsetHeight = 120;
  instance.height = 180;
  const external = instance.config.options.plugins.tooltip.external;
  external({ chart: instance, tooltip: { opacity: 1, dataPoints: [{ dataIndex: 0 }], caretX: 100, caretY: 90 } });
  assert(tooltip.textContent.includes('older'));
  assert(tooltip.querySelectorAll('.pill')[0].textContent === 'PASS');
  assert.equal(tooltip.querySelectorAll('.trend-tooltip-series').length, 3);
  assert(tooltip.querySelectorAll('.trend-tooltip-stats')[0].textContent.includes('1 operations · 0 failures · 0 accepted · 1 cases'));
  assert(/^\d{1,2} [A-Z][a-z]+ 2026 - \d{2}:\d{2}:\d{2}$/.test(tooltip.querySelectorAll('.trend-tooltip-date')[0].textContent));
  external({ chart: instance, tooltip: { opacity: 0 } });
  assert.equal(tooltip.hidden, true);
  assert.equal(instance.config.options.interaction.mode, 'index');
  const guideCalls = [];
  instance.config.plugins[0].afterDatasetsDraw({
    getActiveElements: () => [{ element: { x: 100 } }], chartArea: { top: 10, bottom: 150 },
    ctx: { save() {}, restore() {}, beginPath() {}, stroke() {},
      setLineDash: dash => guideCalls.push(Array.from(dash)),
      moveTo: (x, y) => guideCalls.push([x, y]), lineTo: (x, y) => guideCalls.push([x, y]) },
  });
  assert.deepEqual(guideCalls, [[4, 4], [100, 10], [100, 150]]);
  const canvas = charts[i].querySelectorAll('canvas')[0];
  canvas.events.keydown[0]({ key: 'End', preventDefault() {} });
  assert.equal(instance.tooltipActive[0].index, 1);
  assert.equal(instance.tooltipActive.length, 3);
  canvas.events.keydown[0]({ key: 'ArrowLeft', preventDefault() {} });
  assert.equal(instance.tooltipActive[0].index, 0);
  canvas.events.keydown[0]({ key: 'Escape', preventDefault() {} });
  assert.equal(instance.tooltipActive.length, 0);
});
get('trend-group').onchange();
assert(instances.every(instance => instance.destroyed));
// Removing a focused canvas can emit blur after Chart.js has been destroyed.
charts[0].querySelectorAll('canvas')[0].events.blur[0]();
assert.equal(ChartAdapter.instances.filter(instance => !instance.destroyed).length, 2);
const singleRunChart = context.chart([JSON.parse(elements.get('report-data').textContent).runs[0]], [{ label: 'Observed cases', values: [1], color: '#94bfff' }], 'Observed cases');
assert.equal(singleRunChart.querySelectorAll('canvas').length, 1);
assert.equal(ChartAdapter.instances.at(-1).config.data.datasets[0].data.length, 1);
const sameDayRuns = ['2026-10-12T10:00:00', '2026-10-13T10:00:00', '2026-10-13T14:00:00']
  .map((started_at, i) => ({ started_at, build_number: 'dated-' + i }));
context.chart(sameDayRuns, [{ label: 'Observed cases', values: [1, 2, 3], color: '#94bfff' }], 'Observed cases');
assert.equal(context.tooltipDate('2026-10-13T15:48:17'), '13 Oct 2026 - 15:48:17');
assert.equal(context.tooltipDate(null), 'Unknown timestamp');
const datedChart = ChartAdapter.instances.at(-1).config;
assert.equal(datedChart.data.datasets[0].data.length, 3);
assert.deepEqual(Array.from(datedChart.data.labels), ['12 Oct 2026', '13 Oct 2026', '13 Oct 2026']);
assert.equal(datedChart.options.scales.x.ticks.callback(0), '12 Oct 2026');
assert.equal(datedChart.options.scales.x.ticks.callback(1), '13 Oct 2026');
assert.equal(datedChart.options.scales.x.ticks.callback(2), null);
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
