// buddy.js — cosmetic ASCII companion that lives inline in the composer footer.
// Inspired by github.com/1270011/claude-buddy, reduced to the part that fits
// Brain's web UI: a little animated pet, picked per-user, that reacts to what
// the agent is doing — typing, thinking, running a tool, writing, warming up,
// compacting — each with its own pose, motion and whimsical status bubble.
//
// Purely client-side. The only server state is the `buddy_species` preference
// (see PREFERENCE_DEFAULTS / BUDDY_SPECIES in server_lib/auth.py — keep the id
// list in sync). Selection: explicit species from prefs, "off" to disable, or
// "" (the default) → a species deterministically derived from the user id so
// each user reliably gets "their" buddy.
//
// Each species has three idle frames + one blink + a `poses` map (one pose per
// active phase). Every frame of a species is the same line count and width
// (rendered in a monospace <pre>, so columns must line up).

const BUDDY_SPECIES = {
  duck: {
    label: 'Duck',
    frames: [
      [' __',
       '<(o )___',
       ' ( ._> /',
       '  `---\''],
      [' __',
       '<(o )___',
       ' ( ._> /',
       '  `--\'`'],
      [' __',
       '<(o )___',
       ' ( ._> /',
       '  `-\'`-'],
    ],
    blink:
      [' __',
       '<(- )___',
       ' ( ._> /',
       '  `---\''],
    poses: {
      // ASCII-only, every line same width as idle. Eyes/mouth/limb swaps only.
      typing:    [[' __', '<(O )___', ' ( ._> /', '  `---\''], [' __', '<(O )___', ' ( ._> /', '  `-\'`-']],
      thinking:  [[' __', '<(o )___', ' ( -_> /', '  `---\''], [' __', '<(. )___', ' ( ._> /', '  `---\'']],
      tool:      [[' __', '<(o )=#=', ' ( ._> /', '  `---\''], [' __', '<(o )=*=', ' ( ._> /', '  `---\'']],
      writing:   [[' __', '<(o )___', ' ( ._>~/', '  `---\''], [' __', '<(o )___', ' ( ._>-/', '  `---\'']],
      warmup:    [[' __', '<(o )___', ' ( ._> /', '  `--\'`'], [' __', '<(o )___', ' ( ._> /', '  `\'--`']],
      compacting:[[' __', '<(o )_><', ' ( ._> /', '  `---\''], [' __', '<(o )><_', ' ( ._> /', '  `---\'']],
    },
  },
  cat: {
    label: 'Cat',
    frames: [
      ['  /\\_/\\',
       ' ( o.o )',
       '  > ^ < '],
      ['  /\\_/\\',
       ' ( o.o )',
       '  >_^_< '],
      ['  /\\_/\\',
       ' ( o.o )',
       '  > ^ <~'],
    ],
    blink:
      ['  /\\_/\\',
       ' ( -.- )',
       '  > ^ < '],
    poses: {
      typing:    [['  /\\_/\\', ' ( O.O )', '  > ^ < '], ['  /\\_/\\', ' ( O.O )', '  >_^_< ']],
      thinking:  [['  /\\_/\\', ' ( o.- )', '  > ^ < '], ['  /\\_/\\', ' ( -.o )', '  > ^ < ']],
      tool:      [['  /\\_/\\', ' ( o.o )', '  >=#=< '], ['  /\\_/\\', ' ( o.o )', '  >=*=< ']],
      writing:   [['  /\\_/\\', ' ( o.o )', '  > ^ <~'], ['  /\\_/\\', ' ( o.o )', '  > ^ <-']],
      warmup:    [['  /\\_/\\', ' ( o.o )', ' ~> ^ <~'], ['  /\\_/\\', ' ( o.o )', '  > ^ < ']],
      compacting:[['  /\\_/\\', ' ( o.o )', '  >< ^ <'], ['  /\\_/\\', ' ( o.o )', '  > ^ ><']],
    },
  },
  dragon: {
    label: 'Dragon',
    frames: [
      ['   __/\\',
       '  / o \\__',
       ' <_/\\__> )',
       '    """"'],
      ['   __/\\',
       '  / o \\__',
       ' <_/\\__> )',
       '    \'""\''],
      ['   __/\\',
       '  / o \\__',
       ' <_/\\__>~)',
       '    """"'],
    ],
    blink:
      ['   __/\\',
       '  / - \\__',
       ' <_/\\__> )',
       '    """"'],
    poses: {
      typing:    [['   __/\\', '  / O \\__', ' <_/\\__> )', '    """"'], ['   __/\\', '  / O \\__', ' <_/\\__> )', '    \'""\'']],
      thinking:  [['   __/\\', '  / - \\__', ' <_/\\__> )', '    """"'], ['   __/\\', '  / . \\__', ' <_/\\__> )', '    """"']],
      tool:      [['   __/\\', '  / o \\__', ' <_/\\__>=#', '    """"'], ['   __/\\', '  / o \\__', ' <_/\\__>=*', '    """"']],
      writing:   [['   __/\\', '  / o \\__', ' <_/\\__>~)', '    """"'], ['   __/\\', '  / o \\__', ' <_/\\__>-)', '    """"']],
      warmup:    [['   __/\\', '  / o \\__', ' <_/\\__> )', '   ~""""~'], ['   __/\\', '  / o \\__', ' <_/\\__> )', '    """"']],
      compacting:[['   __/\\', '  / o \\__', ' <_/\\_><)', '    """"'], ['   __/\\', '  / o \\__', ' <_/><__)', '    """"']],
    },
  },
  octopus: {
    label: 'Octopus',
    frames: [
      ['  ___',
       ' /o o\\',
       ' \\ - /',
       ' //|\\\\'],
      ['  ___',
       ' /o o\\',
       ' \\ - /',
       ' \\\\|//'],
      ['  ___',
       ' /o o\\',
       ' \\ - /',
       ' /\\|/\\'],
    ],
    blink:
      ['  ___',
       ' /- -\\',
       ' \\ - /',
       ' //|\\\\'],
    poses: {
      typing:    [['  ___', ' /O O\\', ' \\ - /', ' //|\\\\'], ['  ___', ' /O O\\', ' \\ - /', ' \\\\|//']],
      thinking:  [['  ___', ' /o -\\', ' \\ - /', ' //|\\\\'], ['  ___', ' /- o\\', ' \\ - /', ' //|\\\\']],
      tool:      [['  ___', ' /o o\\', ' \\ - /', ' /#|#\\'], ['  ___', ' /o o\\', ' \\ - /', ' \\*|*/']],
      writing:   [['  ___', ' /o o\\', ' \\ - /', ' //|~\\'], ['  ___', ' /o o\\', ' \\ - /', ' /~|\\\\']],
      warmup:    [['  ___', ' /o o\\', ' \\ - /', '\\\\\\|///'], ['  ___', ' /o o\\', ' \\ - /', ' //|\\\\']],
      compacting:[['  ___', ' /o o\\', ' \\ - /', ' >||< '], ['  ___', ' /o o\\', ' \\ - /', ' <||> ']],
    },
  },
  penguin: {
    label: 'Penguin',
    frames: [
      ['  .--.',
       ' ( o o)',
       ' (  v )',
       '  ^^ ^^'],
      ['  .--.',
       ' ( o o)',
       ' (  v )',
       '  ^^^^ '],
      ['  .--.',
       ' ( o o)',
       ' (  v )',
       ' ^^ ^^ '],
    ],
    blink:
      ['  .--.',
       ' ( - -)',
       ' (  v )',
       '  ^^ ^^'],
    poses: {
      typing:    [['  .--.', ' ( O O)', ' (  v )', '  ^^ ^^'], ['  .--.', ' ( O O)', ' (  v )', '  ^^^^ ']],
      thinking:  [['  .--.', ' ( o -)', ' (  v )', '  ^^ ^^'], ['  .--.', ' ( - o)', ' (  v )', '  ^^ ^^']],
      tool:      [['  .--.', ' ( o o)', ' (# v#)', '  ^^ ^^'], ['  .--.', ' ( o o)', ' (* v*)', '  ^^ ^^']],
      writing:   [['  .--.', ' ( o o)', ' (  v~)', '  ^^ ^^'], ['  .--.', ' ( o o)', ' (  v-)', '  ^^ ^^']],
      warmup:    [['  .--.', ' ( o o)', ' (  v )', ' ^^  ^^'], ['  .--.', ' ( o o)', ' (  v )', '  ^^^^ ']],
      compacting:[['  .--.', ' ( o o)', ' ( >v<)', '  ^^ ^^'], ['  .--.', ' ( o o)', ' (< v>)', '  ^^ ^^']],
    },
  },
  fox: {
    label: 'Fox',
    frames: [
      [' /\\   /\\',
       '(  o.o  )',
       ' >  v  < ',
       '  \\___/ '],
      [' /\\   /\\',
       '(  o.o  )',
       ' >  v  < ',
       '  \\__~/ '],
      [' /\\   /\\',
       '(  o.o  )',
       ' >  v  <~',
       '  \\___/ '],
    ],
    blink:
      [' /\\   /\\',
       '(  -.-  )',
       ' >  v  < ',
       '  \\___/ '],
    poses: {
      typing:    [[' /\\   /\\', '(  O.O  )', ' >  v  < ', '  \\___/ '], [' /\\   /\\', '(  O.O  )', ' >  v  < ', '  \\__~/ ']],
      thinking:  [[' /\\   /\\', '(  o.-  )', ' >  v  < ', '  \\___/ '], [' /\\   /\\', '(  -.o  )', ' >  v  < ', '  \\___/ ']],
      tool:      [[' /\\   /\\', '(  o.o  )', ' >#  #< ', '  \\___/ '], [' /\\   /\\', '(  o.o  )', ' >*  *< ', '  \\___/ ']],
      writing:   [[' /\\   /\\', '(  o.o  )', ' >  v  <~', '  \\___/ '], [' /\\   /\\', '(  o.o  )', ' >  v  <-', '  \\___/ ']],
      warmup:    [['/\\    /\\ ', '(  o.o  )', ' >  v  < ', '  \\___/ '], [' /\\   /\\', '(  o.o  )', ' >  v  < ', '  \\___/ ']],
      compacting:[[' /\\   /\\', '(  o.o  )', ' > >v< < ', '  \\___/ '], [' /\\   /\\', '(  o.o  )', ' >< v >< ', '  \\___/ ']],
    },
  },
  owl: {
    label: 'Owl',
    frames: [
      ['  ,___,',
       '  (O,O)',
       '  /)_)',
       '   " " '],
      ['  ,___,',
       '  (O,O)',
       '  (_(\\',
       '   " " '],
      ['  ,___,',
       '  (O,O)',
       '  /)_)',
       '  " "  '],
    ],
    blink:
      ['  ,___,',
       '  (-,-)',
       '  /)_)',
       '   " " '],
    poses: {
      typing:    [['  ,___,', '  (O,O)', '  /)_)', '   " " '], ['  ,___,', '  (O,O)', '  (_(\\', '   " " ']],
      thinking:  [['  ,___,', '  (o,-)', '  /)_)', '   " " '], ['  ,___,', '  (-,o)', '  /)_)', '   " " ']],
      tool:      [['  ,___,', '  (O,O)', '  /#_#', '   " " '], ['  ,___,', '  (O,O)', '  *_*\\', '   " " ']],
      writing:   [['  ,___,', '  (O,O)', '  /)_)~', '   " " '], ['  ,___,', '  (O,O)', '  /)_)-', '   " " ']],
      warmup:    [['  ,___,', '  (O,O)', ' </)_)>', '   " " '], ['  ,___,', '  (O,O)', '  /)_)', '   " " ']],
      compacting:[['  ,___,', '  (O,O)', '  >)_)<', '   " " '], ['  ,___,', '  (O,O)', '  <)_)>', '   " " ']],
    },
  },
  crab: {
    label: 'Crab',
    frames: [
      [' (\\/)',
       ' (o o)',
       '(  V  )',
       ' /   \\'],
      [' (\\/)',
       ' (o o)',
       '(  V  )',
       ' \\   /'],
      ['(\\/) ',
       ' (o o)',
       '(  V  )',
       ' /   \\'],
    ],
    blink:
      [' (\\/)',
       ' (- -)',
       '(  V  )',
       ' /   \\'],
    poses: {
      typing:    [[' (\\/)', ' (O O)', '(  V  )', ' /   \\'], [' (\\/)', ' (O O)', '(  V  )', ' \\   /']],
      thinking:  [[' (\\/)', ' (o -)', '(  V  )', ' /   \\'], [' (\\/)', ' (- o)', '(  V  )', ' /   \\']],
      tool:      [[' (\\/)', ' (o o)', '<# V #>', ' /   \\'], [' (\\/)', ' (o o)', '<* V *>', ' /   \\']],
      writing:   [[' (\\/)', ' (o o)', '(  V ~)', ' /   \\'], [' (\\/)', ' (o o)', '(  V -)', ' /   \\']],
      warmup:    [['(\\/) ', ' (o o)', '(  V  )', ' /   \\'], [' (\\/)', ' (o o)', '(  V  )', ' /   \\']],
      compacting:[[' (\\/)', ' (o o)', '(> V <)', ' /   \\'], [' (\\/)', ' (o o)', '(< V >)', ' /   \\']],
    },
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
// Three states: deep-idle (no keystrokes ~60s, no turn) → active-idle (recent
// keystroke) → turn (a reply is generating). Keep in sync with main.css.
const BUDDY_OP_DEEP_IDLE = 0.08;
const BUDDY_IDLE_FADE_MS  = 60000;   // no-keystroke delay before fading to deep-idle
const BUDDY_TYPING_HOLD_MS = 2500;   // typing reverts to idle after this much quiet

// Per-phase config. `pose` names the BUDDY_SPECIES.poses key (null = use the
// idle frames + blink). `speed` is ms per frame. `op` is the opacity target.
// `motion` is a CSS class toggled on the elements for a phase-specific wiggle.
// `words` is the rotating whimsical bubble pool (Claude-Code-CLI flavour);
// empty means no bubble (only idle stays quiet). One word is picked at phase
// entry and re-rolled every few seconds while the phase holds. The `typing`
// pool is "buddy watching you compose" flavour, distinct from the busy phases.
const BUDDY_PHASES = {
  idle:       { pose: null,        speed: 700, op: BUDDY_OP_DEEP_IDLE, motion: '',           words: [] },
  typing:     { pose: 'typing',    speed: 380, op: 0.45,               motion: 'buddy-perk',  words: ['Listening', 'Watching', 'Reading along', 'All ears', 'Go on', 'Tell me more', 'Mm-hmm'] },
  thinking:   { pose: 'thinking',  speed: 520, op: 0.9,                motion: 'buddy-bob',   words: ['Pondering', 'Cogitating', 'Ruminating', 'Musing', 'Noodling', 'Mulling'] },
  tool:       { pose: 'tool',      speed: 300, op: 0.9,                motion: 'buddy-shake', words: ['Rummaging', 'Fetching', 'Tinkering', 'Digging', 'Foraging', 'Wrangling'] },
  writing:    { pose: 'writing',   speed: 340, op: 0.9,                motion: 'buddy-bob',   words: ['Scribbling', 'Composing', 'Penning', 'Drafting', 'Inkling'] },
  warmup:     { pose: 'warmup',    speed: 600, op: 0.9,                motion: 'buddy-stretch', words: ['Stretching', 'Warming up', 'Limbering', 'Booting up'] },
  compacting: { pose: 'compacting',speed: 420, op: 0.9,                motion: 'buddy-squish', words: ['Tidying', 'Squishing', 'Condensing', 'Folding', 'Decluttering'] },
};
const BUDDY_MOTION_CLASSES = ['buddy-perk','buddy-bob','buddy-shake','buddy-stretch','buddy-squish'];

// The companion lives inline in the composer footer. The composer template is
// cloned into three views, so there are up to three `.composer-buddy` elements
// (each paired with a `.composer-buddy-bubble`) — we drive them all in lockstep.
// Born once on app load, it never stops. It tracks a single `phase` (idle /
// typing / thinking / tool / writing / warmup / compacting); each phase has its
// own pose, animation speed, motion style, opacity and whimsical bubble words.
// Phase precedence is handled by the callers: stream callbacks set thinking /
// tool / writing / warmup / compacting; composer input sets typing; a turn
// ending or the typing hold expiring returns to idle.
class FloatingBuddy {
  constructor() {
    this.species = null;
    this.phase = 'idle';
    this.frame = 0;
    this.frameTimer = null;
    this.idleTimer = null;     // deep-idle opacity fade
    this.wordTimer = null;     // bubble word rotation
    this.typingTimer = null;   // typing → idle revert
  }
  _els()    { return document.querySelectorAll('.composer-buddy'); }
  _bubbles(){ return document.querySelectorAll('.composer-buddy-bubble'); }
  _draw(lines) {
    const txt = lines.join('\n');
    this._els().forEach(el => { el.textContent = txt; });
  }
  _setOp(v)   { this._els().forEach(el => el.style.setProperty('--buddy-op', String(v))); }
  _show(on)   { this._els().forEach(el => { el.style.display = on ? '' : 'none'; }); }
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
  // Pick the frame list for the current phase: the phase's pose, or the idle
  // frames as a fallback. Idle also slips in an occasional blink.
  _phaseFrames() {
    const sp = BUDDY_SPECIES[this.species];
    const cfg = BUDDY_PHASES[this.phase] || BUDDY_PHASES.idle;
    if (cfg.pose && sp.poses && sp.poses[cfg.pose]) return sp.poses[cfg.pose];
    return sp.frames;
  }
  _tick() {
    const sp = BUDDY_SPECIES[this.species];
    if (!sp) return;
    if (this.phase === 'idle' && Math.random() < 0.16) { this._draw(sp.blink); return; }
    const frames = this._phaseFrames();
    this.frame = (this.frame + 1) % frames.length;
    this._draw(frames[this.frame]);
  }
  _restartFrameTimer(ms) {
    if (this.frameTimer) clearInterval(this.frameTimer);
    this.frameTimer = setInterval(() => this._tick(), ms);
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

  // The single entry point. Switches the buddy to `phase` (idempotent — no-op if
  // already there). Unknown phase falls back to idle.
  setPhase(phase) {
    if (!this.species) return;
    if (!BUDDY_PHASES[phase]) phase = 'idle';
    if (phase === this.phase) return;
    this.phase = phase;
    const cfg = BUDDY_PHASES[phase];
    this.frame = 0;
    this._setOp(cfg.op);
    this._setMotion(cfg.motion);
    this._draw(this._phaseFrames()[0]);
    this._restartFrameTimer(cfg.speed);
    this._startWords(cfg.words);
    if (phase === 'idle') this._armIdleFade();
  }

  // (Re)resolve the species from prefs and show/hide accordingly.
  refresh() {
    const species = buddyResolveSpecies();
    if (!species) {                          // "off" → hide every copy, stop timers
      [this.frameTimer, this.idleTimer, this.wordTimer, this.typingTimer]
        .forEach(t => t && clearTimeout(t));
      if (this.frameTimer) clearInterval(this.frameTimer);
      this.frameTimer = this.idleTimer = this.wordTimer = this.typingTimer = null;
      this.species = null;
      this._show(false);
      this._setBubble('');
      return;
    }
    this.species = species;
    this.phase = 'idle';
    this.frame = 0;
    this._show(true);
    this._setMotion('');
    this._setBubble('');
    this._draw(BUDDY_SPECIES[species].frames[0]);
    this._restartFrameTimer(BUDDY_PHASES.idle.speed);
    // Briefly surface the (possibly just-changed) buddy so a species switch in
    // settings is actually visible — at deep-idle 0.08 the change is invisible.
    // Pulse to visible, then ease back to deep-idle after a couple seconds
    // (unless a real phase change has taken over by then).
    this._setOp(0.7);
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => {
      if (this.phase === 'idle') this._setOp(BUDDY_OP_DEEP_IDLE);
    }, 2500);
  }

  // User typed in the composer: show the "typing" phase, but only when no turn
  // is running (a live turn's phase takes precedence). Auto-reverts to idle
  // after a short quiet period.
  poke() {
    if (!this.species) return;
    const busy = !['idle', 'typing'].includes(this.phase);
    if (!busy) this.setPhase('typing');
    this._armIdleFade();
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

// Public hooks.
function buddyInit()       { buddy()?.refresh(); }            // app load + after settings save
function buddyPhase(phase) { buddy()?.setPhase(phase); }      // stream callbacks drive this
function buddyTurnStart()  { buddy()?.setPhase('thinking'); } // first beat of a turn
function buddyTurnEnd()    { buddy()?.turnEnd(); }

// Brighten / show "typing" on any keystroke anywhere in the app.
document.addEventListener('keydown', () => { _buddy && _buddy.poke(); }, true);
