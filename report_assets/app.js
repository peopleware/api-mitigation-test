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
const state = { run: 0, filter: 'all', view: 'report', entity: '' };
const PHASES = ['examples', 'coverage', 'fuzzing', 'stateful'];
const STATUS = {
  pass: { label: 'Passed', icon: '✓', edge: '#426e54' },
  fail: { label: 'Failed', icon: '×', edge: '#92515b' },
  accepted: { label: 'Accepted failure', icon: '!', edge: '#8b7546' },
  neutral: { label: 'Incomplete evidence', icon: '?', edge: '#556170' },
  not_executed: { label: 'Not executed', icon: '–', edge: '#454d58' },
  untested: { label: 'Not tested', icon: '–', edge: '#454d58' },
};
function checksOf(cases) { return cases.flatMap(item => item.checks); }
function notExecuted(item) {
  if (item.execution_status != null) return item.execution_status === 'not_executed';
  return !item.checks.length && !item.verification && item.response_status == null && item.request == null && item.response == null;
}
function outcome(cases, tested = true, scenarios = []) {
  cases = cases.filter(item => !notExecuted(item));
  const checks = checksOf(cases);
  if (checks.some(check => check.status === 'failure' && !check.exception)) return 'fail';
  if (checks.some(check => !['success', 'failure'].includes(check.status)) ||
      cases.some(item => (!item.checks.length && !item.verification) || item.response_status == null) ||
      scenarios.some(item => ['error', 'failure'].includes(item.status)) && !checks.some(check => check.status === 'failure')) return 'neutral';
  if (checks.some(check => check.status === 'failure')) return 'accepted';
  return tested && (checks.length || cases.some(item => item.verification)) ? 'pass' : 'untested';
}
function statusPill(kind) { return pill(STATUS[kind].label, ['untested', 'not_executed'].includes(kind) ? 'neutral' : kind); }
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
  state.entity = '';
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
    ['Cases', run.generated_count ?? run.case_count, 'Generated cases · ' + (run.executed_count ?? run.case_count) + ' executed · ' + (run.unexecuted_count || 0) + ' not executed' + (run.unknown_execution_count ? ' · ' + run.unknown_execution_count + ' execution unknown' : '') + (run.verified_by_parent_count ? ' · ' + run.verified_by_parent_count + ' verified by parent check' : ''), 'var(--blue)'],
    ['Checks', run.check_count, (run.success_count || 0) + ' successful check results', 'var(--blue)'],
    ['Failed checks', run.failure_count, (run.failure_count - run.accepted_count) + ' unexplained failures', run.failure_count ? 'var(--red)' : 'var(--green)'],
    ['Exceptions', run.accepted_count, 'Failures accepted by an exception', 'var(--amber)'],
  ];
  for (const [label, value, description, accent] of values) {
    const card = node(label === 'Failed checks' ? 'button' : 'div', 'stat');
    if (label === 'Failed checks') {
      card.type = 'button';
      card.setAttribute('aria-label', 'Filter failed operations');
      card.onclick = () => { state.filter = 'fail'; state.entity = ''; $('operation-search').value = ''; renderFilters(); renderOperations(); $('operation-search').focus(); };
    }
    card.style.setProperty('--accent', accent);
    const top = node('div', 'stat-top');
    top.append(node('span', 'stat-label', label), node('span', 'stat-dot'));
    card.append(top, node('div', 'stat-value', value), node('p', 'stat-description', description));
    stats.append(card);
  }
  const notes = $('run-notices');
  clear(notes);
  if (!run.http_available) notes.append(node('p', 'notice', 'This older run has no recorded HTTP exchanges.'));
  notes.hidden = !notes.childNodes.length;
  $('run-context-summary').textContent = 'Run details · ' + (run.configuration_id || 'Unspecified configuration') + ' · v' + (run.component_version || 'unknown');
  const provenance = $('run-provenance');
  clear(provenance);
  provenance.append(metadata('Schema', run.schema_location), metadata('Schema SHA-256', run.schema_sha256), metadata('Configuration SHA-256', run.config_sha256));
  renderFilters();
  renderOperations();
}
function renderFilters() {
  const operations = DATA.runs[state.run].operations, root = $('operation-filters');
  clear(root);
  for (const [value,label] of [['all','All'],['fail','Failed'],['neutral','Incomplete'],['accepted','Accepted'],['untested','Not tested']]) {
    const count = value === 'all' ? operations.length : operations.filter(op => outcome(op.cases,op.tested,op.scenarios) === value).length;
    const button = node('button','filter',label); button.type = 'button';
    button.setAttribute('data-count', String(count));
    button.setAttribute('aria-pressed',String(state.filter === value));
    button.append(node('span','filter-count',count));
    button.onclick = () => { state.filter = value; state.entity = ''; renderFilters(); renderOperations(); };
    root.append(button);
  }
}
function renderOperations() {
  const root = $('operations'); clear(root);
  const run = DATA.runs[state.run], query = $('operation-search').value.trim().toLowerCase();
  const matched = run.operations.filter(op => (state.filter === 'all' || outcome(op.cases,op.tested,op.scenarios) === state.filter) && [op.entity,op.method,op.path,op.summary].join(' ').toLowerCase().includes(query));
  const groups = new Map();
  for (const op of matched) { if (!groups.has(op.entity)) groups.set(op.entity,[]); groups.get(op.entity).push(op); }
  const nav = $('entity-navigation'); clear(nav); nav.append(node('h2','','Entities'));
  const choices = [['','All entities',matched.length], ...[...groups].sort((a,b) => a[0].localeCompare(b[0])).map(([name,ops]) => [name,name,ops.length])];
  const mobileLabel = node('label','sr-only','Filter by entity'); mobileLabel.htmlFor = 'entity-select';
  const mobileSelect = node('select','entity-select'); mobileSelect.id = 'entity-select';
  for (const [value,label,count] of choices) { const option = node('option','',label + ' (' + count + ')'); option.value = value; mobileSelect.append(option); }
  mobileSelect.value = state.entity;
  mobileSelect.onchange = () => { state.entity = mobileSelect.value; renderOperations(); };
  nav.append(mobileLabel,mobileSelect);
  for (const [value,label,count] of choices) {
    const button = node('button'); button.type = 'button'; button.setAttribute('aria-pressed',String(state.entity === value));
    button.append(node('span','',label),node('small','',count));
    button.onclick = () => { state.entity = value; renderOperations(); }; nav.append(button);
  }
  const visible = [...groups].filter(([name]) => !state.entity || state.entity === name).sort((a,b) => a[0].localeCompare(b[0]));
  const count = visible.reduce((sum,[,ops]) => sum + ops.length,0);
  $('operation-result-count').textContent = count + ' of ' + run.operations.length + ' operations · ' + visible.length + ' entities';
  for (const [entity,ops] of visible) {
    const details = node('details','entity'); details.open = true;
    const summary = node('summary'), title = node('div','entity-title');
    title.append(node('h2','',entity),node('small','',ops.length + (ops.length === 1 ? ' operation' : ' operations')));
    const stats = node('div','entity-stats');
    for (const kind of ['fail','neutral','accepted','untested','pass']) {
      const total = ops.filter(op => outcome(op.cases,op.tested,op.scenarios) === kind).length;
      if (total) stats.append(pill(total + ' ' + STATUS[kind].label.toLowerCase(),kind));
    }
    summary.append(title,stats);
    const body = node('div','entity-body');
    for (const op of ops) body.append(renderOperation(op));
    details.append(summary,body); root.append(details);
  }
  if (!count) {
    const empty = node('div','empty'); empty.append(node('p','','No operations match this search or filter.'));
    const reset = node('button','','Clear search and filters'); reset.type = 'button';
    reset.onclick = () => { state.entity = ''; state.filter = 'all'; $('operation-search').value = ''; renderFilters(); renderOperations(); $('operation-search').focus(); };
    empty.append(reset); root.append(empty);
  }
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
    const kind = outcome(cases, cases.length > 0, scenarios);
    if (state.filter !== 'all' && kind !== state.filter) continue;
    const visibleCases = cases.filter(item => state.filter === 'all' || outcome([item]) === state.filter);
    const summary = node('summary'), counts = metrics(cases);
    const skipped = !cases.length && scenarios.length && scenarios.every(item => item.status === 'skip');
    summary.append(node('span', 'phase-name', phase), node('span', 'muted', counts.cases + ' cases · ' + counts.checks + ' checks'), skipped ? pill('Skipped') : statusPill(kind));
    body.append(lazyDetails('phase', summary, () => {
      const content = node('div', 'phase-body');
      for (const scenario of scenarios.filter(x => x.status === 'skip')) content.append(node('p', 'notice', 'Skipped: ' + (scenario.skip_reason || 'No reason recorded')));
      for (const item of visibleCases) content.append(renderCase(item));
      if (!cases.length) content.append(node('p', 'empty', 'No observed cases in this phase.'));
      return content;
    }));
  }
  if (!phases.length) body.append(node('p', 'empty', 'No phases or cases recorded for this operation.'));
  return body;
}
function compactCheck(check) {
  const kind = check.status === 'failure' ? (check.exception ? 'accepted' : 'fail') : check.status === 'success' ? 'pass' : 'neutral';
  const row = node('div','check compact-check');
  row.append(pill(kind === 'accepted' ? 'ACCEPTED' : kind === 'pass' ? 'PASS' : kind === 'fail' ? 'FAIL' : 'UNKNOWN',kind));
  const text = node('div'); text.append(node('strong','',check.name || 'Unnamed check'));
  if(check.failure_type) text.append(node('p','failure-type',check.failure_type));
  if(check.failure_message) {
    if(check.failure_message.length <= 240) text.append(node('p','failure-message',check.failure_message));
    else {
      const message = node('details','check-message');
      message.append(node('summary','',check.failure_message.replace(/\s+/g,' ').slice(0,140) + '…'),node('pre','',check.failure_message)); text.append(message);
    }
  }
  if(check.exception) text.append(node('div','exception',(check.exception.reason || 'No reason') + ' · Owner: ' + (check.exception.owner || 'unknown') + ' · Expires: ' + (check.exception.expiry || 'unknown')));
  row.append(text); return row;
}
function copyControl(label,textValue) {
  const button = node('button','copy-control',label); button.type = 'button';
  button.onclick = async () => {
    try { await navigator.clipboard.writeText(textValue); button.textContent = 'Copied'; }
    catch { button.textContent = 'Copy unavailable'; }
    setTimeout(() => { button.textContent = label; },1800);
  };
  return button;
}
function renderExchange(label,data) {
  const box = node('section','exchange-panel');
  const header = node('div','exchange-heading'); header.append(node('h3','',label));
  if(label === 'Request' && data?.method) header.append(node('span','method method-' + data.method.toLowerCase(),data.method));
  if(label === 'Response' && data) header.append(pill('HTTP ' + (data.status ?? 'unknown')));
  if(label === 'Response' && data?.elapsed != null) header.append(node('span','muted',data.elapsed + ' s'));
  box.append(header);
  if(!data) { box.append(node('p','evidence-unavailable','Not recorded in this evidence database.')); return box; }
  if(label === 'Request' && data.uri) {
    if(data.uri.length > 180) {
      const uri = node('details','exchange-uri'); uri.append(node('summary','','Full request URL'),node('pre','request-line',data.uri)); box.append(uri);
    } else box.append(node('p','request-line',data.uri));
  }
  const headers = data.headers || {}, entries = Object.entries(headers);
  const headerDetails = node('details','exchange-headers');
  headerDetails.append(node('summary','','Headers · ' + entries.length));
  const table = node('dl','header-list');
  for(const [name,value] of entries) table.append(node('dt','',name),node('dd','',Array.isArray(value) ? value.join('\n') : typeof value === 'string' ? value : JSON.stringify(value)));
  if(!entries.length) table.append(node('p','muted','No headers recorded.'));
  headerDetails.append(table); box.append(headerDetails);
  const body = data.body || {status:'unavailable'};
  const bodyHeading = node('div','payload-heading'); bodyHeading.append(node('h4','','Body'));
  if(body.text != null) {
    const raw = String(body.text); let formatted = raw, format = 'Text';
    try { formatted = JSON.stringify(JSON.parse(raw),null,2); format = 'JSON'; } catch {}
    bodyHeading.append(node('span','muted',format + (body.status !== 'available' ? ' · ' + body.status : '') + ' · ' + raw.length.toLocaleString() + ' characters'),copyControl('Copy body',raw));
    box.append(bodyHeading);
    if(raw.length) {
      const pre = node('pre','payload',formatted); pre.tabIndex = 0; pre.setAttribute('role','region'); pre.setAttribute('aria-label',label + ' body, scrollable');
      box.append(pre);
      if(raw.length > 600 || formatted.split('\n').length > 12) {
        const expand = node('button','payload-expand','Show full body'); expand.type = 'button'; expand.setAttribute('aria-expanded','false');
        expand.onclick = () => { const full = pre.classList.toggle('full-payload'); expand.textContent = full ? 'Compact body' : 'Show full body'; expand.setAttribute('aria-expanded',String(full)); };
        box.append(expand);
      }
    } else box.append(node('p','evidence-unavailable','Empty body.'));
  } else { box.append(bodyHeading,node('p','evidence-unavailable',body.status === 'available' ? 'No body text recorded.' : 'Body ' + body.status + '.')); }
  return box;
}
function renderCase(item) {
  const summary = node('summary');
  summary.append(node('span','case-id','Case ' + (item.case_id || 'unknown')),node('span','muted','HTTP ' + (item.response_status ?? 'unknown') + ' · ' + item.checks.length + ' checks'),statusPill(notExecuted(item) ? 'not_executed' : outcome([item])));
  return lazyDetails('case',summary,() => {
    const body = node('div','case-body compact-case');
    if(notExecuted(item)) body.append(node('p','notice','No HTTP interaction or check results recorded. This generated case is classified as not executed and earns no pass or coverage credit.'));
    else if(item.verification) body.append(node('p','notice','Verified by ' + item.verification.check + ' on parent case ' + item.verification.parent_case_id + '.'));
    else if(!item.checks.length) body.append(node('p','notice','No check results recorded.'));
    const checks = node('section','case-checks'); checks.setAttribute('aria-label','Check results');
    const passed = item.checks.filter(check=>check.status === 'success'), other = item.checks.filter(check=>check.status !== 'success');
    const heading = node('div','check-overview'); heading.append(node('h3','','Checks'));
    const failures = other.filter(check=>check.status==='failure' && !check.exception).length;
    const accepted = other.filter(check=>check.status==='failure' && check.exception).length;
    if(failures) heading.append(pill(failures + ' failed','fail'));
    if(accepted) heading.append(pill(accepted + ' accepted','accepted'));
    if(passed.length) heading.append(pill(passed.length + ' passed','pass'));
    checks.append(heading);
    for(const check of other) checks.append(compactCheck(check));
    if(passed.length) {
      const success = node('details','passed-checks'); success.append(node('summary','','Passed checks · ' + passed.length));
      const list = node('div','passed-check-list');
      for(const check of passed) list.append(check.failure_type || check.failure_message || check.exception ? compactCheck(check) : node('span','passed-check-name',check.name || 'Unnamed check'));
      success.append(list); checks.append(success);
    }
    body.append(checks);
    const evidence = node('section','case-evidence'); evidence.setAttribute('aria-label','HTTP exchange');
    const title = node('div','evidence-heading'); title.append(node('h3','','HTTP exchange'),node('span','muted','Request and response'));
    const exchange = node('div','exchange'); exchange.append(renderExchange('Request',item.request),renderExchange('Response',item.response));
    evidence.append(title,exchange); body.append(evidence); return body;
  });
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
const compactViewport = matchMedia('(max-width:767px)');
const syncRunContext = () => { $('run-context').open = !compactViewport.matches; };
compactViewport.addEventListener('change', syncRunContext);
syncRunContext();
renderHeader();
renderRun();
showView('report');
