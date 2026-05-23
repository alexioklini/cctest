// Playwright config for the web/js smoke gate. Single chromium project, headless,
// only the smoke spec, fast fail. The dev server is assumed already running at
// 127.0.0.1:8420 (js_gate.sh checks before invoking).
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: 'smoke.spec.js',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30000,
  reporter: [['line']],
  use: {
    headless: true,
    actionTimeout: 8000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
