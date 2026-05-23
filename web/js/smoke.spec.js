// smoke.spec.js — gate component 4 (plan §0).
//
// Boots against the running dev server, logs in admin/admin, and exercises the
// core flows the 3 monster files own. The load-bearing assertion is ZERO console
// errors during each flow: a thrown ReferenceError from a global a split lost
// shows here even if ESLint somehow missed it (dynamic dispatch, inline-handler
// typos). All flows are READ-ONLY (open views/modals, type-without-send) so they
// never create chats/projects/data (plan §"Open question" decision).

const { test, expect } = require('@playwright/test');

const BASE = 'http://127.0.0.1:8420';

// Collect console errors + page exceptions for the whole test; assert empty at
// the end of each flow. Filter out benign network noise (favicon, optional
// polling endpoints) that isn't a code regression.
function attachConsoleGuard(page) {
  const errors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const t = msg.text();
      // ignore HTTP-status noise and resource 404s — not JS-global regressions
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
  // Auth overlay may be hidden if a session cookie already exists.
  const userField = page.locator('#auth-username');
  if (await userField.isVisible().catch(() => false)) {
    await userField.fill('admin');
    await page.locator('#auth-password').fill('admin');
    await page.getByRole('button', { name: 'Sign in' }).click();
  }
  // Welcome view is the post-login landing.
  await expect(page.locator('#welcome-view')).toBeVisible({ timeout: 10000 });
}

test('login → welcome view, no console errors', async ({ page }) => {
  const errors = attachConsoleGuard(page);
  await login(page);
  await page.waitForTimeout(500);
  expect(errors, errors.join('\n')).toEqual([]);
});

test('chat composer — type without send (send button activates)', async ({ page }) => {
  const errors = attachConsoleGuard(page);
  await login(page);
  // newChat() shows welcome with composer; the composer is cloned from template.
  const input = page.locator('.composer-input').first();
  await expect(input).toBeVisible({ timeout: 8000 });
  await input.fill('smoke test draft — not sent');
  // updateSendButton() should enable the send button (data-id="send-btn").
  const send = page.locator('[data-id="send-btn"]').first();
  await expect(send).toBeEnabled({ timeout: 4000 });
  // Do NOT click — read-only smoke. Clear the draft.
  await input.fill('');
  await page.waitForTimeout(300);
  expect(errors, errors.join('\n')).toEqual([]);
});

test('General Settings modal opens + a fetch-triggering tab renders', async ({ page }) => {
  const errors = attachConsoleGuard(page);
  await login(page);
  await page.evaluate(() => openGeneralSettings());
  await expect(page.locator('.modal-overlay .modal-title')).toHaveText('General Settings', { timeout: 8000 });
  // Switch to Models tab — triggers a /v1/... fetch in switchGeneralTab.
  await page.locator('.modal-tab', { hasText: 'Models' }).first().click();
  await page.waitForTimeout(1200);
  // Switch to Providers too (another fetch path).
  await page.locator('.modal-tab', { hasText: 'Providers' }).first().click();
  await page.waitForTimeout(1000);
  // Close the modal (read-only — no save).
  await page.locator('.modal-overlay .modal-close').first().click();
  await page.waitForTimeout(300);
  expect(errors, errors.join('\n')).toEqual([]);
});

test('right panel toggles', async ({ page }) => {
  const errors = attachConsoleGuard(page);
  await login(page);
  await page.evaluate(() => toggleRightPanel());
  await page.waitForTimeout(400);
  await page.evaluate(() => toggleRightPanel());
  await page.waitForTimeout(400);
  expect(errors, errors.join('\n')).toEqual([]);
});

test('navigate to projects + chats lists', async ({ page }) => {
  const errors = attachConsoleGuard(page);
  await login(page);
  await page.evaluate(() => navigateTo('projects'));
  await expect(page.locator('#projects-view')).toBeVisible({ timeout: 8000 });
  await page.waitForTimeout(600);
  await page.evaluate(() => navigateTo('chats'));
  await expect(page.locator('#chats-view')).toBeVisible({ timeout: 8000 });
  await page.waitForTimeout(600);
  expect(errors, errors.join('\n')).toEqual([]);
});
