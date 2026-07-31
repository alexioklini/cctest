---
name: GDPR granular configuration (v8.12.0)
description: What the dedicated Settings→GDPR tab lets the user configure; key config keys, helpers, and action-resolution rules
type: project
originSessionId: e631057a-c622-40d3-9d80-892e315e29d3
---
Shipped 2026-04-24 as v8.12.0 — dedicated Settings → GDPR tab replaces the Server-tab 4-checkbox card.

**Config shape** (`config.json` → `gdpr_scanner`):
- `enabled`, `server_log`, `server_block`, `default_local_fallback_model` — unchanged semantics
- `categories: {cat: {action: ignore|warn|block}}` — 8 categories: `secrets` (block default), `national_id`, `national_id_ctx`, `financial`, `personal`, `bare_id` (warn default), `contact`, `network` (ignore default)
- `rule_overrides: {rule_id: action}` — single-rule override wins over category
- `email_allowlist: [str]` — full address OR `@domain` pattern, case-insensitive, no whitespace; suppresses findings entirely

**Why:** user wanted granular control over which PII categories trigger warnings vs blocks, plus an allowlist so known-safe emails (e.g. their own `alexander@me.com`) don't constantly flag.

**How to apply:**
- `PII_RULE_CATEGORIES` + `PII_DEFAULT_CATEGORY_ACTIONS` in `claude_cli.py` are the single source of truth — mirror them in `PIIScanner.ruleCategories` + `defaultCategoryActions` in `web/index.html` if you add a new rule.
- `_pii_effective_action(rule_id, cfg)` resolves rule_overrides → category → default, and downgrades `block` → `warn` when `server_block` master is off.
- `_pii_worst_action(findings)` returns `block > warn > ignore`. Both `send_message` and `gdpr_pick_model_for_background` gate refusals on `worst == "block"`, not on "any finding". Warn-only findings never raise.
- `ignore`-action rules are skipped during scan entirely — no findings, no audit rows, no modal.
- Client applies policy via `applyGdprConfigToScanner(gs)` — call this anywhere `state.pii*` needs to sync from the server response. It also invalidates per-chat history scan caches so category changes take effect immediately.
- Server POST validates: unknown `rule_id` → 400, unknown category silently dropped, action must be `ignore|warn|block`, allowlist entries must contain `@` and no whitespace.
- `piiBlockActive(chat)` now returns true only when the draft or history contains a **block-severity** finding, not any finding. This fixes the prior behavior where `server_block=true` + contact category on would constantly filter the model dropdown to locals.
