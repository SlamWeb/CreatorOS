// Real Chromium + loopback HTTP regression; no CreatorOS server, DB or model.
// Run: node --test web/e2e/benchmark-refresh.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const driver = fileURLToPath(new URL('./live-benchmark-v2.mjs', import.meta.url));
const sid = 'isolated-refresh-session', creator = 'isolated-creator', turn = 'isolated-turn';
const query = 'Retain this exact user turn through navigation';
const answer = 'Retain this exact assistant answer and Trace';

async function runFixture({ hostEvent = false, fault = null } = {}) {
  const output = await mkdtemp(path.join(tmpdir(), 'creatoros-benchmark-refresh-'));
  let opened = 0, submitted = false, turnPosts = 0, finishPosts = 0, traceGets = 0;
  let retained;
  const document = () => ({
    id: fault === 'session' && opened >= 2 ? 'wrong-session' : sid,
    creator_id: creator, status: 'idle', error: null,
    entries: submitted ? [
      { id: 'user-entry', kind: 'user', turn_id: fault === 'turn' && opened >= 2 ? 'wrong-turn' : turn, text: query },
      { id: 'assistant-entry', kind: 'assistant', turn_id: turn, text: answer },
    ] : [],
  });
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const json = (value, status = 200) => {
      response.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      response.end(JSON.stringify(value));
    };
    const body = async () => {
      let text = '';
      for await (const chunk of request) text += chunk;
      return JSON.parse(text);
    };
    if (url.pathname === '/__benchmark__/scenario') return json({
      case_id: 'refresh-regression', creator_id: creator, timeout_seconds: 15,
      steps: [{ kind: 'user', text: query }, ...(hostEvent ? [{ kind: 'event', name: 'fixture-only' }] : [])],
    });
    if (url.pathname === '/__benchmark__/event') { await body(); return json({ variables: {} }); }
    if (url.pathname === '/__benchmark__/finish') {
      finishPosts++; retained = await body(); return json({ retained: true });
    }
    if (url.pathname === '/__benchmark__/result') return json({ fixture_only: true });
    if (url.pathname === `/api/agent/sessions/${sid}/turns`) {
      turnPosts++; await body(); submitted = true; return json(document());
    }
    if (url.pathname === `/api/agent/sessions/${sid}/turn-trace/${turn}`) {
      traceGets++; return json({ turn_id: fault === 'trace' && opened >= 2 ? 'wrong-turn' : turn, requests: [] });
    }
    if (url.pathname === `/api/agent/sessions/${sid}`) {
      if (opened >= 2 && fault === 'http') return json({ error: 'fixture HTTP failure' }, 503);
      if (opened >= 2 && fault === 'json') {
        response.writeHead(200, { 'Content-Type': 'application/json' }); return response.end('{invalid');
      }
      return json(document());
    }
    if (url.pathname === '/favicon.ico') { response.writeHead(204); return response.end(); }
    if (!['/', '/opened'].includes(url.pathname)) { response.writeHead(404); return response.end(); }
    // Each refresh closes the panel; reopening does a real document navigation.
    // This deterministically invalidates the previous document's CDP response.
    const isOpen = url.pathname === '/opened' && ++opened % 2 === 1;
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    response.end(`<!doctype html><html><body><main></main><script>
      const sid = ${JSON.stringify(sid)}, turn = ${JSON.stringify(turn)};
      const endpoint = '/api/agent/sessions/' + sid;
      async function boot() {
        const doc = await (await fetch(endpoint)).json();
        const main = document.querySelector('main');
        if (!${isOpen}) {
          main.innerHTML = '<button>打开账号对话</button>';
          main.querySelector('button').onclick = () => { location.href = '/opened?navigation=' + Date.now(); };
          return;
        }
        main.innerHTML = '<section class="account-chat-panel"><div id="entries"></div><textarea aria-label="给 Agent 的消息"></textarea><button id="send">发送 ↑</button></section>';
        function render(value) {
          document.querySelector('#entries').innerHTML = value.entries.map(e => e.kind === 'user'
            ? '<div class="chat-user">' + e.text + '</div>'
            : '<div class="chat-reply"><div class="chat-answer">' + e.text + '</div><button class="trace">查看回复 Trace</button></div>').join('');
          for (const button of document.querySelectorAll('.trace')) button.onclick = async () => {
            await (await fetch(endpoint + '/turn-trace/' + turn)).json();
            const dialog = document.createElement('div'); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-label', '回复 Trace');
            dialog.innerHTML = '<button>关闭 Trace</button>'; dialog.querySelector('button').onclick = () => dialog.remove(); document.body.append(dialog);
          };
        }
        render(doc);
        document.querySelector('#send').onclick = async () => {
          const text = document.querySelector('textarea').value;
          const value = await (await fetch(endpoint + '/turns', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, request_id: turn }) })).json();
          render(value);
        };
        ${fault === 'post' ? `if (${opened} >= 2) await fetch(endpoint + '/turns', { method: 'POST', body: JSON.stringify({ text: 'unexpected', request_id: 'unexpected' }) });` : ''}
      }
      boot();
    </script></body></html>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const child = spawn(process.execPath, [driver, base, output], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let log = '';
    child.stdout.on('data', chunk => { log += chunk; });
    child.stderr.on('data', chunk => { log += chunk; });
    const timer = setTimeout(() => child.kill(), 60_000);
    const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('close', resolve); });
    clearTimeout(timer);
    const report = JSON.parse(await readFile(path.join(output, 'browser-report.json'), 'utf8'));
    return { code, report, retained, turnPosts, finishPosts, traceGets, output, log };
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}

for (const hostEvent of [false, true]) test(`real driver survives ${hostEvent ? 'hostEvent + finalRefresh' : 'finalRefresh'} panel navigation`, async () => {
  const result = await runFixture({ hostEvent });
  assert.equal(result.code, 0, `${result.output}\n${JSON.stringify(result.report.errors)}\n${result.log}`);
  assert.equal(result.report.completed, true);
  assert.equal(result.report.refresh_ok, true);
  assert.equal(result.report.refresh.session_id, sid);
  assert.equal(result.report.refresh.user.turn_id, turn);
  assert.equal(result.report.refresh.assistant.turn_id, turn);
  assert.equal(result.report.refresh.visible_reply_trace.turn_id, turn);
  assert.equal(result.report.refresh.rendered_reply, answer);
  assert.equal(result.turnPosts, 1);
  assert.equal(result.report.refresh.turn_posts_before, 1);
  assert.equal(result.report.refresh.turn_posts_after, 1);
  assert.equal(result.finishPosts, 1);
  assert.equal(result.retained.browser.completed, true);
  assert.equal(result.traceGets, 2);
  if (hostEvent) assert.equal(result.report.events[0].same_session_after_reload, true);
});

for (const [fault, message] of [
  ['http', /HTTP 503/], ['json', /JSON|Unexpected token|property name/],
  ['session', /changed the account conversation/], ['turn', /lost the last user query/],
  ['trace', /another server turn ID/], ['post', /extra turn POST/],
]) test(`real driver still rejects ${fault} failures`, async () => {
  const result = await runFixture({ fault });
  assert.equal(result.code, 1, result.output);
  assert.equal(result.report.completed, false);
  assert.match(result.report.errors.map(error => error.message).join('\n'), message);
  assert.equal(result.finishPosts, 1);
  assert.equal(result.retained.browser.completed, false);
});
