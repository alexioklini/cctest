# spaCy NER — Phase 1 Handover

**Status**: design approved, not yet implemented.
**Scope**: add server-side German Named Entity Recognition (PERSON / LOC / ORG) to the existing GDPR / PII pipeline, so names and addresses get the same detect → anonymise → de-anonymise treatment IBANs and emails already get.
**Out of scope** (deferred to Phase 2): English + Russian models, client-side preview-while-typing.

---

## Why this exists

The current PII pipeline is **regex-only** with checksum validation (IBAN mod-97, credit cards Luhn, ~30 country IDs). It catches structured PII well. It cannot catch **names** ("Maria Schmidt") or **addresses** ("Hauptstraße 12, 80331 München") because those have no fixed pattern and collide with common nouns.

spaCy ships pre-trained NER models per language. `de_core_news_sm` (~15 MB) recognises:
- **PER** → person names
- **LOC** → locations / places (covers addresses by virtue of street+city tokens)
- **ORG** → organisations

These three labels become three new PII rule categories in Brain.

---

## Design decisions (locked in user review)

| Decision | Choice | Reason |
|---|---|---|
| Topology | **Server-only** | One process to load the model. The browser scanner stays regex-only. Trade-off: composer modal won't preview name/address findings before send — they surface in the server-side pre-send modal and in `/v1/attachments/scan` instead. |
| Load timing | **Eager at startup** | ~1-2 s startup cost, ~50 MB RAM resident. Predictable latency on first chat. |
| Pipeline integration | **Reuse existing pseudonymizer** | NER findings get shaped exactly like regex findings (`{rule_id, span, value, category, action}`) and flow into the same `pseudonymize_text` / `deanonymize_text` / `pseudonym_maps` machinery. Zero new round-trip infrastructure. |
| Default action | **`warn`** (per new category) | NER has higher false-positive rate than checksum-validated regex. Default to `warn` so users see flags without forcing the anonymise modal. Admins flip to `block` per category in Settings → GDPR once they trust the precision. |
| Languages (Phase 1) | **German only** | Validate FP rate on real chats for ~1 week. Phase 2 adds EN + RU. |
| Language detection | **Use translation pipeline's existing detector** | Brain already runs language detection for translation. Reuse the same call site rather than duplicating. Default to German when detection returns "unknown" or fails. |

---

## Data shape

### New rule_ids

Three new entries added to `PII_RULE_CATEGORIES` (`brain.py:20173`):

```python
PII_RULE_CATEGORIES = {
    # ... existing entries unchanged ...
    "name":         "personal",   # PER from spaCy
    "address":      "personal",   # LOC from spaCy
    "organisation": "personal",   # ORG from spaCy
}
```

All three sit in the existing `personal` category, so the existing per-category action mechanism in Settings → GDPR already governs them. **No new category** — keeps the admin UI lean. Admins who want finer control use the per-rule override row underneath.

### New default actions

Append to `PII_DEFAULT_CATEGORY_ACTIONS` (`brain.py:20231`):

```python
PII_DEFAULT_CATEGORY_ACTIONS = {
    # ... existing entries unchanged ...
    # 'personal' category default stays 'warn' — no change needed,
    # the new rule_ids inherit it via PII_RULE_CATEGORIES.
}
```

No code change here — the existing `_pii_effective_action` resolver (`brain.py:20320`) already looks up category from rule_id and pulls the action.

### Per-rule override schema

Existing per-rule override already supports any `rule_id` in `PII_RULE_CATEGORIES`. So `gdpr_scanner.rule_overrides.name = "block"` works once the rule_id is registered. Validation lives in `_handle_gdpr_save` (handler in `handlers/admin.py`) — it rejects unknown rule_ids. The new rule_ids land in `PII_RULE_CATEGORIES`, so validation passes automatically.

### Tokens minted by `pseudonymize_text`

`pseudonymizer._mint_token(rule_id, idx, salt)` already keys on `rule_id`, so:

- `name` → `<NAME_1_a8k2>`, `<NAME_2_a8k2>`, …
- `address` → `<ADDRESS_1_a8k2>`, …
- `organisation` → `<ORGANISATION_1_a8k2>`, …

The opaque-token format and the tolerant reverse regex (`<\s*(\w+_\d+_\w+)\s*>`) already handle arbitrary `rule_id` values — no pseudonymizer change needed for the token round-trip. **Verify** in implementation: spaCy's `LOC` label maps to `address` (not `location`) because that's the term users will recognise in the modal.

---

## Implementation plan

### File-by-file

#### 1. `requirements.txt` — add dependencies

```
spacy>=3.7,<4.0
de_core_news_sm @ https://github.com/explosion/spacy-models/releases/download/de_core_news_sm-3.7.0/de_core_news_sm-3.7.0-py3-none-any.whl
```

The model wheel approach (rather than `python -m spacy download`) is **load-bearing** — it makes the install reproducible in CI and on the user's machine without an extra post-install step. Pin the model version. The wheel name follows `<lang>_core_<domain>_<size>-<version>` convention.

**Install size impact**: spaCy itself is ~20 MB on disk; the `sm` model is ~15 MB; resident RAM ~50 MB once loaded with parser/tagger/lemmatizer disabled.

**CI**: this URL is hosted on GitHub releases. Stable. No external API dependency at runtime.

#### 2. `engine/pii_ner.py` (NEW, ~120 LOC)

New module — kept in `engine/` not `brain.py` to avoid bloating the 22 kLOC monolith further. Module-level cache + lazy fallback (see "model load failure" below):

```python
"""spaCy NER PII detector.

Loads German NER model at startup, scans text for PER / LOC / ORG entities,
emits findings in the same shape as brain._pii_scan_text so the existing
pseudonymise pipeline picks them up unchanged.

Phase 1: German only. Phase 2 will add EN + RU and language routing.
"""

import logging
import threading
from typing import Optional

_log = logging.getLogger(__name__)

# Module-level model cache. Keyed by language code so Phase 2 can drop
# in additional models without restructuring.
_NLP_CACHE: dict[str, "spacy.Language"] = {}
_NLP_LOCK = threading.Lock()
_LOAD_FAILED: set[str] = set()  # languages that failed to load — never retry

# spaCy entity labels we care about, mapped to Brain rule_ids.
# 'address' is intentionally Brain's term for spaCy's LOC (users recognise
# it; LOC also covers cities/countries which we treat as addressy enough
# in the personal category).
_LABEL_MAP = {
    "PER":  "name",
    "LOC":  "address",
    "ORG":  "organisation",
}

# Minimum entity length to reduce noise — single-letter / short tokens are
# almost always false positives in NER. Tunable.
_MIN_ENTITY_CHARS = 3


def _model_id_for(lang: str) -> Optional[str]:
    """Returns the spaCy package name for a Brain language code, or None
    if Phase 1 doesn't ship that language yet."""
    return {"de": "de_core_news_sm"}.get(lang)


def load_models(languages: tuple[str, ...] = ("de",)) -> None:
    """Called once at server startup. Loads each language's model into the
    cache. Failures are logged but never fatal — NER becomes a no-op for
    that language. Subsequent scan calls degrade to regex-only without
    raising.
    """
    for lang in languages:
        if lang in _NLP_CACHE or lang in _LOAD_FAILED:
            continue
        model_id = _model_id_for(lang)
        if not model_id:
            _LOAD_FAILED.add(lang)
            continue
        try:
            import spacy
            with _NLP_LOCK:
                # Disable parser/tagger/lemmatizer — we only need tok2vec + ner.
                # Cuts load time and resident memory ~3x.
                nlp = spacy.load(model_id, disable=["parser", "tagger", "lemmatizer", "attribute_ruler"])
                _NLP_CACHE[lang] = nlp
            _log.info("[pii_ner] loaded %s (lang=%s)", model_id, lang)
        except Exception as e:
            _LOAD_FAILED.add(lang)
            _log.warning("[pii_ner] failed to load %s: %s — NER disabled for lang=%s", model_id, e, lang)


def is_available(lang: str = "de") -> bool:
    """True if the model is loaded and ready. False after a load failure."""
    return lang in _NLP_CACHE


def scan_text(text: str, *, lang: str = "de", max_findings: int = 100) -> list[dict]:
    """Run NER over `text`, return findings shaped like brain._pii_scan_text.

    Findings carry:
      rule_id   - 'name' | 'address' | 'organisation'
      category  - 'personal' (matches PII_RULE_CATEGORIES)
      value     - the entity text verbatim
      start, end- character offsets in `text`
      source    - 'ner' (for audit / debugging)

    `action` is NOT set here — _pii_scan_text's caller resolves it via
    _pii_effective_action so per-rule overrides apply uniformly.
    """
    if not text:
        return []
    nlp = _NLP_CACHE.get(lang)
    if nlp is None:
        return []  # model unavailable — graceful no-op
    findings: list[dict] = []
    try:
        # spaCy is thread-safe for inference if we don't mutate the pipeline.
        # Use nlp.pipe for batching only when len(text) > some threshold;
        # for single messages a direct call is fine.
        doc = nlp(text[:50_000])  # hard cap — never scan more than 50K chars
    except Exception as e:
        _log.warning("[pii_ner] scan failed: %s", e)
        return []
    for ent in doc.ents:
        rule_id = _LABEL_MAP.get(ent.label_)
        if not rule_id:
            continue
        if len(ent.text) < _MIN_ENTITY_CHARS:
            continue
        findings.append({
            "rule_id": rule_id,
            "category": "personal",
            "value": ent.text,
            "start": ent.start_char,
            "end": ent.end_char,
            "source": "ner",
        })
        if len(findings) >= max_findings:
            break
    return findings
```

**Why a separate module not in brain.py**: the project's `feedback_brain_tool_duplication` memory and `engine/CLAUDE.md` say new functionality slots into engine/ when it's self-contained. NER is.

#### 3. `brain.py:_pii_scan_text` — wire the NER call

Locate the function (~line 17543). The integration point is **after** the regex pass but **before** overlap-suppression returns results, so the existing claim-non-overlapping logic dedupes between regex and NER (regex wins on overlap because checksum-validated findings are higher confidence).

Pseudocode insertion (don't write this verbatim — adapt to the actual control flow you see in `_pii_scan_text` when you read it):

```python
def _pii_scan_text(text, max_findings=100, cfg=None):
    if cfg is None:
        cfg = _get_gdpr_scanner_config()
    if not cfg.get("enabled", True):
        return []

    findings = []
    claimed_spans = []  # existing overlap-suppression state

    # ── existing regex pass ──
    for rule in _pii_rules():
        # ... existing rule loop, populates findings + claimed_spans ...

    # ── existing bare-identifier heuristic ──
    if cfg.get("bare_identifier_check", True):
        for f in _pii_scan_bare_identifiers(text):
            # ... existing dedup ...

    # ── NEW: NER pass ──
    # Runs only if model loaded AND the NER toggle is on. Findings respect
    # the same overlap-suppression — if a regex rule already claimed the
    # span (e.g. an email that happens to contain a name), the NER finding
    # is dropped. Cap inherits from max_findings - len(findings).
    if cfg.get("ner_enabled", True):
        try:
            from engine import pii_ner
            lang = _detect_text_language(text)  # see helper note below
            if pii_ner.is_available(lang):
                for f in pii_ner.scan_text(text, lang=lang,
                                            max_findings=max_findings - len(findings)):
                    s, e = f["start"], f["end"]
                    if _overlaps_any(s, e, claimed_spans):
                        continue
                    # Resolve action via the same path as regex findings.
                    f["action"] = _pii_effective_action(f["rule_id"], cfg=cfg)
                    if f["action"] == "ignore":
                        continue
                    findings.append(f)
                    claimed_spans.append((s, e))
                    if len(findings) >= max_findings:
                        break
        except Exception as e:
            # NER must never break the regex pipeline. Log + continue.
            print(f"[pii_ner] scan skipped: {e}", flush=True)

    return findings
```

**`cfg["ner_enabled"]`** is a new master toggle — defaults to `True` in `_get_gdpr_scanner_config()` (see #4). Setting it `False` short-circuits the NER pass without unloading the model.

**`_detect_text_language(text)`**: Brain's translation pipeline already detects languages via `server_lib/translate/detect.py`. **Reuse that helper.** For Phase 1, hard-code `lang = "de"` if the detect call is expensive — German is the only model loaded anyway. Phase 2 wires it properly. Mark this as a TODO in the integration patch so it's visible.

**`_overlaps_any(s, e, claimed_spans)`**: there's already an inline check in the existing rule loop; extract it into a small helper for the NER reuse.

#### 4. `brain.py:_get_gdpr_scanner_config` — add `ner_enabled` default

The function (~line 20243) builds the cfg dict. Append the new field:

```python
def _get_gdpr_scanner_config() -> dict:
    # ... existing logic ...
    cfg = {
        "enabled": ...,
        "server_log": ...,
        "server_block": ...,
        "ner_enabled": True,   # NEW — Phase 1 ships ON by default
        # ... rest unchanged ...
    }
    # ... existing merge with user config ...
    return cfg
```

**Persistence**: `config.json → gdpr_scanner.ner_enabled` is a bool, validated by the existing GDPR settings save handler — see #6 for the handler change.

#### 5. `server.py` — eager model load at startup

Find the existing server startup sequence (look for where `init_models_config()` is called, or where other startup-time loads happen). Add:

```python
# ── spaCy NER ──
# Load once at startup so the first chat that hits the NER path doesn't
# pay the 1-2s model load cost. Failures are logged and fall back to
# regex-only — never block server startup.
try:
    from engine import pii_ner
    pii_ner.load_models(languages=("de",))
except Exception as e:
    print(f"[startup] spaCy NER skipped: {e}", flush=True)
```

Place it **after** `init_models_config()` and **before** the daemon-thread starts (so the first chat sees the model ready, and so a daemon thread doesn't race the model load).

#### 6. `handlers/admin.py:_handle_gdpr_save` — accept `ner_enabled`

Find the GDPR save handler (look for `POST /v1/services/server` or `gdpr_save`). The validation block currently checks `enabled`, `server_log`, `server_block`, and the categories/rule_overrides. Add a bool check for `ner_enabled`:

```python
if "ner_enabled" in body:
    if not isinstance(body["ner_enabled"], bool):
        self._send_json({"error": "ner_enabled must be a boolean"}, 400)
        return
    new_cfg["ner_enabled"] = body["ner_enabled"]
```

Same pattern as the existing master switches.

#### 7. `web/index.html` — GDPR tab UI: add NER toggle

In the existing Settings → GDPR tab markup, locate the master switches section (where `enabled`, `server_log`, `server_block` checkboxes live). Add a fourth row:

```html
<label class="form-row">
  <input type="checkbox" id="gdpr-ner-enabled">
  Erkenne Namen, Adressen, Organisationen (spaCy NER, Deutsch)
  <span class="form-hint">
    Höhere False-Positive-Rate als IBAN/E-Mail. Standard-Aktion: warn.
    Wenn der spaCy-Modell beim Start nicht geladen werden konnte,
    bleibt dieser Schalter wirkungslos.
  </span>
</label>
```

Wire it the same way the other master switches are wired in `web/js/settings.js` (look for `gdpr-enabled` / `gdpr-server-log` to find the load + save sites). The save POSTs `ner_enabled: bool` alongside the existing fields.

**Note**: do NOT add the new rule_ids (`name`, `address`, `organisation`) to the JavaScript `PIIScanner.rules` array — they don't exist client-side. Add them only to `PIIScanner.ruleCategories` and `defaultCategoryActions` so the **category-default + per-rule override UI** still shows them under the `personal` category. This lets admins toggle the per-rule action without the client needing the actual detector.

In `web/index.html`'s embedded `PIIScanner` definition (search for `PIIScanner.ruleCategories`), append:

```javascript
PIIScanner.ruleCategories = {
  // ... existing entries ...
  name: 'personal',
  address: 'personal',
  organisation: 'personal',
};
```

Same for `defaultCategoryActions` if it carries per-rule defaults (most likely it doesn't — category-level is enough).

#### 8. `tests/test_pii_ner.py` (NEW, ~80 LOC)

Three test classes:

```python
class TestNERLoad(unittest.TestCase):
    def test_load_german_success(self):
        # Skip if spacy or de_core_news_sm not installed
        # Otherwise assert is_available('de') becomes True after load_models
        ...

    def test_load_unsupported_lang_no_op(self):
        # load_models(('xx',)) marks 'xx' as failed, is_available('xx')→False
        ...

    def test_load_failure_graceful(self):
        # Monkeypatch spacy.load to raise; load_models should log + continue
        ...


class TestNERScan(unittest.TestCase):
    def test_german_person_detected(self):
        # "Maria Schmidt hat mir gesagt …" → finding rule_id=name value='Maria Schmidt'
        ...

    def test_german_location_mapped_to_address(self):
        # "Ich wohne in Hauptstraße 12, 80331 München" → at least one rule_id=address
        ...

    def test_short_entities_filtered(self):
        # "Du, AB" → no findings (3-char minimum)
        ...

    def test_empty_text_returns_empty(self):
        ...

    def test_findings_shape_matches_regex(self):
        # rule_id, category, value, start, end, source all present
        ...


class TestNERIntegration(unittest.TestCase):
    def test_pii_scan_text_merges_ner_with_regex(self):
        # Text with BOTH email AND name → both findings returned
        ...

    def test_overlap_suppression_regex_wins(self):
        # Construct text where a regex rule's span overlaps an NER span;
        # only the regex finding should appear (regex claimed first).
        ...

    def test_ner_disabled_via_cfg(self):
        # cfg.ner_enabled=False → no NER findings even with PER in text
        ...
```

Tests skip cleanly when spaCy / model isn't installed so CI without the model still passes (`unittest.skipUnless`).

---

## Things that need verification during implementation

1. **`pseudonymizer._mint_token` rule_id format**: verify the token-format regex `<KIND_N_HEX>` actually works for `rule_id='organisation'` (longer string). If `_mint_token` uppercases the rule_id and the reverse regex `\w+_\d+_\w+` matches `<ORGANISATION_1_a8k2>` — it should, but worth a smoke test.

2. **Hyphenated street names** ("Karl-Marx-Straße"): spaCy small German model gets these mostly right but occasionally splits. Worth a manual eyeball in the smoke test.

3. **German compound words**: "Müller" as a surname vs. "der Müller" as occupation. The sm model has decent context handling but expect FPs. This is the strongest argument for the `warn` default.

4. **Token stability for names across attachments**: the existing cross-file token stability test in `tests/test_pseudonymizer_files.py` should still pass — extend it with a name appearing in two attachments to confirm the same `<NAME_1_xxxx>` is minted both times.

5. **Audit log volume**: NER findings will increase `pii_detected` audit row count noticeably. Verify the audit log retention policy isn't going to choke on this. (No retention policy currently — flag this as a follow-up if it becomes a problem.)

6. **Settings UI for per-rule override of `name`/`address`/`organisation`**: the existing collapsible per-category panel renders per-rule overrides. Verify it picks up the three new rule_ids automatically once they're in `PIIScanner.ruleCategories` (it should — the panel iterates the category's rule_ids).

7. **Client-side `PIIScanner.scan()` parity**: the client scanner won't have name/address detection. That's by design (Phase 1 server-only). Verify the existing modal still renders server-side findings correctly when the server reports a finding the client didn't find — this path already works for the `bare_identifier` rules which behave the same way, so it should be fine.

---

## What's intentionally NOT in Phase 1

- **English + Russian models**: Phase 2.
- **Client-side preview**: Phase 2 if real-world FP rate warrants the extra round-trip.
- **Per-NER-language threshold tuning**: spaCy doesn't expose per-entity confidence by default in the `sm` model. If FP rate is too high, switching to `md` (~45 MB) or `lg` (~550 MB) is one fix; another is bolting on a token-level confidence filter via `nlp.add_pipe('span_ruler', …)`. Defer until we see real-world data.
- **GPU**: `sm` runs fine on CPU. No GPU plumbing.
- **Custom-trained models**: out of scope. Use pre-trained as-is.
- **Removing the legacy `_pii_scan_bare_identifiers` heuristic**: keep it. The bare-id heuristic catches "what is this number?" pastes where neither regex nor NER help.

---

## Acceptance criteria

Phase 1 ships when:

- [ ] `pip install -r requirements.txt` brings in spaCy + the German model wheel
- [ ] Server startup logs `[pii_ner] loaded de_core_news_sm (lang=de)` within 2 s of the listener binding
- [ ] `_pii_scan_text("Maria Schmidt arbeitet bei Siemens in München.")` returns at least one finding each for `name`, `organisation`, and `address` (the city counts as LOC)
- [ ] The same text, passed through the existing chat anonymise flow, produces a mapping with `<NAME_1_*>`, `<ORG_1_*>`, `<ADDRESS_1_*>` tokens that round-trip cleanly (LLM reply mentions the tokens → de-anonymisation restores the originals)
- [ ] Settings → GDPR shows the new "Erkenne Namen…" master toggle; flipping it OFF stops NER findings from appearing without server restart
- [ ] Settings → GDPR → personal category lists three new rule rows (`name`, `address`, `organisation`) with per-rule action override dropdowns
- [ ] Test suite green (`pytest tests/test_pii_ner.py`), existing `test_pseudonymizer*.py` still green
- [ ] Manual smoke test: chat message "Maria Schmidt hat mir per E-Mail (maria@example.com) gesagt, dass sie in der Hauptstraße 12 wohnt." with anonymise verdict → cloud LLM never sees the name, email, or address; reply renders with all three highlighted on reload

---

## Open questions to answer before starting

1. **Language detection call site**: is `server_lib/translate/detect.py` fast enough to call on every PII scan, or should we cache detection per-session / per-message? Look at the detect implementation before deciding. Phase 1 can hard-code `lang="de"` and TODO this — the only model loaded is German anyway.

2. **Where in `_pii_scan_text` does the existing overlap-suppression live?** Read the function before writing the integration patch — there may already be a helper, or the dedup may be inline. Don't duplicate.

3. **`engine/__init__.py` exports**: does it currently re-export sub-modules, or do callers import via `from engine import <module>`? Match the existing convention.

4. **Audit log row format for NER findings**: the existing `pii_detected` audit row has `args_summary` capturing finding counts. Verify NER findings get a sensible row (one per scan, not one per finding — would flood) — most likely already aggregated, but worth checking the `_pii_scan_text` caller.

---

## Rollback plan

If NER causes problems in production:

1. **Soft kill**: set `gdpr_scanner.ner_enabled = false` in `config.json` and restart. Scanner falls back to regex-only.
2. **Hard kill**: remove the `from engine import pii_ner` import + the `pii_ner.scan_text` call from `_pii_scan_text`. Restart. spaCy still loaded but unused — costs ~50 MB RAM.
3. **Full removal**: revert the commit. `requirements.txt` change is independent — spaCy + model wheel install is idempotent and reversible.

The pseudonymizer doesn't care where findings come from, so disabling NER never strands persisted mappings — they continue to de-anonymise correctly on reload.

---

## Estimated effort

- Code: ~250 LOC across 5 files
- Tests: ~80 LOC, 1 new test file
- Manual smoke testing: 30-45 min on a real chat session
- Documentation: changelog entry, CLAUDE.md update (`brain.py` § GDPR section gets a new bullet for the NER layer)

**Total: ~1 focused session, including testing.**

---

## Resuming this work

Start the next session by reading this document end-to-end. Then:

1. Confirm spaCy install works locally (`pip install spacy && python -m spacy download de_core_news_sm` — if both succeed, the wheel approach in `requirements.txt` will too).
2. Open `brain.py` to `_pii_scan_text` (line ~17543) and read the surrounding 100 lines to understand the actual control flow before writing the integration patch.
3. Implement files in this order: `engine/pii_ner.py` → `tests/test_pii_ner.py` (run them, confirm the unit tests pass standalone) → `brain.py` integration → `server.py` startup hook → `_get_gdpr_scanner_config` → admin handler → web UI toggle. This order lets you validate the detector in isolation before wiring it into the pipeline.
4. Smoke-test against a fresh chat with the example sentence from the acceptance criteria.
5. Version bump to `9.3.0` (minor — new feature). Changelog entry per the existing format. Commit, push.

If anything in this plan is wrong about the actual code shape (function signatures, line numbers, helper names), trust the code over this document. Update the document with the corrections as you go so the next handover is accurate.
