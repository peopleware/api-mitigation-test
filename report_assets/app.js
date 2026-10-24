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
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (dateOnly) return dateOnly[3] + '/' + dateOnly[2] + '/' + dateOnly[1];
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' }) + ', ' +
    date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
};
// Classic scripts allow on-demand loading from a double-clicked file:// report.
const chunkCache = new Map(), pendingChunks = new Map(), chunkQueue = [];
const CACHE_BYTES = 16 * 1024 * 1024;
let cacheBytes = 0, runningChunks = 0;
window.reportChunk = (ref, bytes, payload) => {
  const pending = pendingChunks.get(ref);
  if (pending?.receive) pending.receive({ bytes, payload });
};
function rememberChunk(ref, value, bytes) {
  if (bytes > CACHE_BYTES) return;
  while (cacheBytes + bytes > CACHE_BYTES && chunkCache.size) {
    const oldest = chunkCache.keys().next().value;
    cacheBytes -= chunkCache.get(oldest).bytes;
    chunkCache.delete(oldest);
  }
  chunkCache.set(ref, { value, bytes });
  cacheBytes += bytes;
}
function loadChunk(ref) {
  const cached = chunkCache.get(ref);
  if (cached) {
    chunkCache.delete(ref); chunkCache.set(ref, cached);
    return Promise.resolve(cached.value);
  }
  if (pendingChunks.has(ref)) return pendingChunks.get(ref).promise;
  const pending = {};
  pending.promise = new Promise((resolve, reject) => {
    chunkQueue.push(async () => {
      try {
        if (typeof DecompressionStream !== 'function') throw new Error('Update Chrome or Edge to open this compressed report.');
        const { bytes, payload } = await new Promise((receive, fail) => {
          const script = document.createElement('script');
          let registered = false;
          const cleanup = () => { clearTimeout(timer); pending.receive = null; script.remove(); };
          const timer = setTimeout(() => { cleanup(); fail(new Error('Timed out loading report data.')); }, 30000);
          pending.receive = value => { registered = true; cleanup(); receive(value); };
          script.onload = () => { if (!registered) { cleanup(); fail(new Error('Invalid report data file.')); } };
          script.onerror = () => { cleanup(); fail(new Error('Report data file is missing or could not be loaded.')); };
          script.src = ref;
          document.head.append(script);
        });
        const compressed = Uint8Array.from(atob(payload), character => character.charCodeAt(0));
        const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
        const decoded = await new Response(stream).arrayBuffer();
        if (decoded.byteLength !== bytes) throw new Error('Report data length does not match its manifest.');
        const value = JSON.parse(new TextDecoder().decode(decoded));
        rememberChunk(ref, value, bytes);
        resolve(value);
      } catch (error) { reject(error); }
      finally { pendingChunks.delete(ref); runningChunks--; pumpChunks(); }
    });
  });
  pendingChunks.set(ref, pending);
  pumpChunks();
  return pending.promise;
}
function pumpChunks() {
  while (runningChunks < 4 && chunkQueue.length) { runningChunks++; chunkQueue.shift()(); }
}
function loadError(root, error, retry) {
  clear(root); root.setAttribute('aria-busy', 'false');
  root.append(node('p', 'notice', error.message + ' Keep the HTML and its .data folder together. If the report was rebuilt, reload this page.'));
  const button = node('button', '', 'Retry'); button.type = 'button'; button.onclick = retry; root.append(button);
}
function pagination(total, size, page, onPage, label) {
  const pages = Math.max(1, Math.ceil(total / size)), nav = node('nav', 'pagination');
  nav.setAttribute('aria-label', label + ' pages');
  const previous = node('button', '', 'Previous'), next = node('button', '', 'Next');
  previous.type = next.type = 'button'; previous.disabled = page === 0; next.disabled = page >= pages - 1;
  previous.setAttribute('aria-label', 'Previous ' + label.toLowerCase() + ' page');
  next.setAttribute('aria-label', 'Next ' + label.toLowerCase() + ' page');
  previous.onclick = () => onPage(page - 1); next.onclick = () => onPage(page + 1);
  const status = node('span', '', 'Page ' + (page + 1) + ' of ' + pages + ' · ' + total + ' ' + label.toLowerCase());
  status.setAttribute('aria-live', 'polite'); nav.append(previous, status, next);
  return nav;
}
const state = { run: DATA.combined ? -1 : 0, filter: 'all', view: 'report', entity: '', failureType: '', page: 0 };
let runEpoch = 0, renderEpoch = 0;
const failureType = check => check.failure_type || 'Unknown failure type';
const currentRun = () => state.run === -1 ? DATA.combined : DATA.runs[state.run];
function documentedResponseCoverage(run) {
  const rows = run.operations.flatMap(op => (op.response_coverage || []).map(response => ({ ...response, method: op.method, path: op.path })));
  if (!rows.length) return null;
  const covered = rows.filter(row => row.hits > 0).length;
  const probes = new Set(run.operations.filter(op => op.method_probe).map(op => op.path));
  const uncovered = rows.filter(row => !row.hits).map(row => ({ ...row,
    methodProbe: row.status === '405' && probes.has(row.path) }));
  return { covered, total: rows.length, percent: (100 * covered / rows.length).toFixed(2), uncovered };
}
const PHASES = ['examples', 'coverage', 'fuzzing', 'stateful'];
const STATUS = {
  pass: { label: 'Passed', icon: '✓', edge: '#426e54' },
  fail: { label: 'Failed', icon: '×', edge: '#92515b' },
  accepted: { label: 'Accepted failure', icon: '!', edge: '#8b7546' },
  neutral: { label: 'Incomplete evidence', icon: '?', edge: '#556170' },
  not_executed: { label: 'Not executed', icon: '–', edge: '#454d58' },
  untested: { label: 'Not tested', icon: '–', edge: '#454d58' },
};
function notExecuted(item) { return item.execution_status === 'not_executed'; }
function statusPill(kind) { return pill(STATUS[kind].label, ['untested', 'not_executed'].includes(kind) ? 'neutral' : kind); }
function reportOperations(run) {
  return run.operations.filter(op => !op.hidden_probe);
}
function lazyDetails(className, summary, render) {
  const details = node('details', className);
  details.append(summary);
  let body = null;
  details.addEventListener('toggle', () => {
    if (details.open && !body) { body = render(); details.append(body); }
    if (!details.open && body) { body.remove(); body = null; }
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
async function selectRun(index) {
  const epoch = ++runEpoch;
  ++renderEpoch;
  const previous = currentRun();
  if (previous) delete previous.operations;
  state.run = index;
  state.entity = '';
  state.filter = 'all';
  state.failureType = '';
  state.page = 0;
  $('run-search').value = '';
  $('operation-search').value = '';
  $('run-picker').open = false;
  for (const id of ['run-stats', 'run-notices', 'run-metadata', 'run-provenance', 'operations', 'entity-navigation', 'operation-pagination', 'operation-filters', 'failure-type-filter']) clear($(id));
  $('failure-type-filter').hidden = true;
  $('failure-type-select').disabled = true;
  $('operation-result-count').textContent = 'Loading operations…';
  const root = $('operations'); root.setAttribute('aria-busy', 'true');
  renderHeader();
  const run = currentRun();
  try {
    if (DATA.format_version !== 1) throw new Error('Unsupported report format. Regenerate this report.');
    const operations = [];
    for (const ref of run.operations_ref) {
      const chunk = await loadChunk(ref);
      if (epoch !== runEpoch) return;
      for (const op of chunk) operations.push(op);
    }
    run.operations = operations;
    root.setAttribute('aria-busy', 'false');
    renderRun();
    if (state.view === 'trends') {
      const selected = DATA.runs[Math.max(0, state.run)];
      $('trend-group').value = (selected.configuration_id || 'Unspecified') + ' · ' + (selected.base_url || 'Unspecified target');
      renderTrends();
    }
  } catch (error) {
    if (epoch === runEpoch) { $('operation-result-count').textContent = 'Operations unavailable'; loadError(root, error, () => selectRun(index)); }
  }
}
function renderHeader() {
  const run = currentRun();
  $('selected-run').textContent = run.build_number;
  $('selected-time').textContent = fmtDate(run.started_at);
  $('selected-time').dateTime = run.started_at || '';
  $('selected-status').className = 'pill ' + (run.passed ? 'pass' : 'fail');
  $('selected-status').textContent = run.passed ? 'PASS' : 'FAIL';
  $('run-picker-summary').setAttribute('aria-label', 'Choose a run. Selected ' + run.build_number + ', ' + fmtDate(run.started_at) + ', ' + (run.passed ? 'PASS' : 'FAIL'));
  $('run-count').textContent = run.combined ? DATA.runs.length + ' scenarios · combined overview' : 'Run ' + (state.run + 1) + ' of ' + DATA.runs.length + ' · newest first';
  $('older-run').disabled = state.run === DATA.runs.length - 1;
  $('newer-run').disabled = state.run === (DATA.combined ? -1 : 0);
  renderRunOptions();
}
function renderRunOptions() {
  const root = $('run-options');
  clear(root);
  const query = $('run-search').value.toLowerCase();
  const choices = DATA.runs.map((run, index) => ({ run, index }));
  if (DATA.combined) choices.unshift({ run: DATA.combined, index: -1 });
  choices.forEach(({ run, index }) => {
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
  const run = currentRun(), meta = $('run-metadata');
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
  const coverage = documentedResponseCoverage(run);
  if (coverage) {
    notes.append(node('p', 'notice', 'Documented response coverage: ' + coverage.percent + '% · ' + coverage.covered + '/' + coverage.total +
      ' unique method/path/status variants recorded by TraceCov.'));
    if (coverage.uncovered.length) {
      const details = lazyDetails('issues', node('summary', '', 'Response variants without TraceCov coverage · ' + coverage.uncovered.length), () => {
        const content = node('div');
        function showPage(page) {
          content.replaceChildren(pagination(coverage.uncovered.length, 100, page, showPage, 'Response variants'));
          for (const response of coverage.uncovered.slice(page * 100, (page + 1) * 100)) content.append(node('p', 'notice',
            response.method + ' ' + response.path + ' → ' + response.status +
            (response.methodProbe ? ' · 405 was observed on this URL by unsupported-method probes; TraceCov does not credit it to the declared operation.' : '')));
        }
        showPage(0);
        return content;
      });
      notes.append(details);
    }
  }
  notes.hidden = !notes.childNodes.length;
  $('run-context-summary').textContent = 'Run details · ' + (run.configuration_id || 'Unspecified configuration') + ' · v' + (run.component_version || 'unknown');
  const provenance = $('run-provenance');
  clear(provenance);
  for (const source of run.combined ? DATA.runs : [run]) {
    if (run.combined) provenance.append(metadata('Source scenario', source.configuration_id, source.build_number));
    provenance.append(metadata('Schema', source.schema_location), metadata('Schema SHA-256', source.schema_sha256), metadata('Configuration SHA-256', source.config_sha256));
  }
  renderFilters();
  renderOperations();
}
function renderFilters() {
  const select = $('failure-type-select');
  const types = Object.keys(currentRun().failure_types).sort();
  clear(select);
  for (const type of ['', ...types]) {
    const option = node('option', '', type || 'All failure types'); option.value = type; select.append(option);
  }
  select.value = state.failureType;
  select.disabled = !types.length;
  select.onchange = () => { state.failureType = select.value; state.entity = ''; renderFilters(); renderOperations(); };
  const failureFilter = $('failure-type-filter');
  clear(failureFilter);
  failureFilter.hidden = !state.failureType;
  if (state.failureType) {
    const reset = node('button', '', 'Clear failure type filter'); reset.type = 'button';
    reset.onclick = () => { state.failureType = ''; renderFilters(); renderOperations(); };
    failureFilter.append(node('span', '', 'Failure type: ' + state.failureType), reset);
  }
  const operations = reportOperations(currentRun()), root = $('operation-filters');
  clear(root);
  for (const [value,label] of [['all','All'],['fail','Failed'],['neutral','Incomplete'],['accepted','Accepted'],['untested','Not tested']]) {
    const count = value === 'all' ? operations.length : operations.filter(op => op.kind === value).length;
    const button = node('button','filter',label); button.type = 'button';
    button.setAttribute('data-count', String(count));
    button.setAttribute('aria-pressed',String(state.filter === value));
    button.append(node('span','filter-count',count));
    button.onclick = () => { state.filter = value; state.entity = ''; renderFilters(); renderOperations(); };
    root.append(button);
  }
}
function renderOperations(page = 0) {
  ++renderEpoch;
  if (!currentRun().operations) return;
  state.page = page;
  const root = $('operations'); clear(root);
  const run = currentRun(), query = $('operation-search').value.trim().toLowerCase();
  const operations = reportOperations(run);
  const matched = operations.filter(op => (!state.failureType || Object.hasOwn(op.failure_types, state.failureType)) &&
    (state.filter === 'all' || op.kind === state.filter) && [op.entity,op.method,op.path,op.summary].join(' ').toLowerCase().includes(query));
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
  const hidden = run.operations.length - operations.length;
  $('operation-result-count').textContent = count + ' of ' + operations.length + ' operations · ' + visible.length + ' entities' +
    (hidden ? ' · ' + hidden + ' passing unsupported-method probes hidden' : '');
  const flattened = visible.flatMap(([entity, ops]) => ops.map(op => ({ entity, op })));
  state.page = Math.min(state.page, Math.max(0, Math.ceil(count / 100) - 1));
  const pageGroups = new Map();
  for (const { entity, op } of flattened.slice(state.page * 100, (state.page + 1) * 100)) {
    if (!pageGroups.has(entity)) pageGroups.set(entity, []);
    pageGroups.get(entity).push(op);
  }
  $('operation-pagination').replaceChildren(pagination(count, 100, state.page, renderOperations, 'Operations'));
  for (const [entity,ops] of pageGroups) {
    const details = node('details','entity'); details.open = true;
    const summary = node('summary'), title = node('div','entity-title');
    title.append(node('h2','',entity),node('small','',ops.length + (ops.length === 1 ? ' operation' : ' operations')));
    const stats = node('div','entity-stats');
    for (const kind of ['fail','neutral','accepted','untested','pass']) {
      const total = ops.filter(op => op.kind === kind).length;
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
    reset.onclick = () => { state.entity = ''; state.filter = 'all'; state.failureType = ''; $('operation-search').value = ''; renderFilters(); renderOperations(); $('operation-search').focus(); };
    empty.append(reset); root.append(empty);
  }
}
function metric(label, value) {
  const item = node('span', 'metric');
  item.append(node('strong', '', value), node('small', '', label));
  return item;
}
function renderOperation(op) {
  const kind = op.kind, summary = node('summary');
  summary.setAttribute('aria-label', op.method + ' ' + op.path + ', ' + STATUS[kind].label + ', ' + op.case_count + ' cases');
  const icon = node('span', 'status-icon ' + (kind === 'untested' ? 'neutral' : kind), STATUS[kind].icon);
  icon.setAttribute('aria-hidden', 'true');
  const method = node('span', 'method method-' + op.method.toLowerCase(), op.method || 'OTHER');
  const path = node('div', 'op-label');
  path.append(node('span', 'op-path', op.path));
  if (op.summary) path.append(node('p', 'op-description', op.summary));
  const counts = { cases: op.case_count, checks: op.check_count }, stats = node('span', 'op-metrics');
  stats.append(metric('Cases', counts.cases), metric('Checks', counts.checks));
  summary.append(icon, method, path, stats, statusPill(kind));
  const details = lazyDetails('operation', summary, () => renderPhases(op));
  details.style.setProperty('--edge', STATUS[kind].edge);
  if (state.failureType) details.open = true;
  return details;
}
function renderPhases(op) {
  const body = node('div', 'operation-body');
  if (!op.expected) body.append(node('p', 'notice', 'This operation is outside the selected test scope.'));
  const phases = [...op.phases].sort((a, b) => (PHASES.includes(a.name) ? PHASES.indexOf(a.name) : 99) -
    (PHASES.includes(b.name) ? PHASES.indexOf(b.name) : 99) || a.name.localeCompare(b.name));
  for (const phase of phases) {
    if (state.filter !== 'all' && phase.kind !== state.filter) continue;
    if (state.failureType && !Object.hasOwn(phase.failure_types, state.failureType)) continue;
    const summary = node('summary');
    const skipped = !phase.case_count && phase.scenarios.length && phase.scenarios.every(item => item.status === 'skip');
    summary.append(node('span', 'phase-name', phase.name), node('span', 'muted', phase.case_count + ' cases · ' + phase.check_count + ' checks'),
      skipped ? pill('Skipped') : statusPill(phase.kind));
    body.append(lazyDetails('phase', summary, () => renderCasePages(phase)));
  }
  if (!phases.length) body.append(node('p', 'empty', 'No phases or cases recorded for this operation.'));
  return body;
}
function indexMatches(index, filter, type) {
  if (type) return index.types[type]?.[filter] || 0;
  return filter === 'all' ? index.count : index.kinds[filter] || 0;
}
function renderCasePages(phase) {
  const root = node('div', 'phase-body'), notices = node('div'), content = node('div'), controls = node('div');
  const filter = state.filter, type = state.failureType, epoch = renderEpoch;
  let request = 0;
  for (const scenario of phase.scenarios.filter(item => !type && item.status === 'skip')) notices.append(node('p', 'notice',
    (scenario.source ? scenario.source.configuration_id + ' · ' : '') + 'Skipped: ' + (scenario.skip_reason || 'No reason recorded')));
  root.append(notices, controls, content);
  const total = phase.indexes.reduce((sum, index) => sum + indexMatches(index, filter, type), 0);
  async function showPage(page) {
    const ticket = ++request;
    clear(content); content.setAttribute('aria-busy', 'true');
    content.append(node('p', 'muted', 'Loading cases…'));
    controls.replaceChildren(pagination(total, 50, page, showPage, 'Cases'));
    try {
      let skip = page * 50;
      const visible = [];
      for (const index of phase.indexes) {
        const count = indexMatches(index, filter, type);
        if (!count) continue;
        if (skip >= count) { skip -= count; continue; }
        const cases = await loadChunk(index.ref);
        if (epoch !== renderEpoch || ticket !== request || !root.isConnected) return;
        for (const item of cases) {
          if ((filter !== 'all' && item.kind !== filter) || (type && !item.failure_types.includes(type))) continue;
          if (skip) { skip--; continue; }
          visible.push(item);
          if (visible.length === 50) break;
        }
        if (visible.length === 50) break;
      }
      if (epoch !== renderEpoch || ticket !== request || !root.isConnected) return;
      clear(content); content.setAttribute('aria-busy', 'false');
      for (const item of visible) content.append(renderCase(item));
      if (!visible.length) content.append(node('p', 'empty', phase.case_count ? 'No cases match this filter.' : 'No observed cases in this phase.'));
    } catch (error) {
      if (epoch === renderEpoch && ticket === request && root.isConnected) loadError(content, error, () => showPage(page));
    }
  }
  queueMicrotask(() => showPage(0));
  return root;
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
  if(check.exception) text.append(node('div','exception',(check.exception.reason || 'No reason') + ' · Owner: ' + (check.exception.owner || 'unknown') + ' · Expires: ' + (check.exception.expiry ? fmtDate(check.exception.expiry) : 'unknown')));
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
  summary.append(node('span','case-id','Case ' + (item.case_id || 'unknown')),node('span','muted','HTTP ' + (item.response_status ?? 'unknown') + ' · ' + item.check_count + ' checks'),statusPill(notExecuted(item) ? 'not_executed' : item.kind));
  const source = currentRun().combined ? DATA.runs.find(run => run.id === item.source_run) : null;
  if (source) summary.append(pill(source.configuration_id));
  return lazyDetails('case', summary, () => {
    const root = node('div', 'case-loading'), epoch = renderEpoch;
    async function show() {
      clear(root); root.setAttribute('aria-busy', 'true'); root.append(node('p', 'muted', 'Loading evidence…'));
      try {
        const records = await loadChunk(item.detail_ref);
        if (epoch !== renderEpoch || !root.isConnected) return;
        const record = records.find(value => value.uid === item.uid);
        if (!record) throw new Error('Case evidence was not found in its data file.');
        root.replaceChildren(renderCaseBody({ ...record, source })); root.setAttribute('aria-busy', 'false');
      } catch (error) { if (epoch === renderEpoch && root.isConnected) loadError(root, error, show); }
    }
    queueMicrotask(show);
    return root;
  });
}
function renderCaseBody(item) {
    const body = node('div','case-body compact-case');
    if (item.source) body.append(node('p', 'notice', 'Source: ' + item.source.configuration_id + ' · ' + item.source.build_number));
    if(notExecuted(item)) body.append(node('p','notice','No HTTP interaction or check results recorded. This generated case is classified as not executed and earns no pass or coverage credit.'));
    else if(item.verification) body.append(node('p','notice','Verified by ' + item.verification.check + ' on parent case ' + item.verification.parent_case_id + '.'));
    else if(!item.checks.length) body.append(node('p','notice','No check results recorded.'));
    const checks = node('section','case-checks'); checks.setAttribute('aria-label','Check results');
    const visibleChecks = state.failureType ? item.checks.filter(check => check.status === 'failure' && failureType(check) === state.failureType) : item.checks;
    const passed = visibleChecks.filter(check=>check.status === 'success'), other = visibleChecks.filter(check=>check.status !== 'success');
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
const trendCharts = new Set();
function operationCounts(run) {
  return run.operation_counts;
}
function tooltipDate(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return 'Unknown timestamp';
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) + ' - ' +
    date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
}
function passedChange(current, previous) {
  if (previous == null) return { symbol: '', kind: '', description: 'No previous run' };
  if (!current.observed || !previous.observed) return { symbol: '', kind: '', description: 'Ratio comparison unavailable: no observed operations' };
  const delta = current.passed * previous.observed - previous.passed * current.observed;
  if (!delta) return { symbol: '—', kind: 'unchanged', description: 'Passed ratio unchanged from previous run' };
  const points = Math.abs(delta) * 100 / (current.observed * previous.observed);
  const difference = points < 0.01 ? 'less than 0.01' : points.toLocaleString('en-GB', { maximumFractionDigits: 2 });
  return { symbol: delta > 0 ? '▲' : '▼', kind: delta > 0 ? 'up' : 'down',
    description: 'Passed or accepted ratio ' + (delta > 0 ? 'increased' : 'decreased') + ' by ' + difference + ' percentage points vs previous run' };
}
function chart(runs, series, label, { stacked = false } = {}) {
  async function openSegment(index, datasetIndex) {
    if (!stacked || !series[datasetIndex]?.values[index]) return;
    showView('report');
    const selection = selectRun(DATA.runs.indexOf(runs[index]));
    const epoch = runEpoch;
    await selection;
    if (epoch !== runEpoch || !currentRun().operations) return;
    state.failureType = series[datasetIndex].label;
    renderFilters(); renderOperations();
    $('failure-type-filter').querySelectorAll('button')[0].focus();
  }
  const dates = runs.map(run => {
    const date = new Date(run.started_at);
    return !run.started_at || Number.isNaN(date.getTime()) ? 'Unknown date' :
      date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  });
  const seenDates = new Set(), dateTicks = new Set();
  dates.forEach((date, index) => {
    if (!seenDates.has(date)) dateTicks.add(index);
    seenDates.add(date);
  });
  const container = node('div', 'trend-chart-container');
  if (stacked) {
    container.classList.toggle('failure-types-chart');
    container.style.height = (280 + Math.ceil(series.length / 3) * 24) + 'px';
  }
  const canvas = node('canvas', 'trend-chart');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', label + ', oldest to newest: ' + runs.map((run, i) => run.build_number + ': ' + series.map(item => item.label + ' ' + item.values[i]).join(', ')).join(', '));
  canvas.tabIndex = 0;
  const tooltip = node('div', 'trend-tooltip');
  tooltip.hidden = true;
  tooltip.setAttribute('role', 'tooltip');
  container.append(canvas, tooltip);
  const externalTooltip = ({ chart: instance, tooltip: model }) => {
    if (!model.opacity || !model.dataPoints?.length) { tooltip.hidden = true; return; }
    const index = model.dataPoints[0].dataIndex, run = runs[index];
    const heading = node('div', 'trend-tooltip-heading');
    heading.append(node('strong', '', run.build_number), pill(run.passed ? 'PASS' : 'FAIL', run.passed ? 'pass' : 'fail'));
    const timestamp = node('time', 'trend-tooltip-date', tooltipDate(run.started_at));
    timestamp.dateTime = run.started_at || '';
    tooltip.replaceChildren(heading, timestamp);
    const tooltipSeries = stacked ? [series[model.dataPoints[0].datasetIndex]] : series;
    for (const item of tooltipSeries) {
      const row = node('div', 'trend-tooltip-series'), marker = node('span', 'trend-tooltip-marker');
      marker.style.backgroundColor = item.color;
      marker.setAttribute('aria-hidden', 'true');
      row.append(marker, node('span', '', item.label + ': ' + item.values[index] +
        (item.accepted ? (item.values[index] === 1 ? ' occurrence (' : ' occurrences (') + item.accepted[index] + ' accepted)' : '')));
      tooltip.append(row);
    }
    tooltip.append(node('div', 'trend-tooltip-stats', operationCounts(run).observed + ' operations · ' +
      (run.failure_count - run.accepted_count) + ' failures · ' + run.accepted_count + ' accepted · ' + run.case_count + ' cases'));
    tooltip.hidden = false;
    let left = model.caretX + 10;
    if (left + tooltip.offsetWidth > instance.width) left = model.caretX - tooltip.offsetWidth - 10;
    tooltip.style.left = Math.max(0, Math.min(left, instance.width - tooltip.offsetWidth)) + 'px';
    tooltip.style.top = Math.max(0, Math.min(model.caretY - tooltip.offsetHeight / 2, instance.height - tooltip.offsetHeight)) + 'px';
  };
  const instance = new Chart(canvas, {
    type: stacked ? 'bar' : 'line',
    data: {
      labels: dates,
      datasets: series.map(item => ({ label: item.label, data: item.values,
        borderColor: item.color, backgroundColor: item.color, borderDash: item.dash || [],
        borderWidth: stacked ? 1 : 3, pointRadius: 2, pointBorderWidth: 1,
        pointHoverRadius: 4, pointHoverBorderWidth: 1, fill: false,
        tension: 0.4, cubicInterpolationMode: 'default' })),
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      onResize: () => { tooltip.hidden = true; },
      onClick: (_event, elements) => { if (elements.length) openSegment(elements[0].index, elements[0].datasetIndex); },
      onHover: (_event, elements) => { canvas.style.cursor = stacked && elements.length ? 'pointer' : 'default'; },
      interaction: stacked ? { mode: 'nearest', intersect: true, axis: 'xy' } : { mode: 'index', intersect: false, axis: 'x' },
      plugins: { legend: { display: series.length > 1, position: 'bottom',
        labels: { color: '#a1aab8', usePointStyle: true, pointStyle: stacked ? 'rect' : 'line', boxWidth: 24, padding: 12, font: { size: 11 } } },
        tooltip: { enabled: false, external: externalTooltip } },
      scales: {
        x: { stacked, grid: { display: false }, border: { color: '#414b58' },
          ticks: { color: '#a1aab8', maxRotation: 0, autoSkip: true, maxTicksLimit: 4, font: { size: 10 },
            callback: value => dateTicks.has(value) ? dates[value] : null } },
        y: { stacked, beginAtZero: true, border: { display: false }, grid: { color: '#414b58' },
          ticks: { color: '#a1aab8', precision: 0, maxTicksLimit: 4, font: { size: 10 } } },
      },
    },
    plugins: stacked ? [] : [{
      id: 'runGuide',
      afterDatasetsDraw(instance) {
        const active = instance.getActiveElements()[0];
        if (!active) return;
        const { ctx, chartArea } = instance;
        ctx.save(); ctx.strokeStyle = '#a1aab8'; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.moveTo(active.element.x, chartArea.top); ctx.lineTo(active.element.x, chartArea.bottom);
        ctx.stroke(); ctx.restore();
      },
    }],
  });
  trendCharts.add(instance);
  let focusedIndex = -1;
  let focusedSeries = 0;
  canvas.addEventListener('keydown', event => {
    if (stacked && event.key === 'Enter') { event.preventDefault(); const active = instance.getActiveElements()[0]; if (active) openSegment(active.index, active.datasetIndex); return; }
    const segmentKey = stacked && ['ArrowUp', 'ArrowDown'].includes(event.key);
    if (!segmentKey && !['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Escape') { hideTooltip(); return; }
    focusedIndex = segmentKey ? Math.max(0, focusedIndex) : event.key === 'Home' ? 0 : event.key === 'End' ? runs.length - 1 :
      Math.max(0, Math.min(runs.length - 1, focusedIndex + (event.key === 'ArrowLeft' ? -1 : 1)));
    let active = series.map((_, datasetIndex) => ({ datasetIndex, index: focusedIndex }))
      .filter(item => instance.isDatasetVisible(item.datasetIndex));
    if (!active.length) { hideTooltip(); return; }
    if (stacked) {
      focusedSeries = Math.max(0, Math.min(active.length - 1, focusedSeries + (segmentKey ? (event.key === 'ArrowUp' ? 1 : -1) : 0)));
      active = [active[focusedSeries]];
    }
    const point = instance.getDatasetMeta(active[0].datasetIndex).data[focusedIndex];
    instance.setActiveElements(active);
    instance.tooltip.setActiveElements(active, { x: point.x, y: point.y });
    instance.update('none');
  });
  function hideTooltip() {
    if (!instance.canvas) return;
    instance.setActiveElements([]);
    instance.tooltip.setActiveElements([], { x: 0, y: 0 });
    instance.update('none');
    tooltip.hidden = true;
  }
  canvas.addEventListener('blur', hideTooltip);
  return container;
}
function failureTypeBreakdown(runs) {
  const counts = runs.map(run => new Map(Object.entries(run.failure_types)));
  const types = [...new Set(counts.flatMap(count => [...count.keys()]))].sort();
  const panel = node('section', 'panel failure-types-panel');
  const heading = node('div', 'history-heading');
  heading.append(node('h3', '', 'Failure types per run'));
  panel.append(heading);
  if (!types.length) {
    panel.append(node('p', 'failure-types-description muted', 'No failed checks recorded for these runs.'));
    return panel;
  }
  panel.append(node('p', 'failure-types-description muted',
    'One bar per run, oldest to newest; each segment counts a failure type, including accepted failures. Hover for counts; click a segment to inspect matching failures. Select a legend item to toggle a type.'));
  const series = types.map((type, index) => {
    return { label: type, color: 'hsl(' + Math.round((210 + index * 137.508) % 360) + ', 65%, 68%)',
      values: counts.map(count => count.get(type)?.total || 0),
      accepted: counts.map(count => count.get(type)?.accepted || 0) };
  });
  panel.append(chart(runs, series, 'Failure types per run', { stacked: true }));
  return panel;
}
function renderTrends() {
  const groups = trendGroups(), select = $('trend-group'), previous = select.value;
  clear(select);
  for (const key of groups.keys()) { const option = node('option','',key); option.value = key; select.append(option); }
  const selected = DATA.runs[Math.max(0, state.run)];
  const selectedGroup = (selected.configuration_id || 'Unspecified') + ' · ' + (selected.base_url || 'Unspecified target');
  select.value = groups.has(previous) ? previous : selectedGroup;
  const runs = groups.get(select.value) || [], root = $('trend-content');
  for (const instance of trendCharts) instance.destroy();
  trendCharts.clear();
  clear(root);
  $('trend-description').textContent = runs.length + ' runs · oldest to newest charts · newest first history';
  if (!runs.length) { root.append(node('p','empty','No recorded runs for this configuration.')); return; }
  const latest = runs.at(-1), prior = runs.at(-2), strip = node('div','trend-summary');
  const latestItem = node('div','trend-latest');
  latestItem.append(node('span','stat-label','Latest run'), node('strong','',latest.build_number),pill(latest.passed ? 'Passed' : 'Failed',latest.passed ? 'pass':'fail'));
  strip.append(latestItem);
  const values = [['Unexplained failed checks',latest.failure_count-latest.accepted_count,prior ? (latest.failure_count-latest.accepted_count)-(prior.failure_count-prior.accepted_count):null,'fail'],['Observed cases',latest.case_count,prior ? latest.case_count-prior.case_count:null,''],['Accepted failures',latest.accepted_count,prior ? latest.accepted_count-prior.accepted_count:null,'accepted']];
  for (const [label,value,delta,kind] of values) {
    const item = node('div','trend-stat'); item.append(node('span','stat-label',label),node('strong',kind,value.toLocaleString()));
    item.append(node('span','trend-delta',delta == null ? 'First recorded run' : (delta === 0 ? 'No change' : (delta>0?'+':'') + delta.toLocaleString()) + ' vs previous'));
    strip.append(item);
  }
  root.append(strip);
  const grid = node('div','trend-grid');
  const operations = runs.map(operationCounts);
  const charts = [
    ['Failed checks', [
      { label: 'Unexplained failures', values: runs.map(run => run.failure_count - run.accepted_count), color: '#ff9299' },
      { label: 'Accepted failures', values: runs.map(run => run.accepted_count), color: '#efc06c' },
      { label: 'Total failed checks', values: runs.map(run => run.failure_count), color: '#94bfff' },
    ], 'Total, unexplained and accepted failures'],
    ['Operations', [
      { label: 'Passed or accepted operations', values: operations.map(counts => counts.passed), color: '#77d79a' },
      { label: 'Failed operations', values: operations.map(counts => counts.failed), color: '#ff9299' },
      { label: 'Observed operations', values: operations.map(counts => counts.observed), color: '#94bfff' },
    ], 'Observed operations by result'],
  ];
  for (const [title, series, description] of charts) {
    const panel = node('section','panel trend-panel');
    const heading = node('div','trend-panel-heading'); heading.append(node('h3','',title),node('span','muted',description));
    panel.append(heading,chart(runs,series,title)); grid.append(panel);
  }
  root.append(grid);
  root.append(failureTypeBreakdown(runs));
  const history = node('section','panel history-panel');
  const heading = node('div','history-heading'); heading.append(node('h3','','Run history'),node('span','muted','Select a run to inspect its operations')); history.append(heading);
  const table = node('table','history-table');
  table.append(node('caption','sr-only','Run history for ' + select.value + ', newest first'));
  const head = node('thead'), header = node('tr');
  for (const label of ['Run / date','Result','Operations','Unexplained failures','Cases']) { const th = node('th','',label); th.scope = 'col'; header.append(th); }
  head.append(header); table.append(head); const body = node('tbody');
  [...runs].reverse().forEach((run,index) => {
    const row = node('tr'); if(run === selected) row.className = 'selected-history';
    const name = node('td'), button = node('button','history-run',run.build_number); button.type = 'button';
    if(run === selected) button.setAttribute('aria-current','true');
    button.onclick = () => { selectRun(DATA.runs.indexOf(run)); showView('report'); };
    name.append(button,node('time','history-date',fmtDate(run.started_at)));
    const older = runs[runs.length-2-index];
    if(older && older.schema_sha256 !== run.schema_sha256) name.append(pill('Schema changed','warn'));
    const result = node('td'); result.append(pill(run.passed?'Passed':'Failed',run.passed?'pass':'fail'));
    const counts = operationCounts(run);
    const operations = node('td','numeric history-operations');
    const change = passedChange(counts, older ? operationCounts(older) : null);
    if (change.symbol) {
      const indicator = node('span','operation-change ' + change.kind,change.symbol);
      indicator.setAttribute('aria-hidden','true');
      operations.append(indicator);
    }
    operations.append(node('span','history-operation-ratio',counts.passed + '/' + counts.observed));
    operations.setAttribute('aria-label',counts.passed + ' passed or accepted of ' + counts.observed + ' observed operations. ' + change.description);
    operations.title = 'Passed or accepted / observed operations · ' + change.description;
    const failures = node('td','numeric' + (run.failure_count-run.accepted_count>0?' failure-number':''),(run.failure_count-run.accepted_count).toLocaleString());
    const cases = node('td','numeric',run.case_count.toLocaleString());
    row.append(name,result,operations,failures,cases); body.append(row);
  });
  table.append(body); history.append(table); root.append(history);
}
$('older-run').onclick = () => selectRun(state.run + 1);
$('newer-run').onclick = () => selectRun(state.run - 1);
$('run-search').oninput = renderRunOptions;
$('operation-search').oninput = () => renderOperations();
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
$('generated-time').textContent = fmtDate($('generated-time').dateTime);
const compactViewport = matchMedia('(max-width:767px)');
const syncRunContext = () => { $('run-context').open = !compactViewport.matches; };
compactViewport.addEventListener('change', syncRunContext);
syncRunContext();
showView('report');
window.reportReady = selectRun(state.run);
