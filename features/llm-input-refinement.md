# Feature Proposal: LLM-Assisted Input Refinement

**Status:** Proposed
**Author:** Brain Agent Team
**Date:** 2026-03-20
**Effort:** Small-Medium (4-5 days)
**Priority:** Medium

---

## Problem

Users frequently type quick, rough messages that produce suboptimal results. The quality
of an LLM response is directly proportional to the quality of the input. Common patterns:

1. **Vague requests** -- "fix the bugs" instead of specifying which module, what symptoms,
   and what the expected behavior should be.
2. **Missing context** -- "make it faster" without specifying what "it" is, what the
   current performance is, or what target is acceptable.
3. **Ambiguous instructions** -- "update the config" without specifying which fields,
   what values, or why.
4. **Poor soul.md/memory quality** -- when editing agent personality or memory files,
   users write rough notes that could be more structured and effective.

This is a known problem in the LLM space. "Prompt engineering" is a skill, and most users
are not prompt engineers. A lightweight assist that refines rough input into well-structured
prompts would improve results across the board without requiring users to learn prompting
techniques.

## Proposed Solution

Add a **"Refine" function** accessible from any text input in the application. When
triggered, the current text is sent to a fast, cheap LLM for improvement. The user sees
the refined version and can accept, edit, or cancel.

### Refinement Targets

1. **Chat input** -- refine the message before sending to the agent.
2. **Soul.md editor** -- improve agent personality/instruction text.
3. **Memory editor** -- improve stored memory content.
4. **Workflow prompts** -- refine stage prompts in workflow definitions.
5. **Custom command templates** -- improve prompt templates.

### Refinement Modes

- **Expand** (default) -- add specificity, structure, and missing context to a rough
  message. Preserves the user's intent while making it clearer.
- **Concise** -- shorten a long message while preserving all key information.
- **Technical** -- add technical precision, proper terminology, and structured format.
- **Friendly** -- soften tone for external communications (emails, messages).

### Refinement Prompt (Internal)

The system sends this to the refinement model (not visible to the user):

```text
You are a prompt refinement assistant. Improve the following text while preserving
the user's original intent. Make it clearer, more specific, and better structured.

Rules:
- Do not change the meaning or add requirements the user did not express
- Add structure (numbered lists, sections) where helpful
- Expand vague terms into specific instructions
- Keep the same voice and tone
- If the text is instructions for an AI agent, optimize it for LLM comprehension
- Do not add pleasantries or filler

Mode: {{mode}}

Original text:
{{user_text}}

Refined text:
```

## Web UI Mockups

### Chat Input with Refine Button

```text
+-----------------------------------------------------------------------+
|  Chat with main                                                       |
+-----------------------------------------------------------------------+
|                                                                       |
|  [Agent response above...]                                            |
|                                                                       |
+-----------------------------------------------------------------------+
|                                                                       |
|  +---------------------------------------------------------------+   |
|  | make the auth faster and fix bugs                              |   |
|  |                                                               |   |
|  +---------------------------------------------------------------+   |
|                                                [Refine]  [Send ->]   |
|                                                                       |
+-----------------------------------------------------------------------+
```

### Refinement Preview Popup

```text
+-----------------------------------------------------------------------+
|                                                                       |
|  +---------------------------------------------------------------+   |
|  |                     Refine Message                             |   |
|  +---------------------------------------------------------------+   |
|  |                                                               |   |
|  |  Mode: [Expand v]  [Concise]  [Technical]  [Friendly]        |   |
|  |                                                               |   |
|  |  ORIGINAL:                                                    |   |
|  |  +-----------------------------------------------------------+|   |
|  |  | make the auth faster and fix bugs                         ||   |
|  |  +-----------------------------------------------------------+|   |
|  |                                                               |   |
|  |  REFINED:                                                     |   |
|  |  +-----------------------------------------------------------+|   |
|  |  | Optimize the authentication module for performance:       ||   |
|  |  |                                                           ||   |
|  |  | 1. Profile the auth flow to identify bottlenecks          ||   |
|  |  |    (token validation, session lookup, database queries)   ||   |
|  |  | 2. Implement caching where appropriate (session tokens,   ||   |
|  |  |    user lookups)                                          ||   |
|  |  | 3. Review and fix existing bugs in the auth module:       ||   |
|  |  |    - Check token expiration handling                      ||   |
|  |  |    - Verify session cleanup on logout                     ||   |
|  |  |    - Test edge cases (expired tokens, concurrent          ||   |
|  |  |      sessions, invalid credentials)                       ||   |
|  |  |                                                           ||   |
|  |  | Report what you find and what you changed.                ||   |
|  |  +-----------------------------------------------------------+|   |
|  |                             ^ editable                        |   |
|  |                                                               |   |
|  |         [ Cancel ]     [ Re-refine ]     [ Accept ]           |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
+-----------------------------------------------------------------------+
```

### Soul.md Editor with Refine

```text
+-----------------------------------------------------------------------+
|  Agent Config: Researcher                                             |
|  [Soul] [Settings] [Skills] [MCP] [Schedule] [Commands]              |
+-----------------------------------------------------------------------+
|                                                                       |
|  soul.md                                        [Refine with AI]      |
|  +---------------------------------------------------------------+   |
|  | # Researcher                                                  |   |
|  |                                                               |   |
|  | you are a research agent. look things up and write reports.   |   |
|  | use exa search and web fetch. be thorough. cite sources.      |   |
|  | when you find something interesting save it to memory.        |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
|                                                 [Cancel] [Save]       |
+-----------------------------------------------------------------------+
```

After clicking "Refine with AI":

```text
+-----------------------------------------------------------------------+
|  Agent Config: Researcher                                             |
|  [Soul] [Settings] [Skills] [MCP] [Schedule] [Commands]              |
+-----------------------------------------------------------------------+
|                                                                       |
|  soul.md (refined)                    [Undo Refinement] [Re-refine]   |
|  +---------------------------------------------------------------+   |
|  | # Researcher                                                  |   |
|  |                                                               |   |
|  | You are a specialized research agent focused on deep          |   |
|  | investigation and comprehensive analysis.                     |   |
|  |                                                               |   |
|  | ## Core Behavior                                              |   |
|  | - Use `exa_search` for web research and `web_fetch` to        |   |
|  |   retrieve full page content when needed.                     |   |
|  | - Be thorough: cross-reference multiple sources before        |   |
|  |   drawing conclusions.                                        |   |
|  | - Always cite sources with URLs and dates.                    |   |
|  |                                                               |   |
|  | ## Output Format                                              |   |
|  | - Structure reports with clear sections and headings.         |   |
|  | - Include a summary at the top and detailed findings below.   |   |
|  |                                                               |   |
|  | ## Memory                                                     |   |
|  | - Store key findings and interesting discoveries to memory     |   |
|  |   using `memory_store` for future reference.                  |   |
|  | - Tag memories with topic keywords for easy recall.           |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
|                                                 [Cancel] [Save]       |
+-----------------------------------------------------------------------+
```

### Memory Editor with Refine

```text
+-----------------------------------------------------------------------+
|  Memory: project_summary                                              |
+-----------------------------------------------------------------------+
|                                                                       |
|  +---------------------------------------------------------------+   |
|  | brain agent is a multi agent platform. it has a server on     |   |
|  | port 8420 and uses qmd for memory search. there are agents    |   |
|  | like researcher and coder. it supports telegram and web ui.   |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
|  [Refine with AI]                              [Cancel] [Save]        |
+-----------------------------------------------------------------------+
```

## TUI Mockups

### Ctrl+R Refinement in Chat Input

```text
> make the auth faster and fix bugs
                                                          [Ctrl+R to refine]

  (user presses Ctrl+R)

  Refining...

  +---------------------------------------------------------------+
  | REFINED (press Enter to accept, Esc to cancel, Tab to edit):  |
  +---------------------------------------------------------------+
  | Optimize the authentication module for performance:           |
  |                                                               |
  | 1. Profile the auth flow to identify bottlenecks (token       |
  |    validation, session lookup, database queries)              |
  | 2. Implement caching where appropriate                        |
  | 3. Review and fix existing bugs in the auth module:           |
  |    - Token expiration handling                                |
  |    - Session cleanup on logout                                |
  |    - Edge cases (expired tokens, concurrent sessions)         |
  |                                                               |
  | Report what you find and what you changed.                    |
  +---------------------------------------------------------------+

  (user presses Enter)

> Optimize the authentication module for performance: ...
  [sent]
```

### Refinement Mode Selection in TUI

```text
> update the deploy script

  (user presses Ctrl+R)

  Refine mode: [e]xpand  [c]oncise  [t]echnical  [f]riendly
  > e

  Refining...

  +---------------------------------------------------------------+
  | REFINED:                                                      |
  | Update the deployment script with the following improvements: |
  | ...                                                           |
  +---------------------------------------------------------------+
```

### Refining Soul.md via TUI

```text
> /soul edit

  Editing soul.md for Researcher...
  (opens in $EDITOR or inline editor)

  ...after editing...

  Refine before saving? [y/n] y

  Refining soul.md content...

  +---------------------------------------------------------------+
  | REFINED soul.md:                                              |
  | # Researcher                                                  |
  |                                                               |
  | You are a specialized research agent...                       |
  +---------------------------------------------------------------+

  Accept refined version? [y/n/e(dit)] y
  Saved.
```

## Model Selection for Refinement

Refinement needs to be **fast and cheap** since it runs before every refined message.
The model should be capable enough to improve text quality but not so expensive that
users hesitate to use it.

| Option | Speed | Cost | Quality | Recommendation |
|---|---|---|---|---|
| Local Crow-4B (oMLX) | ~1s | Free | Adequate | Good for offline/cost-free |
| Claude Haiku | ~1-2s | Very cheap | Good | Best balance for cloud |
| Claude Sonnet | ~2-4s | Moderate | Excellent | Overkill for refinement |
| GPT-4o-mini | ~1-2s | Very cheap | Good | Alternative to Haiku |

**Recommendation:** Use the model configured for `purpose: "fast"` in the smart model
routing config. This defaults to the cheapest available model. Users can override in
settings.

### Configuration

```text
# In config.json or models config
{
  "refinement": {
    "model": "auto",           // "auto" uses fast-purpose model
    "max_tokens": 1024,        // cap output length
    "enabled": true,           // global toggle
    "show_button": true        // show refine button in UI
  }
}
```

## Workflow Example

### Chat Refinement Flow

1. User types a rough message in the chat input box.
2. User clicks the "Refine" button (Web UI) or presses Ctrl+R (TUI).
3. The raw text is sent to the refinement model with the refinement system prompt.
4. The refined text appears in a preview popup (Web UI) or inline (TUI).
5. User reviews the refined text:
   - **Accept** -- refined text replaces the original and is sent.
   - **Edit** -- refined text is placed in the input for further manual editing.
   - **Cancel** -- original text is restored, nothing sent.
   - **Re-refine** -- send the refined text through another refinement pass.
6. The final text is sent as a regular chat message.

### Soul.md Refinement Flow

1. User opens soul.md in the agent config modal.
2. User writes or edits instructions (rough, informal style is fine).
3. User clicks "Refine with AI".
4. The entire soul.md content is sent to the refinement model with a specialized prompt
   that understands agent instruction formatting.
5. Refined version replaces the editor content with an "Undo Refinement" button.
6. User reviews, makes final edits, and saves.

### Memory Refinement Flow

1. User opens a memory document in the QMD document browser.
2. User writes or edits content.
3. User clicks "Refine with AI".
4. Refined content shown in the editor.
5. User accepts or reverts.

## Implementation Plan

### Phase 1: Core Refinement Engine (Day 1)

1. **Refinement function** in `claude_cli.py` -- accepts text, mode, and context type;
   returns refined text. Uses the configured fast model.
2. **Refinement prompts** -- different system prompts for chat messages, soul.md content,
   memory documents, and workflow prompts. Each tuned for the specific context.
3. **API endpoint** -- `POST /v1/refine` with body `{text, mode, context}` returning
   `{refined, model_used, tokens_used}`.

### Phase 2: Web UI - Chat Input (Days 2-3)

1. **Refine button** -- small button next to the send button in the chat input area.
   Only visible when there is text in the input.
2. **Preview popup** -- modal showing original vs refined text with mode selector,
   Accept/Edit/Cancel/Re-refine buttons.
3. **Loading state** -- spinner on the refine button while waiting for the model response.
4. **Keyboard shortcut** -- Ctrl+Enter or Cmd+Shift+R to trigger refinement.

### Phase 3: Web UI - Editors (Day 3)

1. **Soul.md editor** -- "Refine with AI" button above the editor. Replaces content
   with refined version and shows "Undo Refinement" button.
2. **Memory editor** -- same pattern as soul.md editor.
3. **Diff view** (stretch) -- show a side-by-side or inline diff of original vs refined.

### Phase 4: TUI (Day 4)

1. **Ctrl+R binding** -- in the prompt_toolkit input, Ctrl+R triggers refinement of
   the current input text.
2. **Inline preview** -- show refined text in a bordered box below the input with
   Enter/Esc/Tab controls.
3. **Mode selection** -- quick single-key mode picker (e/c/t/f) before refinement.

### Phase 5: Polish (Day 5)

1. **Settings** -- refinement model selection, enable/disable toggle, per-context
   enable/disable.
2. **Usage tracking** -- count refinements per session for analytics (stored locally).
3. **Prompt tuning** -- iterate on refinement prompts based on testing.
4. **Telegram support** -- `/refine` command that takes the replied-to message and
   returns a refined version (user copies and sends manually).

## Privacy Considerations

1. **Text leaves the device** -- if using a cloud model (Haiku, GPT-4o-mini), the user's
   draft text is sent to the provider. Users should be aware of this.
2. **Local model option** -- when using oMLX (Crow-4B), refinement stays fully on-device.
   This should be documented and configurable.
3. **No logging** -- refined text should not be stored in chat history or memory unless
   the user actually sends it. The refinement call is ephemeral.
4. **Opt-in** -- refinement is always user-initiated (button click or keyboard shortcut).
   Never automatic or suggested without the user asking.

## Cost Analysis

Assuming average input of 50 tokens and refined output of 150 tokens:

| Model | Input Cost | Output Cost | Per Refinement | 100/day |
|---|---|---|---|---|
| Crow-4B (local) | Free | Free | $0.00 | $0.00 |
| Claude Haiku | $0.25/M | $1.25/M | $0.0002 | $0.02 |
| GPT-4o-mini | $0.15/M | $0.60/M | $0.0001 | $0.01 |
| Claude Sonnet | $3.00/M | $15.00/M | $0.0024 | $0.24 |

At typical usage (10-30 refinements per day), the cost is negligible with Haiku or
GPT-4o-mini. Local Crow-4B is free but slightly lower quality.

## Benefits

1. **Better results** -- well-structured prompts consistently produce better agent
   responses. Refinement bridges the gap between what users type and what they mean.
2. **Lower barrier to entry** -- users do not need to learn prompt engineering techniques.
   The refinement model handles structure, specificity, and formatting.
3. **Improved agent instructions** -- soul.md files written casually can be refined into
   well-structured instructions that agents follow more reliably.
4. **Time savings** -- users spend less time crafting perfect prompts. Type rough, refine,
   send.
5. **Educational** -- seeing how rough text is refined teaches users what good prompts look
   like over time.
6. **Low risk** -- always user-initiated with preview. Users maintain full control. Cancel
   reverts to the original.
7. **Cheap** -- using a fast model, the cost per refinement is negligible. Local inference
   makes it free.

## Effort Estimate

| Component | Estimate |
|---|---|
| Refinement engine + prompts | 0.5 day |
| API endpoint | 0.5 day |
| Web UI - chat refine button + popup | 1.5 days |
| Web UI - editor integration (soul, memory) | 1 day |
| TUI - Ctrl+R + inline preview | 1 day |
| Settings + model config | 0.5 day |
| **Total** | **~5 days** |

## Open Questions

1. Should refinement be available in Telegram? It would require a two-step flow
   (`/refine` → preview → confirm) that may be awkward. Proposal: add as `/refine`
   reply command, low priority.
2. Should there be an "auto-refine" mode that always refines before sending? Proposal:
   no, this adds latency and cost to every message. Keep it opt-in.
3. Should refinement support images/attachments context? Proposal: not initially; text
   only for v1.
4. Should the refine button show a token/cost estimate before running? Proposal: no,
   the cost is too small to matter. Show model name only.
5. Should refinement history be kept (last N refinements) so users can revert to a
   previous version? Proposal: not for v1; the preview popup with undo is sufficient.
6. How to handle very long texts (full soul.md files)? Proposal: chunk if over 2000
   tokens, refine section by section, or warn the user about cost.
