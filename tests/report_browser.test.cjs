// Optional real-Chrome check: node tests/report_browser.test.cjs <chrome executable> <report.html>
// Uses Node's built-in WebSocket/CDP support, with default file:// security settings.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function main() {
  const [chrome, report] = process.argv.slice(2);
  assert(chrome && report, 'Provide a Chrome executable and a generated HTML report');
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'api-report-chrome-'));
  const child = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile,
    '--no-first-run', '--no-default-browser-check', 'about:blank'], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  let stderr = '', socket;
  child.stderr.on('data', value => { stderr += value; });
  const pending = new Map(), errors = [], requests = [];
  let sequence = 0;
  try {
    const portFile = path.join(profile, 'DevToolsActivePort');
    let port;
    for (let i = 0; i < 200 && !port; i++) {
      try { port = fs.readFileSync(portFile, 'utf8').split('\n')[0]; } catch { /* Chrome is still publishing the port file. */ }
      if (!port) await pause(50);
    }
    assert(port, 'Chrome did not start: ' + stderr.slice(-2000));
    const targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
    socket = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
      if (message.method === 'Network.requestWillBeSent') requests.push(message.params.request.url);
      if (message.id) {
        const task = pending.get(message.id); if (!task) return;
        pending.delete(message.id); clearTimeout(task.timer);
        if (message.error) task.reject(new Error(message.error.message)); else task.resolve(message.result);
      }
    });
    function send(method, params = {}) {
      return new Promise((resolve, reject) => {
        const id = ++sequence;
        const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 20000);
        pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params }));
      });
    }
    async function evaluate(expression) {
      const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
      return result.result.value;
    }
    async function wait(expression) {
      for (let i = 0; i < 300; i++) { if (await evaluate(expression)) return; await pause(20); }
      throw new Error('Rendering did not finish: ' + expression);
    }
    await send('Runtime.enable'); await send('Page.enable'); await send('Performance.enable'); await send('Network.enable');
    await send('Page.navigate', { url: pathToFileURL(path.resolve(report)).href });
    await wait('Boolean(window.reportReady)'); await evaluate('window.reportReady');
    assert(await evaluate("document.querySelectorAll('#operations .operation').length > 0"), 'No operations rendered');
    const initial = await evaluate(`({
      ready_ms: performance.now(),
      operation_rows: document.querySelectorAll('#operations .operation').length,
      evidence_loaded: [...chunkCache.values()].some(chunk => chunk.value.some(item => Array.isArray(item.checks))),
      cases: DATA.runs.reduce((sum, run) => sum + run.case_count, 0),
      checks: DATA.runs.reduce((sum, run) => sum + run.check_count, 0)
    })`);
    initial.resources = requests.filter(url => url.endsWith('.js')).length;
    assert(initial.resources > 0);
    assert(requests.every(url => url.startsWith('file:') || url.startsWith('data:')), 'Report requested a network resource');
    assert(initial.operation_rows <= 100); assert.equal(initial.evidence_loaded, false);
    const metrics = await send('Performance.getMetrics');
    initial.heap_MB = metrics.metrics.find(item => item.name === 'JSHeapUsedSize').value / 1e6;
    const filtering = await evaluate(`(() => {
      const search = document.getElementById('operation-search'), samples = [];
      for (const query of ['/', '', 'unlikely-missing-operation', '', '/']) {
        const start = performance.now(); search.value = query; search.dispatchEvent(new Event('input'));
        samples.push(performance.now() - start);
      }
      search.value = ''; search.dispatchEvent(new Event('input')); return Math.max(...samples);
    })()`);
    await evaluate(String.raw`(() => {
      const operations = [...document.querySelectorAll('#operations .operation')];
      operations.sort((a,b) => Number(b.firstElementChild.getAttribute('aria-label').match(/(\d+) cases$/)[1]) - Number(a.firstElementChild.getAttribute('aria-label').match(/(\d+) cases$/)[1]));
      operations[0].open = true;
    })()`);
    await wait("document.querySelectorAll('#operations .phase').length > 0");
    await evaluate(String.raw`(() => {
      const phases = [...document.querySelectorAll('#operations .phase')];
      phases.sort((a,b) => Number(b.firstElementChild.textContent.match(/(\d+) cases/)[1]) - Number(a.firstElementChild.textContent.match(/(\d+) cases/)[1]));
      phases[0].open = true;
    })()`);
    await wait("document.querySelectorAll('#operations .case').length > 0");
    assert(await evaluate("document.querySelectorAll('#operations .case').length <= 50"));
    assert.equal(await evaluate('[...chunkCache.values()].some(chunk => chunk.value.some(item => Array.isArray(item.checks)))'), false);
    const caseStarted = Date.now();
    await evaluate("document.querySelector('#operations .case').open = true");
    await wait("document.querySelectorAll('#operations .case-evidence').length > 0");
    const caseMs = Date.now() - caseStarted;
    await evaluate("document.getElementById('tab-trends').click()");
    assert(await evaluate("document.querySelectorAll('#trend-content canvas').length >= 2"));
    await evaluate("document.getElementById('tab-report').click()");
    await evaluate('DATA.runs.length > 1 ? Promise.all([selectRun(0), selectRun(1), selectRun(0)]) : selectRun(0)');
    assert.equal(await evaluate('currentRun().id'), await evaluate('DATA.runs[0].id'));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ...initial, max_filter_ms: filtering, case_open_ms: caseMs, console_errors: errors.length }, null, 2));
    await send('Browser.close').catch(() => {});
  } finally {
    socket?.close();
    if (child.exitCode === null) child.kill();
    await Promise.race([new Promise(resolve => child.once('exit', resolve)), pause(2000)]);
    // Remove only this test's newly allocated profile, after Chrome exits.
    fs.rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
