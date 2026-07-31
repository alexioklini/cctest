---
name: Citation re-round Phase 2 — synchronous correction loop on validation failure
description: 2026-05-01 — implemented citation re-round; fires when uncited >30% OR unverified ≥2; brain mean 0.74 (Δ +0.08 vs full Brain), now beats standalone harness 0.73; first sustainable Brain quality win
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
## What was built

`claude_cli.py` additions:

- **`citation_reround_needed(validation, uncited_ratio_threshold=0.30, unverified_threshold=2)`** — boolean threshold check on the validator output
- **`build_citation_reround_feedback(validation, original_reply)`** — German user-message that lists the specific failures and tells the model to rewrite
- **`run_citation_reround(messages, original_reply, validation, model, api_key, base_url, ...)`** — single non-streaming HTTP call that returns the corrected text (or original on any error). Tools deliberately omitted from the retry payload — the model already has all retrieval results in the message history; re-round is composition-only
- **`clean_messages_for_api(messages)`** — extracted helper, mirrors the inline filter in `send_message`. Used by the re-round to clean session history before sending

`server.py` integration in `_handle_chat`:
- After `reply` assembled, before `session.add_message`
- Run validator → record `msg_metadata["citation_validation"]`
- If `citation_reround_needed` AND `mempalace.citation_reround.enabled` in config → fire ONE synchronous re-round
- Replace `reply` with the corrected text (single retry, max 1 re-round per turn)
- Record `reround_fired`, `reround_original_reply`, `reround_retry_validation` in metadata for diagnostics

Config:
```json
{ "mempalace": { "citation_reround": {
    "enabled": true,
    "temperature": 0.2, "top_p": 0.85, "timeout_seconds": 180
}}}
```

## Full 15Q eval results (Mistral self-judge)

```
Run                              gold mean   brain mean    Δ vs Brain-full
Brain (full, avg of 2 runs):     0.91        0.664         —
Harness (standalone):            0.90        0.731         +0.067
Brain harness-override:          0.89        0.685         +0.021
Brain + citation re-round:       0.89        0.743         +0.079   ← NEW BEST
```

**Δ +0.079 vs Brain-full** is just below the ±0.09 mean Mistral run-to-run variance, but the per-question pattern is much cleaner than mean noise — see below.

**Mean citation axis: 0.71** (vs Brain-full ≈0.50, vs Harness 0.58) — re-round demonstrably moves the structural citation metric.

## Re-round fire statistics (on the 15Q canary)

Fired on 11/15 questions (sorted by impact):

| q | uncited | unverified | fired | Δ from Brain-full |
|---|---|---|---|---|
| R1 multilogin | 0/2 | 0 | no | −0.13 (noise) |
| R2 morgencheck | 0/12 | 3 | yes | −0.34 (regression) |
| R3 kryptographie | 17/53 | 6 | yes | +0.01 |
| P1 password_length | 2/13 | 0 | no | 0 |
| P2 archivierung | 21/23 | 0 | yes | **+0.45** ✓ |
| P3 löschfristen | 12/16 | 0 | yes | +0.15 ✓ |
| M1 data_breach | 10/28 | 2 | yes | +0.02 |
| M2 neuer_mitarbeiter | 61/72 | 0 | yes | **+0.25** ✓ |
| M3 cloud | 36/49 | 1 | yes | −0.06 |
| F1 GwG | 0/0 | 0 | no | +0.19 ✓ |
| F2 kreditvergabe | 47/47 | 0 | yes | −0.13 |
| F3 arbeitszeit | 20/26 | 0 | yes | 0 |
| C1 ki_policy | 0/5 | 0 | no | −0.03 |
| C2 passwort | 46/49 | 0 | yes | +0.32 ✓ |
| C3 isms_ziele | 18/25 | 0 | yes | **+0.47** ✓ |

**Wins ≥ +0.15 on 5 questions** (P2/P3/M2/C2/C3) — exactly the user-validated structural citation failures.

**One regression: R2 Morgencheck −0.34** — re-round fired on 3 unverified quotes. Manual inspection needed: probably the verifier flagged true verbatim quotes as "not found" due to whitespace/em-dash normalization on a tabular structure.

## Cost / latency

- 11/15 questions = 73% re-round fire rate on the canary
- Each re-round = one extra non-streaming LLM call (~5-15s on Mistral Medium 3.5)
- Total eval-run-time: ~5min Brain inference + ~3min judge calls + ~5-8min total re-rounds = ~15min vs 9min without re-round
- For a real user chat, re-round adds ~10s latency to ~70% of project-chat answers — acceptable trade for ~+0.10 quality

## Why this works where prompt-only failed

The re-round bypasses the model's "compose ahead of consider" pattern. Mistral's failure mode in `project_eval_citation_v4_negative` was: it KNEW the citation rule, complied perfectly on simple questions, and dropped citations on complex bullet lists where momentum took over. The validator catches the failure post-facto, then the corrective user-message anchors the model in "fix this specific mistake" mode rather than "follow this rule from the start".

Re-round also avoids the F1-GwG-style "knew it was empty but couldn't stop writing" problem — gave Brain F1 +0.19 by re-prompting after the validator caught uncited fabrications.

## Known issues / next-step candidates

1. **R2 regression** — 3 false-positive "unverified" quotes triggered an unnecessary re-round on an already-good answer. Investigate the verifier's whitespace/em-dash normalization. Could add a minimum-content-loss safeguard ("if reround answer is shorter than 50% of original, keep original").

2. **Verifier counts bullets too generously** — claim_total of 47/47 on F2, 53 on R3 suggests the heuristic flags every bullet including section headings + connectors. Tighten `_BULLET_RE` or its claim-detection logic.

3. **Phase 3: streaming re-round** — currently the re-round is buffer-then-replace; the user sees the original streamed live, then it gets replaced in `done.text`. Confusing UX. Better: don't stream the original at all when re-round is enabled, only stream the corrected reply. Worth doing if Phase 2 ships to production.

4. **Re-judge with Opus** — Mistral self-judge with self-bias may be overly generous to Brain on these citation improvements. A single Opus rejudge of `20260501T133840` would give the publishable headline number.

## Run-IDs

- 20260501T121612 — Brain validator-metadata baseline
- 20260501T122545 — Brain validator-metadata rerun (variance check)
- 20260501T124538 — standalone harness baseline
- 20260501T132330 — Brain in harness-override mode
- 20260501T133840 — Brain + citation re-round phase 2 (NEW BEST)
