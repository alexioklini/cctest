# Brain-Agent User Manual

Plain-language guide to the web UI. Read this when the user asks
"how do I…" about anything they can see on screen, or wants a walkthrough
for a real task ("translate a docx", "compare two Excels", "set up a
recurring report"). Don't quote this verbatim — extract the answer to
their specific question and skip the rest.

When the user asks you to **do** the task instead of explaining it,
switch to `04-recipes.md` and execute.

---

## The interface at a glance

**Left sidebar** — main navigation:
- **New chat** — opens the welcome / composer view
- **Search** — fuzzy search across all your chats
- **Chats** — every chat, newest first; archive/star/rename from the row menu
- **Projects** — knowledge bases with their own memory
- **Favourites** — pinned chats, artifacts, prompts, images
- **Artifacts** — every file produced or uploaded across sessions
- **Scheduled** — recurring tasks (cron-like)
- **Workflows** — multi-step automations with approval gates
- **Translation** — text / document / audio / live mic
- **Data** — placeholder for now (feature in development)
- **Settings (gear)** — agent + general settings (most are admin-only)

**Main area** — whatever view the sidebar selected. Welcome view shows
the greeting + composer + prompt cards.

**Right panel** (toggle top-right) — three tabs:
- **Attachments** — files you uploaded into this chat
- **References** — sources the model read this turn (web fetches, doc reads)
- **Files** — artifacts the model produced this turn

**Status bar** (bottom) — connection dot, agent, model, In/Out tokens,
speed, context-window fill, session cost, plan usage, warmup state,
provider queue. Click any of them for details.

**Composer toolbar** (above the textarea, left side):
- 📎 attach files
- 🧠 thinking level (off / low / medium / high — only on models that support it)
- 🔬 research mode override (project chats only)
- ✨ refine — polish the draft before sending
- 🔧 show/hide tool calls in the transcript
- 🛡️ GDPR details (hide / show full PII findings inline)
- 💾 save-to-memory cycle (off / on / auto)
- 🪨 caveman mode (terse responses)
- (extra badges appear when GDPR action is set or PII history present)

---

## Sending a message

Type, hit `Enter` (Shift+Enter for newline), or click the send button.
While the model is responding the send button becomes a stop button.

**Attachments**: click 📎 or drag files into the composer. Supported types:
images, PDF, docx, xlsx, pptx, eml/msg, epub, txt/md, csv/tsv, json, source
code (py/js/ts/go/rs/etc.), zip. The model receives images directly if it
has vision; everything else is converted to markdown server-side and read
with `read_document`.

**Models**: switch via the model badge in the status bar or composer.
Local models stay on-device; cloud models hit the configured provider.

**Cancel / re-attach**: hit stop to cancel a turn. If you close the
browser tab mid-turn, reopen the chat — the worker keeps running and
the transcript catches up.

---

## Chats

Click **Chats** to list everything. Per-row menu (⋯):
- Rename, star, archive, delete
- Set project (move into a project)
- Share — change visibility (private / specific users / team / global)
- Memorize / purge specific turns — pick which messages get filed to memory

**Search** (sidebar): semantic + keyword across all messages you can see.

**Archive ≠ delete**: archived chats are hidden from the list but their
memory drawers stay intact. Delete wipes everything including drawers.

**Save-to-memory** (composer button or per-chat setting):
- **off** — nothing from this chat enters MemPalace
- **on** — every turn is mined into your private wing
- **auto** — server classifier decides per-message (facts / decisions /
  references kept; chitchat skipped)

For pinpoint control, use the per-turn 🌐 menu next to any message:
*memorize this / memorize complete / purge this / purge above / purge below*.

---

## Projects

A project is a knowledge base with its own private memory and its own
attached files. Use a project when you want the AI to consistently
draw from a specific document corpus (policies, manuals, codebases,
research papers, …).

**Create a project**:
1. Sidebar → **Projects** → ＋ new
2. Name it. Optional: add a description and an image.
3. **Input folders** — point at on-disk directories that should be mined.
   Recurse + auto-sync flags per folder.
4. **Ingest** — upload files via the project page (multipart drag-drop).
5. **Research mode** toggle — see below.

**Research mode** (per-project default, per-chat override via the 🔬
composer button):
- **ON** — for policy / compliance / Q&A projects. The model is required
  to consult project memory first, refuses on empty retrieval, must cite
  per-claim with verbatim quotes. Citation validator runs server-side.
- **OFF** — for codegen / drafting / build-with-context projects. Memory
  available but not mandatory; the model can fall back on its training.

**Sync**: input folders are mined by a daemon every 6h. Buttons on the
project page:
- **Sync now** — mine new/changed files immediately
- **Full resync** (admin-only style) — wipe the project's memory wing
  and re-mine everything
- **Sync history** — past runs + per-file results
- **Knowledge graph** — drill into extracted entity-relation triples
  (only meaningful for normative documents like policies / regulations)

**Project chats**: starting a chat from a project page auto-scopes
memory queries to that project. The model sees the project's
`instructions` field plus the appropriate research-mode discipline.

---

## Translation

Sidebar → **Translation**. Four tabs:

### Text tab
- Paste text on the left, translation appears on the right.
- Source language auto-detects; manual override via the **From** pill.
- **Glossary** dropdown — apply a saved term list (consistent terminology).
- **Tone** dropdown — formal / casual / technical / etc.
- 🔊 buttons read the source or translation aloud (TTS).
- Swap arrows flip source↔target.

### Document tab
- Drag-drop a `.docx`, `.pptx`, or `.pdf`.
- Pick **From** and **To** languages.
- Click **Translate**. PDF is converted to docx first.
- Result appears in **History** below — click **Download** to save.
- Formatting (headings, tables, footnotes, slide layouts) is preserved
  in-place via chunked OOXML editing.

### Audio / Video tab
- Drag-drop audio or video. Voxtral transcribes + translates.
- **Mode** selector: subtitles (SRT/VTT), transcript (TXT), or both.
- History row has separate download buttons per output format.

### Live Microphone tab
- Click record. Speak. Translation appears as you talk.
- **Mode**: live captions vs. sentence-by-sentence chunks.
- Stop button finalizes; downloadable as SRT/VTT/TXT.

### Glossaries
- Top-right **Glossaries** button opens the modal.
- Create per-language-pair term lists for consistent translation across
  documents (especially for legal / technical / brand terminology).

---

## Scheduled tasks

Sidebar → **Scheduled** → ＋ new. A scheduled task is a prompt that runs
on a cron schedule with full tool access. Results are saved as artifacts
and visible in the task's history.

**Create**:
- **Name** — slug (used for cancel / run-now)
- **Task** — the prompt the agent gets. Be specific about the deliverable.
- **Schedule** — cron expr (`0 8 * * *` = daily 08:00) or `@every 30m`.
- **Model** — pick. Local models cost nothing; cloud cost the daily quota.
- **Timeout** — seconds before the run is killed (default 300; raise for
  long jobs).
- **Attachments** (optional) — files the task can read (same files reused
  every fire, not per-run copies).
- **Working dir** — overrides the task's cwd.
- **Tool profile**:
  - blank = research-minimal (fewer tools, faster, cheaper)
  - `interactive` = full toolkit (use for "do real work" tasks)
- **Thinking level** + **Caveman chat** — per-task overrides if needed.

**Manage**: filter tabs at top (All / Running). Per-row buttons let you
**run now**, **pause / resume**, **edit**, **delete**, **clear history**,
or open run detail (output + cost + artifacts + traces).

**Artifacts from a scheduled run** appear in the Artifacts view tagged
with the synthetic session `sched-<run_id>` and in the run-detail panel.

---

## Workflows

Sidebar → **Workflows**. Multi-step automations: each step is a prompt
or an `ask_user` gate. Use these when a recurring task needs human
approval at certain points (e.g. "draft the email" → human approves →
"send via gmail_send").

Create from the editor: name, description, ordered steps with optional
file uploads and `ask_user_for_file` / `ask_llm` blocks. Run from the
Workflows view; the **Executions** panel shows live state with
approve / cancel buttons.

---

## Artifacts

Sidebar → **Artifacts**. Two views:
- **Grid**: all output files (md, html, pdf, images) across every session.
  Defaults to **outputs only** (hides intermediates like `.py` / `.csv`
  the model wrote as scratch).
- **Browse**: directory view under `agents/<id>/artifacts/`.

**Per-artifact actions** (right panel when a chat is open + an artifact
is selected): preview, view source, copy, download, share.

Every write/edit creates an **artifact version** (5 MB cap). Open the
artifact panel → version dropdown to compare.

---

## Favourites

Pin anything for quick access: chats, artifacts, prompts, images.
Sidebar → **Favourites** lists them. Click ★ in any list row or use the
share menu.

---

## Settings

Gear icon, bottom-left. Two modals depending on role:

### Agent settings (admin)
- **Soul** — the agent's persona (markdown)
- **Agent** — `agent.json`: tool_groups, token_config, rate_limits, team
- **Skills** — install zip / browse Claude Code plugins / enable per-agent
- **MCP** — connect MCP servers
- **Tokens** — per-tool overrides + per-agent compact threshold
- **Hooks** — event hooks (pre/post tool, pre/post turn)
- **Schedule** — agent's own memory_summary daemon config

### General settings (admin)
- **Server** — default model, chat-summary model, ports
- **Providers** — add/edit/test OpenAI-compatible providers
- **Nodes** — distributed compute peers
- **Models** — per-model config (warmup, thinking, profile, cost)
- **Agents** — list + create
- **Teams** — team CRUD + ACLs
- **Costs** — global cost view per user/model/day
- **Quotas** — per-user daily + cycle limits + enforcement mode
- **GDPR** — PII scanner, category actions, NER models, rule overrides
- **Context** — LCM (lossless context manager) thresholds
- **MemPalace** — chat-sync classifier, wing rules
- **Knowledge Graph** — extraction profile + closet config
- **Tools** — per-tool enable/defer/purpose + prose
- **Research mode** — discipline texts (refusal / precision / citation)

### Account (any user)
User menu (top-right avatar):
- **Profile** — display name, email
- **Preferences** — greeting name, job description, communication
  preferences, memory defaults, daily summary on/off + hour
- **Password** — change own
- **Profile doc** — the auto-maintained user profile markdown; can
  trigger update or reset

---

## FAQ

**Q: Why is the model dropdown suddenly limited to local models?**
A: GDPR scanner found PII in the chat draft or history, and "server
block" is on for that category. Either remove the PII, switch to a
local model (data stays on-device), or — if you're admin — change the
category action in Settings → GDPR from `block` to `warn`.

**Q: Why did the response stop with "Sidecar error…"?**
A: The sidecar subprocess (the part that runs the LLM loop) is down.
Restart it: Settings or `POST /v1/sidecar/restart`. If chat keeps
failing, tail `~/.brain-agent/pi-sidecar.log`.

**Q: My scheduled task says "running" forever.**
A: Either it hit the timeout (raise it in the schedule edit) or it
deadlocked on `ask_user_for_file` / `ask_user` (scheduled tasks can't
prompt). Use **cancel** on the row and rewrite the task without
interactive tools.

**Q: I don't see my project memory in chat.**
A: Three checks:
1. Is the chat actually inside that project? Open the chat → project
   badge should show the project name.
2. Has the project synced? Open the project → **Sync status**.
3. Is research_mode set correctly? Q&A use cases need it ON; codegen
   keeps it OFF.

**Q: The chat says "Context window is getting full".**
A: Click **Compact now** in the warning banner, or the ✂️ icon in the
status bar. The LCM (Lossless Context Manager) will summarize older
turns; nothing is lost (originals stay searchable) but the live
conversation shrinks.

**Q: Translation lost my formatting.**
A: For `.docx` / `.pptx`, formatting is preserved in-place. For PDFs,
the result comes back as `.docx` (PDFs aren't directly editable). If
the source PDF was scanned (image-based), OCR ran first and some
layouts may have been lost — there's no fix for that on the PDF side.

**Q: How do I get the model to cite its sources?**
A: Use a project with **research mode ON**. The model is then required
to cite per-claim with verbatim quotes from the project's documents,
and the server-side validator catches uncited claims.

**Q: My quota turned red.**
A: Either daily or monthly cap exceeded. Click the status-bar quota
pill for breakdown. Enforcement mode determines behavior:
- `warn_only` — you keep going, just visible warning
- `force_local` — cloud models silently swap to a local fallback
- `hard_block` — chat refuses until reset

**Q: The model picked the wrong document.**
A: Open the right-panel **References** tab to see which files it read.
If it grabbed the wrong one, point it explicitly: *"Read
`projects/X/ingested/<filename>.pdf` and answer from there only."*

**Q: How do I share a chat with my team?**
A: Chat menu (⋯) → **Share** → set visibility to **Team**. They'll see
it under Chats with your name as owner. For workflows, projects,
schedules, artifacts — same share menu wherever the object is listed.

**Q: How do I recover from "PII detected — choose action"?**
A: The pre-send modal offers:
- **Stay** (block, edit yourself)
- **Proceed local** (route to a local model; data stays on-device)
- **Proceed pseudonymized** (PII replaced with tokens; an admin-only
  decrypt map is saved in case you need to audit later)
- **Whitelist** — for one-off allowlisting (e.g. your own email
  address in your own messages)

**Q: Can the agent see what I uploaded?**
A: Yes — uploads land in the chat's session folder. Reachable via
`read_document` (rich formats) or `read_file` (plain text). The right
panel **Attachments** tab shows everything available.

**Q: Why is the same prompt cheaper after the first run?**
A: Prompt cache + warmup KV-prefix. The first turn of a fresh session
warms the cache; subsequent turns are faster and cheaper as long as
the system prompt stays stable.

---

## Recipe: translate a Word document

1. Sidebar → **Translation** → **Document** tab.
2. Drag your `.docx` into the drop zone (or click to pick).
3. **From**: leave as Auto-detect or pick explicitly. **To**: target language.
4. Optional: pick a glossary (consistent terminology) and tone.
5. Click **Translate**. Progress bar shows chunk-by-chunk progress.
6. When done, **Download** appears next to the file in History below.
   Original formatting (headings, tables, footnotes) is preserved.

**Tip**: For long documents, the chunking is automatic. If you have a
multi-language glossary (legal terms, brand names), create it once via
the **Glossaries** modal and reuse across documents.

**If PDF**: drop the PDF in the same tab. It's converted to `.docx`
first, then translated. The output is `.docx`, not `.pdf` — PDFs are
not round-trippable.

---

## Recipe: compare two Excel files (do column X differ?)

This is a chat task, not a Translation feature. Workflow:

1. Open a new chat. Pick a model that handles code well (any
   reasoning-capable model is fine).
2. 📎 Attach both `.xlsx` files.
3. Prompt:
   > Compare `file_a.xlsx` and `file_b.xlsx`. They each have a column
   > `customer_id`. List rows where the value of column `amount`
   > differs between the two files for the same `customer_id`. Output
   > a CSV with columns `customer_id, amount_a, amount_b, delta`.

4. The agent reads both with `read_document` (or `python_exec` +
   pandas if it picks that path), produces the comparison, and saves
   the CSV as an artifact you can download from the right panel
   **Files** tab.

**Tip**: If the files are large or sheet structure is unusual, ask for
a header preview first: *"Show me the first 3 rows of each so we agree
on column names."* Then issue the comparison.

**Tip**: For a recurring comparison (e.g. nightly diff of two reports),
make it a scheduled task. Attach both files to the schedule, prompt
identical to above, schedule `0 7 * * *`, tool_profile `interactive`.

---

## Recipe: set up a daily email summary

1. Sidebar → **Scheduled** → ＋ new.
2. Fill in:
   - **Name**: `daily_inbox_summary`
   - **Task**:
     > Use `gmail_search` to find unread messages from the last 24
     > hours. For each thread that looks like it needs a reply, list
     > the sender, subject, and one-sentence reason. Skip newsletters
     > and notifications. Output as a markdown bullet list.
   - **Schedule**: `0 8 * * *`
   - **Model**: any capable model (locally hosted is fine — Gmail
     content stays cheaper)
   - **Tool profile**: `interactive` (needs gmail tools)
3. Save. Click **run now** to test once. Check the run detail for the
   output.
4. If the output is good, leave it. It'll fire daily at 08:00.

**Tip**: Pipe the result somewhere actionable — modify the task to
`gmail_send` a summary email to yourself, or write a markdown file to
your Notes folder.

---

## Recipe: build a project that answers from a folder of PDFs

1. Sidebar → **Projects** → ＋ new. Name it (e.g. `gdpr_policies`).
2. Open the project. **Add input folder** → point at the directory
   containing the PDFs. Check **recursive** if subdirs matter.
3. Click **Sync now**. Wait for the sync to complete (status panel
   shows progress; large folders take minutes).
4. (Optional) Knowledge graph extraction runs automatically if
   enabled; that takes longer. Watch the **Knowledge Graph** button on
   the project page once it lights up.
5. Make sure **Research mode** is ON (top of the project page).
6. Click **New chat** from the project. Ask your question.
7. The model is now required to cite per-claim; you'll see
   `[Quelle: ... — "..."]` brackets in the response. Click any to
   verify against the source.

**Tip**: If the model refuses with "no relevant memory", broaden your
query, or check that sync actually ingested the documents (project →
**Docs** tab lists them).

**Tip**: Re-sync after adding/removing files. Use **Full resync** only
when document content changes drastically (renames, restructured PDFs)
— it's expensive.

---

## Best practices

**For chat:**
- Set save-to-memory to **auto** for general chats — the classifier
  keeps useful facts, drops chitchat.
- For high-signal projects (research, decisions you'll reference later),
  switch to **on** and review per-turn from the 🌐 menu.
- Use **refine** (✨) when your draft is messy — it polishes without
  changing meaning.
- Caveman mode is for "give me one line, nothing else."

**For projects:**
- Research mode ON is the right default for Q&A; OFF for codegen.
- Keep `instructions` short — it goes into the system prompt every turn.
- Trust the citation validator — if the model produces uncited claims
  in a research-mode project, server-side checks catch it and request
  a re-round.

**For scheduled tasks:**
- Be specific about deliverable format ("output a markdown table",
  "save to `report.md`"). Vague tasks produce vague output.
- Start with `tool_profile=""` (research-minimal). Promote to
  `interactive` only if the task genuinely needs the full toolkit.
- Test with **run now** before letting cron run it.
- Pin a model — don't leave it on "default" if you care about cost.

**For attachments:**
- For binary files (PDF, docx, xlsx, …), the model uses `read_document`
  which goes through markitdown / Mistral OCR. Quality varies; if a
  table comes out garbled, ask for a re-read with explicit pagination.
- Image-based PDFs need OCR (slow). Convert to text PDFs upstream if
  you can.

**For memory:**
- Project memory (when research mode is on) is the highest-quality
  signal. Use projects, don't try to teach the agent via long chats.
- Your **user profile** is auto-maintained from your chat activity —
  if it gets something wrong, edit `agents/main/user_profiles/<uid>.md`
  directly or click "reset" in Profile doc.

**For privacy:**
- Local models = data never leaves the host. The status bar shows a
  badge for local vs cloud.
- GDPR scanner runs before every cloud send. Trust it; tune category
  actions if it's too aggressive.

---

## Tips & tricks

- **`/`-commands**: type `/` in the composer to open the slash menu —
  agent commands, search, recent prompts.
- **`@`-mentions**: in a team chat, `@username` notifies that user.
- **Drag-drop**: works on the welcome composer, in-chat composer, and
  the project ingest area.
- **Multi-select**: hold `Cmd/Ctrl` and click multiple chats to
  archive / delete / set project in bulk.
- **Status bar context bar** fills as the conversation grows. At 60%
  the LCM warning appears; at 80% you'll hit truncation. Compact early.
- **Cost preview**: hover the model badge in the composer — it shows
  per-1K-token cost. Useful before sending a long context to an
  expensive model.
- **Speed badge**: tells you tokens/sec. If a local model is slow,
  check warmup status (status-bar warmup pill) — first turn after a
  cold start is always slower.
- **Reattach**: closing the browser tab mid-stream doesn't cancel.
  Reopen the chat and it picks up the live stream.
- **Translation glossaries** apply across all four translation tabs —
  build them once.
- **Schedule + workflow combo**: schedule the workflow, not individual
  steps. The workflow handles approval gates; the schedule fires it
  on cron.
- **Hidden right panel**: top-right toggle. Auto-opens when a tool
  produces a file.
- **Inspect** (🔍 in status bar): when something looks weird, the
  inspect modal shows model, system prompt size, message count, token
  budget — fastest way to spot a misconfiguration.
- **Search before chat**: sidebar **Search** is full-text + semantic
  across every chat you can see. Often the answer you want is in a
  chat you already had.

---

## When to use what

| Goal | Use |
|---|---|
| Quick question, no memory needed | New chat, any model |
| Q&A from a document corpus | Project + research mode ON + ingest the docs |
| Build code, draft text with context | Project + research mode OFF |
| One-off file conversion / extraction | Chat with attachment |
| Translate a document | Translation → Document tab |
| Transcribe + translate audio | Translation → Audio/Video |
| Recurring task ("every day…") | Scheduled |
| Recurring task with approval gates | Workflow + schedule |
| Cross-chat search | Sidebar Search |
| Pin something for fast access | Favourite (★) |
| Share work with team | Share menu → Team |
| Privacy-sensitive content | Local model + GDPR scanner ON |
