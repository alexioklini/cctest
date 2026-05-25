// Shared helpers for the Brainy E2E + context specs (run via
// playwright.brainy.config.js, NOT part of the smoke gate).
//
// All flows log in admin/admin against the running dev server on :8420 and
// drive the real in-page globals (navigateTo, openProject, openGeneralSettings,
// brainyViewContext, …). The Brainy ask goes through the real /v1/helpdesk SSE
// endpoint with the page's auth token, so a real model answers.

const BASE = 'http://127.0.0.1:8420';

function attachConsoleGuard(page) {
  const errors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const t = msg.text();
      if (/Failed to load resource|net::ERR|favicon|the server responded with a status/i.test(t)) return;
      errors.push('console.error: ' + t);
    }
  });
  page.on('pageerror', (err) => {
    errors.push('pageerror: ' + (err && err.message ? err.message : String(err)));
  });
  return errors;
}

async function login(page) {
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  const userField = page.locator('#auth-username');
  if (await userField.isVisible().catch(() => false)) {
    await userField.fill('admin');
    await page.locator('#auth-password').fill('admin');
    await page.getByRole('button', { name: 'Anmelden' }).click();
  }
  await page.waitForSelector('#welcome-view', { state: 'visible', timeout: 10000 });
  // Ensure state.agents etc. are loaded before tests drive navigation.
  await page.waitForTimeout(600);
}

// Authenticated fetch helper inside the page context (reuses the stored token).
async function apiPost(page, path, body) {
  return page.evaluate(async ({ path, body }) => {
    const r = await fetch(location.origin + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') },
      body: JSON.stringify(body || {}),
    });
    let json = null; try { json = await r.json(); } catch (e) {}
    return { status: r.status, json };
  }, { path, body });
}

// Read what brainyViewContext() reports right now (the exact dict the client
// would send to /v1/helpdesk for the current view/modal).
async function readViewContext(page) {
  return page.evaluate(() => (typeof brainyViewContext === 'function') ? brainyViewContext() : null);
}

// Fire ONE Brainy question through the real /v1/helpdesk SSE endpoint, from the
// page context, with the live view_context. Returns {events, reply, error,
// toolCalls, raw}. `viewContext`/`sessionId` default to what the page reports
// (i.e. the genuine current context), but can be overridden.
async function askBrainy(page, message, opts = {}) {
  return page.evaluate(async ({ message, opts }) => {
    const vc = opts.viewContext !== undefined ? opts.viewContext
             : (typeof brainyViewContext === 'function' ? brainyViewContext() : {});
    const sid = opts.sessionId !== undefined ? opts.sessionId
             : (window.state && state.activeChat ? (state.activeChat.sessionId || '') : '');
    const resp = await fetch(location.origin + '/v1/helpdesk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + (localStorage.getItem('auth-token') || '') },
      body: JSON.stringify({ message, session_id: sid, view_context: vc }),
    });
    if (!resp.ok) {
      let e = 'HTTP ' + resp.status; try { e = (await resp.json()).error || e; } catch (x) {}
      return { httpError: e, status: resp.status, events: [], reply: '', error: e, toolCalls: [], raw: '' };
    }
    // Parse the SSE stream. CRITICAL: break on the `done` EVENT, not on stream
    // EOF — the server returns fast but the socket may linger (HTTP/1.0 close /
    // keepalive), so waiting for reader `done` can hang the whole turn.
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '', evType = '';
    const events = [], toolCalls = [];
    let reply = '', error = null, acc = '', finished = false;
    while (!finished) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n'); buf = lines.pop();
      for (const line of lines) {
        if (line.startsWith('event:')) { evType = line.slice(6).trim(); continue; }
        if (line.startsWith('data:')) {
          const payload = line.slice(5).trim();
          if (!payload) continue;
          let data = {}; try { data = JSON.parse(payload); } catch (x) { continue; }
          events.push({ type: evType, data });
          if (evType === 'text_delta' && data.text) acc += data.text;
          else if (evType === 'tool_call') toolCalls.push(data.name);
          else if (evType === 'error') error = data.message || 'error';
          else if (evType === 'done') { reply = (data.reply || acc || '').trim(); finished = true; break; }
        }
      }
    }
    try { await reader.cancel(); } catch (x) {}   // free the socket, don't block
    if (!reply) reply = acc.trim();
    return { events, reply, error, toolCalls, raw: acc };
  }, { message, opts });
}

module.exports = { BASE, attachConsoleGuard, login, apiPost, readViewContext, askBrainy };
