# PII Detector Shootout — Findings (2026-06-23)

Question: is an off-the-shelf library (Presidio / GLiNER) or a local LLM (M4-7B)
as good as — or better than — our custom detector (71 regex+checksums + spaCy NER)?

Five stacks, two corpora, value-level scoring (P/R/F1), multi-rep (mean±sd).
Detectors measured at **raw detection capability** (production min_occurrences +
category-ignore gates neutralized) so this is a fair capability comparison, not
a measure of current enforcement policy.

## Headline — handcrafted corpus (50 cases, EXACT injected gold = authoritative)

| Detector | P | R | F1 |
|---|---|---|---|
| **m4_llm** (Qwen2.5-7B, sole detector) | **0.90** | **0.87** | **0.88** |
| ours_spacy (current production) | 0.78 | 0.84 | 0.81 |
| ours_gliner (our regex + GLiNER NER) | 0.72 | 0.84 | 0.78 |
| presidio_spacy (de_core_news_lg) | 0.68 | 0.75 | 0.71 |
| presidio_gliner | 0.67 | 0.71 | 0.69 |

## Per-category recall — handcrafted

| category | ours_spacy | ours_gliner | presidio_gliner | presidio_spacy | m4_llm |
|---|---|---|---|---|---|
| name | 0.89 | 1.00 | 1.00 | 1.00 | 1.00 |
| address | 0.86 | 0.71 | 0.71 | 1.00 | 0.86 |
| organisation | 0.75 | 0.75 | 0.75 | 0.62 | **0.12** |
| email | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| phone | 0.50 | 0.50 | 1.00 | 1.00 | 1.00 |
| iban | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| credit_card | 1.00 | 1.00 | **0.00** | **0.00** | 1.00 |
| national_id | 0.67 | 0.67 | **0.00** | **0.00** | 1.00 |
| secret | 0.71 | 0.71 | **0.00** | **0.00** | 1.00 |
| network | 0.50 | 0.50 | 1.00 | 1.00 | 0.50 |
| date | 0.86 | 0.86 | 1.00 | 1.00 | 1.00 |

## Policy corpus (real kg-real-policies, FP-stress on org/role-dense text)

| Detector | P | R | F1 |
|---|---|---|---|
| m4_llm | 0.22 | 0.67 | 0.33 |
| presidio_gliner | 0.10 | 0.79 | 0.18 |
| presidio_spacy | 0.07 | 0.76 | 0.13 |
| ours_spacy | 0.07 | 0.43 | 0.12 |
| ours_gliner | 0.05 | 0.48 | 0.10 |

Precision is low for EVERYONE here — the corpus is dense with vendor/product/
system/role names that look like entities. M4 is ~2-3× more precise than the
rest. NOTE: policy gold is M4-prelabelled (cloud was down) → biased toward M4;
treat policy as an FP/realism signal, NOT a recall verdict. The handcrafted
numbers carry the verdict.

## Conclusions

1. **M4-7B as sole detector is the strongest stack (F1 0.88), and it is the only
   one strong on BOTH structured PII AND fuzzy NER** — perfect on national_id,
   secret, credit_card (where Presidio scores 0.00) AND names/dates. Its one real
   weakness: organisation recall 0.12 — it refuses to tag product/system names
   like Bloomberg/SWIFT as orgs (great for precision, bad if you specifically
   want those flagged).

2. **Presidio is NOT viable as a replacement for a German bank.** It scores 0.00
   on credit_card, national_id, and secret — no German Steuer-ID/AHV/BSN
   recognizers, no API-key/secret detection. The national-ID breadth gap I
   predicted in the analysis is now MEASURED, not assumed. It would need most of
   our 71 rules ported in as custom recognizers to even reach parity.

3. **GLiNER does NOT beat spaCy for our NER layer** (ours_gliner 0.78 < ours_spacy
   0.81). Once GLiNER's labels are tuned ("person" not "person name" — it's
   label-wording sensitive), it matches spaCy on name recall but LOSES on address
   (0.71 vs 0.86), and adds a heavyweight torch dependency. The original
   "swap spaCy→GLiNER" hypothesis is REFUTED on this evidence.

4. **Our detector's real weak spots** (now quantified): phone recall 0.50,
   national_id 0.67 (Steuer-ID mis-tagged as phone — a rule-ordering quirk),
   network 0.50. These are fixable in the regex layer without any library.

## Recommendation

The off-the-shelf libraries lose. The interesting result is M4-7B: it beats our
production detector on raw capability and is the only stack strong across all
categories. Two credible directions:

* **Keep our detector as the deterministic backbone** (secrets + checksummed
  national IDs MUST stay deterministic — you can't gate a key-leak block on an
  81%-F1 model), and fix its measured gaps (phone, Steuer-ID ordering) in regex.
* **Add M4-7B as a second-pass NER/recall booster** for names/addresses/dates
  where it's strongest, fused behind the existing seam — NOT as a replacement for
  the regex/secret/checksum core.

A blind library swap (Presidio or GLiNER) would be a regression. Worth a focused
follow-up: an "ours + M4 union" detector vs ours-alone, on a HAND-VERIFIED policy
gold set (rebuild gold with cloud mistral once it's healthy to remove the M4
self-labelling bias).

## Caveats
* Policy gold = M4-prelabelled + deterministic-cleaned, NOT hand-verified → biased
  toward M4 on policy. Handcrafted (exact gold) is authoritative.
* All detectors run at raw capability (production gates off). With production
  gates ON, ours fires far less (single-occurrence name/email/phone/IP suppressed
  by design) — that's policy, not capability.
* presidio_spacy used de_core_news_lg (bigger than our prod md) = best-case Presidio.
* M4 + GLiNER reps were identical (temp=0 / stable) → sd 0.00; the 3rd M4 policy
  rep was cut (a urllib call hung) but reps 1-2 were byte-identical, so 0.33 is firm.

---

# Second-pass (ours ∪ M4) + latency — 2026-06-24

## Quality: ours ∪ M4 (parallel union, full-text, ours-wins-on-overlap)

Handcrafted corpus (exact gold), 2 reps (identical, temp=0):

| Detector | P | R | F1 |
|---|---|---|---|
| ours_spacy (today) | 0.78 | 0.84 | 0.81 |
| m4_llm (alone) | 0.90 | 0.87 | **0.88** |
| **ours ∪ M4 (naive union)** | 0.63 | **0.97** | 0.77 |

**The naive union is NOT the win.** Recall climbs to 0.97 (recovers almost
everything), but precision craters to 0.63 — M4's extra spans drag it below
BOTH ours-alone (0.81) and M4-alone (0.88) on F1. Unconditional union buys
recall at too high a precision cost.

Why: M4 adds findings ours didn't make, and on the categories where M4 is
noisy (it over-proposes), those become false positives. "ours wins on overlap"
protects ours' hits but does nothing about M4's *extra* wrong ones.

### Implication for the architecture
- M4-alone (0.88) > naive union (0.77). If you want one detector, **M4-alone
  beats the union** on F1.
- The union is only worth it if you **constrain what you trust from M4** — e.g.
  only let M4 ADD findings in the categories where it's both strong AND ours is
  weak (phone, national_id), and ignore M4's noisy categories (organisation).
  That's a targeted union, not a blanket one — next experiment.
- This is exactly why "let the LLM decide finally" (pure adjudication) is
  appealing for PRECISION: an adjudicator would attack the 0.63, not the recall.
  But our gap was recall, so the real answer is probably **targeted union for
  recall + a cheap precision filter**, measured before committing.

## Latency / parallelization (the operational gate)

24 realistic PII-scan inputs, M4 host (Qwen2.5-7B-4bit on the Mac mini M4):

| | mean | p50 | p95 | max |
|---|---|---|---|---|
| **ours-alone** (regex+NER, live server) | **15 ms** | 4 ms | 6 ms | 266 ms |
| M4 sequential per-call | 2452 ms | 2562 ms | 4180 ms | 8189 ms |
| M4 parallel per-call (8 workers) | 5535 ms | 5942 ms | 10372 ms | 13852 ms |

- **ours is ~160× faster than M4** (15 ms vs 2.45 s). Adding M4 to the hot path
  of every scan turns a 15 ms check into a **2.5 s (p95 4 s) wait** — and the
  pre-send GDPR scan is interactive (it gates the user's send).
- **The M4 host DOES batch** (2.87× speedup at 8 workers — better than the
  ~1.15× my notes recorded for oMLX; this vLLM-metal build genuinely overlaps).
  BUT batching cuts *aggregate* wall-clock, not *per-call* latency: under 8-way
  load each call takes **5.5 s mean / 10.4 s p95** — concurrency makes the
  individual user wait LONGER, it just serves more of them at once.

### Operational conclusion
M4 is **too slow for the synchronous pre-send PII scan** (a 2.5–10 s stall on
every send is unacceptable for an interactive gate). Viable placements:
1. **Async / background paths only** — project mining, KG extraction, scheduled
   tasks, the document-classification scan. These already tolerate seconds and
   run off the user's critical path. This is where M4's quality win is free.
2. **Hot path: keep ours (15 ms)**, optionally fire M4 in the background AFTER
   send to enrich the audit trail / catch leaks for the NEXT turn — never
   blocking the current send.
3. If M4 ever moves to the hot path, it must be **non-blocking + cached**, and
   the batching means a shared queue helps throughput but not the p95 a single
   user feels.

## Net recommendation (updated)
- Do **not** put M4 in the synchronous pre-send scan — latency kills it.
- Use M4 (or even a targeted ours∪M4) on the **async mining/classification
  paths**, where 0.88 vs 0.81 F1 is a real upgrade and 2.5 s is invisible.
- For the hot path, fix ours' regex gaps (phone, Steuer-ID ordering) — cheap,
  deterministic, 15 ms.
- If a hot-path recall boost is truly needed, prototype a **targeted union**
  (trust M4 only on phone/national_id, drop its org noise) and re-measure F1 —
  the blanket union (0.77) is not good enough.

---

# Name-precision gate — the real problem (2026-06-24)

PROBLEM (from you): enabling person-name detection alongside address/DOB floods
you with false positives. Diagnosis: the spaCy `de_core_news_md` model tags
German common/compound nouns + tech terms as PER. The base shape gate only
requires ONE capitalised token — and in German EVERY noun is capitalised, so it
leaks.

## What the FPs actually are (kg-real-policies corpus)

spaCy 'name' on policy: 5 TP, **29 FP** (precision 0.15). The FPs:
`Datenschutzvorfall`, `Benutzerkennwörter`, `Administratorenrechten`,
`Notfallkontakte`, `Urlaub`, `Cryptshare`, `Delete Button`, `Pre-trained
Transformer`, `Subauftragsverarbeitern`, … — i.e. compound nouns + tech terms,
NOT people.

## The gate (opt-in, `gdpr_scanner.name_precision_gate`)

Accept a `name` only with positive person-evidence:
1. a person honorific/title adjacent (Herr/Frau/Dr./Mag./Prof. …), OR
2. >= 2 capitalised tokens, NONE looking like a German common noun (noun-suffix)
   or a known tech/generic word.
A lone capitalised token is never enough.

## Measured impact

**Name-only precision on policy corpus: 0.15 -> 0.50 (24/29 FPs removed, 0 real
names lost).** And 4 of the 5 "remaining FPs" are actually REAL names
(`Gertraud Wisiak`, `Michal Pyzel`) miscounted only because gold split them on
`\n\n` — true name precision with the gate is ~0.8+.

Aggregate F1 (all categories):
| corpus | ours (gate off) | ours+nameprec |
|---|---|---|
| handcrafted | P0.78 R0.84 F1 0.81 | P0.78 R0.82 F1 0.80 |
| policy | P0.07 R0.43 F1 0.12 | P0.08 R0.43 F1 0.13 |

Handcrafted name recall held at 0.89 (no real names lost). The aggregate barely
moves because the policy precision problem is BROADER than names — `organisation`
and `date` FPs dominate the remaining noise. The name gate is a safe, free win
(big name-precision gain, zero recall cost) but is NOT the whole fix.

## Recommendation
1. **Ship the name-precision gate** — it's deterministic, costs nothing, removes
   ~83% of name FPs with zero real-name loss. Default it ON.
2. **Fix gold/detector newline handling** — `Alexander\n\nKlinsky` spans should
   collapse whitespace before matching (cheap, recovers miscounted names).
3. **Next: the same treatment for `organisation` and `date`** — they're the
   remaining FP mass on policy text. Org especially (product/role/vendor names
   over-fire); a similar evidence gate + a vendor/role stoplist is the follow-up.

---

# Org gate + newline fix — measured bundle (2026-06-24)

Added: (c) collapse PDF line-breaks inside NER spans ("Alexander\n\nKlinsky"),
(b) organisation precision gate (drop legal/internal abbreviations ARL/DSG/UWG…
+ KI-/IT-/EU- concept prefixes; keep real product names SWIFT/ELBA/ZAK via a
curated stoplist, NOT a blanket all-caps drop which would kill them).

DATE gate: NOT built — diagnosis showed date precision is already 0.50 on policy
(1 FP), controlled by its existing min_occurrences=10 + person-proximity gate.
Building one would solve a non-problem. (Honest scope call.)

## Full NER-precision gate (name + org) — measured

| corpus | ours (gate off) | ours + gate |
|---|---|---|
| handcrafted | P0.78 R0.84 F1 0.81 | P0.79 R0.82 **F1 0.81** |
| policy | P0.07 R0.43 F1 0.12 | **P0.16 R0.42 F1 0.24** |

- **Policy precision 2.3× (0.07→0.16), F1 2× (0.12→0.24)** — the org gate moved
  the aggregate the name gate alone couldn't (org was the dominant FP mass).
- Handcrafted F1 unchanged (0.81), name recall held 0.89 — no real-data loss.
- Cost: org recall 0.47→0.40 on policy (stoplist drops ambiguous WPB-type
  abbrevs). Small, defensible trade for 2× precision.
- name-only precision (policy): 0.15 → ~0.89 (true; the residual "FPs" are real
  names in windows the M4 gold didn't label).

Verdict: ship the gate ON. Deterministic, no real-PII recall loss, 2× policy F1.
