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
    color: '#e3a008',
    frames: [
      ['   ___   ', '  (o ,>  ', ' (   )___', ' /     / ', '  ^^^^^  '],
      ['   ___   ', '  (o ,>  ', ' (   )___', '  /    / ', '  ^^^^^  '],
      ['   ___   ', '  (o ,>  ', ' (   )___', ' /     / ', '   ^^^^^ '],
    ],
    blink:
      ['   ___   ', '  (- ,>  ', ' (   )___', ' /     / ', '  ^^^^^  '],
    poses: {
      typing: [['   ___   ', '  (O ,>  ', ' (   )___', ' /     / ', '  ^^^^^  '], ['   ___   ', '  (O ,>  ', ' (   )___', '  /    / ', '  ^^^^^  ']],
      thinking: [['   ___ ? ', '  (o ,>  ', ' (   )___', ' /     / ', '  ^^^^^  '], ['   ___?  ', '  (- ,>  ', ' (   )___', ' /     / ', '  ^^^^^  ']],
      tool: [['   ___   ', '  (o ,>  ', ' (   )-=#', ' /     / ', '  ^^^^^  '], ['   ___   ', '  (o ,>  ', ' (   )-=*', ' /     / ', '  ^^^^^  ']],
      writing: [['   ___   ', '  (o ,>  ', ' (   )__/', ' /     /~', '  ^^^^^  '], ['   ___   ', '  (o ,>  ', ' (   )__/', ' /     /-', '  ^^^^^  ']],
      warmup: [['   ___   ', '  (o ,>  ', ' (   )___', ' /     / ', ' ~^^^^^~ '], ['   ___   ', '  (o ,>  ', ' (   )___', ' /     / ', '  ^^^^^  ']],
      compacting: [['   ___   ', '  (o ,>  ', ' (   )_><', ' /     / ', '  ^^^^^  '], ['   ___   ', '  (o ,>  ', ' (  )><__', ' /    /  ', '  ^^^^   ']],
    },
  },
  cat: {
    label: 'Cat',
    color: '#9ca3af',
    frames: [
      [' /\\___/\\ ', '(  o o  )', ' )  ^  ( ', '(  )=(  )', ' (__)(__)'],
      [' /\\___/\\ ', '(  o o  )', ' )  ^  ( ', '(  )=(  )', ' (__)(__)'],
      [' /\\___/\\ ', '(  o o  )', ' )  ^  (~', '(  )=(  )', ' (__)(__)'],
    ],
    blink:
      [' /\\___/\\ ', '(  - -  )', ' )  ^  ( ', '(  )=(  )', ' (__)(__)'],
    poses: {
      typing: [[' /\\___/\\ ', '(  O O  )', ' )  ^  ( ', '(  )=(  )', ' (__)(__)'], [' /\\___/\\ ', '(  O O  )', ' )  -  ( ', '(  )=(  )', ' (__)(__)']],
      thinking: [[' /\\___/\\ ', '(  o -  )', ' )  ^  ( ', '(  )=(  )', ' (__)(__)'], [' /\\___/\\ ', '(  - o  )', ' )  ^  ( ', '(  )=(  )', ' (__)(__)']],
      tool: [[' /\\___/\\ ', '(  o o  )', ' )  ^  ( ', '(  )#(  )', ' (__)(__)'], [' /\\___/\\ ', '(  o o  )', ' )  ^  ( ', '(  )*(  )', ' (__)(__)']],
      writing: [[' /\\___/\\ ', '(  o o  )', ' )  ^  (~', '(  )=(  )', ' (__)(__)'], [' /\\___/\\ ', '(  o o  )', ' )  ^  (-', '(  )=(  )', ' (__)(__)']],
      warmup: [[' /\\___/\\ ', '(  o o  )', ' )  ^  ( ', '(  )=(  )', '~(__)(__)'], [' /\\___/\\ ', '(  o o  )', ' )  ^  ( ', '(  )=(  )', ' (__)(__)']],
      compacting: [[' /\\___/\\ ', '(  o o  )', ' ) >^< ( ', '(  )=(  )', ' (__)(__)'], [' /\\___/\\ ', '(  o o  )', ' ) <^> ( ', '(  )=(  )', ' (__)(__)']],
    },
  },
  dragon: {
    label: 'Dragon',
    color: '#16a34a',
    frames: [
      ['    __<>   ', '  _/ o \\_  ', ' / \\___/ \\ ', ' \\_/   \\_/ ', '   "" ""   '],
      ['    __<>   ', '  _/ o \\_  ', ' / \\___/ \\ ', ' \\_/   \\_/ ', '   "" ""   '],
      ['    __<>   ', '  _/ o \\_  ', ' / \\___/ \\~', ' \\_/   \\_/ ', '   "" ""   '],
    ],
    blink:
      ['    __<>   ', '  _/ - \\_  ', ' / \\___/ \\ ', ' \\_/   \\_/ ', '   "" ""   '],
    poses: {
      typing: [['    __<>   ', '  _/ O \\_  ', ' / \\___/ \\ ', ' \\_/   \\_/ ', '   "" ""   '], ['    __<>   ', '  _/ O \\_  ', ' / \\___/ \\ ', ' \\_/   \\_/ ', '  "" ""    ']],
      thinking: [['    __<> ? ', '  _/ o \\_  ', ' / \\___/ \\ ', ' \\_/   \\_/ ', '   "" ""   '], ['    __<>?  ', '  _/ - \\_  ', ' / \\___/ \\ ', ' \\_/   \\_/ ', '   "" ""   ']],
      tool: [['    __<>   ', '  _/ o \\_  ', ' / \\___/=# ', ' \\_/   \\_/ ', '   "" ""   '], ['    __<>   ', '  _/ o \\_  ', ' / \\___/=* ', ' \\_/   \\_/ ', '   "" ""   ']],
      writing: [['    __<>   ', '  _/ o \\_  ', ' / \\___/ \\~', ' \\_/   \\_/ ', '   "" ""   '], ['    __<>   ', '  _/ o \\_  ', ' / \\___/ \\-', ' \\_/   \\_/ ', '   "" ""   ']],
      warmup: [['    __<>   ', '  _/ o \\_  ', ' / \\___/ \\ ', ' \\_/   \\_/ ', '  ~"" ""~  '], ['    __<>   ', '  _/ o \\_  ', ' / \\___/ \\ ', ' \\_/   \\_/ ', '   "" ""   ']],
      compacting: [['    __<>   ', '  _/ o \\_  ', ' / \\_><_/  ', ' \\_/   \\_/ ', '   "" ""   '], ['    __<>   ', '  _/ o \\_  ', ' / >\\_/< \\ ', ' \\_/   \\_/ ', '   "" ""   ']],
    },
  },
  octopus: {
    label: 'Octopus',
    color: '#9333ea',
    frames: [
      ['   _____  ', '  / o o \\ ', ' |   ^   |', '  \\_____/ ', ' (/(/ \\)\\)'],
      ['   _____  ', '  / o o \\ ', ' |   ^   |', '  \\_____/ ', ' )\\)\\ (/(/'],
      ['   _____  ', '  / o o \\ ', ' |   ^   |', '  \\_____/ ', ' (/)\\ (/)\\'],
    ],
    blink:
      ['   _____  ', '  / - - \\ ', ' |   ^   |', '  \\_____/ ', ' (/(/ \\)\\)'],
    poses: {
      typing: [['   _____  ', '  / O O \\ ', ' |   ^   |', '  \\_____/ ', ' (/(/ \\)\\)'], ['   _____  ', '  / O O \\ ', ' |   ^   |', '  \\_____/ ', ' )\\)\\ (/(/']],
      thinking: [['   _____ ?', '  / o - \\ ', ' |   ^   |', '  \\_____/ ', ' (/(/ \\)\\)'], ['   _____? ', '  / - o \\ ', ' |   ^   |', '  \\_____/ ', ' (/(/ \\)\\)']],
      tool: [['   _____  ', '  / o o \\ ', ' |   ^   |', '  \\_____/ ', ' (/#/ \\#\\)'], ['   _____  ', '  / o o \\ ', ' |   ^   |', '  \\_____/ ', ' )*)* (*(*']],
      writing: [['   _____  ', '  / o o \\ ', ' |   ^   |', '  \\_____/ ', ' (/(/ \\)~)'], ['   _____  ', '  / o o \\ ', ' |   ^   |', '  \\_____/ ', ' (~(/ \\)\\)']],
      warmup: [['   _____  ', '  / o o \\ ', ' |   ^   |', '  \\_____/ ', '\\)\\)\\(/(/('], ['   _____  ', '  / o o \\ ', ' |   ^   |', '  \\_____/ ', ' (/(/ \\)\\)']],
      compacting: [['   _____  ', '  / o o \\ ', ' |  >^<  |', '  \\_____/ ', ' (/(/ \\)\\)'], ['   _____  ', '  / o o \\ ', ' |  <^>  |', '  \\_____/ ', ' (/(/ \\)\\)']],
    },
  },
  penguin: {
    label: 'Penguin',
    color: '#475569',
    frames: [
      ['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\  ww  / ', '  ^^  ^^  '],
      ['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\  ww  / ', '  ^^  ^^  '],
      ['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\  ww  / ', '   ^^^^   '],
    ],
    blink:
      ['  .----.  ', ' / -  - \\ ', ' |  <>  | ', ' \\  ww  / ', '  ^^  ^^  '],
    poses: {
      typing: [['  .----.  ', ' / O  O \\ ', ' |  <>  | ', ' \\  ww  / ', '  ^^  ^^  '], ['  .----.  ', ' / O  O \\ ', ' |  <>  | ', ' \\  ww  / ', '   ^^^^   ']],
      thinking: [['  .----. ?', ' / o  - \\ ', ' |  <>  | ', ' \\  ww  / ', '  ^^  ^^  '], ['  .----.? ', ' / -  o \\ ', ' |  <>  | ', ' \\  ww  / ', '  ^^  ^^  ']],
      tool: [['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\# ww #/ ', '  ^^  ^^  '], ['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\* ww *# ', '  ^^  ^^  ']],
      writing: [['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\  ww  /~', '  ^^  ^^  '], ['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\  ww  /-', '  ^^  ^^  ']],
      warmup: [['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\  ww  / ', ' ^^    ^^ '], ['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\  ww  / ', '   ^^^^   ']],
      compacting: [['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\ >ww< / ', '  ^^  ^^  '], ['  .----.  ', ' / o  o \\ ', ' |  <>  | ', ' \\ <ww> / ', '  ^^  ^^  ']],
    },
  },
  fox: {
    label: 'Fox',
    color: '#ea580c',
    frames: [
      [' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\  ww  /  ', '  \\_vv_/   '],
      [' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\  ww  /  ', '  \\_vv_/   '],
      [' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\  ww  /~ ', '  \\_vv_/   '],
    ],
    blink:
      [' /\\    /\\  ', '/  \\__/  \\ ', '\\  -  -  / ', ' \\  ww  /  ', '  \\_vv_/   '],
    poses: {
      typing: [[' /\\    /\\  ', '/  \\__/  \\ ', '\\  O  O  / ', ' \\  ww  /  ', '  \\_vv_/   '], [' /\\    /\\  ', '/  \\__/  \\ ', '\\  O  O  / ', ' \\  --  /  ', '  \\_vv_/   ']],
      thinking: [[' /\\    /\\ ?', '/  \\__/  \\ ', '\\  o  -  / ', ' \\  ww  /  ', '  \\_vv_/   '], [' /\\    /\\? ', '/  \\__/  \\ ', '\\  -  o  / ', ' \\  ww  /  ', '  \\_vv_/   ']],
      tool: [[' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\# ww #/  ', '  \\_vv_/   '], [' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\* ww */  ', '  \\_vv_/   ']],
      writing: [[' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\  ww  /~ ', '  \\_vv_/   '], [' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\  ww  /- ', '  \\_vv_/   ']],
      warmup: [['/\\     /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\  ww  /  ', '  \\_vv_/   '], [' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\  ww  /  ', '  \\_vv_/   ']],
      compacting: [[' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\ >ww< /  ', '  \\_vv_/   '], [' /\\    /\\  ', '/  \\__/  \\ ', '\\  o  o  / ', ' \\ <ww> /  ', '  \\_vv_/   ']],
    },
  },
  owl: {
    label: 'Owl',
    color: '#a16207',
    frames: [
      ['  ,___,  ', ' {o,o}   ', ' /)___)\\ ', ' "  v  " ', '  ^   ^  '],
      ['  ,___,  ', ' {o,o}   ', ' /)___)\\ ', ' "  v  " ', '  ^   ^  '],
      ['  ,___,  ', ' {o,o}   ', ' (\\___(\\ ', ' "  v  " ', '  ^   ^  '],
    ],
    blink:
      ['  ,___,  ', ' {-,-}   ', ' /)___)\\ ', ' "  v  " ', '  ^   ^  '],
    poses: {
      typing: [['  ,___,  ', ' {O,O}   ', ' /)___)\\ ', ' "  v  " ', '  ^   ^  '], ['  ,___,  ', ' {O,O}   ', ' (\\___(\\ ', ' "  v  " ', '  ^   ^  ']],
      thinking: [['  ,___, ?', ' {o,-}   ', ' /)___)\\ ', ' "  v  " ', '  ^   ^  '], ['  ,___,? ', ' {-,o}   ', ' /)___)\\ ', ' "  v  " ', '  ^   ^  ']],
      tool: [['  ,___,  ', ' {o,o}   ', ' /#___#\\ ', ' "  v  " ', '  ^   ^  '], ['  ,___,  ', ' {o,o}   ', ' /*___*\\ ', ' "  v  " ', '  ^   ^  ']],
      writing: [['  ,___,  ', ' {o,o}   ', ' /)___)\\~', ' "  v  " ', '  ^   ^  '], ['  ,___,  ', ' {o,o}   ', ' /)___)\\-', ' "  v  " ', '  ^   ^  ']],
      warmup: [['  ,___,  ', ' {o,o}   ', ' /)___)\\ ', ' "  v  " ', ' ^     ^ '], ['  ,___,  ', ' {o,o}   ', ' /)___)\\ ', ' "  v  " ', '  ^   ^  ']],
      compacting: [['  ,___,  ', ' {o,o}   ', ' /)___)\\ ', ' " >v< " ', '  ^   ^  '], ['  ,___,  ', ' {o,o}   ', ' /)___)\\ ', ' " <v> " ', '  ^   ^  ']],
    },
  },
  crab: {
    label: 'Crab',
    color: '#dc2626',
    frames: [
      ['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', ' (   ww   )  ', '  /\\    /\\   '],
      ['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', ' (   ww   )  ', '  /\\    /\\   '],
      ['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', ' (   ww   )  ', ' </\\    /\\>  '],
    ],
    blink:
      ['  (\\/) (\\/)  ', '   \\____/    ', '  (-    -)   ', ' (   ww   )  ', '  /\\    /\\   '],
    poses: {
      typing: [['  (\\/) (\\/)  ', '   \\____/    ', '  (O    O)   ', ' (   ww   )  ', '  /\\    /\\   '], ['  (\\/) (\\/)  ', '   \\____/    ', '  (O    O)   ', ' (   --   )  ', '  /\\    /\\   ']],
      thinking: [['  (\\/) (\\/) ?', '   \\____/    ', '  (o    -)   ', ' (   ww   )  ', '  /\\    /\\   '], ['  (\\/) (\\/)? ', '   \\____/    ', '  (-    o)   ', ' (   ww   )  ', '  /\\    /\\   ']],
      tool: [['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', '#(   ww   )# ', '  /\\    /\\   '], ['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', '*(   ww   )* ', '  /\\    /\\   ']],
      writing: [['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', ' (   ww   )~ ', '  /\\    /\\   '], ['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', ' (   ww   )- ', '  /\\    /\\   ']],
      warmup: [[' (\\/)  (\\/)  ', '   \\____/    ', '  (o    o)   ', ' (   ww   )  ', '  /\\    /\\   '], ['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', ' (   ww   )  ', '  /\\    /\\   ']],
      compacting: [['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', ' ( >ww< )    ', '  /\\    /\\   '], ['  (\\/) (\\/)  ', '   \\____/    ', '  (o    o)   ', ' ( <ww> )    ', '  /\\    /\\   ']],
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
  // Words are kept short (≤8 chars) so they fit inside the fixed-width cloud
  // thought-bubble drawn in main.css (.composer-buddy-bubble). Longer words get
  // clipped by the cloud — trim here, not by widening the cloud.
  idle:       { pose: null,        speed: 700, op: BUDDY_OP_DEEP_IDLE, motion: '',           words: [] },
  typing:     { pose: 'typing',    speed: 380, op: 0.45,               motion: 'buddy-perk',  words: ['Listening', 'Watching', 'All ears', 'Go on', 'Mm-hmm'] },
  thinking:   { pose: 'thinking',  speed: 520, op: 0.9,                motion: 'buddy-bob',   words: ['Pondering', 'Musing', 'Noodling', 'Mulling', 'Hmm'] },
  tool:       { pose: 'tool',      speed: 300, op: 0.9,                motion: 'buddy-shake', words: ['Fetching', 'Tinkering', 'Digging', 'Foraging'] },
  writing:    { pose: 'writing',   speed: 340, op: 0.9,                motion: 'buddy-bob',   words: ['Composing', 'Penning', 'Drafting', 'Inkling'] },
  warmup:     { pose: 'warmup',    speed: 600, op: 0.9,                motion: 'buddy-stretch', words: ['Warming', 'Limbering', 'Booting'] },
  compacting: { pose: 'compacting',speed: 420, op: 0.9,                motion: 'buddy-squish', words: ['Tidying', 'Folding', 'Squishing'] },
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
  // Drive opacity on BOTH the pet and its bubble so the cloud fades in lockstep
  // with the buddy (custom props set inline on the pet don't cascade to the
  // sibling bubble, so it must be set on both).
  _setOp(v)   {
    this._els().forEach(el => el.style.setProperty('--buddy-op', String(v)));
    this._bubbles().forEach(b => b.style.setProperty('--buddy-op', String(v)));
  }
  // Per-species accent color, applied to both the pet and its status text so
  // the whole companion reads as one themed unit. Falls back to the brand accent.
  _setColor(c) {
    const col = c || 'var(--accent-brand)';
    this._els().forEach(el => el.style.setProperty('--buddy-color', col));
    this._bubbles().forEach(b => b.style.setProperty('--buddy-color', col));
  }
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
    this._setColor(BUDDY_SPECIES[species].color);
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
