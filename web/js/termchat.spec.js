// termchat.spec.js — end-to-end verification of the Code-Mode Terminal-Chat.
// Run via: npx playwright test termchat.spec.js  (server must be up; qb is a
// code-mode project on this dev box). NOT part of js_gate's default smoke set.
const { test, expect } = require('@playwright/test');
const BASE = 'http://127.0.0.1:8420';
const PROJECT = 'qb';

function guard(page) {
  const errors = [];
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const t = m.text();
    if (/Failed to load resource|net::ERR|favicon|responded with a status/i.test(t)) return;
    errors.push('console.error: ' + t);
  });
  page.on('pageerror', (e) => errors.push('pageerror: ' + (e && e.message || e)));
  return errors;
}

async function login(page) {
  await page.goto(BASE, { waitUntil: 'load' });
  const u = page.locator('#auth-username');
  if (await u.isVisible().catch(() => false)) {
    await u.fill('admin');
    await page.locator('#auth-password').fill('admin');
    await page.getByRole('button', { name: 'Anmelden' }).click();
  }
  await page.waitForFunction(
    () => typeof state === 'object' && state && state.modelsConfigReady === true
      && typeof API === 'function' && typeof terminalTogglePanel === 'function'
      && typeof _terminalAddChatTab === 'function' && typeof renderTermchatHistory === 'function'
      && typeof tcSend === 'function' && typeof tcSlash === 'function'
      && typeof tcShell === 'function',
    { timeout: 40000 });
}

// Boot the terminal panel against the qb project WITHOUT relying on nav UI:
// set the code-mode context directly, then call terminalTogglePanel(true).
async function openTerminalForQb(page) {
  await page.evaluate(async (proj) => {
    state.currentProject = proj;
    state.activeAgentId = 'main';
    // prime the workdir cache so _terminalCtx resolves code-mode
    const p = await API.getProject('main', proj);
    window._workdirProjectCache[proj] = { code_mode: !!p.code_mode, working_dir: p.working_dir || '', agent: 'main', name: proj };
    state._projectDetail = p; state._projectDetailName = proj; state._projectDetailAgent = 'main';
    await terminalTogglePanel(true);
  }, PROJECT);
  await page.waitForSelector('#terminal-panel', { state: 'visible', timeout: 10000 });
}

test('new endpoint returns code_chat list (authenticated)', async ({ page }) => {
  await login(page);
  const res = await page.evaluate(async (proj) => {
    return await API.get(`/v1/agents/main/projects/${encodeURIComponent(proj)}/code-chats`);
  }, PROJECT);
  expect(res).toHaveProperty('sessions');
  expect(Array.isArray(res.sessions)).toBe(true);
});

test('chat tab builds, slash commands work, history renders', async ({ page }) => {
  const errors = guard(page);
  await login(page);
  await openTerminalForQb(page);

  // Create a chat tab (lazy session — no turn yet).
  const built = await page.evaluate(() => {
    const t = _terminalAddChatTab('', null, 'Test Chat');
    return { hasLog: !!t.el.querySelector('.tc-log'), hasInput: !!t.el.querySelector('.tc-ta'),
             hasStatus: !!t.el.querySelector('.tc-status'), kind: t.kind, id: t.id };
  });
  expect(built.kind).toBe('chat');
  expect(built.hasLog && built.hasInput && built.hasStatus).toBe(true);

  // The ACTIVE chat tab's textarea must be visible (target by the tab id, not
  // DOM order — a persisted chat may also exist in the DOM but hidden).
  const visOk = await page.evaluate((id) => {
    const t = _term.tabs.find(x => x.id === id);
    const ta = t && t.el.querySelector('.tc-ta');
    return !!ta && t.el.style.display === 'flex' && t.el.offsetParent !== null;
  }, built.id);
  expect(visOk).toBe(true);

  // /help prints command lines into the log.
  const helpOk = await page.evaluate((id) => {
    const t = _term.tabs.find(x => x.id === id);
    tcSlash(t, '/help');
    return t.el.querySelector('.tc-log').textContent.includes('/model');
  }, built.id);
  expect(helpOk).toBe(true);

  // /think medium updates the status footer.
  const thinkOk = await page.evaluate((id) => {
    const t = _term.tabs.find(x => x.id === id);
    tcSlash(t, '/think medium');
    return t.thinking === 'medium' && /think:Mittel/.test(t.el.querySelector('.tc-status').textContent);
  }, built.id);
  expect(thinkOk).toBe(true);

  // /tools off flips the flag + status badge.
  const toolsOk = await page.evaluate((id) => {
    const t = _term.tabs.find(x => x.id === id);
    tcSlash(t, '/tools off');
    return t.showTools === false && /tools:aus/.test(t.el.querySelector('.tc-status').textContent);
  }, built.id);
  expect(toolsOk).toBe(true);

  // Unknown command → error line.
  const unknownOk = await page.evaluate((id) => {
    const t = _term.tabs.find(x => x.id === id);
    tcSlash(t, '/nope');
    return /Unbekannter Befehl/.test(t.el.querySelector('.tc-log').textContent);
  }, built.id);
  expect(unknownOk).toBe(true);

  // History section is present under the tree.
  await expect(page.locator('#terminal-chats .tc-hist-title')).toHaveText('Terminal-Chats');

  await page.waitForTimeout(300);
  expect(errors, errors.join('\n')).toEqual([]);
});

test('! command runs a shell command in the working_dir', async ({ page }) => {
  const errors = guard(page);
  await login(page);
  await openTerminalForQb(page);
  const built = await page.evaluate(() => _terminalAddChatTab('', null, 'Shell').id);

  // Run a deterministic command via the ! path (no LLM, no session needed).
  await page.evaluate((id) => {
    const t = _term.tabs.find(x => x.id === id);
    return tcShell(t, 'echo TERMCHAT_OK && pwd');
  }, built);

  // Output block must show the echoed marker + the project working_dir (pwd).
  await page.waitForFunction((id) => {
    const t = _term.tabs.find(x => x.id === id);
    const out = t && t.el.querySelector('.tc-shout');
    return out && /TERMCHAT_OK/.test(out.textContent);
  }, built, { timeout: 15000 });

  const shellInfo = await page.evaluate((id) => {
    const t = _term.tabs.find(x => x.id === id);
    return {
      out: t.el.querySelector('.tc-shout').textContent,
      echo: !!t.el.querySelector('.tc-shell .tc-shprompt'),  // `$ echo …` echo line
    };
  }, built);
  expect(shellInfo.echo).toBe(true);
  expect(shellInfo.out).toContain('TERMCHAT_OK');
  expect(shellInfo.out).toContain('/qb');   // pwd is the qb working_dir

  // A nonzero exit prints an "exit N" line.
  await page.evaluate((id) => tcShell(_term.tabs.find(x => x.id === id), 'exit 3'), built);
  await page.waitForFunction((id) => {
    const t = _term.tabs.find(x => x.id === id);
    return /exit 3/.test(t.el.querySelector('.tc-log').textContent);
  }, built, { timeout: 15000 });

  // A banned command is rejected with an error line.
  await page.evaluate((id) => tcShell(_term.tabs.find(x => x.id === id), 'rm -rf /'), built);
  await page.waitForFunction((id) => {
    const t = _term.tabs.find(x => x.id === id);
    return /verbotenes Muster/.test(t.el.querySelector('.tc-log').textContent);
  }, built, { timeout: 15000 });

  await page.waitForTimeout(200);
  expect(errors, errors.join('\n')).toEqual([]);
});

test('send a real turn — streams + persists as code_chat', async ({ page }) => {
  test.setTimeout(90000);
  const errors = guard(page);
  await login(page);
  await openTerminalForQb(page);

  // Fresh chat tab. NB: the tab id MUTATES on first send (_tcRekey swaps the
  // temp id for chat-<sessionId>), so we track the (sole) chat tab by kind,
  // not by a frozen id.
  await page.evaluate(() => { _terminalAddChatTab('', null, 'Turn Test'); });

  // Fire the send WITHOUT awaiting it in-page (tcSend resolves only when the
  // whole SSE stream ends; awaiting it would block the evaluate past timeout).
  // We poll for completion via waitForFunction below.
  await page.evaluate(() => {
    const t = _term.tabs.find(x => x.kind === 'chat');
    t.model = 'auto';
    tcSend(t, 'Antworte mit genau dem Wort: PONG');   // fire-and-forget
  });

  // Wait for streaming to finish + assistant text to render.
  await page.waitForFunction(() => {
    const t = _term.tabs.find(x => x.kind === 'chat');
    return t && !t.streaming && t.el.querySelector('.tc-asst') &&
           t.el.querySelector('.tc-asst').textContent.trim().length > 0;
  }, null, { timeout: 75000 });

  const result = await page.evaluate(async (proj) => {
    const t = _term.tabs.find(x => x.kind === 'chat');
    const asst = t.el.querySelector('.tc-asst').textContent;
    const sid = t.sessionId;
    // The new code_chat session must appear in the code-chats list…
    const cc = await API.get(`/v1/agents/main/projects/${encodeURIComponent(proj)}/code-chats`);
    const inCodeChats = (cc.sessions || []).some(s => s.id === sid);
    // …and must NOT appear in the normal project chat list.
    const normal = await API.get(`/v1/sessions?agent=main&project=${encodeURIComponent(proj)}`);
    const inNormal = (normal.sessions || []).some(s => s.id === sid);
    return { asst, sid, inCodeChats, inNormal,
             status: t.el.querySelector('.tc-status').textContent };
  }, PROJECT);

  expect(result.sid).toBeTruthy();
  expect(result.asst.toUpperCase()).toContain('PONG');
  expect(result.inCodeChats).toBe(true);
  expect(result.inNormal).toBe(false);     // the load-bearing isolation assertion
  expect(result.status).toMatch(/tok/);    // footer shows token counts

  // Clean up the test session so we don't leave junk.
  await page.evaluate(async (sid) => { try { await API.deleteSession(sid); } catch (e) {} }, result.sid);

  expect(errors, errors.join('\n')).toEqual([]);
});
