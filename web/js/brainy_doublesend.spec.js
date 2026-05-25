// Regression: two questions in a row through the REAL Brainy UI.
//
// Reproduces the reported bug — after the first question the send button
// stayed disabled (brainyState.streaming stuck true) because the SSE reader
// waited for stream EOF instead of breaking on the `done` event, and the
// kept-alive socket never closed. This drives the actual bubble + button +
// /v1/helpdesk SSE, so it fails if the client hangs the turn.
//
// Run: ./brainy_test.sh  (or npx playwright test -c playwright.brainy.config.js brainy_doublesend.spec.js)

const { test, expect } = require('@playwright/test');
const { login, attachConsoleGuard } = require('./brainy_helpers');

test('two Brainy questions in a row — button re-enables, second send works', async ({ page }) => {
  const errors = attachConsoleGuard(page);
  await login(page);

  // Open Brainy via its global (the bubble is present in every view).
  await page.evaluate(() => brainyOpen());
  await page.waitForSelector('#brainy-input', { state: 'visible', timeout: 5000 });

  const input = page.locator('#brainy-input');
  const sendBtn = page.locator('#brainy-send-btn');

  // ── Question 1 ──
  await input.fill('Sag bitte nur kurz Hallo.');
  await sendBtn.click();

  // While streaming the button is disabled and brainyState.streaming is true.
  await expect.poll(() => page.evaluate(() => brainyState.streaming), { timeout: 5000 }).toBe(true);

  // The turn must FINISH: streaming clears AND the button re-enables. This is
  // the exact thing that hung before. Generous timeout — a local model turn.
  await expect.poll(() => page.evaluate(() => brainyState.streaming),
    { timeout: 180000, intervals: [1000] }).toBe(false);
  await expect(sendBtn).toBeEnabled({ timeout: 5000 });

  // First exchange rendered an answer (not the "keine Antwort" error).
  const firstAnswer = await page.locator('.brainy-exchange-live .brainy-bot .brainy-bubble-body').last().innerText();
  expect(firstAnswer.length).toBeGreaterThan(0);
  expect(firstAnswer).not.toContain('keine Antwort');

  // ── Question 2 (the part that was impossible) ──
  await input.fill('Und was kann ich hier alles machen?');
  await expect(sendBtn).toBeEnabled();
  await sendBtn.click();

  await expect.poll(() => page.evaluate(() => brainyState.streaming), { timeout: 5000 }).toBe(true);
  await expect.poll(() => page.evaluate(() => brainyState.streaming),
    { timeout: 180000, intervals: [1000] }).toBe(false);
  await expect(sendBtn).toBeEnabled({ timeout: 5000 });

  // Two live exchanges now exist, both with non-empty bot answers.
  const liveCount = await page.locator('.brainy-exchange-live').count();
  expect(liveCount).toBe(2);

  expect(errors, 'no console errors during the two-question flow').toEqual([]);
});
