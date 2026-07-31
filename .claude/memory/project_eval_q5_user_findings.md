---
name: User-validated eval finding — Q5 P2_archivierung_dauer (no-proactive run)
description: 2026-05-01 — User confirmed by inspecting Brain session that P2 has citation discipline failure + partial fabrication; matches Mistral judge verdict
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
User-confirmed finding from manually inspecting Brain sessions in the `sysprompt-no-proactive` run (results dir `20260501T110032_disc-none_sysprompt-no-proactive`):

**Q5 — P2_archivierung_dauer (Brain session `fb15b50e3f09`):**
- "keine Zitierung und teilweise Halluzination"
- Judge said: lacks verbatim quotes; fabricates unsourced details (RPO/RTO, Office365)
- Score: brain 0.5 vs gold 1.0
- **User agrees with judge verdict** — this is a real Brain content failure, not a rubric calibration issue

**Why it matters:** P1/P3 (other precision questions) Brain handles well. P2 has a specific failure mode where Brain pulls in adjacent technical details (Office365 retention, RPO/RTO) that aren't in the source document. The user is reviewing other questions to spot which judge verdicts are real failures vs miscalibrations.

**Q1-Q4 status (user-validated):** Brain answers are OK as-is, no further improvement needed:
- Q1 R1_multilogin: brain 0.98 (won)
- Q2 R2_morgencheck: brain 0.98 (tie with gold)
- Q3 R3_kryptographie: brain 1.0 (won, but gold was meta-ack glitch — still a clean Brain answer)
- Q4 P1_password_length: brain 0.92 (small gap to gold, acceptable)

**Q5 P2_archivierung_dauer (brain 0.5):** keine Zitierung und teilweise Halluzination (RPO/RTO, Office365 ohne Quelle). Real Brain content failure, judge calibration correct.

**Q6 P3_loeschfristen (brain 0.8):** Zitierung fehlt. Werte mostly correct but quotes paraphrased rather than verbatim. Citation discipline failure, judge calibration correct.

**Q7 M1_data_breach_flow (brain 0.93):** Inhaltlich OK, Zitierung gut. **Separate UI-Beobachtung:** in Brain agent UI werden Zitate nicht immer als Badge gerendert (extractReferencesFromToolResult / inline ref-badge logic in web/index.html — possibly a regex match issue when multiple `[Quelle: ...]` brackets appear in close succession). Worth investigating in a separate UI session, not a content/eval issue.

**Q8 M2_neuer_mitarbeiter (brain 0.68):** Zitierung fehlt + leichte Halluzination (timelines, roles ohne Quelle). User confirms judge verdict.

**Q9 M3_cloud_drittparteien (brain 0.98, tie with gold):** OK, no improvement needed.

**Q10 F1_geldwaesche (brain 0.27, judge said catastrophic refusal failure):** **User disagrees with judge.** Brain DID refuse correctly (named that the corpus has no GwG content), then provided **truthful, useful alternative info from the actual corpus**. User considers this acceptable — perhaps even desirable — behavior.

**This is a rubric calibration issue, not a Brain failure.** The judge interpreted "Brain mentioned other corpus content after refusing" as fabrication. But naming what the corpus DOES contain after refusing is helpful, not harmful — as long as the alternative info is real and from the actual corpus.

**Implication for the rubric:** the REFUSAL DISCIPLINE rule "do not list what the corpus does cover" is too strict. A refusal that ALSO points the user toward related, real corpus content is more useful than a bare canonical sentence. Need to relax this rule — but keep the "do not synthesize from training data" prohibition intact.

**Q11 F2_kreditvergabe (brain 0.50, judge said catastrophic refusal failure):** **User disagrees with judge — same pattern as Q10.** Brain refuses correctly ("keine expliziten Kreditvergaberichtlinien"), then provides truthful alternative info from actual corpus (CHECK24-Festgeld KYC, Risikomanagement). User considers this acceptable.

**Pattern confirmed across Q10 + Q11 + Q12:** Brain's refusal-with-alternatives is the *desired* behavior on out-of-scope questions where adjacent in-scope content exists. Mistral judge consistently mis-rates this as fabrication. The rubric's REFUSAL DISCIPLINE block needs to:
1. Keep: "do not synthesize from training-data knowledge" (prevents real fabrication)
2. Relax: "do not list what the corpus does cover" (currently penalising helpful behavior)
3. Differentiate: real fabrication (P2-style — invented `§N` references, fake retention numbers) from truthful-alternatives-after-refusal (Q10/Q11/Q12 — actual corpus content offered after a clean refusal)

**Q12 F3_arbeitszeit (brain 0.93, judge said refusal failure):** **User disagrees with judge — third instance of same pattern.** Brain says "keine expliziten Regelungen" then offers helpful pointers (Datenschutz/IT-Sicherheit/Compliance retention periods that ARE in the corpus). User: "passt".

**ALL THREE refusal questions (F1/F2/F3) are user-validated as good Brain behavior, not failures.** This is purely a rubric calibration problem. If the rubric is fixed, Brain mean score on the no-proactive run jumps significantly:
- F1 0.27 → ~0.85+ (only real issue: cited some irrelevant docs while listing alternatives)
- F2 0.50 → ~0.85+
- F3 0.93 → 0.95+ (already near-tie, just refusal-axis penalty)

**Q13 C1_ki_policy_bullets (brain 0.83):** Citation discipline failure on bullet list — user confirms judge verdict. **"Zitierung nicht immer da, vor allem nicht nach jedem bullet point"** — exactly the failure mode the project's CITATION DISCIPLINE block targets ("one citation per claim — never one citation covering a list").

**Citation pattern across Q5/Q6/Q8/Q13:** four different question types (precision, multi-doc, citation), all show the same root cause — Brain finds the right document(s), states the right values, but does NOT reliably emit verbatim per-claim quotes. The single citation per block / paraphrased quote / partial citation pattern is consistent.

**Q14 C2_passwort_zitat (brain 0.68):** Same pattern. Infos stimmen (right document `20_2_2_4_ARL_Arbeitsplatz Richtlinie`, content correct), Zitierung fehlt (one trailing `Quelle:` instead of per-claim verbatim quotes). Five-out-of-five citation-axis failures now confirmed by user (Q5, Q6, Q8, Q13, Q14).

**Q15 C3_isms_ziele (brain 0.33):** Same pattern again. Infos scheinen korrekt zu sein, Zitierung fehlt. Six citation-axis failures now confirmed (Q5, Q6, Q8, Q13, Q14, Q15).

---

## Final user-validated bilance (15Q, no-proactive run)

**Categorisation by user verdict:**

| category | count | questions | what's actually wrong |
|---|---|---|---|
| **OK as-is** | 4 | Q1, Q2, Q3, Q4 | nothing |
| **Tied with gold** | 1 | Q9 | nothing |
| **Citation discipline failure** | 5 | Q5, Q6, Q8, Q13, Q14 | Brain finds right doc, knows answer, doesn't emit per-claim verbatim quotes |
| **Citation + light hallucination** | 2 | Q15, plus partly Q5 | citation missing AND some unsourced specifics |
| **Real fabrication** | 1 | Q5 (P2 partial) | RPO/RTO, Office365, fabricated `§N` references |
| **Refusal-with-alternatives — JUDGE WRONG** | 3 | Q10, Q11, Q12 | Brain refuses correctly + offers truthful corpus alternatives. User considers this DESIRED behavior; rubric is too strict |
| **Tool-call streaming bug (only in hoist-refuse run)** | 0 in this run | — | not present in no-proactive run |

**The single dominant root cause: citation discipline.** 6/15 questions (Q5, Q6, Q8, Q13, Q14, Q15) all share one failure mode — Brain knows the answer, has the right source, but emits no/partial verbatim quotes. Fixing this alone moves brain mean from ~0.75 to roughly 0.85+.

**The secondary fix is a rubric calibration**, not a Brain change: 3/15 questions (Q10, Q11, Q12 refusal-with-alternatives) are wrongly penalised by the judge. Adjusting the rubric to allow truthful corpus alternatives after refusal recovers ~0.5+ on those three.

**Combined effect of the two fixes** (citation discipline + rubric calibration):
- Brain real-quality mean would be ~0.88, vs current measured 0.75
- Effective gap to gold: ~0.10 instead of ~0.14
- Six "citation failure" questions would jump from ~0.65 to ~0.90+
- Three "refusal" questions would jump from ~0.57 to ~0.90+

**Next-step priorities (user-validated):**
1. **Strengthen citation enforcement** — mid-priority prompt experiment, possibly with more emphasis or structural constraint on per-claim citations. Worth testing a "no claim without bracketed quote" hard rule.
2. **Fix rubric** to differentiate between training-data fabrication and truthful-corpus-alternatives-after-refusal.
3. NOT a priority: refusal behavior, retrieval mechanics — both are fine.

**Pattern emerging:** P2, P3, M2 — three different question types, same root cause: **Brain finds the right document(s) and knows the answer, but does NOT reliably emit per-claim verbatim quotes** in the `[Quelle: <basename> — "<wörtliches Zitat>"]` form. Citation discipline is the dominant failure axis, even when retrieval and precision are strong. M2 also shows a secondary failure where missing quotes correlate with light fabrication (timeline/role specifics not in source).

User is going through questions one-by-one in Brain UI; will accumulate validated findings here as we go.
