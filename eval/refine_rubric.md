# Refine eval rubric

A judge model scores each refined output (OLD=polish vs NEW=engineer) for one draft
on four axes, 0.0–1.0. The judge sees: the original draft, the stated intent, the
purpose (chat_prompt/scheduled_task/soul), and the refined output.

## Axes

1. **clarity** (0–1) — Is the refined prompt unambiguous and well-formed? Would a reader
   know exactly what is being asked? Grammar/structure count here.

2. **intent_preserved** (0–1) — Did the refinement keep the user's ACTUAL goal, in their
   language? The Engineer tier is EXPECTED to add structure, output-format, success
   criteria, and an expert role — that is its job and is NOT a violation. The HARD GATE
   fires ONLY on **fabricated FACTS**: a specific filename/path/URL, a number, an API
   field, a library name, or a concrete requirement the user never mentioned and that
   the assistant could get *wrong*. Adding `[the relevant file]` is fine; inventing
   `index.html` is fabrication.
   - Score < 0.7 here = `intent_drift: true` and the sample FAILS — but reserve that ONLY
     for fabricated facts (above) or for dropping/changing the user's actual goal.
   - Adding unrequested structure/role/format to an already-complete prompt is NOT drift
     — it is a token_economy penalty (axis 4), not an intent violation. Keep
     intent_preserved ≥ 0.7 in that case.
   - Dropping a task the user clearly wanted (without noting it) IS intent loss → low score.

3. **actionability** (0–1) — Would an AI agent, handed this refined prompt, produce the
   RIGHT result on the first try? Are success criteria / scope / stop-conditions present
   where the task needs them? For `scheduled_task`, a missing stop-condition or a missing
   destructive-action guard on a dangerous task caps this at 0.4.

4. **token_economy** (0–1) — Is every word load-bearing? Penalize bloat, boilerplate,
   restated obvious context, and padding. A draft that was ALREADY clean/tight and comes
   back ~unchanged scores HIGH here (1.0). A short draft ballooned with ceremony scores low.

## Special cases the judge must honor

- **"already clean/tight/scoped" drafts** (CHAT4, SCHED3, SOUL2): the IDEAL output is
  near-identical to the input. Heavy rewriting of an already-good prompt is a FAILURE of
  token_economy AND often intent_preserved. Reward restraint.
- **reasoning-model drafts** (CHAT5): adding or keeping "think step by step" / CoT
  scaffolding for a reasoning-native target is WRONG → cap actionability at 0.5.
- **dangerous scheduled tasks** (SCHED2): no stop/human-review guard → actionability ≤ 0.4.
- **soul drafts**: switching voice (2nd→1st/3rd person), changing the agent's name/role,
  or inventing capabilities → intent_preserved < 0.7 (drift, fails).

## casual-lookup cases (mode: "casual_lookup")

Some drafts are casual factual questions whose answer comes from a quick web lookup
("what's the weather tomorrow", "today's EUR/USD rate"). The user wants a normal answer
from any decent source — NOT a forensic, citation-grade report. For these the CORRECT
refine is light cleanup only (fix spelling/casing/grammar, keep it one short line).

The failure mode to catch is **over-strictness**: the Engineer injecting precision or
officialness that RAISES the downstream agent's evidentiary bar so it refuses ordinary
web results instead of answering. Treat as over-strict (and FAIL the sample) any
injected demand for:
- precision/exactness words the user didn't ask for — "präzise", "genau", "exakt",
  "verbindlich", "precise", "exact", "to N decimal places", "real-time";
- an authoritative/official source requirement — "offizielle Quelle", "authoritative
  source", "official forecast", naming a specific authority the user never named;
- a rigid output spec (mandated fields/table/sections) on a one-line casual question.

Scoring for `casual_lookup`:
- **intent_preserved** = how well the output stays a CASUAL lookup. A light cleanup =
  1.0. Injecting precision/officialness/strictness = `intent_drift: true` (< 0.7) and
  the sample FAILS — even though no fact was fabricated and the prompt is short. The
  harm is real: it changes what counts as a satisfactory answer.
- **actionability** = would the agent ANSWER from a normal web result? A casual prompt =
  high. An over-strict prompt that would make the agent refuse / hunt for an official
  source = low (≤ 0.4).
- **token_economy** = a one-line casual question should come back as a one-line prompt.
- The judge is told `mode=casual_lookup` so it scores on this basis.

## ask-back cases (mode: "ask_back")

Some drafts are hopelessly under-specified (no file, no symptom, no definition of
the goal). For these the CORRECT refine output is a SHORT clarifying question (or a
prompt that explicitly asks the user to supply the missing detail) — NOT a confidently
sharpened prompt that invents the missing scope. For these cases:
- **intent_preserved** = how well the output AVOIDS inventing scope. A focused
  clarifying question = 1.0. An invented filename/redesign/spec = drift (< 0.7).
- **actionability** = would this output actually move the user forward? A good
  clarifying question that names exactly what's missing = high. A vague "can you
  clarify?" with no specifics = mid. An invented-scope prompt = low (it sends the
  agent off in a guessed direction).
- The judge is told `mode=ask_back` in the sample so it scores on this basis.

## Output format (judge returns strict JSON)

```json
{
  "clarity": 0.0,
  "intent_preserved": 0.0,
  "actionability": 0.0,
  "token_economy": 0.0,
  "intent_drift": false,
  "note": "one sentence justification"
}
```

`intent_drift` = `true` whenever intent_preserved < 0.7. `overall` is computed by the
harness as the mean of the four axes, forced to 0.0 when `intent_drift` is true.

## Pass bar (computed by harness, not the judge)

- NEW (engineer) mean clarity AND actionability ≥ OLD (polish), aggregated over cases.
- ZERO intent_drift regressions: no case may go from drift=false (old) to drift=true (new).
- NEW total tokens ≤ 1.5× OLD on the "already clean" cases (CHAT4/SCHED3/SOUL2).
