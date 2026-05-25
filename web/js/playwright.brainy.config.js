// Playwright config for the Brainy E2E + view-context specs. SEPARATE from the
// smoke gate (playwright.config.js) — these are slower (real LLM turns) and are
// run on demand:  npx playwright test -c playwright.brainy.config.js
// Dev server must be running on 127.0.0.1:8420 with a resolvable Brainy model.
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: /brainy_.*\.spec\.js$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // Real multi-round LLM turns on a local model are slow; a single Brainy turn
  // (skill load + session/activity tool round + answer) can run minutes. Give
  // each test plenty of headroom so the test times the MODEL, not the harness.
  timeout: 360000,
  reporter: [['line']],
  use: {
    headless: true,
    actionTimeout: 15000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
