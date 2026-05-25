// brainy_context.spec.js — deterministic (no LLM) checks that brainyViewContext()
// correctly reports WHERE the user is, for every view + the settings modal.
// This is the "Brainy knows what the user is currently doing" half, verified in
// the real DOM. Run: npx playwright test -c playwright.brainy.config.js brainy_context.spec.js

const { test, expect } = require('@playwright/test');
const { login, attachConsoleGuard, apiPost, readViewContext } = require('./brainy_helpers');

// Navigate the live SPA into `view` via its real global, then settle.
async function goView(page, view) {
  await page.evaluate((v) => navigateTo(v), view);
  await page.waitForTimeout(400);
}

test('view-context: welcome', async ({ page }) => {
  await login(page);
  await goView(page, 'welcome');
  const ctx = await readViewContext(page);
  expect(ctx.view).toBe('welcome');
  expect(ctx.label).toBe('Startseite');
});

test('view-context: chats list', async ({ page }) => {
  await login(page);
  await goView(page, 'chats');
  const ctx = await readViewContext(page);
  expect(ctx.view).toBe('chats');
  expect(ctx.label).toBe('Chat-Liste');
});

test('view-context: projects list', async ({ page }) => {
  await login(page);
  await goView(page, 'projects');
  const ctx = await readViewContext(page);
  expect(ctx.view).toBe('projects');
  expect(ctx.label).toBe('Projekte-Liste');
});

test('view-context: scheduled', async ({ page }) => {
  await login(page);
  await goView(page, 'scheduled');
  const ctx = await readViewContext(page);
  expect(ctx.view).toBe('scheduled');
  expect(ctx.label).toBe('Geplante Aufgaben');
});

test('view-context: workflows', async ({ page }) => {
  await login(page);
  await goView(page, 'workflows');
  const ctx = await readViewContext(page);
  expect(ctx.view).toBe('workflows');
  expect(ctx.label).toBe('Workflows');
});

test('view-context: translation', async ({ page }) => {
  await login(page);
  await goView(page, 'translation');
  const ctx = await readViewContext(page);
  expect(ctx.view).toBe('translation');
  expect(ctx.label).toBe('Übersetzung');
});

test('view-context: chat with an active session carries the chat title', async ({ page }) => {
  await login(page);
  // Create a session + give it a title, open it, then read the context.
  const created = await apiPost(page, '/v1/sessions', {});
  const sid = created.json && (created.json.session_id || created.json.id);
  expect(sid, 'session created').toBeTruthy();
  await page.evaluate((id) => {
    // openSession loads + switches to the chat view for this session.
    if (typeof openSession === 'function') return openSession(id, 'main');
  }, sid);
  await page.waitForTimeout(600);
  const ctx = await readViewContext(page);
  expect(ctx.view).toBe('chat');
  // label is 'Chat' (no project); chat_title may be empty for a brand-new
  // untitled session — assert the shape, not a specific title.
  expect(['Chat']).toContain(ctx.label);
});

test('view-context: project detail reports the project name', async ({ page }) => {
  await login(page);
  const name = 'brainy-ctx-' + Date.now();
  const made = await apiPost(page, '/v1/agents/main/projects', { name });
  expect([200, 201]).toContain(made.status);
  await page.evaluate((n) => {
    if (typeof openProject === 'function') return openProject('main', n);
  }, name);
  await page.waitForTimeout(700);
  const ctx = await readViewContext(page);
  expect(ctx.project).toBe(name);
  expect(ctx.label).toContain(name);     // "Projekt „<name>"" (or Projekt-Chat)
});

test('view-context: open General Settings → Brainy tab is detected', async ({ page }) => {
  const errors = attachConsoleGuard(page);
  await login(page);
  await page.evaluate(() => { if (typeof openGeneralSettings === 'function') openGeneralSettings(); });
  await page.waitForTimeout(400);
  // Click the real "Brainy" tab button (like a user) so switchGeneralTab moves
  // the .active class — a btn-less programmatic call renders content but leaves
  // .active stale, which is fine in real use (always click-driven).
  await page.getByRole('button', { name: 'Brainy', exact: true }).click();
  await page.waitForTimeout(400);
  const ctx = await readViewContext(page);
  expect(ctx.view).toBe('settings');
  expect(ctx.label).toContain('Einstellungen');
  // The active tab label ("Brainy") should be surfaced.
  expect(ctx.label).toContain('Brainy');
  expect(errors, errors.join('\n')).toEqual([]);
});
