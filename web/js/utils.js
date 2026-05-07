/* ═══════════════════════════════════════════════════════════
   UTILITY FUNCTIONS
   ═══════════════════════════════════════════════════════════ */
function esc(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function showToast(msg, isError) {
  const t = document.createElement('div');
  t.className = 'toast' + (isError ? ' error' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

function relativeTime(ts) {
  if (!ts) return '';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  if (diff < 604800) return Math.floor(diff/86400) + 'd ago';
  return new Date(ts).toLocaleDateString();
}

function modelShortName(modelId, withProvider = true) {
  if (!modelId) return '';
  const cfg = state.modelsConfig?.models?.[modelId];
  let name = '';
  // Check display_name first (user-configurable), then shortname
  if (cfg?.display_name) { name = cfg.display_name; }
  else if (cfg?.shortname && cfg.shortname !== modelId && !cfg.shortname.startsWith('claude-')) { name = cfg.shortname; }
  else {
    const m = modelId.toLowerCase();
    if (m === 'claude-opus-4-6' || m === 'claude-opus-4-20250514') name = 'Opus 4.6';
    else if (m === 'claude-sonnet-4-6' || m === 'claude-sonnet-4-20250514') name = 'Sonnet 4.6';
    else if (m.includes('claude-3-7-sonnet')) name = 'Sonnet 3.7';
    else if (m.includes('claude-3-5-sonnet')) name = 'Sonnet 3.5';
    else if (m.includes('haiku-4-5') || m.includes('haiku-4.5')) name = 'Haiku 4.5';
    else if (m.includes('haiku')) name = 'Haiku';
    else if (m.includes('crow-9b')) name = 'Crow 9B';
    else if (m.includes('crow-4b')) name = 'Crow 4B';
    else if (m === 'minimax-m2.7' || m === 'minimax-m2.5') name = modelId;
    else if (m.includes('gemini-3.1')) name = 'Gemini 3.1 Flash Lite';
    else if (m.includes('gemini-3-flash')) name = 'Gemini 3 Flash';
    else if (m.includes('gemini-2.5-pro')) name = 'Gemini 2.5 Pro';
    else if (m.includes('gemini-2.5-flash')) name = 'Gemini 2.5 Flash';
    else if (m.includes('gemini')) name = modelId;
    else if (m.includes('qwen')) name = modelId;
    else {
      const parts = modelId.split('/');
      const n = parts[parts.length - 1];
      name = n.length > 25 ? n.substring(0, 23) + '...' : n;
    }
  }
  if (withProvider && cfg?.provider) return `${name} (${cfg.provider})`;
  return name;
}

// User-editable description for a model — shown as tooltip in dropdowns.
// Falls back to provider-qualified short name when empty.
function modelDescription(modelId) {
  if (!modelId) return '';
  const cfg = state.modelsConfig?.models?.[modelId];
  const desc = (cfg?.description || '').trim();
  if (desc) return desc;
  return modelShortName(modelId, true);
}

// Renders <option title="<description>">…</option> for a model id.
// Tooltip falls back to the qualified short name when no description is set,
// so dropdowns always show something useful on hover.
function modelOption(mid, { selected = false, label = null, suffix = '' } = {}) {
  const lbl = label != null ? label : modelShortName(mid);
  return `<option value="${esc(mid)}" title="${esc(modelDescription(mid))}"${selected ? ' selected' : ''}>${esc(lbl)}${suffix}</option>`;
}

// True when the model is enabled and its capabilities list includes `cap`.
// Default cap is 'chat' — every general model dropdown filters by this.
function modelHasCapability(midOrCfg, cap) {
  cap = cap || 'chat';
  const cfg = (typeof midOrCfg === 'string')
    ? (state.modelsConfig?.models || {})[midOrCfg]
    : (midOrCfg || {});
  if (!cfg || cfg.enabled === false) return false;
  const caps = Array.isArray(cfg.capabilities) ? cfg.capabilities : [];
  return caps.includes(cap);
}

// Returns [[mid, cfg], ...] for enabled models with capability `cap`,
// sorted by priority (desc). Default cap is 'chat'.
function enabledModelsWithCapability(cap) {
  cap = cap || 'chat';
  const mc = state.modelsConfig?.models || {};
  return Object.entries(mc)
    .filter(([mid, cfg]) => modelHasCapability(cfg, cap))
    .sort((a, b) => (b[1].priority || 0) - (a[1].priority || 0));
}

function autoResizeInput(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 200) + 'px';
}

// Returns the textarea for the currently active composer view. The project-
// detail view now hosts a full composer (same toggles as chat), so it picks
// `project-input`; welcome/chat keep their existing ids.
function _composerInputEl() {
  if (state.currentView === 'welcome')        return document.getElementById('welcome-input');
  if (state.currentView === 'project-detail') return document.getElementById('project-input');
  return document.getElementById('chat-input');
}

function _composerToggleEls(suffix) {
  return [...document.querySelectorAll(`[data-composer-toggle="${suffix}"]`)];
}

function updateSendButton() {
  const hasFiles = state._pendingImages.length > 0 || state._pendingFiles.length > 0;
  const pairs = [
    ['welcome-send-btn', 'welcome-input'],
    ['chat-send-btn',    'chat-input'],
    ['project-send-btn', 'project-input'],
  ];
  for (const [btnId, inputId] of pairs) {
    const btn = document.getElementById(btnId);
    if (!btn) continue;
    const inp = document.getElementById(inputId);
    btn.classList.toggle('active', (inp?.value?.trim().length > 0) || hasFiles);
  }
}

/* ═══════════════════════════════════════════════════════════
   PII / GDPR PRE-SUBMIT SCANNER
   Free, offline regex-based detection of personal data in
   outgoing payloads. Mirrored on the server (claude_cli.py
   _pii_scan_text) for belt-and-suspenders coverage.
   ═══════════════════════════════════════════════════════════ */
const PIIScanner = {
  // Shared helpers used by multiple validators.
  _luhn(digits){ let sum=0, alt=false; for(let i=digits.length-1;i>=0;i--){ let n=+digits[i]; if(alt){ n*=2; if(n>9) n-=9; } sum+=n; alt=!alt; } return sum%10===0; },
  _mod11(digits, weights){ let s=0; for(let i=0;i<weights.length;i++) s += (+digits[i]) * weights[i]; return s % 11; },
  _validDate(y,m,d){ if(m<1||m>12) return false; const dim=[31, (y%4===0&&(y%100!==0||y%400===0))?29:28, 31,30,31,30,31,31,30,31,30,31]; return d>=1 && d<=dim[m-1]; },

  // ── Category + action model ─────────────────────────────────────────
  // Mirrors PII_RULE_CATEGORIES in claude_cli.py. Each rule belongs to exactly
  // one category; users configure actions per category (ignore/warn/block).
  ruleCategories: {
    // Tier 2 — cloud secrets
    pem_private_key:'secrets', aws_access_key:'secrets', aws_secret_key:'secrets',
    github_app_token:'secrets', github_pat:'secrets', slack_token:'secrets',
    slack_webhook:'secrets', google_api_key:'secrets', google_oauth_client:'secrets',
    stripe_live:'secrets', stripe_test:'secrets', openai_key:'secrets',
    anthropic_key:'secrets', twilio_sid:'secrets', sendgrid_key:'secrets',
    mailgun_key:'secrets', jwt:'secrets', azure_storage_conn:'secrets',
    azure_account_key:'secrets', basic_auth_url:'secrets',
    generic_secret_assignment:'secrets',
    // Tier 1 — national IDs with checksum
    de_steuerid:'national_id', uk_nino:'national_id', uk_nhs:'national_id',
    nl_bsn:'national_id', be_national:'national_id', pl_pesel:'national_id',
    pt_nif:'national_id', se_personnummer:'national_id', dk_cpr:'national_id',
    no_fnr:'national_id', ch_ahv:'national_id', cz_rc:'national_id',
    ro_cnp:'national_id', hu_taj:'national_id', gr_amka:'national_id',
    bg_egn:'national_id', ie_pps:'national_id', br_cpf:'national_id',
    br_cnpj:'national_id', ca_sin:'national_id', mx_curp:'national_id',
    ar_dni:'national_id', in_aadhaar:'national_id', jp_mynumber:'national_id',
    kr_rrn:'national_id', sg_nric:'national_id', tw_nid:'national_id',
    at_svnr:'national_id', fr_insee:'national_id', es_dni_nie:'national_id',
    it_codicefiscale:'national_id', us_ssn:'national_id', us_ssn_ctx:'national_id',
    // Context fallback
    svnr_ctx:'national_id_ctx', ssn_ctx_loose:'national_id_ctx',
    tax_id_ctx:'national_id_ctx', insurance_number_ctx:'national_id_ctx',
    id_card_ctx:'national_id_ctx', drivers_license_ctx:'national_id_ctx',
    passport_ctx_loose:'national_id_ctx', health_insurance_ctx:'national_id_ctx',
    // Financial
    iban:'financial', credit_card:'financial', bank_account_ctx:'financial',
    // Contact
    email:'contact', phone:'contact',
    // Network
    ipv4:'network', ipv6:'network',
    // Personal / biographical
    passport:'personal', dob:'personal',
    // Heuristic
    bare_identifier:'bare_id',
  },
  defaultCategoryActions: {
    secrets:'block', national_id:'warn', national_id_ctx:'warn',
    financial:'warn', contact:'ignore', network:'ignore',
    personal:'warn', bare_id:'warn',
  },
  categoryLabels: {
    secrets:'Secrets & API keys',
    national_id:'National IDs (checksum-verified)',
    national_id_ctx:'ID-like values (context-matched)',
    financial:'Financial (IBAN, cards, accounts)',
    contact:'Contact info (emails, phone)',
    network:'Network addresses (IP)',
    personal:'Biographical (passport, DOB)',
    bare_id:'Bare numeric identifiers',
  },

  // Current policy — refreshed from /v1/services/status. Defaults are safe.
  policy: {
    enabled: true,
    serverBlock: false,
    categories: null,        // {cat: 'ignore'|'warn'|'block'} — null means use defaults
    ruleOverrides: {},       // {rule_id: 'ignore'|'warn'|'block'}
    emailAllowlist: [],
  },

  // Resolve effective action for a rule_id. Mirrors _pii_effective_action.
  effectiveAction(ruleId) {
    const ovr = this.policy.ruleOverrides?.[ruleId];
    if (ovr === 'ignore' || ovr === 'warn' || ovr === 'block') {
      return this._applyMasterSwitch(ovr);
    }
    const cat = this.ruleCategories[ruleId] || 'personal';
    const catCfg = this.policy.categories?.[cat];
    const catAct = (catCfg && (catCfg.action || catCfg)) || this.defaultCategoryActions[cat] || 'warn';
    return this._applyMasterSwitch(catAct);
  },
  _applyMasterSwitch(action) {
    return (action === 'block' && !this.policy.serverBlock) ? 'warn' : action;
  },

  emailAllowed(email) {
    const list = this.policy.emailAllowlist || [];
    if (!email || !list.length) return false;
    const e = email.trim().toLowerCase();
    for (const pat of list) {
      const p = (pat||'').trim().toLowerCase();
      if (!p) continue;
      if (p.startsWith('@')) {
        if (e.endsWith(p)) return true;
      } else if (e === p) {
        return true;
      }
    }
    return false;
  },

  // Each rule: {id, label, regex, validate?} — validate() reduces false positives.
  rules: [
    // ── Tier 2: cloud secrets & API keys (distinct prefixes → high priority) ──
    { id:'pem_private_key', label:'Private key',
      regex:/-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----[\s\S]{1,10000}?-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----/g },
    { id:'aws_access_key', label:'AWS access key ID',
      regex:/(?<![A-Z0-9])(?:AKIA|ASIA|AIDA|AROA|AIPA|ANPA|ANVA|ABIA|ACCA)[A-Z0-9]{16}(?![A-Z0-9])/g },
    { id:'aws_secret_key', label:'AWS secret access key',
      // Context-gated: 40-char b64-like string preceded by aws-secret cue + separator
      regex:/(?:aws_secret_access_key|aws[_-]?secret[_-]?access[_-]?key|aws[_-]?secret)[\s:="']*([A-Za-z0-9\/+]{40})(?![A-Za-z0-9\/+=])/gi },
    { id:'github_app_token', label:'GitHub app token',
      // More specific (longer/new format) — must come before generic ghp_ rule
      regex:/\bgithub_pat_[A-Za-z0-9_]{82}\b/g },
    { id:'github_pat', label:'GitHub personal access token',
      regex:/\bgh[pousr]_[A-Za-z0-9]{36,255}\b/g },
    { id:'slack_token', label:'Slack token',
      regex:/\bxox[abprs]-[A-Za-z0-9-]{10,200}\b/g },
    { id:'slack_webhook', label:'Slack webhook URL',
      regex:/https:\/\/hooks\.slack\.com\/services\/T[A-Z0-9]+\/B[A-Z0-9]+\/[A-Za-z0-9]+/g },
    { id:'google_api_key', label:'Google API key',
      regex:/\bAIza[0-9A-Za-z_\-]{35}\b/g },
    { id:'google_oauth_client', label:'Google OAuth client ID',
      regex:/\b\d{12}-[a-z0-9]{32}\.apps\.googleusercontent\.com\b/g },
    { id:'stripe_live', label:'Stripe live key',
      regex:/\b(?:sk|rk|pk)_live_[0-9a-zA-Z]{24,99}\b/g },
    { id:'stripe_test', label:'Stripe test key',
      regex:/\b(?:sk|rk|pk)_test_[0-9a-zA-Z]{24,99}\b/g },
    { id:'openai_key', label:'OpenAI API key',
      regex:/\bsk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20,}\b/g },
    { id:'anthropic_key', label:'Anthropic API key',
      regex:/\bsk-ant-[a-z0-9]{2,6}-[A-Za-z0-9_\-]{85,120}\b/g },
    { id:'twilio_sid', label:'Twilio account SID',
      regex:/\bAC[a-f0-9]{32}\b/g },
    { id:'sendgrid_key', label:'SendGrid API key',
      regex:/\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b/g },
    { id:'mailgun_key', label:'Mailgun API key',
      regex:/\bkey-[a-f0-9]{32}\b/g },
    { id:'jwt', label:'JWT',
      regex:/\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b/g },
    { id:'azure_storage_conn', label:'Azure Storage connection string',
      regex:/DefaultEndpointsProtocol=https;AccountName=[A-Za-z0-9]+;AccountKey=[A-Za-z0-9+\/=]{80,};?(?:EndpointSuffix=[^;\s]+)?/g },
    { id:'azure_account_key', label:'Azure account key',
      regex:/(?:AccountKey|SharedAccessKey)=([A-Za-z0-9+\/=]{40,100})(?=[;"'\s]|$)/g },
    { id:'basic_auth_url', label:'Credentials in URL',
      regex:/\b(?:https?|ftp|ssh|git|postgres|postgresql|mysql|mongodb|redis):\/\/[^\s:@\/]+:[^\s@\/]+@[A-Za-z0-9.\-]+/g,
      validate:(m)=>!/:\/\/[^:]*:(password|changeme|example|xxx+|\*+)@/i.test(m) },
    { id:'generic_secret_assignment', label:'Hard-coded secret',
      // `token = "abc..."` or `api_key: "abc..."` with 20+ char value
      regex:/\b(?:api[_-]?key|secret|token|password|passwd|pwd|auth|bearer)[\s:=]{1,4}["']([A-Za-z0-9+\/=_\-]{20,})["']/gi,
      validate:(m)=>{
        const v = m.match(/["']([A-Za-z0-9+\/=_\-]{20,})["']/)?.[1] || '';
        // Cut placeholders, UUID-only, base64 of "example", etc.
        if (/^(xxx+|\*+|changeme|example|placeholder|your[_-]?(?:key|token|secret))$/i.test(v)) return false;
        // Entropy heuristic: reject if only 2 distinct chars
        const distinct = new Set(v).size;
        return distinct >= 6;
      } },

    // ── Standard identifiers (phone is after national-ID rules below to
    // avoid eating checksum-valid IDs shaped like XXX-XXX-XXXX) ──
    { id:'email', label:'Email address',
      regex:/\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g },
    // Credit card moved below national IDs — a valid-Luhn 13-digit national ID
    // would otherwise be classified as a card. See below after ID block.
    { id:'iban', label:'IBAN',
      regex:/\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9][ ]?){11,30}\b/g,
      validate:(m)=>{
        const iban = m.replace(/\s/g,'').toUpperCase();
        if (iban.length<15 || iban.length>34) return false;
        // Move first 4 chars to end, convert letters to numbers, mod 97 == 1
        const rearr = iban.slice(4) + iban.slice(0,4);
        let num = '';
        for (const c of rearr) {
          num += /[A-Z]/.test(c) ? (c.charCodeAt(0)-55).toString() : c;
        }
        // Big number mod-97 via piecewise
        let rem = 0;
        for (const d of num) { rem = (rem*10 + parseInt(d,10)) % 97; }
        return rem === 1;
      } },
    { id:'ipv4', label:'IPv4 address',
      regex:/(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?!\d)/g,
      validate:(m)=>{
        // Skip obvious non-personal private/link-local/loopback ranges
        if (/^(0\.|127\.|255\.|169\.254\.)/.test(m)) return false;
        return true;
      } },
    { id:'ipv6', label:'IPv6 address',
      regex:/\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b/g },
    { id:'us_ssn', label:'US Social Security Number',
      // XXX-XX-XXXX with required dashes (cuts false positives). Context-gated
      // variants (SSN: 123456789) handled as a second pattern.
      regex:/(?<!\d)(\d{3})-(\d{2})-(\d{4})(?!\d)/g,
      validate:(m)=>{
        const parts = m.split('-');
        const [a,b,c] = parts;
        if (a === '000' || a === '666' || a[0] === '9') return false;
        if (b === '00' || c === '0000') return false;
        return true;
      } },
    { id:'us_ssn_ctx', label:'US Social Security Number',
      // No dashes, but must be preceded by SSN/social context
      regex:/(?:\bSSN\b|\bsocial\s+security\b)[^\w\n]{0,15}(\d{9})(?!\d)/gi,
      validate:(m)=>{
        const digits = m.match(/\d{9}/)?.[0];
        if (!digits) return false;
        const a = digits.slice(0,3), b = digits.slice(3,5), c = digits.slice(5);
        if (a === '000' || a === '666' || a[0] === '9') return false;
        if (b === '00' || c === '0000') return false;
        return true;
      } },
    { id:'at_svnr', label:'Austrian Sozialversicherungsnummer',
      regex:/(?<!\d)\d{10}(?!\d)/g,
      validate:(m)=>{
        const w = [3,7,9,5,8,4,2,1,6];
        const d = [...m].map(c => +c);
        const vals = [d[0], d[1], ...d.slice(3)];
        let total = 0;
        for (let i=0;i<vals.length;i++) total += vals[i] * w[i];
        if (total % 11 !== d[2]) return false;
        // Also verify positions 5-10 form a plausible DDMMYY
        const dd = +m.slice(4,6), mm = +m.slice(6,8);
        if (dd < 1 || dd > 31 || mm < 1 || mm > 12) return false;
        return true;
      } },
    { id:'fr_insee', label:'French INSEE / NIR',
      // 13 digits + 2-digit key; mod-97 check. Corsica uses 2A/2B in dept slot — we skip those.
      regex:/(?<!\d)([12])(\d{2})(0[1-9]|1[0-2]|[2-9]\d)(\d{2}|\dA|\dB)(\d{3})(\d{3})[\s ]?(\d{2})(?!\d)/gi,
      validate:(m)=>{
        const clean = m.replace(/[\sAB]/gi,'0');
        if (clean.length !== 15) return false;
        const body = clean.slice(0,13), key = parseInt(clean.slice(13),10);
        const n = parseInt(body,10);
        return (97 - (n % 97)) === key;
      } },
    // ── Context-gated rules first (keyword match beats bare-digit rules) ──
    { id:'de_steuerid', label:'German Steuer-ID',
      regex:/(?:\bSteuer[- ]?ID\b|Steueridentifikationsnummer|\bTIN\b)[^\d\n]{0,20}(\d{11})(?!\d)/gi,
      validate:(m)=>{
        const d = (m.match(/\d{11}/) || [''])[0];
        if (!d || d[0] === '0') return false;
        const counts = {};
        for (const x of d) counts[x] = (counts[x]||0)+1;
        const repeats = Object.values(counts).filter(n => n > 1);
        return repeats.length === 1 && (repeats[0] === 2 || repeats[0] === 3);
      } },

    // ── Tier 1: EU national IDs ──
    { id:'uk_nino', label:'UK National Insurance Number',
      // 2 letters (not D,F,I,Q,U,V; first char not O; not combinations FY,GB,NK,KN,TN,NT,ZZ),
      // 6 digits, 1 suffix letter A-D or space.
      regex:/\b(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z][0-9]{6}[A-D]?\b/g },
    { id:'uk_nhs', label:'UK NHS number',
      regex:/(?<!\d)\d{3}[ -]?\d{3}[ -]?\d{4}(?!\d)/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 10) return false;
        let sum = 0;
        for (let i = 0; i < 9; i++) sum += (+d[i]) * (10 - i);
        let check = 11 - (sum % 11);
        if (check === 11) check = 0;
        if (check === 10) return false;
        return check === +d[9];
      } },
    { id:'nl_bsn', label:'Dutch BSN',
      // Context-gated — 8-9 digit shape collides with many other numbers
      regex:/(?:\bBSN\b|burgerservicenummer|sofinummer)[^\d\n]{0,15}(\d{8,9})(?!\d)/gi,
      validate:(m)=>{
        const raw = (m.match(/\d{8,9}/) || [''])[0];
        const d = raw.padStart(9, '0');
        if (d.length !== 9) return false;
        const w = [9,8,7,6,5,4,3,2,-1];
        let s = 0;
        for (let i = 0; i < 9; i++) s += (+d[i]) * w[i];
        return s % 11 === 0 && +d !== 0;
      } },
    { id:'be_national', label:'Belgian national number',
      regex:/(?<!\d)\d{2}[. ]?\d{2}[. ]?\d{2}[- ]?\d{3}[. ]?\d{2}(?!\d)/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 11) return false;
        const body9 = d.slice(0,9);
        const check = +d.slice(9);
        // Pre-2000 and post-2000 formulas
        const a = 97 - (parseInt(body9, 10) % 97);
        const b = 97 - (parseInt('2' + body9, 10) % 97);
        return check === a || check === b;
      } },
    { id:'pl_pesel', label:'Polish PESEL',
      regex:/(?<!\d)\d{11}(?!\d)/g,
      validate:(m)=>{
        const w = [1,3,7,9,1,3,7,9,1,3];
        let s = 0;
        for (let i = 0; i < 10; i++) s += (+m[i]) * w[i];
        const check = (10 - (s % 10)) % 10;
        if (check !== +m[10]) return false;
        // Positions 3-4 encode month with century offset (01-12,21-32,41-52,61-72,81-92)
        const mm = +m.slice(2,4);
        return (mm >= 1 && mm <= 12) || (mm >= 21 && mm <= 32) || (mm >= 41 && mm <= 52) || (mm >= 61 && mm <= 72) || (mm >= 81 && mm <= 92);
      } },
    { id:'pt_nif', label:'Portuguese NIF',
      // Context-gated — generic 9-digit strings would false-positive too often
      regex:/(?:\bNIF\b|número\s+fiscal|contribuinte)[^\d\n]{0,15}(\d{9})(?!\d)/gi,
      validate:(m)=>{
        const d = m.match(/\d{9}/)?.[0];
        if (!d || !'123568 9'.includes(d[0])) return false;
        let s = 0;
        for (let i = 0; i < 8; i++) s += (+d[i]) * (9 - i);
        let check = 11 - (s % 11);
        if (check >= 10) check = 0;
        return check === +d[8];
      } },
    { id:'se_personnummer', label:'Swedish personnummer',
      regex:/(?<!\d)(?:\d{2})?\d{6}[-+]?\d{4}(?!\d)/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 10 && d.length !== 12) return false;
        const short = d.length === 12 ? d.slice(2) : d;
        // Luhn on first 9 digits
        let s = 0;
        for (let i = 0; i < 9; i++) {
          let n = (+short[i]) * (i % 2 === 0 ? 2 : 1);
          if (n > 9) n -= 9;
          s += n;
        }
        const check = (10 - (s % 10)) % 10;
        return check === +short[9];
      } },
    { id:'dk_cpr', label:'Danish CPR',
      // 10 digits, optional dash after 6: DDMMYY-XXXX
      regex:/(?<!\d)\d{6}[- ]?\d{4}(?!\d)/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 10) return false;
        const dd = +d.slice(0,2), mm = +d.slice(2,4);
        return dd >= 1 && dd <= 31 && mm >= 1 && mm <= 12;
      } },
    { id:'no_fnr', label:'Norwegian fødselsnummer',
      regex:/(?<!\d)\d{11}(?!\d)/g,
      validate:(m)=>{
        const d = [...m].map(c => +c);
        const w1 = [3,7,6,1,8,9,4,5,2];
        const w2 = [5,4,3,2,7,6,5,4,3,2];
        let s1 = 0;
        for (let i = 0; i < 9; i++) s1 += d[i] * w1[i];
        let k1 = 11 - (s1 % 11);
        if (k1 === 11) k1 = 0;
        if (k1 === 10 || k1 !== d[9]) return false;
        let s2 = 0;
        for (let i = 0; i < 10; i++) s2 += d[i] * w2[i];
        let k2 = 11 - (s2 % 11);
        if (k2 === 11) k2 = 0;
        return k2 !== 10 && k2 === d[10];
      } },
    { id:'ch_ahv', label:'Swiss AHV (OASI)',
      // EAN-13 starting with 756
      regex:/\b756[.\- ]?\d{4}[.\- ]?\d{4}[.\- ]?\d{2}\b/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 13 || !d.startsWith('756')) return false;
        let s = 0;
        for (let i = 0; i < 12; i++) s += (+d[i]) * (i % 2 === 0 ? 1 : 3);
        const check = (10 - (s % 10)) % 10;
        return check === +d[12];
      } },
    { id:'cz_rc', label:'Czech rodné číslo',
      regex:/(?<!\d)\d{6}\/?\d{3,4}(?!\d)/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 9 && d.length !== 10) return false;
        if (d.length === 10) {
          // mod-11 rule
          const n = parseInt(d, 10);
          if (n % 11 !== 0 && !(n % 11 === 10 && d[9] === '0')) return false;
        }
        const mm = +d.slice(2,4);
        const m_real = mm > 50 ? mm - 50 : mm;
        return m_real >= 1 && m_real <= 12;
      } },
    { id:'ro_cnp', label:'Romanian CNP',
      regex:/(?<!\d)\d{13}(?!\d)/g,
      validate:(m)=>{
        const w = [2,7,9,1,4,6,3,5,8,2,7,9];
        let s = 0;
        for (let i = 0; i < 12; i++) s += (+m[i]) * w[i];
        let check = s % 11;
        if (check === 10) check = 1;
        if (check !== +m[12]) return false;
        const mm = +m.slice(3,5), dd = +m.slice(5,7);
        return mm >= 1 && mm <= 12 && dd >= 1 && dd <= 31;
      } },
    { id:'hu_taj', label:'Hungarian TAJ',
      // Context-gated — same 9-digit shape as Dutch BSN, US SSN (no dashes),
      // and many others; keyword required to avoid collisions.
      regex:/(?:\bTAJ\b|társadalom|társadalombiztos)[^\d\n]{0,15}(\d{3}[- ]?\d{3}[- ]?\d{3})(?!\d)/gi,
      validate:(m)=>{
        const d = (m.match(/\d{3}[- ]?\d{3}[- ]?\d{3}/) || [''])[0].replace(/\D/g,'');
        if (d.length !== 9) return false;
        let s = 0;
        for (let i = 0; i < 8; i++) s += (+d[i]) * (i % 2 === 0 ? 3 : 7);
        return (s % 10) === +d[8];
      } },
    { id:'gr_amka', label:'Greek AMKA',
      regex:/(?<!\d)\d{11}(?!\d)/g,
      validate:(m)=>{
        // Starts with DDMMYY and Luhn over 11 digits
        const dd = +m.slice(0,2), mm = +m.slice(2,4);
        if (dd < 1 || dd > 31 || mm < 1 || mm > 12) return false;
        return PIIScanner._luhn(m);
      } },
    { id:'bg_egn', label:'Bulgarian EGN',
      regex:/(?<!\d)\d{10}(?!\d)/g,
      validate:(m)=>{
        const w = [2,4,8,5,10,9,7,3,6];
        let s = 0;
        for (let i = 0; i < 9; i++) s += (+m[i]) * w[i];
        const check = (s % 11) % 10;
        if (check !== +m[9]) return false;
        // Month is offset for 1800s / 2000s; real month ≤ 12 after offset
        const mm = +m.slice(2,4);
        const real = mm > 40 ? mm - 40 : (mm > 20 ? mm - 20 : mm);
        return real >= 1 && real <= 12;
      } },
    { id:'ie_pps', label:'Irish PPS',
      regex:/\b\d{7}[A-W][A-IW]?\b/g,
      validate:(m)=>{
        const s = m.toUpperCase();
        if (s.length !== 8 && s.length !== 9) return false;
        const digits = s.slice(0,7);
        const check = s[7];
        const letters = 'WABCDEFGHIJKLMNOPQRSTUV'; // index 0 => 'W' (remainder 0)
        const w = [8,7,6,5,4,3,2];
        let sum = 0;
        for (let i = 0; i < 7; i++) sum += (+digits[i]) * w[i];
        if (s.length === 9) {
          // 9th char adds to the sum (A=1,...W=0)
          const extra = s[8] === 'W' ? 0 : (s.charCodeAt(8) - 64);
          sum += extra * 9;
        }
        return letters[sum % 23] === check;
      } },

    // ── Tier 1: Americas + APAC national IDs ──
    { id:'br_cpf', label:'Brazilian CPF',
      regex:/\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 11 || /^(\d)\1{10}$/.test(d)) return false;
        const calc = (end) => {
          let sum = 0;
          for (let i = 0; i < end; i++) sum += (+d[i]) * (end + 1 - i);
          const r = (sum * 10) % 11;
          return r === 10 ? 0 : r;
        };
        return calc(9) === +d[9] && calc(10) === +d[10];
      } },
    { id:'br_cnpj', label:'Brazilian CNPJ',
      regex:/\b\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}\b/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 14 || /^(\d)\1{13}$/.test(d)) return false;
        const weights1 = [5,4,3,2,9,8,7,6,5,4,3,2];
        const weights2 = [6,5,4,3,2,9,8,7,6,5,4,3,2];
        const calc = (end, ws) => {
          let sum = 0;
          for (let i = 0; i < end; i++) sum += (+d[i]) * ws[i];
          const r = sum % 11;
          return r < 2 ? 0 : 11 - r;
        };
        return calc(12, weights1) === +d[12] && calc(13, weights2) === +d[13];
      } },
    { id:'ca_sin', label:'Canadian SIN',
      regex:/(?<!\d)\d{3}[- ]?\d{3}[- ]?\d{3}(?!\d)/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 9) return false;
        if (d[0] === '0' || d[0] === '8') return false;
        return PIIScanner._luhn(d);
      } },
    { id:'mx_curp', label:'Mexican CURP',
      regex:/\b[A-Z][AEIOUX][A-Z]{2}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b/gi },
    { id:'ar_dni', label:'Argentine DNI',
      // Context-gated: "DNI" keyword + 7-8 digits (optionally separated)
      regex:/\bDNI[\s:]*\d{1,2}\.?\d{3}\.?\d{3}\b/gi },
    { id:'in_aadhaar', label:'Indian Aadhaar',
      // Context-gated: Verhoeff alone passes ~10% of random 12-digit strings,
      // too noisy without the "aadhaar"/"UID" keyword nearby
      regex:/(?:\baadhaar\b|\bUID\b|\bUIDAI\b)[^\d\n]{0,20}([2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?!\d)/gi,
      validate:(m)=>{
        // Verhoeff checksum
        const d2 = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]];
        const p = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]];
        const digits = m.replace(/\D/g,'').split('').reverse().map(Number);
        let c = 0;
        for (let i = 0; i < digits.length; i++) c = d2[c][p[i % 8][digits[i]]];
        return c === 0;
      } },
    { id:'jp_mynumber', label:'Japanese My Number',
      regex:/(?<!\d)\d{12}(?!\d)/g,
      validate:(m)=>{
        const w = [6,5,4,3,2,7,6,5,4,3,2];
        let s = 0;
        for (let i = 0; i < 11; i++) s += (+m[i]) * w[i];
        const r = s % 11;
        const check = r <= 1 ? 0 : 11 - r;
        return check === +m[11];
      } },
    { id:'kr_rrn', label:'Korean RRN',
      regex:/(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)/g,
      validate:(m)=>{
        const d = m.replace(/\D/g,'');
        if (d.length !== 13) return false;
        const w = [2,3,4,5,6,7,8,9,2,3,4,5];
        let s = 0;
        for (let i = 0; i < 12; i++) s += (+d[i]) * w[i];
        const check = (11 - (s % 11)) % 10;
        if (check !== +d[12]) return false;
        const mm = +d.slice(2,4), dd = +d.slice(4,6);
        return mm >= 1 && mm <= 12 && dd >= 1 && dd <= 31;
      } },
    { id:'sg_nric', label:'Singapore NRIC/FIN',
      regex:/\b[STFGM]\d{7}[A-Z]\b/g,
      validate:(m)=>{
        const first = m[0], digits = m.slice(1,8), check = m[8];
        const w = [2,7,6,5,4,3,2];
        let s = 0;
        for (let i = 0; i < 7; i++) s += (+digits[i]) * w[i];
        if (first === 'T' || first === 'G') s += 4;
        if (first === 'M') s += 3;
        const r = s % 11;
        const stTables = {
          S: 'JZIHGFEDCBA', T: 'JZIHGFEDCBA',
          F: 'XWUTRQPNMLK', G: 'XWUTRQPNMLK',
          M: 'KLJNPQRTUWX',
        };
        const table = stTables[first];
        if (!table) return false;
        return table[r] === check;
      } },
    { id:'tw_nid', label:'Taiwan national ID',
      regex:/\b[A-Z][12]\d{8}\b/g,
      validate:(m)=>{
        // First letter maps to two digits via table
        const map = { A:10,B:11,C:12,D:13,E:14,F:15,G:16,H:17,I:34,J:18,K:19,L:20,M:21,N:22,O:35,P:23,Q:24,R:25,S:26,T:27,U:28,V:29,W:32,X:30,Y:31,Z:33 };
        const prefix = map[m[0]];
        if (!prefix) return false;
        const first = Math.floor(prefix/10), second = prefix%10;
        const digits = [first, second, ...m.slice(1).split('').map(Number)];
        const w = [1,9,8,7,6,5,4,3,2,1,1];
        let s = 0;
        for (let i = 0; i < digits.length; i++) s += digits[i] * w[i];
        return s % 10 === 0;
      } },

    // ── Credit card (after national IDs: 13-digit IDs shouldn't be stolen) ──
    { id:'credit_card', label:'Credit card number',
      regex:/(?<![+\d])(?:\d[ -]?){13,19}(?!\d)/g,
      validate:(m)=>{
        const digits = m.replace(/\D/g,'');
        if (digits.length<13 || digits.length>19) return false;
        let sum=0, alt=false;
        for (let i=digits.length-1;i>=0;i--) {
          let n=parseInt(digits[i],10);
          if (alt) { n*=2; if (n>9) n-=9; }
          sum+=n; alt=!alt;
        }
        return sum%10===0;
      } },

    // ── Phone (generic — runs after national-ID rules so checksum IDs win) ──
    { id:'phone', label:'Phone number',
      regex:/(?:(?<![\w.])\+\d{1,3}[\s().-]?(?:\d[\s().-]?){7,14}\d|(?<!\d)\d{3}[\s.-]\d{3,4}[\s.-]\d{3,4}(?!\d))/g,
      validate:(m)=>{ const digits = m.replace(/\D/g,''); return digits.length>=8 && digits.length<=15; } },

    { id:'es_dni_nie', label:'Spanish DNI/NIE',
      // DNI: 8 digits + letter. NIE: X/Y/Z + 7 digits + letter.
      regex:/(?<![A-Z0-9])(?:[XYZ]?\d{7,8}[A-HJ-NP-TV-Z])(?![A-Z0-9])/gi,
      validate:(m)=>{
        const s = m.toUpperCase();
        const letters = 'TRWAGMYFPDXBNJZSQVHLCKE';
        let num;
        if (/^[XYZ]/.test(s)) {
          num = parseInt('XYZ'.indexOf(s[0]) + s.slice(1,-1), 10);
        } else {
          num = parseInt(s.slice(0,-1), 10);
        }
        return letters[num%23] === s.slice(-1);
      } },
    { id:'it_codicefiscale', label:'Italian Codice Fiscale',
      regex:/\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b/gi },
    { id:'passport', label:'Passport number (heuristic)',
      // Context-triggered: must be preceded by "passport" within 20 chars
      regex:/passport[^\w\n]{0,20}([A-Z][0-9]{6,9}|[A-Z]{1,2}[0-9]{6,8})/gi,
      validate:(_)=>true },
    { id:'dob', label:'Date of birth',
      // Context: "DOB:", "born", "geboren", "date of birth" near a date
      regex:/(?:\b(?:DOB|born|date\s+of\s+birth|geboren|geburtsdatum|né|née|nacido)\b[^\n]{0,20}?(?:\d{1,2}[\/.\- ]\d{1,2}[\/.\- ]\d{2,4}|\d{4}-\d{2}-\d{2}))/gi },

    // ── Context-fallback rules: fire on keyword + number-shape even when
    // the checksum fails. The user is clearly discussing that PII category
    // regardless of whether the specific value is valid. Runs LAST so the
    // strict checksum rules above still win via overlap suppression. ──
    { id:'svnr_ctx', label:'Social-insurance number (likely)',
      regex:/(?:\bSVNR\b|\bSV[- ]?Nr\.?\b|\bSV[- ]?Nummer\b|Sozialversicherungsnummer|social[- ]?insurance|national[- ]?insurance|\bNIN\b)[^\d\n]{0,20}(\d[\d \-\/.]{7,19}\d)/gi },
    { id:'ssn_ctx_loose', label:'Social Security Number (likely)',
      regex:/(?:\bSSN\b|social[- ]?security[- ]?(?:number|no\.?|#)?)[^\d\n]{0,15}(\d{3}[- ]?\d{2}[- ]?\d{4}|\d{9})/gi },
    { id:'tax_id_ctx', label:'Tax identification number (likely)',
      regex:/(?:\bTIN\b|tax[- ]?id(?:entification)?[- ]?(?:number|no\.?)?|Steuer[- ]?ID|Steuernummer|USt[- ]?ID|VAT[- ]?(?:number|no\.?))[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-.\/]{6,18}[A-Z0-9])/gi },
    { id:'insurance_number_ctx', label:'Insurance number (likely)',
      regex:/(?:insurance[- ]?number|insurance[- ]?no\.?|Versicherungsnummer|numéro[- ]?(?:de[- ]?)?sécurité[- ]?sociale|numero[- ]?(?:di[- ]?)?previdenza)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-.\/]{6,19}[A-Z0-9])/gi },
    { id:'id_card_ctx', label:'ID / identity card number (likely)',
      regex:/(?:\bID[- ]?(?:number|no\.?|card)\b|Personalausweis|carte[- ]?d['\s-]identit|documento[- ]?(?:de[- ]?)?identi[dt]ad|cédula)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-.\/]{5,16}[A-Z0-9])/gi },
    { id:'drivers_license_ctx', label:"Driver's license number (likely)",
      regex:/(?:driver'?s?[- ]?licen[sc]e|Führerschein|permis[- ]?de[- ]?conduire|carnet[- ]?de[- ]?conducir|patente)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-.\/]{5,16}[A-Z0-9])/gi },
    { id:'passport_ctx_loose', label:'Passport number (likely)',
      regex:/(?:passport|Reisepass|passeport|pasaporte|passaporto)[^\w\n]{0,20}([A-Z0-9][A-Z0-9\- ]{5,14}[A-Z0-9])/gi },
    { id:'bank_account_ctx', label:'Bank account number (likely)',
      regex:/(?:\baccount[- ]?(?:number|no\.?|#)\b|\bacct\.?[- ]?(?:no\.?|#)?\b|\bIBAN\b|Kontonummer|numéro[- ]?de[- ]?compte|número[- ]?de[- ]?cuenta)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-\/.]{7,30}[A-Z0-9])/gi },
    { id:'health_insurance_ctx', label:'Health insurance number (likely)',
      regex:/(?:health[- ]?insurance|Krankenversicherungsnummer|Krankenkasse|assurance[- ]?maladie|seguridad[- ]?social|Medicare|Medicaid|\bNHS[- ]?(?:number|no\.?)?|\bAMKA\b|\bTAJ\b)[^\d\n]{0,20}([A-Z0-9][A-Z0-9 \-.\/]{5,19}[A-Z0-9])/gi },
  ],

  // Heuristic: bare numeric identifier. Fires when the overall message is
  // dominated by 9-14 digit numbers and has very little prose — classic
  // "what is this number?" paste. Not a regex rule because it needs a
  // whole-text view; returns same-shaped finding list.
  _scanBareIdentifiers(text) {
    if (!text || text.length > 2000) return [];
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    if (!lines.length) return [];
    // Must be mostly digit-shaped lines (>=60% of non-empty lines are 9-14 digits, possibly w/ separators)
    const idLike = lines.filter(l => /^\d[\d .\-\/]{7,18}\d$/.test(l));
    const tooFew = idLike.length < Math.max(1, Math.ceil(lines.length * 0.6));
    if (tooFew) return [];
    const findings = [];
    for (const line of idLike) {
      const digits = line.replace(/\D/g,'');
      if (digits.length < 9 || digits.length > 14) continue;
      const idx = text.indexOf(line);
      findings.push({
        ruleId: 'bare_identifier',
        label: 'Numeric identifier (unverified)',
        match: line,
        index: idx,
      });
      if (findings.length >= 20) break;
    }
    return findings;
  },

  // Cross-field duplicate suppression: if a credit-card match is already
  // covered by Luhn-valid IBAN, drop it (and similar overlaps).
  // Applies per-category actions: rules with action='ignore' are skipped;
  // email findings matching the allowlist are dropped silently.
  scan(text) {
    if (!text || typeof text !== 'string') return [];
    const findings = [];
    const seenSpans = []; // [start, end, ruleId]
    for (const rule of this.rules) {
      const action = this.effectiveAction(rule.id);
      if (action === 'ignore') continue;
      const cat = this.ruleCategories[rule.id] || 'personal';
      const re = new RegExp(rule.regex.source, rule.regex.flags);
      let m;
      while ((m = re.exec(text)) !== null) {
        const match = m[0];
        if (rule.validate && !rule.validate(match)) continue;
        const start = m.index, end = m.index + match.length;
        if (seenSpans.some(([s,e]) => start < e && end > s)) continue;
        if (rule.id === 'email' && this.emailAllowed(match)) continue;
        seenSpans.push([start, end, rule.id]);
        findings.push({ ruleId: rule.id, label: rule.label, match, index: start, category: cat, action });
        if (findings.length >= 100) break;
      }
    }
    const bareAction = this.effectiveAction('bare_identifier');
    if (bareAction !== 'ignore') {
      for (const f of this._scanBareIdentifiers(text)) {
        const start = f.index, end = f.index + f.match.length;
        if (seenSpans.some(([s,e]) => start < e && end > s)) continue;
        seenSpans.push([start, end, f.ruleId]);
        f.category = 'bare_id';
        f.action = bareAction;
        findings.push(f);
        if (findings.length >= 100) break;
      }
    }
    return findings;
  },

  // Return the most-severe action across findings: block > warn > ignore.
  worstAction(findings) {
    let worst = 'ignore';
    for (const f of findings || []) {
      const a = f.action || 'warn';
      if (a === 'block') return 'block';
      if (a === 'warn' && worst !== 'block') worst = 'warn';
    }
    return worst;
  },

  // Summarize findings as a count-per-category map.
  summarize(findings) {
    const counts = {};
    for (const f of findings) counts[f.label] = (counts[f.label]||0) + 1;
    return counts;
  },

  // Inline preview string: "2 emails, 1 IBAN"
  formatCounts(counts) {
    const parts = [];
    for (const [label, n] of Object.entries(counts)) {
      parts.push(n + ' ' + (n === 1 ? label.toLowerCase() : label.toLowerCase() + 's'));
    }
    return parts.join(', ');
  },

  // True if the attachment's MIME type is plausibly human-readable text we can scan.
  isTextMime(mime) {
    if (!mime) return false;
    if (mime.startsWith('text/')) return true;
    if (mime === 'application/json' || mime === 'application/xml' || mime === 'application/yaml') return true;
    if (mime === 'application/javascript' || mime === 'application/typescript') return true;
    return false;
  },

  // Decode a base64 attachment to text, skipping if it looks binary.
  decodeAttachmentText(file) {
    if (!file || !file.data || file.encoding !== 'base64') return '';
    if (!this.isTextMime(file.type)) {
      // Filename-based fallback for common text extensions
      const ext = (file.name || '').split('.').pop().toLowerCase();
      const textExts = new Set(['txt','md','csv','tsv','json','xml','yaml','yml','html','htm','css','js','ts','py','rb','go','rs','java','c','cpp','h','sh','log','toml','ini']);
      if (!textExts.has(ext)) return '';
    }
    try {
      const bytes = Uint8Array.from(atob(file.data), c => c.charCodeAt(0));
      // Heuristic: if > 5% of the first 1KB are non-printable, treat as binary.
      const probe = bytes.slice(0, Math.min(1024, bytes.length));
      let nonPrint = 0;
      for (const b of probe) {
        if (b === 9 || b === 10 || b === 13) continue;
        if (b < 32 || b === 127) nonPrint++;
      }
      if (probe.length > 0 && nonPrint / probe.length > 0.05) return '';
      return new TextDecoder('utf-8', {fatal:false}).decode(bytes);
    } catch(e) { return ''; }
  },

  // Scan a full outgoing payload: user text + all text-readable attachments.
  // Returns {findings, bySource} where bySource is {text: [...], 'file:name': [...]}.
  scanPayload(text, files) {
    const bySource = {};
    const all = [];
    if (text) {
      const fs = this.scan(text);
      if (fs.length) { bySource.text = fs; all.push(...fs); }
    }
    for (const f of (files || [])) {
      const decoded = this.decodeAttachmentText(f);
      if (!decoded) continue;
      const fs = this.scan(decoded);
      if (fs.length) {
        bySource['file:' + f.name] = fs;
        all.push(...fs);
      }
    }
    return { findings: all, bySource, counts: this.summarize(all),
             worstAction: this.worstAction(all) };
  },
};
window.PIIScanner = PIIScanner;

/* ═══════════════════════════════════════════════════════════
   NEXT-PROMPT SUGGESTIONS (ghost text in composer)
   ═══════════════════════════════════════════════════════════ */
const NextPrompt = {
  _suggestion: null,
  _origPlaceholder: null,
  _fetchToken: 0,

  _input() {
    return document.getElementById('chat-input');
  },

  clear() {
    this._suggestion = null;
    const input = this._input();
    if (!input) return;
    input.classList.remove('has-suggestion');
    if (this._origPlaceholder != null) {
      input.placeholder = this._origPlaceholder;
    }
  },

  set(text) {
    const input = this._input();
    if (!input || !text) return;
    // Only show if input is empty — otherwise we'd stomp what the user typed
    if (input.value.length > 0) return;
    this._suggestion = text;
    if (this._origPlaceholder == null) this._origPlaceholder = input.placeholder || '';
    input.placeholder = text;
    input.classList.add('has-suggestion');
  },

  accept({ submit = false } = {}) {
    const input = this._input();
    const text = this._suggestion;
    if (!input || !text) return false;
    input.value = text;
    this.clear();
    autoResizeInput(input);
    updateSendButton();
    if (submit) {
      sendMessage();
    } else {
      input.focus();
      const end = input.value.length;
      try { input.setSelectionRange(end, end); } catch (e) {}
    }
    return true;
  },

  active() {
    return !!this._suggestion;
  },

  async fetchFor(sessionId) {
    if (!sessionId) return;
    const token = ++this._fetchToken;
    try {
      const data = await API.get(`/v1/sessions/${encodeURIComponent(sessionId)}/next-prompt`);
      // Drop stale responses if a newer fetch was issued in the meantime
      if (token !== this._fetchToken) return;
      // Drop if the active session changed while we were waiting
      if (state.activeChat?.sessionId !== sessionId) return;
      const text = (data?.suggestion || '').trim();
      if (text) this.set(text);
    } catch (e) {
      // Silent — suggestions are best-effort
    }
  },
};

