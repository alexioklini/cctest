/* ─── Persistent settings ──────────────────────────────────────────── */
const { app } = require('electron');
const path = require('path');
const fs = require('fs');

const settingsPath = path.join(app.getPath('userData'), 'settings.json');

function loadSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsPath, 'utf-8'));
  } catch {
    return {};
  }
}

function saveSettings(settings) {
  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2));
}

// Resolve server URL: CLI arg > saved setting > default
function resolveServerUrl() {
  const serverArg = process.argv.find(a => a.startsWith('--server='));
  if (serverArg) {
    return serverArg.split('=')[1].replace(/\/+$/, '');
  }
  const saved = loadSettings();
  if (saved.serverUrl) return saved.serverUrl;
  return 'http://localhost:8420';
}

module.exports = { loadSettings, saveSettings, resolveServerUrl };
