---
name: project-transparent-anonymisation-complete
description: 2026-05-16 v8.5.0 — transparent-anonymisation rollout complete (all 6 steps shipped); admin audit view + system-prompt clamp + sticky pref + composer shield
metadata: 
  node_type: memory
  type: project
  originSessionId: 95754a26-4d1b-4810-ae60-2675fae4c4ad
---

Shipped v8.5.0 on 2026-05-16. Transparent-anonymisation rollout (steps
1–6) is feature-complete; SDK_TRANSPARENT_ANONYMISATION_HANDOVER.md can
be archived once committed (kept for the design + invariants).

**Why:** the v8.41/8.42 work landed the core mechanism (text + file
walkers, recovery flow, sidecar-context plumbing) but left the polish
unfinished. Step 6 closed the four loose ends called out in the
handover.

**How to apply:**

- `_GDPR_ANON_CLAMP` lives in `brain._apply_system_prompt_postprocess`,
  gated on the `_thread_local._gdpr_anonymising` flag the chat worker
  sets next to `session._gdpr_mapping_id`. **Don't add the clamp to the
  cached base prose** — it would break warmpool KV-prefix sharing for
  non-anon turns (only anon turns should pay the bytes).
- Sticky preference: `sessions.gdpr_action_pref` column;
  `'cancel'` is NEVER a valid value (would brick the chat — server
  refuses with 400).
- Composer reset = the sticky-pref reset surface (`btn-gdpr-pref` →
  `resetGdprActionPref()`). Don't add a separate settings panel button.
- Admin audit endpoints are admin-only by design — **owners do not see
  plaintext PII even on their own chats**. Pseudonymisation is a privacy
  boundary, not a UX feature. If anyone proposes "let users see their
  own decrypted maps", check with the user first; it might be a feature
  request but it's a deliberate scope expansion.
- Per-mapping `<details>` lazy-decrypts on open (saves cycles on chats
  with many mappings). `pseudonymizer.load_mapping(id) → None` for
  unknown ids maps cleanly to a 404.

**Tests** (64 green): combined run is
`python3 -m unittest tests.test_pseudonymizer tests.test_pseudonymizer_persistence tests.test_chat_worker_helpers tests.test_pseudonymizer_files tests.test_gdpr_clamp tests.test_gdpr_audit`.

Related: [[project-sdk-phase5-complete-v9]] (parallel rollout in the
same week); [[feedback_kv_cache_stability]] (why the clamp goes in
postprocess, not the cached prose).
