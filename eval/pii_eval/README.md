# PII Detector Shootout

Evaluates candidate PII-detection stacks against our custom detector, to answer:
**is an off-the-shelf library (Presidio / GLiNER) or a local LLM (M4-7B) as good
as — or better than — our 71-regex + spaCy-NER detector?**

Built 2026-06-23. Two corpora, four+1 detectors, value-level scoring, multi-rep.

## Detectors

| key | stack | where it runs |
|-----|-------|---------------|
| `ours_spacy` | **#2** our regex+checksums + spaCy `de_core_news_md` NER | live Brain server `/v1/gdpr/scan-text` |
| `ours_gliner` | **#3** our regex+checksums + **GLiNER** NER (spaCy swapped out) | server regex + eval-venv GLiNER |
| `presidio_gliner` | **#1** Presidio analyzer, GLiNER as NER engine | eval venv |
| `presidio_spacy` | bonus baseline: Presidio analyzer, spaCy `de_core_news_lg` NER | eval venv |
| `m4_llm` | **#4** Qwen2.5-7B-Instruct-4bit as the SOLE detector (LLM does it all) | M4 host direct |

`ours_gliner` reuses the server's regex/checksum layer (the part that's already
strong) and only swaps the fuzzy NER layer (name/address/org) for GLiNER — the
hypothesis from the analysis: *keep the regex, upgrade the NER.*

## Corpora

1. **`handcrafted`** — `data/handcrafted_de.jsonl`, 50 German bank sentences with
   **exact injected gold** across every category (names, addresses, IBANs,
   Steuer-IDs, credit cards, secrets, phones, emails, national IDs, IPs, dates)
   **plus deliberate negatives** (policy prose that must yield nothing — FP
   traps). This is the **authoritative** recall/precision set: gold is exact by
   construction, no LLM in the loop.

2. **`policy`** — the real **kg-real-policies** corpus (59 extracted `.md` from
   `/Users/alexander/Documents/kg-real-policies/`). German bank IT-policy docs.
   **PII-sparse** (regulatory prose: process descriptions, role names, dense
   vendor/product/system names, occasional author names + dates in headers).
   Tests **real-occurrence recall** on the little PII that exists and — more
   importantly — **false-positive behaviour** on org/role-dense text that NER
   detectors love to mis-flag as persons. Gold is built by `build_policy_gold.py`.

## Scoring (value-level, type-aware) — `common.py`

* Every detector → a set of `(normalized_value, canonical_type)` findings. We
  score by **whether each gold VALUE was found** (+ correct type), not by char
  offsets — LLMs can't emit reliable offsets, so offset-scoring would penalize
  arithmetic, not detection. Regex/NER findings get reduced to their value too,
  so all detectors are judged on one axis.
* **Type-forgiving**: a finding matches if the value matches and the predicted
  type is in the gold type's accept-set (e.g. IBAN-as-"financial" still matches
  gold `iban`). Value match with the wrong type is tracked as `value_only`
  (detected but misclassified — e.g. our detector calling a Steuer-ID a phone).
* **Free-text containment**: "Klinsky" matches gold "Alexander Klinsky" and vice
  versa (≥4-char guard). Structured types (iban/cc/id/phone/ip) match on
  separator-stripped exact equality.
* **Multi-rep**: non-deterministic detectors (`m4_llm`, GLiNER-backed) run
  `--reps` times; we report **mean ± sample-stdev**. Per
  `feedback_eval_single_run_noise`, a single-run delta < 0.05 is noise.

## Raw-detection vs production-policy (important)

Our production config runs two enforcement gates the eval must understand:

* **`min_occurrences`** — a rule needs N distinct values per doc before it fires
  (name/email/phone=3, date=10, …). Single-occurrence PII is deliberately
  suppressed in production.
* **category `action: ignore`** — `contact` (name/email/phone), `network` (IP),
  `business_id` (org) are set to `ignore` in this deployment, so `_pii_scan_text`
  **drops them entirely**.

To measure **detection capability** (the fair cross-detector question), the
`ours_*` adapters call the endpoint with `{"raw_detection": true}`, which
neutralizes both gates server-side (eval-only escape hatch in
`handlers/chat.py`). To instead measure **what production actually fires**, set
`PII_OURS_PRODUCTION_GATE=1` — useful to show the policy gap, but not a detector
comparison.

## Running

Prereqs: Brain server up (`:8420`), eval venv built (`.venv_pii_eval`, py3.12,
presidio+gliner+spacy `de_core_news_lg`), M4 host reachable.

```bash
# full matrix (run from repo root, inside the eval venv):
BRAIN_USER=admin BRAIN_PASS=admin .venv_pii_eval/bin/python \
  eval/pii_eval/run_pii_eval.py --reps 3

# subset:
... run_pii_eval.py --detectors ours_spacy,ours_gliner,m4_llm --corpora handcrafted --reps 3
```

Build / rebuild the policy gold first (idempotent, writes `data/policy_gold.jsonl`):

```bash
python3 eval/pii_eval/build_policy_gold.py --max-windows 120
```

Outputs land in `eval/pii_eval/results/<timestamp>/` (`report.md` + `summary.csv`).

## Caveats (read before trusting a number)

* **Policy gold is machine-prelabelled** (M4 by default, because cloud mistral was
  returning `api_error` in this environment) + deterministic substring-verified —
  **NOT hand-verified end-to-end**. When the gold model == a detector under test
  (M4), policy results are biased toward M4. **The handcrafted corpus carries the
  load-bearing recall/precision verdict; policy carries the realism/FP signal.**
  Rebuild gold with an unbiased judge once cloud is healthy:
  `PII_GOLD_PROVIDER=CLIProxyAPI PII_GOLD_MODEL=CLIProxyAPI/mistral-medium-3.5`.
* **GLiNER is non-deterministic near its threshold** — names hover around the
  0.45 cut and recall jitters run-to-run. That's why GLiNER-backed detectors are
  multi-repped; the spread in the report is real, not noise to hide.
* The Brain **sidecar `/turn`** path returned empty completions in this
  environment for both cloud and local models, so `m4_llm` and the gold-builder
  call the M4 host **directly** via its native Anthropic `/v1/messages` endpoint.
* `presidio_spacy` uses `de_core_news_lg` (larger than our production
  `de_core_news_md`) — so it's a slightly favourable Presidio baseline, on
  purpose (best-case Presidio).

## Files

```
common.py              taxonomy + value-level scorer + multi-rep stats
run_pii_eval.py        the runner (detector registry, corpora, report)
build_policy_gold.py   LLM-prelabel + substring-verify gold for the policy corpus
adapters/
  ours_adapter.py      #2/#3 regex half — live server /v1/gdpr/scan-text
  gliner_adapter.py    GLiNER spans (shared by #1 and #3)
  presidio_adapter.py  #1 / bonus — Presidio analyzer (spacy|gliner NER)
  m4_llm_adapter.py    #4 — M4-7B direct
data/
  handcrafted_de.jsonl exact-gold corpus (50 cases)
  policy_gold.jsonl    generated by build_policy_gold.py
```
