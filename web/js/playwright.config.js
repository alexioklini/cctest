// Playwright config for the web/js smoke gate. Single chromium project, headless,
// only the smoke spec, fast fail. The dev server is assumed already running at
// 127.0.0.1:8420 (js_gate.sh checks before invoking).
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: 'smoke.spec.js',
  fullyParallel: false,
  workers: 1,
  // One retry absorbs a transient asset-load reset right after a server
  // (re)start (the dev HTTP server can drop a few of the ~51 parallel script
  // requests under burst). A REAL regression fails both attempts, so this
  // removes false-positive flakiness without masking breaks. (9.205.5)
  retries: 1,
  // Generous so the login() readiness wait (state.modelsConfigReady, which can
  // take a few seconds while the server warms up its model config) has room.
  timeout: 45000,
  reporter: [['line']],
  use: {
    headless: true,
    actionTimeout: 20000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
