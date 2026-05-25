// buddy.js — cosmetic comic companion that lives inline in the composer footer.
// Inspired by github.com/1270011/claude-buddy, reduced to the part that fits
// Brain's web UI: a little flat-comic animal, picked per-user, that reacts to
// what the agent is doing — typing, thinking, running a tool, writing, warming
// up, compacting — each with its own motion and whimsical status word.
//
// Purely client-side. The only server state is the `buddy_species` preference
// (see PREFERENCE_DEFAULTS / BUDDY_SPECIES in server_lib/auth.py — keep the id
// list in sync). Selection: explicit species from prefs, "off" to disable, or
// "" (the default) → a species deterministically derived from the user id so
// each user reliably gets "their" buddy.
//
// Each species is a hand-drawn inline-SVG cartoon. Line-work uses currentColor
// (the element's `color`, set to the buddy's --buddy-color), so each buddy
// recolours cleanly. Per-phase expression (blink, wide eyes, busy mouth, zzz)
// is done by toggling a `phase-*` CSS class on the buddy element (see main.css)
// — no per-frame redraw. viewBox is a shared 0 0 40 40 so they line up.
//
// All buddies share a base template (round head, two eyes, a mouth) and vary
// by ears/snout + colour. `face()` draws the common eyes+mouth+blink+zzz layers
// so each species body only adds its silhouette. Eyes are <circle>s the CSS
// targets by class for the phase expressions.

// Common face layer: pupils (blink/wide via CSS), mouth, a 'zzz' for idle, and
// a 'spark' the busy phases show. cx/cy let species nudge the face if needed.
function buddyFace(opts = {}) {
  const ex = opts.eyeDx ?? 6;     // half-distance between eyes
  const cx = opts.cx ?? 20, cy = opts.cy ?? 19;
  return (
    `<g class="b-face">` +
    // eyes: outer is the open eye, the .b-lid line is shown on blink by CSS
    `<circle class="b-eye" cx="${cx - ex}" cy="${cy}" r="2.4"/>` +
    `<circle class="b-eye" cx="${cx + ex}" cy="${cy}" r="2.4"/>` +
    `<line class="b-lid" x1="${cx - ex - 2.6}" y1="${cy}" x2="${cx - ex + 2.6}" y2="${cy}"/>` +
    `<line class="b-lid" x1="${cx + ex - 2.6}" y1="${cy}" x2="${cx + ex + 2.6}" y2="${cy}"/>` +
    // mouth: a small smile; .b-mouth-o (busy) swaps to an 'o' via CSS opacity
    `<path class="b-mouth" d="M ${cx - 3} ${cy + 5.5} Q ${cx} ${cy + 8} ${cx + 3} ${cy + 5.5}" fill="none"/>` +
    `<circle class="b-mouth-o" cx="${cx}" cy="${cy + 6.5}" r="1.8"/>` +
    // idle 'zzz' (top-right), shown only in idle by CSS
    `<text class="b-zzz" x="33" y="9" font-size="6" font-family="var(--font-mono)">z</text>` +
    // busy spark (top-right), shown by CSS in tool/thinking/etc.
    `<g class="b-spark"><path d="M33 6 l1.4 3 3 1.4 -3 1.4 -1.4 3 -1.4 -3 -3 -1.4 3 -1.4 z"/></g>` +
    `</g>`
  );
}

// Each `body` is the species silhouette; buddyFace() overlays the shared face.
// stroke-width/stroke/fill are set on the host <svg> in CSS (currentColor).
const BUDDY_SPECIES = {
  cat: {
    label: 'Katze', color: '#9ca3af',
    face: { cy: 15, eyeDx: 5 },
    body:
      `<path d="M11 8 L13 1 L19 6 M29 8 L27 1 L21 6"/>` +             // small pointed ears
      `<circle cx="20" cy="14" r="10"/>` +                            // round head
      `<path d="M13 16 h-6 M13 18 h-6 M27 16 h6 M27 18 h6"/>` +       // whiskers
      `<path d="M11 23 Q9 32 13 37 Q20 40 27 37 Q31 32 29 23"/>` +    // sitting body
      `<path d="M29 30 Q38 30 37 22 Q36 18 32 19"/>`,                 // curled tail
  },
  fox: {
    label: 'Fuchs', color: '#ea580c',
    body:
      `<path d="M8 14 L5 4 L15 11 M32 14 L35 4 L25 11"/>` +           // big ears
      `<path d="M7 17 Q20 9 33 17 Q34 28 20 35 Q6 28 7 17 Z"/>`,      // angular head
  },
  dog: {
    label: 'Hund', color: '#a16207',
    body:
      `<path d="M9 13 Q3 14 5 24 Q9 24 11 19 M31 13 Q37 14 35 24 Q31 24 29 19"/>` + // floppy ears
      `<circle cx="20" cy="22" r="12"/>` +                            // head
      `<circle class="b-nose" cx="20" cy="25" r="1.6"/>`,             // nose
  },
  bear: {
    label: 'Bär', color: '#7c5e3c',
    body:
      `<circle cx="9" cy="11" r="4"/><circle cx="31" cy="11" r="4"/>` + // round ears
      `<circle cx="20" cy="22" r="13"/>` +                             // head
      `<ellipse cx="20" cy="26" rx="5" ry="3.5"/>`,                    // snout
  },
  panda: {
    label: 'Panda', color: '#475569',
    body:
      `<circle cx="9" cy="11" r="4" fill="currentColor"/>` +           // filled ears
      `<circle cx="31" cy="11" r="4" fill="currentColor"/>` +
      `<circle cx="20" cy="22" r="13"/>` +
      `<ellipse class="b-patch" cx="14" cy="20" rx="3.2" ry="4"/>` +   // eye patches
      `<ellipse class="b-patch" cx="26" cy="20" rx="3.2" ry="4"/>`,
  },
  frog: {
    label: 'Frosch', color: '#16a34a',
    body:
      `<circle cx="12" cy="11" r="5"/><circle cx="28" cy="11" r="5"/>` + // eye bulges
      `<path d="M6 18 Q20 12 34 18 Q34 32 20 33 Q6 32 6 18 Z"/>`,        // wide head
  },
  owl: {
    label: 'Eule', color: '#7c3aed',
    face: { cx: 20, cy: 16, eyeDx: 6 },
    body:
      `<path d="M9 12 Q7 5 13 7 Q11 9 12 12 M31 12 Q33 5 27 7 Q29 9 28 12"/>` + // feathered ear tufts
      `<path d="M8 15 Q8 6 20 6 Q32 6 32 15 Q33 28 20 34 Q7 28 8 15 Z"/>` +     // body
      `<circle cx="14" cy="16" r="5"/><circle cx="26" cy="16" r="5"/>` +        // big eye rings
      `<path d="M20 19 l-2 3 h4 z" fill="currentColor"/>` +                     // beak
      `<path d="M14 25 q2 2 4 0 q2 2 4 0 q2 2 4 0 M15 29 q2.5 2 5 0 q2.5 2 5 0"/>` + // belly scallops
      `<path d="M4 35 h32 M17 34 l0 2 M23 34 l0 2"/>`,                          // branch + feet
  },
  penguin: {
    label: 'Pinguin', color: '#0369a1',
    body:
      `<path d="M10 13 Q20 4 30 13 Q33 28 20 36 Q7 28 10 13 Z"/>` +    // body
      `<path class="b-belly" d="M14 18 Q20 14 26 18 Q27 28 20 32 Q13 28 14 18 Z"/>` + // belly
      `<path d="M20 24 l-2 2 4 0 z" fill="currentColor"/>`,            // beak
  },
  dragon: {
    label: 'Drache', color: '#16a34a',
    face: { cy: 15, eyeDx: 5 },
    body:
      `<path d="M12 7 Q8 2 12 0 M28 7 Q32 2 28 0"/>` +                 // curved horns
      `<path d="M11 15 Q2 11 1 19 Q5 18 6 21 Q8 19 11 21"/>` +        // left wing
      `<path d="M29 15 Q38 11 39 19 Q35 18 34 21 Q32 19 29 21"/>` +   // right wing
      `<circle cx="20" cy="16" r="11"/>` +                            // head
      `<ellipse cx="20" cy="25" rx="4" ry="2.6"/>` +                  // snout
      `<circle class="b-nose" cx="18.7" cy="24.4" r="0.6"/>` +        // nostrils
      `<circle class="b-nose" cx="21.3" cy="24.4" r="0.6"/>`,
  },
  crab: {
    label: 'Krabbe', color: '#ef4444',
    face: { cy: 11, eyeDx: 6, cx: 20 },
    body:
      `<path d="M8 24 Q8 16 20 16 Q32 16 32 24 Q32 30 20 30 Q8 30 8 24 Z"/>` + // shell
      `<path d="M15 16 L14 13 M25 16 L26 13"/>` +                     // eye stalks
      `<path d="M8 22 Q3 20 3 15 Q3 12 6 12 M3 15 Q1 13 2 11"/>` +    // left claw
      `<path d="M32 22 Q37 20 37 15 Q37 12 34 12 M37 15 Q39 13 38 11"/>` + // right claw
      `<path d="M11 29 l-2 4 M15 30 l-1 4 M25 30 l1 4 M29 29 l2 4"/>`, // legs
  },
};

const BUDDY_IDS = Object.keys(BUDDY_SPECIES);

// Deterministic species pick from a user id — mulberry-ish: a cheap string
// hash mod the species count. Same id always lands on the same species.
function buddyDefaultSpecies(userId) {
  const s = String(userId || 'anon');
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return BUDDY_IDS[(h >>> 0) % BUDDY_IDS.length];
}

// Standalone SVG markup for a species (or the current user's species). Used by
// Brainy (the helpdesk modal) so its avatar IS the user's buddy. Returns '' if
// the species is unknown / buddy is off — caller falls back to an emoji.
function buddySvgMarkup(species) {
  const sp = BUDDY_SPECIES[species || buddyResolveSpecies()];
  if (!sp) return '';
  return `<svg class="b-svg" viewBox="0 0 40 40" fill="none" `
    + `stroke="currentColor" stroke-width="2" stroke-linecap="round" `
    + `stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">`
    + sp.body + buddyFace(sp.face || {})
    + `</svg>`;
}
// The themed accent color of the current user's species (for Brainy's header).
function buddyColor(species) {
  const sp = BUDDY_SPECIES[species || buddyResolveSpecies()];
  return (sp && sp.color) || 'var(--accent-brand)';
}

// Resolve the species the current user should see, or null when disabled.
function buddyResolveSpecies() {
  // `state` is a top-level `const` in state.js — it is NOT a property of
  // `window`, so don't gate on `window.state` (that's always undefined and
  // silently drops us to the deterministic fallback). Reference `state`
  // directly, guarded by typeof for the rare pre-init call.
  const u = (typeof state !== 'undefined' && state.authUser) || {};
  const pref = ((u.preferences || {}).buddy_species || '').toLowerCase();
  if (pref === 'off') return null;
  if (pref && BUDDY_SPECIES[pref]) return pref;
  return buddyDefaultSpecies(u.id || u.username);
}

// Opacity targets, set as the CSS var --buddy-op (smoothly transitioned in CSS).
// `op` drives the STATUS-CLOUD opacity (the FAB itself stays fully opaque):
// deep-idle (no turn) → busy (a reply is generating). Keep in sync with main.css.
const BUDDY_OP_DEEP_IDLE = 0;        // status cloud hidden when idle
const BUDDY_IDLE_FADE_MS  = 1200;    // delay before the status cloud fades out
const BUDDY_TYPING_HOLD_MS = 2500;   // 'typing' reverts to idle after this much quiet

// Per-phase config. `op` is the status-cloud opacity target. `motion` is a CSS
// class toggled for a phase-specific wiggle on the buddy SVG. The phase name
// also becomes a `phase-<name>` class on the FAB, which main.css uses to drive
// the facial expression (blink, busy 'o' mouth, busy spark) — no JS frame loop.
// `words` is the rotating whimsical status pool (Claude-Code-CLI flavour); empty
// means no word (idle stays quiet).
const BUDDY_PHASES = {
  idle:       { op: BUDDY_OP_DEEP_IDLE, motion: '',            words: [] },
  typing:     { op: 0.9,                motion: 'buddy-perk',  words: ['Hört zu', 'Schaut zu', 'Ganz Ohr', 'Nur zu', 'Mhm'] },
  thinking:   { op: 0.9,                motion: 'buddy-bob',   words: ['Grübelt', 'Sinniert', 'Tüftelt', 'Überlegt', 'Hmm'] },
  tool:       { op: 0.9,                motion: 'buddy-shake', words: ['Holt', 'Werkelt', 'Gräbt', 'Stöbert'] },
  writing:    { op: 0.9,                motion: 'buddy-bob',   words: ['Verfasst', 'Schreibt', 'Entwirft', 'Formuliert'] },
  warmup:     { op: 0.9,                motion: 'buddy-stretch', words: ['Wärmt auf', 'Lockert', 'Startet'] },
  compacting: { op: 0.9,                motion: 'buddy-squish', words: ['Räumt auf', 'Faltet', 'Verdichtet'] },
};
const BUDDY_PHASE_CLASSES = Object.keys(BUDDY_PHASES).map(p => 'phase-' + p);
const BUDDY_MOTION_CLASSES = ['buddy-perk','buddy-bob','buddy-shake','buddy-stretch','buddy-squish'];

// The companion lives IN the Brainy bubble (#brainy-bubble, bottom-right, every
// view), with its phase word shown in the status cloud beside it (#brainy-status).
// Born once on app load. It tracks a single `phase` (idle / thinking / tool /
// writing / warmup / compacting); each phase has its own facial expression,
// motion wiggle and whimsical status words. Phase precedence is handled by the
// callers: the chat stream callbacks set thinking / tool / writing / warmup /
// compacting; a turn ending returns to idle.
class FloatingBuddy {
  constructor() {
    this.species = null;
    this.phase = 'idle';
    this.idleTimer = null;     // deep-idle opacity fade
    this.wordTimer = null;     // status-word rotation
    this.typingTimer = null;   // typing → idle revert
  }
  // The buddy now LIVES IN the Brainy floating bubble (bottom-right, every
  // view). `_els()` is the bubble host; `_bubbles()` is the status-word cloud
  // beside it. Both are single elements (no per-view clones anymore).
  _els()    { const el = document.getElementById('brainy-bubble'); return el ? [el] : []; }
  _bubbles(){ const el = document.getElementById('brainy-status'); return el ? [el] : []; }
  // Render the species' comic SVG into the bubble. Body silhouette + the shared
  // face layer; line-work inherits currentColor so --buddy-color tints it.
  // Animation (blink, expression, bob) is all CSS, driven by the phase-* class.
  _draw(species) {
    const sp = BUDDY_SPECIES[species];
    if (!sp) return;
    const svg =
      `<svg class="b-svg" viewBox="0 0 40 40" fill="none" ` +
      `stroke="currentColor" stroke-width="2" stroke-linecap="round" ` +
      `stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg">` +
      sp.body + buddyFace(sp.face || {}) +
      `</svg>`;
    this._els().forEach(el => {
      el.innerHTML = `<span class="brainy-fab-buddy">${svg}</span>`;
    });
  }
  // Toggle the phase-<name> class on each buddy host so CSS shows the right
  // expression for the current phase.
  _setPhaseClass(phase) {
    const cls = 'phase-' + (BUDDY_PHASES[phase] ? phase : 'idle');
    this._els().forEach(el => {
      el.classList.remove(...BUDDY_PHASE_CLASSES);
      el.classList.add(cls);
    });
  }
  // The FAB itself stays fully opaque (it's an action button) — only the status
  // cloud fades. Kept as _setOp so the phase machine is unchanged.
  _setOp(v)   {
    this._bubbles().forEach(b => b.style.setProperty('--buddy-op', String(v)));
  }
  // Per-species accent color, applied to the bubble + its status text so the
  // whole companion reads as one themed unit. Falls back to the brand accent.
  _setColor(c) {
    const col = c || 'var(--accent-brand)';
    this._els().forEach(el => el.style.setProperty('--buddy-color', col));
    this._bubbles().forEach(b => b.style.setProperty('--buddy-color', col));
  }
  // The FAB is always shown (it's the Brainy entry point); when the buddy is
  // "off" we just fall back to the 🧠 emoji symbol (handled in refresh()).
  _show(on)   {}
  _setMotion(cls) {
    this._els().forEach(el => {
      el.classList.remove(...BUDDY_MOTION_CLASSES);
      if (cls) el.classList.add(cls);
    });
  }
  _setBubble(text) {
    this._bubbles().forEach(b => {
      b.textContent = text || '';
      b.style.display = text ? '' : 'none';
    });
  }
  _armIdleFade() {
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => {
      if (this.phase === 'idle') this._setOp(BUDDY_OP_DEEP_IDLE);
    }, BUDDY_IDLE_FADE_MS);
  }
  // Start (or re-roll) the bubble word rotation for the current phase.
  _startWords(words) {
    if (this.wordTimer) { clearInterval(this.wordTimer); this.wordTimer = null; }
    if (!words || !words.length) { this._setBubble(''); return; }
    const roll = () => this._setBubble(words[Math.floor(Math.random() * words.length)] + '…');
    roll();
    this.wordTimer = setInterval(roll, 2600);
  }

  // The single entry point. Switches the buddy to `phase`. Unknown phase falls
  // back to idle. Lazily resolves the species so a turn that fires before
  // refresh() still animates (and so a null species never silently kills it).
  setPhase(phase) {
    if (!this.species) {
      this.species = buddyResolveSpecies();
      if (this.species) this._setColor(BUDDY_SPECIES[this.species].color);
    }
    if (!this.species) return;               // buddy genuinely off → stay quiet
    if (!BUDDY_PHASES[phase]) phase = 'idle';
    const cfg = BUDDY_PHASES[phase];
    const samePhase = (phase === this.phase);
    this.phase = phase;
    this._setOp(cfg.op);
    this._setMotion(cfg.motion);
    this._setPhaseClass(phase);
    // Re-roll words even on a same-phase call (a fresh turn re-entering
    // 'thinking' should still surface a word immediately).
    if (!samePhase || (this.wordTimer == null && cfg.words.length)) {
      this._startWords(cfg.words);
    }
    if (phase === 'idle') this._armIdleFade();
  }

  // (Re)resolve the species from prefs. The Brainy bubble owns the symbol
  // (brainyRefreshBubble renders the species SVG or 🧠); here we just track the
  // species so the phase machine knows whether to animate + which color to use.
  refresh() {
    const species = buddyResolveSpecies();
    // brainyRefreshBubble() paints the FAB symbol (buddy SVG or 🧠 fallback).
    if (typeof brainyRefreshBubble === 'function') brainyRefreshBubble();
    if (!species) {                          // "off" → no animation, 🧠 fallback
      [this.idleTimer, this.wordTimer, this.typingTimer]
        .forEach(t => t && clearTimeout(t));
      this.idleTimer = this.wordTimer = this.typingTimer = null;
      this.species = null;
      this._setBubble('');
      return;
    }
    this.species = species;
    this.phase = 'idle';
    this._setMotion('');
    this._setBubble('');
    this._setColor(BUDDY_SPECIES[species].color);
    this._setPhaseClass('idle');
  }

  // The user is typing in the composer: show the attentive 'typing' phase, but
  // only when no turn is running (a live turn's phase takes precedence). Reverts
  // to idle after a short quiet period.
  poke() {
    const busy = !['idle', 'typing'].includes(this.phase);
    if (busy) return;
    this.setPhase('typing');
    if (this.typingTimer) clearTimeout(this.typingTimer);
    this.typingTimer = setTimeout(() => {
      if (this.phase === 'typing') this.setPhase('idle');
    }, BUDDY_TYPING_HOLD_MS);
  }

  turnEnd() { this.setPhase('idle'); }
}

let _buddy = null;
function buddy() {
  if (!_buddy) _buddy = new FloatingBuddy();
  return _buddy;
}

// Public hooks (the stream callbacks in chat_send.js drive these — unchanged
// API; they now animate the Brainy bubble instead of the composer companion).
function buddyInit()       { buddy()?.refresh(); }            // app load + after settings save
function buddyPhase(phase) { buddy()?.setPhase(phase); }      // stream callbacks drive this
function buddyTurnStart()  { buddy()?.setPhase('thinking'); } // first beat of a turn
function buddyTurnEnd()    { buddy()?.turnEnd(); }
function buddyPoke()       { buddy()?.poke(); }               // composer typing
