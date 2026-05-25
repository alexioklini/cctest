// brainy_e2e.spec.js — END-TO-END with a real LLM. For each view the user can
// be in, we (1) create the relevant test data via the API, (2) navigate the SPA
// into that view, (3) ask Brainy a context-specific question through the real
// /v1/helpdesk SSE endpoint (with the genuine view_context the client sends),
// and (4) assert Brainy answered WITHOUT a tool error, with a non-empty reply,
// and that it actually picked up the current context.
//
// Run (dev server up + a resolvable Brainy model configured):
//   cd web/js && npx playwright test -c playwright.brainy.config.js brainy_e2e.spec.js
//
// NOTE: real model output is non-deterministic. Assertions are therefore:
//   - HARD: no error event, reply non-empty (these must always hold).
//   - CONTEXT: the reply references the concrete context token (session title /
//     project name / "geplant"/"workflow"/"übersetz"…). Logged + asserted, but
//     phrased to tolerate paraphrase (case-insensitive substring / domain words).

const { test, expect } = require('@playwright/test');
const { login, apiPost, askBrainy, readViewContext } = require('./brainy_helpers');

// Shared hard assertions every Brainy answer must satisfy.
function assertHealthy(res, label) {
  expect(res.httpError, `${label}: HTTP error: ${res.httpError}`).toBeFalsy();
  expect(res.error, `${label}: Brainy error event: ${res.error}`).toBeFalsy();
  expect((res.reply || '').length, `${label}: empty reply`).toBeGreaterThan(0);
  // A tool error surfaces inside the reply as Brainy's "kann ... nicht abrufen"
  // apology — guard against the v9.21.5 regression class.
  expect(/nicht abrufen|technischer Fehler|AttributeError|no active session/i.test(res.reply),
    `${label}: reply admits a tool failure:\n${res.reply}`).toBeFalsy();
}

function ctxHit(reply, ...needles) {
  const r = (reply || '').toLowerCase();
  return needles.some((n) => r.includes(String(n).toLowerCase()));
}

test('E2E: chat session — Brainy reads the current chat', async ({ page }) => {
  await login(page);
  // Create a session and seed one user turn so session_info has real content.
  const made = await apiPost(page, '/v1/sessions', {});
  const sid = made.json && made.json.session_id;
  expect(sid, 'session created').toBeTruthy();
  await page.evaluate((id) => openSession(id, 'main'), sid);
  await page.waitForTimeout(600);

  const res = await askBrainy(page, 'Worum geht es in diesem Chat gerade, und was ist hier eingestellt?');
  console.log('[chat] tools=' + JSON.stringify(res.toolCalls) + ' reply=' + JSON.stringify(res.reply.slice(0, 280)));
  assertHealthy(res, 'chat');
  // Brainy should have consulted the session (tool) OR at least talk about THIS chat.
  expect(res.toolCalls.includes('helpdesk_session_info') || ctxHit(res.reply, 'chat', 'sitzung', 'unterhaltung'),
    'chat: no session awareness').toBeTruthy();
});

test('E2E: project detail — Brainy knows which project', async ({ page }) => {
  await login(page);
  const name = 'brainy-e2e-proj-' + Date.now();
  const made = await apiPost(page, '/v1/agents/main/projects', { name, description: 'E2E Testprojekt' });
  expect([200, 201]).toContain(made.status);
  await page.evaluate((n) => openProject('main', n), name);
  await page.waitForTimeout(700);

  const vc = await readViewContext(page);
  expect(vc.project, 'view_context carries the project').toBe(name);

  const res = await askBrainy(page, 'Auf welchem Projekt bin ich gerade und wie konfiguriere ich es?');
  console.log('[project] vc=' + JSON.stringify(vc) + ' reply=' + JSON.stringify(res.reply.slice(0, 280)));
  assertHealthy(res, 'project');
  expect(ctxHit(res.reply, name, 'projekt'), 'project: reply ignores project context').toBeTruthy();
});

test('E2E: project chat — Brainy distinguishes a project chat', async ({ page }) => {
  await login(page);
  const name = 'brainy-e2e-pchat-' + Date.now();
  await apiPost(page, '/v1/agents/main/projects', { name });
  // Enter the project, then start a chat inside it (project context stays set).
  await page.evaluate((n) => openProject('main', n), name);
  await page.waitForTimeout(500);
  // currentProject is set; a new chat in this context reports Projekt-Chat.
  const res = await askBrainy(page, 'Was ist der Unterschied zwischen diesem Projekt-Chat und einem normalen Chat?');
  console.log('[project-chat] reply=' + JSON.stringify(res.reply.slice(0, 280)));
  assertHealthy(res, 'project-chat');
  expect(ctxHit(res.reply, 'projekt'), 'project-chat: reply ignores project context').toBeTruthy();
});

test('E2E: scheduled — Brainy explains scheduled tasks in context', async ({ page }) => {
  await login(page);
  const tname = 'brainy-e2e-sched-' + Date.now();
  await apiPost(page, '/v1/schedule', {
    action: 'add', name: tname, task: 'Sag Hallo', schedule: 'daily 09:00',
  });
  await page.evaluate(() => navigateTo('scheduled'));
  await page.waitForTimeout(500);

  const res = await askBrainy(page, 'Was sehe ich hier und wie lege ich eine neue geplante Aufgabe an?');
  console.log('[scheduled] reply=' + JSON.stringify(res.reply.slice(0, 280)));
  assertHealthy(res, 'scheduled');
  expect(ctxHit(res.reply, 'geplant', 'zeitplan', 'aufgabe', 'schedul', 'cron'),
    'scheduled: reply off-topic').toBeTruthy();
});

test('E2E: workflows — Brainy explains workflows in context', async ({ page }) => {
  await login(page);
  await page.evaluate(() => navigateTo('workflows'));
  await page.waitForTimeout(500);

  const res = await askBrainy(page, 'Wozu dient diese Ansicht und wie erstelle ich einen Workflow?');
  console.log('[workflows] reply=' + JSON.stringify(res.reply.slice(0, 280)));
  assertHealthy(res, 'workflows');
  expect(ctxHit(res.reply, 'workflow', 'ablauf'), 'workflows: reply off-topic').toBeTruthy();
});

test('E2E: translation — Brainy explains the translation view', async ({ page }) => {
  await login(page);
  await page.evaluate(() => navigateTo('translation'));
  await page.waitForTimeout(500);

  const res = await askBrainy(page, 'Was kann ich auf dieser Seite tun?');
  console.log('[translation] reply=' + JSON.stringify(res.reply.slice(0, 280)));
  assertHealthy(res, 'translation');
  expect(ctxHit(res.reply, 'übersetz', 'translation', 'sprache'),
    'translation: reply off-topic').toBeTruthy();
});

test('E2E: user activity — Brainy can summarise what the user has done', async ({ page }) => {
  await login(page);
  // Ensure there is at least one project + schedule to report.
  await apiPost(page, '/v1/agents/main/projects', { name: 'brainy-e2e-act-' + Date.now() });
  await page.evaluate(() => navigateTo('welcome'));
  await page.waitForTimeout(400);

  const res = await askBrainy(page, 'Was habe ich bisher in brain-agent gemacht? Nenne ein paar meiner Projekte oder Chats.');
  console.log('[activity] tools=' + JSON.stringify(res.toolCalls) + ' reply=' + JSON.stringify(res.reply.slice(0, 320)));
  assertHealthy(res, 'activity');
  expect(res.toolCalls.includes('helpdesk_user_activity') || ctxHit(res.reply, 'projekt', 'chat', 'aufgabe'),
    'activity: no activity awareness').toBeTruthy();
});
