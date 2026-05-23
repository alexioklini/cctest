// ESLint 9 flat config for web/js/ — these are GLOBAL SCRIPTS, not ES modules.
//
// Purpose (gate component 1): catch "a moved function lost its global" during the
// Tier-F refactor. Globals come from .globals.json, which is AUTO-GENERATED from
// the defining source files by gen-globals.sh (run as step 1 of js_gate.sh). A
// function called but defined nowhere is absent from that list -> no-undef fires.
//
// ESLint 8 is EOL; this is the flat-config (9.x) form. sourceType "script" so
// top-level `const X` is a global, matching how the browser loads these files.

const fs = require('fs');
const path = require('path');

const globalsFile = path.join(__dirname, '.globals.json');
const projectGlobals = JSON.parse(fs.readFileSync(globalsFile, 'utf8')).globals;

module.exports = [
  // Global ignore — the gate's own Node tooling is not browser source.
  { ignores: ['eslint.config.js', 'playwright.config.js', 'smoke.spec.js', 'node_modules/**'] },
  {
    files: ['**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'script',
      globals: projectGlobals,
    },
    rules: {
      'no-undef': 'error',      // the load-bearing rule: missing/typo'd global
      // builtinGlobals:false so a name's own source definition isn't flagged as
      // "redeclaring a configured global" — but TWO source definitions of the
      // same name (a split that copied instead of moved) still fire. That is the
      // duplicate-definition signal we want.
      'no-redeclare': ['error', { builtinGlobals: false }],
      'no-unused-vars': 'warn', // too noisy as error initially (plan §0)
    },
  },
];
