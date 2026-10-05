const DATA = JSON.parse(document.getElementById('report-data').textContent);
const $ = id => document.getElementById(id);
function node(tag, className = '', content = '') {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== null) element.textContent = String(content);
  return element;
}
const clear = element => element.replaceChildren();
const pill = (label, kind = 'neutral') => node('span', 'pill ' + kind, label);
const fmtDate = value => {
  if (!value) return 'Unknown timestamp';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};
const state = { run: 0, filter: 'all', view: 'report' };
const PHASES = ['examples', 'coverage', 'fuzzing', 'stateful'];
const STATUS = {
  pass: { label: 'Passed', icon: '✓', edge: '#426e54' },
  fail: { label: 'Failed', icon: '×', edge: '#92515b' },
  accepted: { label: 'Accepted failure', icon: '!', edge: '#8b7546' },
  neutral: { label: 'Incomplete evidence', icon: '?', edge: '#556170' },
  untested: { label: 'Not tested', icon: '–', edge: '#454d58' },
};
function checksOf(cases) { return cases.flatMap(item => item.checks); }
function outcome(cases, tested = true, scenarios = []) {
  const checks = checksOf(cases);
  if (checks.some(check => check.status === 'failure' && !check.exception)) return 'fail';
  if (checks.some(check => !['success', 'failure'].includes(check.status)) ||
      cases.some(item => !item.checks.length || item.response_status == null) ||
      scenarios.some(item => ['error', 'failure'].includes(item.status)) && !checks.some(check => check.status === 'failure')) return 'neutral';
  if (checks.some(check => check.status === 'failure')) return 'accepted';
  return tested && checks.length ? 'pass' : 'untested';
}
function statusPill(kind) { return pill(STATUS[kind].label, kind === 'untested' ? 'neutral' : kind); }
function metrics(cases) {
  const checks = checksOf(cases);
  return { cases: cases.length, checks: checks.length, failed: checks.filter(x => x.status === 'failure' && !x.exception).length };
}
function lazyDetails(className, summary, render) {
  const details = node('details', className);
  details.append(summary);
  let rendered = false;
  details.addEventListener('toggle', () => {
    if (details.open && !rendered) {
      rendered = true;
      details.append(render());
    }
  });
  return details;
}
function showView(view) {
  state.view = view;
  $('report').hidden = view !== 'report';
  $('trends').hidden = view !== 'trends';
  for (const name of ['report', 'trends']) $('tab-' + name).setAttribute('aria-current', name === view ? 'page' : 'false');
  if (view === 'trends') renderTrends();
}
function selectRun(index) {
  state.run = index;
  state.filter = 'all';
  $('run-search').value = '';
  $('operation-search').value = '';
  $('run-picker').open = false;
  renderHeader();
  renderRun();
  if (state.view === 'trends') {
    const run = DATA.runs[state.run];
    $('trend-group').value = (run.configuration_id || 'Unspecified') + ' · ' + (run.base_url || 'Unspecified target');
    renderTrends();
  }
}
function renderHeader() {
  const run = DATA.runs[state.run];
  $('selected-run').textContent = run.build_number;
  $('selected-time').textContent = fmtDate(run.started_at);
  $('selected-time').dateTime = run.started_at || '';
  $('selected-status').className = 'pill ' + (run.passed ? 'pass' : 'fail');
  $('selected-status').textContent = run.passed ? 'PASS' : 'FAIL';
  $('run-picker-summary').setAttribute('aria-label', 'Choose a run. Selected ' + run.build_number + ', ' + fmtDate(run.started_at) + ', ' + (run.passed ? 'PASS' : 'FAIL'));
  $('run-count').textContent = 'Run ' + (state.run + 1) + ' of ' + DATA.runs.length + ' · newest first';
  $('older-run').disabled = state.run === DATA.runs.length - 1;
  $('newer-run').disabled = state.run === 0;
  renderRunOptions();
}
function renderRunOptions() {
  const root = $('run-options');
  clear(root);
  const query = $('run-search').value.toLowerCase();
  DATA.runs.forEach((run, index) => {
    if (![run.build_number, run.configuration_id, run.started_at, run.passed ? 'pass' : 'fail'].join(' ').toLowerCase().includes(query)) return;
    const button = node('button', 'run-option');
    button.type = 'button';
    button.setAttribute('aria-current', String(index === state.run));
    const label = node('span');
    label.append(node('strong', '', run.build_number), node('small', '', fmtDate(run.started_at) + ' · ' + (run.configuration_id || 'No configuration')));
    button.append(label, pill(run.passed ? 'PASS' : 'FAIL', run.passed ? 'pass' : 'fail'));
    button.onclick = () => selectRun(index);
    root.append(button);
  });
  if (!root.childNodes.length) root.append(node('p', 'empty', 'No matching runs.'));
}
function metadata(label, value, extra = '') {
  const item = node('div');
  item.append(node('span', 'meta-label', label), node('span', 'meta-value', value || 'Unavailable'));
  if (extra) item.append(node('p', 'meta-extra', extra));
  return item;
}
function renderRun() {
  const run = DATA.runs[state.run], meta = $('run-metadata');
  clear(meta);
  meta.append(metadata('Configuration', run.configuration_id), metadata('Component version', run.component_version),
    metadata('Target', run.base_url, 'Schemathesis ' + (run.schemathesis_version || 'unknown') + ' · TraceCov ' + (run.tracecov_version || 'unknown')));
  if (run.coverage_href) {
    const link = node('a', 'coverage-link', 'TraceCov coverage ↗');
    link.href = run.coverage_href;
    link.target = '_blank';
    link.rel = 'noopener';
    meta.append(link);
  }
  const stats = $('run-stats');
  clear(stats);
  const expected = run.operations.filter(op => op.expected);
  const values = [
    ['Operations', expected.filter(op => op.tested).length, 'of ' + expected.length + ' selected operations tested', 'var(--green)'],
    ['Cases', run.case_count, 'Observed request and response cases', 'var(--blue)'],
    ['Checks', run.check_count, (run.success_count || 0) + ' successful check results', 'var(--blue)'],
    ['Failed checks', run.failure_count, (run.failure_count - run.accepted_count) + ' unexplained failures', run.failure_count ? 'var(--red)' : 'var(--green)'],
    ['Exceptions', run.accepted_count, 'Failures accepted by an exception', 'var(--amber)'],
  ];
  for (const [label, value, description, accent] of values) {
    const card = node('div', 'stat');
    card.style.setProperty('--accent', accent);
    const top = node('div', 'stat-top');
    top.append(node('span', 'stat-label', label), node('span', 'stat-dot'));
    card.append(top, node('div', 'stat-value', value), node('p', 'stat-description', description));
    stats.append(card);
  }
  const notes = $('run-notices');
  clear(notes);
  if (!run.http_available) notes.append(node('p', 'notice', 'This older run has no recorded HTTP exchanges.'));
  if (run.issues.length) {
    const issues = node('details', 'issues');
    issues.append(node('summary', '', run.issues.length + ' run issue' + (run.issues.length === 1 ? '' : 's')));
    for (const issue of run.issues) issues.append(node('p', 'notice', issue));
    notes.append(issues);
  }
  const provenance = $('run-provenance');
  clear(provenance);
  provenance.append(metadata('Schema', run.schema_location), metadata('Schema SHA-256', run.schema_sha256), metadata('Configuration SHA-256', run.config_sha256));
  renderFilters();
  renderOperations();
}
function renderFilters() {
  const operations = DATA.runs[state.run].operations, root = $('operation-filters');
  clear(root);
  for (const [value, label] of [['all', 'All'], ['fail', 'Failed'], ['accepted', 'Accepted'], ['untested', 'Not tested']]) {
    const count = value === 'all' ? operations.length : operations.filter(op => outcome(op.cases, op.tested, op.scenarios) === value).length;
    const button = node('button', 'filter', label);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(state.filter === value));
    button.append(node('span', 'filter-count', count));
    button.onclick = () => { state.filter = value; renderFilters(); renderOperations(); };
    root.append(button);
  }
}
function renderOperations() {
  const root = $('operations');
  clear(root);
  const run = DATA.runs[state.run], query = $('operation-search').value.toLowerCase();
  const operations = run.operations.filter(op => (state.filter === 'all' || outcome(op.cases, op.tested, op.scenarios) === state.filter) &&
    [op.entity, op.method, op.path, op.summary].join(' ').toLowerCase().includes(query));
  const groups = new Map();
  for (const op of operations) {
    if (!groups.has(op.entity)) groups.set(op.entity, []);
    groups.get(op.entity).push(op);
  }
  $('operation-result-count').textContent = operations.length + ' of ' + run.operations.length + ' operations · ' + groups.size + ' entities';
  for (const [entity, ops] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
    const details = node('details', 'entity');
    details.open = true;
    const summary = node('summary'), title = node('div', 'entity-title');
    title.append(node('h2', '', entity), node('small', '', ops.length + ' operation' + (ops.length === 1 ? '' : 's')));
    const stats = node('div', 'entity-stats');
    for (const [kind, label] of [['pass', 'passed'], ['fail', 'failed'], ['accepted', 'accepted'], ['neutral', 'incomplete'], ['untested', 'not tested']]) {
      const count = ops.filter(op => outcome(op.cases, op.tested, op.scenarios) === kind).length;
      if (count) stats.append(node('span', '', count + ' ' + label));
    }
    summary.append(title, stats);
    const body = node('div', 'entity-body');
    for (const op of ops) body.append(renderOperation(op));
    details.append(summary, body);
    root.append(details);
  }
  if (!operations.length) root.append(node('p', 'empty', 'No operations match your search or filter.'));
}
function metric(label, value) {
  const item = node('span', 'metric');
  item.append(node('strong', '', value), node('small', '', label));
  return item;
}
function renderOperation(op) {
  const kind = outcome(op.cases, op.tested, op.scenarios), summary = node('summary');
  summary.setAttribute('aria-label', op.method + ' ' + op.path + ', ' + STATUS[kind].label + ', ' + op.cases.length + ' cases');
  const icon = node('span', 'status-icon ' + (kind === 'untested' ? 'neutral' : kind), STATUS[kind].icon);
  icon.setAttribute('aria-hidden', 'true');
  const method = node('span', 'method method-' + op.method.toLowerCase(), op.method || 'OTHER');
  const path = node('div', 'op-label');
  path.append(node('span', 'op-path', op.path));
  if (op.summary) path.append(node('p', 'op-description', op.summary));
  const counts = metrics(op.cases), stats = node('span', 'op-metrics');
  stats.append(metric('Cases', counts.cases), metric('Checks', counts.checks));
  summary.append(icon, method, path, stats, statusPill(kind));
  const details = lazyDetails('operation', summary, () => renderPhases(op));
  details.style.setProperty('--edge', STATUS[kind].edge);
  return details;
}
function renderPhases(op) {
  const body = node('div', 'operation-body');
  if (!op.expected) body.append(node('p', 'notice', 'This operation is outside the selected test scope.'));
  const phases = [...new Set([...op.scenarios.map(x => x.phase || 'Unknown'), ...op.cases.map(x => x.phase || 'Unknown')])]
    .sort((a, b) => (PHASES.includes(a) ? PHASES.indexOf(a) : 99) - (PHASES.includes(b) ? PHASES.indexOf(b) : 99) || a.localeCompare(b));
  for (const phase of phases) {
    const scenarios = op.scenarios.filter(x => (x.phase || 'Unknown') === phase), cases = op.cases.filter(x => (x.phase || 'Unknown') === phase);
    const summary = node('summary'), counts = metrics(cases);
    const skipped = !cases.length && scenarios.length && scenarios.every(item => item.status === 'skip');
    summary.append(node('span', 'phase-name', phase), node('span', 'muted', counts.cases + ' cases · ' + counts.checks + ' checks'), skipped ? pill('Skipped') : statusPill(outcome(cases, cases.length > 0, scenarios)));
    body.append(lazyDetails('phase', summary, () => {
      const content = node('div', 'phase-body');
      for (const scenario of scenarios.filter(x => x.status === 'skip')) content.append(node('p', 'notice', 'Skipped: ' + (scenario.skip_reason || 'No reason recorded')));
      for (const item of cases) content.append(renderCase(item));
      if (!cases.length) content.append(node('p', 'empty', 'No observed cases in this phase.'));
      return content;
    }));
  }
  if (!phases.length) body.append(node('p', 'empty', 'No phases or cases recorded for this operation.'));
  return body;
}
function renderCase(item) {
  const summary = node('summary');
  summary.append(node('span', 'case-id', 'Case ' + (item.case_id || 'unknown')), node('span', 'muted', 'HTTP ' + (item.response_status ?? 'unknown') + ' · ' + item.checks.length + ' checks'), statusPill(outcome([item])));
  return lazyDetails('case', summary, () => {
    const body = node('div', 'case-body');
    if (!item.checks.length) body.append(node('p', 'notice', 'No check results recorded.'));
    for (const check of item.checks) {
      const row = node('div', 'check'), kind = check.status === 'failure' ? (check.exception ? 'accepted' : 'fail') : check.status === 'success' ? 'pass' : 'neutral';
      row.append(pill(kind === 'accepted' ? 'ACCEPTED' : kind === 'pass' ? 'PASS' : kind === 'fail' ? 'FAIL' : 'UNKNOWN', kind));
      const text = node('div');
      text.append(node('strong', '', check.name || 'Unnamed check'));
      if (check.failure_type) text.append(node('p', '', check.failure_type));
      if (check.failure_message) text.append(node('p', '', check.failure_message));
      if (check.exception) text.append(node('div', 'exception', (check.exception.reason || 'No reason') + ' · Owner: ' + (check.exception.owner || 'unknown') + ' · Expires: ' + (check.exception.expiry || 'unknown')));
      row.append(text);
      body.append(row);
    }
    const details = node('details', 'http-details');
    details.append(node('summary', '', 'Request and response'));
    const exchange = node('div', 'exchange');
    exchange.append(renderExchange('Request', item.request), renderExchange('Response', item.response));
    details.append(exchange);
    body.append(details);
    return body;
  });
}
function renderExchange(label, data) {
  const box = node('section');
  box.append(node('h3', '', label));
  if (!data) { box.append(node('p', 'muted', 'Not recorded in this evidence database.')); return box; }
  box.append(node('p', 'request-line', label === 'Request' ? (data.method || '') + ' ' + (data.uri || '') :
    'HTTP ' + (data.status ?? 'unknown') + (data.elapsed == null ? '' : ' · ' + data.elapsed + ' s')));
  box.append(node('h4', '', 'Headers'), node('pre', '', JSON.stringify(data.headers || {}, null, 2)), node('h4', '', 'Body'));
  const body = data.body || { status: 'unavailable' };
  if (body.status !== 'available') box.append(node('p', 'muted', body.status));
  if (body.text) box.append(node('pre', '', body.text));
  return box;
}
function trendGroups() {
  const groups = new Map();
  for (const run of [...DATA.runs].reverse()) {
    const key = (run.configuration_id || 'Unspecified') + ' · ' + (run.base_url || 'Unspecified target');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(run);
  }
  return groups;
}
function svgNode(tag, attributes, text = '') {
  const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
  element.textContent = text;
  return element;
}
function chart(runs, values, color, label) {
  const container = node('div', 'trend-chart-container');
  const tooltip = node('div', 'trend-tooltip');
  tooltip.hidden = true;
  tooltip.setAttribute('role', 'tooltip');
  const svg = svgNode('svg', { viewBox: '0 0 560 200', class: 'trend-chart', role: 'img', 'aria-label': 'Oldest to newest values: ' + values.join(', ') });
  const max = Math.max(1, ...values);
  for (const value of [0, max]) {
    const y = 160 - value / max * 125;
    svg.append(svgNode('line', { x1: 38, y1: y, x2: 525, y2: y, stroke: '#414b58' }), svgNode('text', { x: 30, y: y + 4, fill: '#a1aab8', 'text-anchor': 'end', 'font-size': 11 }, String(value)));
  }
  const points = values.map((value, index) => [38 + (values.length === 1 ? 245 : index * 487 / (values.length - 1)), 160 - value / max * 125]);
  svg.append(svgNode('polyline', { points: points.map(point => point.join(',')).join(' '), fill: 'none', stroke: color, 'stroke-width': 3 }));
  const dots = points.map((point, index) => {
    const dot = svgNode('circle', { cx: point[0], cy: point[1], r: 4, fill: color });
    dot.append(svgNode('title', {}, runs[index].build_number + ': ' + values[index]));
    svg.append(dot);
    return dot;
  });
  svg.append(svgNode('text', { x: 38, y: 187, fill: '#a1aab8', 'font-size': 11 }, fmtDate(runs[0].started_at)),
    svgNode('text', { x: 525, y: 187, fill: '#a1aab8', 'text-anchor': 'end', 'font-size': 11 }, fmtDate(runs.at(-1).started_at)));
  const guide = svgNode('line', { class: 'trend-guide', x1: 0, x2: 0, y1: 25, y2: 160, visibility: 'hidden' });
  svg.append(guide);
  let activeIndex = -1;
  const hide = () => {
    guide.setAttribute('visibility', 'hidden');
    tooltip.hidden = true;
    if (activeIndex >= 0) dots[activeIndex].setAttribute('r', 4);
    activeIndex = -1;
  };
  const hover = event => {
    const matrix = svg.getScreenCTM();
    if (!matrix) return;
    const pointer = svg.createSVGPoint();
    pointer.x = event.clientX;
    pointer.y = event.clientY;
    const x = pointer.matrixTransform(matrix.inverse()).x;
    let index = 0;
    points.forEach((point, candidate) => {
      if (Math.abs(point[0] - x) < Math.abs(points[index][0] - x)) index = candidate;
    });
    if (activeIndex !== index) {
      if (activeIndex >= 0) dots[activeIndex].setAttribute('r', 4);
      activeIndex = index;
      const run = runs[index];
      const heading = node('div', 'trend-tooltip-heading');
      heading.append(node('strong', '', run.build_number),
        pill(run.passed ? 'PASS' : 'FAIL', run.passed ? 'pass' : 'fail'));
      tooltip.replaceChildren(heading,
        node('time', 'muted', fmtDate(run.started_at)),
        node('div', '', label + ': ' + values[index]),
        node('div', 'muted', (run.failure_count - run.accepted_count) + ' failures · ' + run.accepted_count + ' accepted · ' + run.case_count + ' cases'));
    }
    const point = points[index];
    guide.setAttribute('x1', point[0]);
    guide.setAttribute('x2', point[0]);
    guide.setAttribute('visibility', 'visible');
    dots[index].setAttribute('r', 6);
    tooltip.hidden = false;
    pointer.x = point[0];
    pointer.y = point[1];
    const screenPoint = pointer.matrixTransform(matrix);
    const bounds = container.getBoundingClientRect();
    tooltip.style.left = Math.max(0, Math.min(screenPoint.x - bounds.left - tooltip.offsetWidth / 2, bounds.width - tooltip.offsetWidth)) + 'px';
  };
  svg.addEventListener('pointerenter', hover);
  svg.addEventListener('pointermove', hover);
  svg.addEventListener('pointerleave', hide);
  svg.addEventListener('pointercancel', hide);
  container.append(svg, tooltip);
  return container;
}
function renderTrends() {
  const groups = trendGroups(), select = $('trend-group'), previous = select.value;
  clear(select);
  for (const key of groups.keys()) { const option = node('option', '', key); option.value = key; select.append(option); }
  const selected = DATA.runs[state.run];
  const selectedGroup = (selected.configuration_id || 'Unspecified') + ' · ' + (selected.base_url || 'Unspecified target');
  select.value = groups.has(previous) ? previous : selectedGroup;
  const runs = groups.get(select.value) || [], root = $('trend-content');
  clear(root);
  if (!runs.length) return;
  const grid = node('div', 'trend-grid');
  for (const [title, values, color] of [['Unexplained failed checks', runs.map(x => x.failure_count - x.accepted_count), '#ff9299'], ['Observed cases', runs.map(x => x.case_count), '#94bfff']]) {
    const panel = node('section', 'panel'), delta = values.length > 1 ? values.at(-1) - values.at(-2) : null;
    panel.append(node('h2', '', title), node('p', 'muted', 'Latest: ' + values.at(-1) + (delta === null ? '' : ' · ' + (delta >= 0 ? '+' : '') + delta + ' from previous')), chart(runs, values, color, title));
    grid.append(panel);
  }
  root.append(grid);
  const history = node('section', 'panel');
  history.append(node('h2', '', 'Run history'));
  [...runs].reverse().forEach((run, index) => {
    const row = node('div', 'trend-row'), button = node('button', '', run.build_number);
    button.type = 'button';
    button.onclick = () => { selectRun(DATA.runs.indexOf(run)); showView('report'); };
    row.append(button, node('time', 'muted', fmtDate(run.started_at)), pill(run.passed ? 'PASS' : 'FAIL', run.passed ? 'pass' : 'fail'), node('span', '', (run.failure_count - run.accepted_count) + ' unexplained failures · ' + run.case_count + ' cases'));
    const older = runs[runs.length - 2 - index];
    if (older && older.schema_sha256 !== run.schema_sha256) row.append(pill('Schema changed', 'warn'));
    history.append(row);
  });
  root.append(history);
}
$('older-run').onclick = () => selectRun(state.run + 1);
$('newer-run').onclick = () => selectRun(state.run - 1);
$('run-search').oninput = renderRunOptions;
$('operation-search').oninput = renderOperations;
$('tab-report').onclick = () => showView('report');
$('tab-trends').onclick = () => showView('trends');
$('trend-group').onchange = renderTrends;
$('expand-operations').onclick = () => {
  for (const details of $('operations').querySelectorAll('.entity, .operation')) details.open = true;
};
$('collapse-all').onclick = () => {
  for (const details of $('operations').querySelectorAll('details')) details.open = false;
};
document.addEventListener('click', event => {
  if (!$('run-picker').contains(event.target)) $('run-picker').open = false;
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && $('run-picker').open) {
    $('run-picker').open = false;
    $('run-picker-summary').focus();
  }
});
for (const warning of DATA.warnings) $('warnings').append(node('p', 'notice', warning));
renderHeader();
renderRun();
showView('report');
