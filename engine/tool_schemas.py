"""engine/tool_schemas.py — pure tool-schema data literals.

Extracted from brain.py (refactor E2). This module is PURE DATA: the
Anthropic-shape `TOOL_DEFINITIONS` list, the auto-derived OpenAI-shape
mirror `TOOL_DEFINITIONS_OPENAI`, and the two name→def lookup indices.
Zero function calls, zero brain runtime — imports NOTHING from brain.

brain.py re-exports all four names so `brain.TOOL_DEFINITIONS` and every
bare-name use inside brain.py (resolve_active_tools, _render_tool_descriptions,
warmup, get_tool_breakdown) still resolve. The import in brain.py is one-way
and cycle-free.

Adding a new tool still edits brain.py for TOOL_GROUPS / the tool_*()
function / TOOL_DISPATCH — only the schema dict moves here.
"""


TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file. Returns the full text content. "
            "Use offset and limit to read a specific range of lines from large files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path to read"},
                "offset": {"type": "integer", "description": "Line number to start reading from (1-based, default: 1)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to read (default: all)"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file or overwrite an existing file with the given content. "
            "Creates parent directories automatically if they don't exist. "
            "Use a RELATIVE filename (e.g. `report.docx`) so the file lands in the "
            "session's artifact folder and auto-promotes to the Artifacts panel. "
            "WRITES ARE RESTRICTED TO THE ARTIFACT FOLDER: an absolute path, or a "
            "relative path containing '..', that resolves OUTSIDE it is REFUSED with "
            "an error — pass a plain relative filename, never an absolute path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative filename for the output (lands in your session artifact folder). Absolute paths and '..' escapes are refused."},
                "content": {"type": "string", "description": "The full content to write to the file"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": ["path", "content"],
        },
        # research_minimal: harness-style lean purpose. `minimal` flags the
        # tool as participating; `minimal_role` is the one-line description
        # of its role in the workflow, composed into the dynamic prompt.
        "minimal": True,
        "minimal_role": "to save the final deliverable",
    },
    {
        "name": "edit_file",
        "description": (
            "Edit an existing file by replacing an exact string match with new content. "
            "The old_string must match exactly (including whitespace/indentation). "
            "Use replace_all=true to replace every occurrence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "old_string": {"type": "string", "description": "Exact string to find and replace"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences (default: false)"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "ask_llm",
        "description": (
            "One-shot LLM call. Returns plain text from the model — no tools, no agentic loop. "
            "For deterministic text transformations: summarisation, extraction, rewriting. "
            "Returns: {text, model}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "User message / instruction"},
                "system": {"type": "string", "description": "Optional system prompt"},
                "model": {"type": "string", "description": "Model id (defaults to refinement model)"},
            },
            "required": ["prompt"],
        },
        "primary_field": "text",
    },
    {
        "name": "agent_step",
        "description": (
            "Run ONE bounded agentic LLM turn as a workflow step. The model gets the "
            "instruction (plus optional plan context and input files) and works it off "
            "with tools (read/write files, web, python) inside the workflow's shared "
            "workspace. Use from .flow scripts for judgment-heavy plan steps; the .flow "
            "script stays the deterministic spine. Pass skill=\"<slug>\" to run a SAVED "
            "skill as the method — the skill's instructions become the plan, so the "
            "workflow references a reusable procedure instead of inlining it. "
            "Returns: {text, model, rounds, files}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "What this step must do (a plan section or the whole plan)"},
                "plan": {"type": "string", "description": "Optional full plan markdown injected as context"},
                "skill": {"type": "string", "description": "Optional slug of a saved skill to run as this step's method (loaded as the plan; resolves the workflow owner's visible skills)"},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Input file paths (uploads, outputs of earlier steps)"},
                "model": {"type": "string", "description": "Model id (defaults to the workflow MODEL header)"},
                "max_rounds": {"type": "integer", "description": "Agentic round cap (default 16, max 24)"},
                "expected_output": {"type": "string", "description": "Required result shape (e.g. 'Markdown-Report mit Abschnitten X, Y')"},
            },
            "required": ["instruction"],
        },
        "primary_field": "text",
    },
    {
        "name": "transcribe_audio",
        "description": (
            "Transcribe an audio file (.wav/.mp3/.m4a/.flac/.ogg) to text. "
            "Routes to any model in the models config flagged with the 'audio_transcription' capability — "
            "cloud Voxtral-mini, local Whisper, or anything else added with that flag. Falls back to "
            "the configured local fallback when GDPR block_unscannable_on_cloud is on or the cloud call errors. "
            "Optionally translate the transcript by passing translate_to (ISO 639-1). "
            "Returns: {transcript, language, duration_s, model, file, [translation, target_lang], [fallback_used]}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Absolute path to the audio file"},
                "language": {"type": "string", "description": "ISO language code (e.g. 'en', 'de'); auto-detect when omitted"},
                "model": {"type": "string", "description": "Any model id whose capabilities list includes 'audio'. Defaults to transcribe_audio.default_model."},
                "translate_to": {"type": "string", "description": "Optional ISO 639-1 code. When set, the transcript is translated into this language and returned alongside it."},
                "glossary": {"type": "string", "description": "Optional glossary slug for the translation step. Ignored if translate_to is empty."},
            },
            "required": ["file"],
        },
        "primary_field": "transcript",
    },
    {
        "name": "generate_audio_overview",
        "description": (
            "Generate a NotebookLM-style AUDIO OVERVIEW (a two-host podcast .mp3). "
            "Two AI hosts (Oliver & Jane) discuss the material in a natural, engaging "
            "conversation, synthesised to speech and stitched into one audio file. "
            "Use when the user asks for a podcast, audio overview, audio summary, or "
            "'listen to' version. SOURCE: in a PROJECT it discusses the project's "
            "sources; OUTSIDE a project it discusses the CURRENT CHAT's conversation "
            "(so any chat can become a podcast). AUDIO IS ENGLISH-ONLY regardless of "
            "source language (TTS voice constraint). Saves a .mp3 (the podcast) plus "
            "a .md (the dialogue script) to the session artifact folder. Returns: "
            "{status, audio_file, script_file, spoken_lines, hosts}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Optional focus — what within the project the conversation should centre on. Omit for a broad overview."},
                "audience": {"type": "string", "description": "Optional target audience to pitch the explanation level (e.g. 'beginners', 'compliance officers')."},
                "length": {"type": "string", "enum": ["short", "std", "long"], "description": "Episode length: short (~2-3 min), std (~5-7 min), long (deep dive). Default std."},
            },
            "required": [],
        },
        "primary_field": "audio_file",
    },
    {
        "name": "translate_text",
        "description": (
            "Translate a text passage into another language using the configured Mistral model. "
            "Auto-detects the source language when source_lang is omitted. Optionally applies a glossary "
            "for bank-specific terminology. Preserves formatting (line breaks, lists, markdown). "
            "Returns: {translation, source_lang, target_lang, detected, model, glossary, noop}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Source text to translate."},
                "target_lang": {"type": "string", "description": "Target language as ISO 639-1 (e.g. 'en', 'de', 'fr')."},
                "source_lang": {"type": "string", "description": "Optional source language ISO 639-1. Auto-detected when omitted."},
                "glossary": {"type": "string", "description": "Optional glossary slug. Use list_glossaries to discover available ones."},
                "model": {"type": "string", "description": "Optional model id override. Defaults to tools_config.translation.default_model."},
                "tone": {"type": "string", "description": "Optional tone hint, e.g. 'formal', 'plain', 'marketing'."},
            },
            "required": ["text", "target_lang"],
        },
        "primary_field": "translation",
    },
    {
        "name": "detect_language",
        "description": (
            "Detect the language of a text snippet. Uses the offline lingua detector and falls back "
            "to a tiny LLM call for very short or ambiguous inputs. "
            "Returns: {lang, confidence, source}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to inspect."},
            },
            "required": ["text"],
        },
        "primary_field": "lang",
    },
    {
        "name": "list_glossaries",
        "description": (
            "List all stored translation glossaries. "
            "Returns: {glossaries: [{slug, name, description, source, target, entry_count, do_not_translate_count}]}."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_glossary",
        "description": (
            "Read a translation glossary by slug. "
            "Returns: {glossary: {slug, name, description, source, target, entries[], do_not_translate[]}}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Glossary slug (from list_glossaries)."},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "translate_document",
        "description": (
            "Translate a DOCX, PPTX, or PDF document into another language. "
            "DOCX/PPTX preserve original layout (text replaced in place inside the OOXML). "
            "PDFs are converted to DOCX (no library round-trips PDF without breaking layout) — "
            "the result file's extension changes to .docx accordingly. "
            "The translated file is written into the current chat's artifact folder and "
            "appears in the artifact panel automatically. "
            "Returns: {output_path, format, runs, source_lang, target_lang, glossary, model, fallback}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the source .docx/.pptx/.pdf file."},
                "target_lang": {"type": "string", "description": "Target language as ISO 639-1."},
                "source_lang": {"type": "string", "description": "Optional source language ISO 639-1. Auto-detected when omitted."},
                "glossary": {"type": "string", "description": "Optional glossary slug. Use list_glossaries to discover available ones."},
                "model": {"type": "string", "description": "Optional model id override. Defaults to tools_config.translation.default_model."},
            },
            "required": ["path", "target_lang"],
        },
        "primary_field": "output_path",
    },
    {
        "name": "ask_user_for_file",
        "description": (
            "Pause execution and ask the user to upload a file. "
            "The frontend opens a file picker; this call blocks until a file is uploaded or cancelled. "
            "Returns: {path, filename, size_bytes}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Message shown to the user explaining what to upload"},
                "accept": {"type": "string", "description": "File type filter, e.g. 'audio/*' or '.wav,.mp3' (optional)"},
            },
            "required": ["prompt"],
        },
        "primary_field": "path",
    },
    {
        "name": "list_directory",
        "description": (
            "List files and directories at a given path. "
            "Supports glob patterns (e.g. '*.py', '**/*.js'). "
            "Returns file names, sizes, and types."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list (default: current directory)"},
                "pattern": {"type": "string", "description": "Glob pattern to filter results (e.g. '*.py', '**/*.ts')"},
                "recursive": {"type": "boolean", "description": "List recursively (default: false)"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": [],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search for a regex pattern across files. Returns matching lines with file paths and line numbers. "
            "Similar to grep/ripgrep. Use glob to filter which files to search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in (default: current directory)"},
                "glob": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.py')"},
                "case_insensitive": {"type": "boolean", "description": "Case-insensitive search (default: false)"},
                "max_results": {"type": "integer", "description": "Maximum number of matches to return (default: 50)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "execute_command",
        "description": (
            "Execute a shell command and return its output (stdout + stderr). "
            "Commands run in the current working directory with no TTY (non-interactive). "
            "IMPORTANT: Only use non-interactive commands. For example use 'top -l 1' (not 'top'), "
            "'ps aux' (not 'htop'), 'cat' (not 'less'). "
            "Use this for: running scripts, git commands, package managers, compiling, testing, "
            "system administration, or any shell operation. "
            "The working directory is the session's artifact folder — write output files with a "
            "RELATIVE filename so they auto-promote to the Artifacts panel; avoid absolute paths "
            "unless the user gave you one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory for the command. Default: the current session's artifact folder (files written there auto-promote to the Artifacts panel)."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 120)"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "python_exec",
        "description": (
            "Execute Python code in a sandboxed subprocess. "
            "Use for computation, data transformation, file processing, math, JSON/CSV parsing, "
            "or any task that benefits from writing code instead of chaining multiple tool calls. "
            "Standard library is fully available. Packages from the configured venv are available if set. "
            "The working directory is the session's artifact folder — any files you write there "
            "(e.g. open('results.txt','w')) become viewable artifacts for the user. "
            "For large results, WRITE them to a file instead of printing to stdout. "
            "Print only a short summary to stdout. Stdout is returned as the tool result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute. Use print() for output. Write large results to files instead of printing."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: from config, typically 30)"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "web_fetch",
        "description": (
            "Fetch content from a URL. Returns the response body as text. "
            "Works with web pages, APIs, raw files, etc. Academic landing "
            "pages (arxiv, bioRxiv/medRxiv, PubMed Central) are automatically "
            "resolved to their full-text PDF and returned as extracted text — "
            "just pass the abstract/article URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "method": {"type": "string", "description": "HTTP method (default: GET)", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
                "headers": {"type": "object", "description": "Additional HTTP headers as key-value pairs"},
                "body": {"type": "string", "description": "Request body (for POST/PUT/PATCH)"},
                "max_length": {"type": "integer", "description": "Max response length in characters (default: 50000)"},
            },
            "required": ["url"],
        },
        "minimal": True,
        "minimal_role": "to read full pages",
    },
    {
        "name": "gmail_inbox",
        "description": "List recent emails from Gmail inbox. Returns subject, from, date for each email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of emails to return (default: 10)"},
                "folder": {"type": "string", "description": "Mailbox folder (default: INBOX)"},
            },
            "required": [],
        },
    },
    {
        "name": "gmail_read",
        "description": "Read a specific email by its ID. Returns full body, attachments list, headers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Email ID from gmail_inbox or gmail_search"},
                "folder": {"type": "string", "description": "Mailbox folder (default: INBOX)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "gmail_search",
        "description": "Search emails using Gmail search syntax (from:, subject:, is:unread, after:, has:attachment, etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "limit": {"type": "integer", "description": "Max results (default: 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_send",
        "description": "Send an email via Gmail. Supports optional file attachments — pass relative paths (resolved against the current session's artifact folder, matching write_file) or absolute paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address. Comma/semicolon-separated or a list for multiple recipients."},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body (plain text)"},
                "cc": {"type": "string", "description": "CC email address (optional)"},
                "attachments": {
                    "type": "array",
                    "description": "Optional list of file paths to attach. Relative paths resolve against the current session's artifact folder.",
                    "items": {"type": "string"},
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_reply",
        "description": "Reply to an existing email by its ID. Preserves threading.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Email ID to reply to"},
                "body": {"type": "string", "description": "Reply body (plain text)"},
            },
            "required": ["id", "body"],
        },
    },
    {
        "name": "exa_search",
        "description": (
            "Search the web using Exa AI for current, relevant information. "
            "Use this tool whenever the user asks to search the web, look something up, "
            "find recent news, or get current information about any topic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query or topic to look up",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of search results to return (default: 5)",
                    "minimum": 1,
                    "maximum": 20,
                },
                "category": {
                    "type": "string",
                    "description": "Optional category: news, research paper, tweet, company, people",
                    "enum": ["news", "research paper", "tweet", "company", "people"],
                },
            },
            "required": ["query"],
        },
        "minimal": True,
        "minimal_role": "to find relevant sources",
    },
    {
        "name": "searxng_search",
        "description": (
            "Search the web via a self-hosted SearXNG metasearch instance. "
            "Use whenever the user asks to search the web, look something up, "
            "or get current information about any topic.\n"
            "Returns a ranked list of URLs (title + link + score) and NOTHING "
            "ELSE — there are no result snippets. You MUST then call web_fetch "
            "on the most relevant URLs (up to 5, in parallel) and answer from "
            "the fetched page text. NEVER answer from the result titles alone — "
            "a title is not evidence. When choosing which URLs to fetch, prefer "
            "the source that directly answers the user's intent over one that "
            "merely mentions the topic — favour primary/authoritative pages for "
            "factual or live data, and reserve news outlets for when the user "
            "actually wants reporting or recent events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query or topic to look up",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of search results to return (default: 5)",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
        "minimal": True,
        "minimal_role": "to find relevant sources",
    },
    # Specialized SearXNG searches — same instance + result shape as
    # searxng_search, but scoped to a topic CATEGORY (science/it/images/news).
    # Deliberately SEPARATE tools rather than a `category` param: an explicitly
    # named tool the model opts into (news_search) can't recreate the v9.124.0
    # failure where an ad-hoc category='news' on a general query buried the
    # authoritative source under press coverage. All route through _searxng_query.
    {
        "name": "science_search",
        "description": (
            "Search SCIENTIFIC LITERATURE (arxiv, PubMed, Google Scholar, "
            "Semantic Scholar) for papers, studies, and academic/medical "
            "sources. Returns papers (title + link + score), many with "
            "publication dates. You MUST then web_fetch the paper/abstract "
            "pages and answer from their text — a title is not evidence. Use "
            "for research papers, academic questions, medical/scientific "
            "topics; for general web use searxng_search instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "The research topic or paper to look up"},
                "num_results": {"type": "integer",
                                "description": "Number of results (default: 5)",
                                "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "dev_search",
        "description": (
            "Search PROGRAMMING/TECHNICAL sources on the web (Stack Overflow, "
            "MDN, GitHub, Ask Ubuntu, PyPI, Docker Hub) for coding questions, "
            "API/library docs, error messages, and dev tooling. Returns Q&A + "
            "docs (title + link + score); web_fetch the best pages for the "
            "answer. NOTE: this searches the public web — it is DISTINCT from "
            "code_search, which queries this codebase's own structure graph."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "The programming question or technical topic"},
                "num_results": {"type": "integer",
                                "description": "Number of results (default: 5)",
                                "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "image_search",
        "description": (
            "Search for IMAGES (Google/Bing/Qwant/Brave Images, Flickr, "
            "Openverse). Each result carries an `image_url` (the DIRECT picture "
            "URL) alongside `link` (the source page). Returns picture URLs, not "
            "web pages — use when the user wants images/photos/pictures/diagrams "
            "of something. To describe or analyse a picture, web_fetch its "
            "image_url (vision models can read it)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "What to find pictures of"},
                "num_results": {"type": "integer",
                                "description": "Number of images (default: 5)",
                                "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "news_search",
        "description": (
            "Search recent NEWS coverage (Google/Bing/DuckDuckGo/Qwant News, "
            "Reuters). Returns dated news items (title + link + score); "
            "web_fetch the articles for the reporting. Use ONLY when the user "
            "actually wants news / recent events / press coverage. For factual "
            "or live data (weather, prices, facts) prefer searxng_search — news "
            "engines bury authoritative primary sources under press coverage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "The news topic or event to look up"},
                "num_results": {"type": "integer",
                                "description": "Number of results (default: 5)",
                                "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
    },
    # MemPalace migration: built-in memory_* tools unregistered from the
    # LLM-facing schema. Agents now query MemPalace directly via mempalace_query
    # below, which imports mempalace.searcher in-process (no MCP, no subprocess).
    # Mining is handled by background daemons in server.py; the user never runs
    # `mempalace mine` by hand.
    {
        "name": "mempalace_query",
        "description": (
            "Search long-term memory (MemPalace). Returns verbatim snippets "
            "(drawers) from past conversations, code, references, and "
            "attachments that match the query. Use this whenever the user "
            "asks about something they (or the agent) said before, a "
            "previously-mentioned project, a past decision, or code you've "
            "seen in this repo. Hybrid BM25+vector ranking; the daemon keeps "
            "the palace up to date automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for. Natural language or keywords.",
                },
                "wing": {
                    "type": "string",
                    "description": (
                        "Optional wing filter. Pass 'brain_code' for "
                        "source/artifacts, or a shared wing name. You may ONLY "
                        "name a wing you have access to — wings private to "
                        "another user, team, or project are refused. Omit to "
                        "search your own accessible wings (the usual case)."
                    ),
                },
                "room": {
                    "type": "string",
                    "description": (
                        "Optional room filter. Brain's project miner files all "
                        "policy/document content under room='general' and "
                        "auto-promoted artifacts under room='artifacts'. "
                        "Chat content (when chat-sync is on) uses 'chat', "
                        "'chat_summary', 'chat_attachment'. Web/search "
                        "references use 'reference'. **DO NOT GUESS room "
                        "names** — invented values like 'document' or "
                        "'documentation' return zero drawers and produce "
                        "false 'no information found' answers. Omit this "
                        "argument unless you have a verified room name "
                        "from a prior result."
                    ),
                },
                "n_results": {
                    "type": "integer",
                    "description": "Max drawers to return (default 5, max 25).",
                    "minimum": 1,
                    "maximum": 25,
                },
                "include_chat_history": {
                    "type": "boolean",
                    "description": (
                        "Project-pinned only. Default false. When true, search "
                        "the project's CHAT memory (past turns, summaries, "
                        "attachment metadata) instead of the project KNOWLEDGE "
                        "wing (mined documents + ingested files). Use when the "
                        "user asks 'what did we discuss earlier' / 'remember "
                        "when I said'. Outside a project this flag is ignored."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "mempalace_kg_query",
        "description": (
            "Query the project's knowledge graph for an entity — get all "
            "(subject, predicate, object) triples where this entity appears. "
            "The graph is built by an LLM extractor over normative documents "
            "(policies, regulations, specs, contracts). Use this when the "
            "user asks 'what does X require / forbid / cite / define', 'who "
            "is responsible for X', 'what depends on X', or wants a "
            "structured view of obligations. Returns triples with source "
            "drawer references — use mempalace_query on the same source_file "
            "to read the verbatim chunk. Auto-scoped to the current project; "
            "refuses outside a project context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": (
                        "The entity to look up — verbatim in the document's "
                        "source language (e.g. German). Case-insensitive."
                    ),
                },
                "direction": {
                    "type": "string",
                    "enum": ["outgoing", "incoming", "both"],
                    "description": (
                        "outgoing = X→? (what this entity requires/forbids/"
                        "cites). incoming = ?→X (what depends on / refers to "
                        "this entity). both = union. Default outgoing."
                    ),
                },
                "as_of": {
                    "type": "string",
                    "description": (
                        "Optional date filter (ISO YYYY-MM-DD). Returns only "
                        "triples valid at that point in time. Omit for all "
                        "currently-valid triples."
                    ),
                },
            },
            "required": ["entity"],
        },
    },
    {
        "name": "mempalace_kg_search",
        "description": (
            "Search the project's knowledge graph (subject-predicate-object "
            "triples). Two modes:\n"
            "  • STRUCTURED: pass `predicate` (and optionally `subject_contains`/"
            "    `object_contains`) for exact-predicate match. Use for "
            "    contradiction- and coverage-detection: 'every requires triple "
            "    about retention', 'every cites triple referencing GDPR'.\n"
            "  • FREE-TEXT: pass `query` (no predicate) for a substring scan "
            "    across subject/predicate/object. Use when you don't know the "
            "    predicate and just want any triple mentioning a topic.\n"
            "Auto-scoped to the current project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "predicate": {
                    "type": "string",
                    "description": (
                        "STRUCTURED mode. The relation type — must be lowercase "
                        "snake_case. Common: requires, forbids, permits, "
                        "defines, cites, applies_to, effective_from, "
                        "supersedes, responsible_party, condition, exception, "
                        "penalty."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "FREE-TEXT mode. Substring matched (case-insensitive) "
                        "across subject, predicate, and object. Ignored when "
                        "`predicate` is set."
                    ),
                },
                "subject_contains": {
                    "type": "string",
                    "description": (
                        "Optional substring filter on the subject (case-"
                        "insensitive). Only used in STRUCTURED mode."
                    ),
                },
                "object_contains": {
                    "type": "string",
                    "description": (
                        "Optional substring filter on the object (case-"
                        "insensitive). Only used in STRUCTURED mode."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max triples to return (default 25, max 200).",
                    "minimum": 1,
                    "maximum": 200,
                },
            },
        },
    },
    {
        "name": "mempalace_kg_neighbors",
        "description": (
            "Multi-hop neighborhood traversal in the project's knowledge "
            "graph. Returns the entities reachable from a starting entity "
            "within N hops, plus the predicates connecting them. Use to "
            "answer 'what is everything connected to X' / 'what are the "
            "downstream implications of X' / 'which obligations cluster "
            "around the same topic'. Auto-scoped to the current project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "The starting entity (verbatim, case-insensitive).",
                },
                "depth": {
                    "type": "integer",
                    "description": "Max hops (default 1, max 3).",
                    "minimum": 1,
                    "maximum": 3,
                },
                "predicate": {
                    "type": "string",
                    "description": (
                        "Optional: only follow edges with this predicate. "
                        "Useful for tracing a single relation type — e.g. "
                        "predicate='cites' walks the citation graph, "
                        "predicate='supersedes' walks version history."
                    ),
                },
            },
            "required": ["entity"],
        },
    },
    {
        "name": "save_chat_to_memory",
        "description": (
            "Enable saving this chat conversation to long-term memory (MemPalace). "
            "Use when the user says 'remember this', 'save this to memory', or wants "
            "to ensure the current conversation is persisted for future recall. "
            "Immediately syncs all messages in this chat to memory and enables "
            "automatic saving for any new messages in this session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "wiki_write",
        "description": (
            "Create or update a page in the WIKI — the user-visible, editable knowledge "
            "base that is also your long-term memory. To CREATE a page give a title (and "
            "content); to UPDATE an existing page give its page_id. Every saved page is "
            "indexed for search, so write durable facts/notes/summaries here instead of "
            "letting them vanish with the chat. scope: 'user' (your private wiki, "
            "default), 'team' (shared with your team), 'global' (everyone). Pages can be "
            "nested via parent_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Page title (required to create a new page)"},
                "content": {"type": "string", "description": "The page body in markdown"},
                "page_id": {"type": "string", "description": "If set, UPDATE this existing page instead of creating one"},
                "scope": {"type": "string", "description": "user (default) | team | global"},
                "parent_id": {"type": "string", "description": "Optional parent page id to nest under"},
                "project": {"type": "string", "description": "Optional project name to tag the page to"},
            },
        },
    },
    {
        "name": "wiki_read",
        "description": (
            "Read the wiki. Give page_id to read one full page; give query to SEARCH the "
            "wiki semantically (the same vector store the rest of memory uses); give "
            "neither to list the page tree you can access. This is your primary recall "
            "tool — search the wiki before answering from assumptions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Semantic search query over wiki pages"},
                "page_id": {"type": "string", "description": "Read this specific page's full content"},
                "filter": {"type": "string", "description": "When listing: mine | team | global | all (default all)"},
                "limit": {"type": "integer", "description": "Max search results (default 8)"},
            },
        },
    },
    {
        "name": "wiki_delete",
        "description": "Delete a wiki page by its page_id. The page's child pages are kept (re-parented to its parent). Use when the user asks to remove a page or it is obsolete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {"type": "string", "description": "Id of the page to delete"},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "wiki_structure",
        "description": (
            "Inspect or reorganize the wiki tree. action='list' (default) returns the "
            "pages you can access (id, title, scope, parent_id, position); action='move' "
            "re-parents and/or repositions a page (parent_id='' moves it to the top "
            "level). Use to keep the wiki tidily organized by topic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "'list' (default) or 'move'"},
                "filter": {"type": "string", "description": "For list: mine | team | global | all (default all)"},
                "page_id": {"type": "string", "description": "For move: the page to move"},
                "parent_id": {"type": "string", "description": "For move: new parent id ('' = top level)"},
                "position": {"type": "integer", "description": "For move: order among siblings"},
            },
        },
    },
    {
        "name": "context_search",
        "description": "Search through compacted conversation history by keyword. Returns matching message excerpts from earlier in the conversation that have been summarized away.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword or phrase"},
                "limit": {"type": "integer", "description": "Max results (default: 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "context_detail",
        "description": "Expand a specific context summary to see the original messages it was created from. Use summary IDs from the conversation context header.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary_id": {"type": "string", "description": "The summary ID to expand"},
            },
            "required": ["summary_id"],
        },
    },
    {
        "name": "context_recall",
        "description": "Deep recall: search compacted conversation history and get a focused answer about a specific topic from earlier in the conversation. Uses a sub-LLM call to analyze original messages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall from earlier conversation"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Format-aware document reader for PDF, DOCX, XLSX, PPTX, CSV/TSV, images, "
            "EML (email), MSG (Outlook), EPUB (ebook), and ZIP archives. "
            "Returns structured content: PDF pages, DOCX paragraphs/tables, XLSX sheets as markdown tables, "
            "PPTX slides with notes, CSV as markdown table, image metadata + vision description, "
            "EML headers+body, EPUB metadata+prose, ZIP recursive file listing with contents. "
            "For unknown extensions, falls back to plain text read. "
            "For XLSX/CSV ANALYSIS (filtering, joining, aggregating, building "
            "a new workbook) prefer xlsx_inspect + xlsx_query instead of "
            "reading the raw rows into chat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to the document"},
                "sheet": {"type": "string", "description": "Sheet name for XLSX (default: all sheets)"},
                "pages": {"type": "string", "description": "Page range for PDF, e.g. '1-5' or '1,3,7'"},
                "slides": {"type": "string", "description": "Slide range for PPTX, e.g. '1-10' or '2,5'"},
                "include_tables": {"type": "boolean", "description": "PDF only: extract tables via pdfplumber and inline as markdown. Works well on PDFs with ruled cell borders (forms, financial reports, invoices). Turn OFF for academic papers, whitespace-aligned tables, or scanned PDFs — pdfplumber produces noisy output in those cases. Default false; adds ~1-3s per page."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_document",
        "description": (
            "Create a new document from markdown content. Dispatches by file extension: "
            ".docx (headings, tables, lists, blockquotes, code blocks, links, bold/italic, "
            "images), .xlsx (markdown tables to sheets), .pptx (`# `/`## ` headings start "
            "slides; bullets, nested lists, real tables with risk-badges, bold/italic/links, "
            "images), .pdf (formatted PDF via reportlab, same rich markdown), .html "
            "(self-contained styled web report, images inlined as base64 — for an HTML "
            "report DEFAULT to style='report' for the polished editorial layout, see the "
            "`style` field). The .docx/.pdf/"
            ".pptx content is parsed with a real CommonMark parser, so standard markdown — "
            "including nested `- `/`1. ` lists, `> ` quotes, fenced code blocks and "
            "`[text](url)` links — converts natively. For how to write markdown that "
            "converts beautifully (the ::kpi + risk-column conventions, cover/TOC, clean "
            "headings), load the `document-markdown` skill via use_skill. "
            "For an HTML report ALWAYS use this tool, NOT write_file with "
            "hand-written HTML — only this tool applies the `style` preset (fonts/colors/"
            "layout + running header/footer/logo) deterministically, so the report is "
            "on-brand instead of ad-hoc invented CSS. Use a RELATIVE filename (e.g. "
            "`report.docx`) so it lands "
            "in the session's artifact folder and auto-promotes to the Artifacts panel. "
            "EMBEDDED DIAGRAMS/CHARTS: a markdown image `![alt](file.png)` is embedded as "
            "a real picture (docx/pptx/pdf/html). For a professional report or presentation "
            "with data-accurate diagrams, first call render_diagram (→ a chart file in the "
            "same artifact folder), then reference that file with `![title](thatfile)` in "
            "the content here. Use PNG for .docx/.pdf embedding (SVG embeds in HTML, not "
            "PDF); in .pptx an image-only slide section becomes a centered full-slide picture. "
            "PROFESSIONAL .docx POLISH (automatic — just write clean markdown): the tool "
            "auto-renders a cover page (from the first `# H1` + leading `Key: value` lines) and "
            "a table-of-contents for substantial reports, dark table headers with zebra rows, "
            "real divider lines for `---`, and COLOUR-CODED RISK BADGES — a table column named "
            "Bewertung/Risiko/Rating/Einstufung whose cells say gering/mittel/erhöht/hoch is "
            "auto-shaded green/amber/red. Do NOT put `**bold**` markers or emojis in headings or "
            "table headers — bold/emoji in headings is handled/stripped for you. KPI STAT BOXES: "
            "to highlight 2–4 headline metrics as a coloured box-strip, emit consecutive lines "
            "`::kpi VALUE | LABEL | risk` (e.g. `::kpi 1,34 | Residualrisiko | gering`); the third "
            "field colours the box by the same risk scale. This is the one polish feature you "
            "trigger explicitly — everything else is automatic from plain markdown. "
            "SPREADSHEETS: for an .xlsx built from EXISTING file data, or one that "
            "needs number formats/charts/master-detail grouping, use xlsx_create "
            "instead (server-side data flow); .xlsx here is only for small tables "
            "you author inline as markdown."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative output filename, extension picks the format (e.g. `report.html`). It lands in your session artifact folder; absolute paths and '..' escapes are REFUSED."},
                "content": {"type": "string", "description": "Markdown content to convert into the document"},
                "style": {"type": "string", "description": "Style preset name (a file in the agent's skills/doc-styles/, e.g. 'corporate') — sets fonts/colors/layout + header/footer/logo deterministically. PREFER passing this for any report/document so the output is on-brand. If omitted, a DEFAULT preset is applied automatically (project's, the global default, or 'corporate') — so do NOT hand-write your own CSS/styling; write PLAIN MARKDOWN content and let the preset style it. (For .html, raw HTML you pass is kept as-is and only gains the header/footer/logo; markdown gets the full preset — so markdown+style gives the most consistent result.) ⟶ MATCH A REFERENCE/TEMPLATE: when the user wants the output to look like an attached reference document or a project template (e.g. a project instruction-file like 'WPB_Risikoanalyse_BRA_2025_v1.0.docx'), pass style='reference' to auto-pick the project's instruction-file .docx, or style='reference:<filename>' to name it. This LIFTS the reference's actual fonts/heading styles/colors/margins (read from the .docx in code) INSTEAD of applying a brand preset — use it whenever the request is 'erstelle X im Format von / wie die Referenz / like the attached doc'. (.docx output only; lifts named-style + margin definitions, not the full visual template/themes.) ⟶ EDITORIAL REPORT LAYOUT (.html only) — THE DEFAULT FOR ANY HTML REPORT: pass style='report' (alias 'editorial') to render an .html file with the SAME polished magazine-style layout Deep Research uses — warm editorial palette, drop-cap intro, gradient-underlined headings, sticky table-of-contents sidebar, collapsible sources, light/dark + print-ready. This is the RIGHT DEFAULT any time the user asks for an HTML report/Bericht/document (e.g. 'erstelle einen html-report', 'due diligence report als HTML') — NOT only when they say the word 'schön/nice'. If in doubt for a .html report, use style='report'; the plain doc-styles preset (corporate/Calibri look) is the fallback only when a specific on-brand letterhead is explicitly required. Write PLAIN MARKDOWN content (the first '# Heading' becomes the report title); do NOT hand-write HTML for this — raw HTML can't be re-flowed into the layout."},
                "hero_image": {"type": "string", "description": "OPTIONAL, only with style='report' (.html): https URL of a real lead image shown full-width under the report headline (the hero, same as Deep Research reports). PREFER setting this when you saw a strong, topical image during research (e.g. an article's og:image or lead photo from a fetched source) — a real photo beats the generated banner. If omitted, the tool auto-tries the og:image of the first links cited in the content; only when nothing is found does it fall back to a generated abstract banner."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_document",
        "description": (
            "Targeted edits to existing documents. Actions by format: "
            "DOCX: replace_text (find/replace in paragraphs). "
            "XLSX: update_cell (sheet, cell, value), add_row (sheet, values). "
            "PPTX: update_slide (slide_index, title, body), add_slide (title, body). "
            "For anything beyond a single XLSX cell/row (bulk rows, computed "
            "columns, conditional updates, sheet management) use xlsx_edit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the document to edit"},
                "action": {
                    "type": "string",
                    "description": "Edit action to perform",
                    "enum": ["replace_text", "update_cell", "add_row", "update_slide", "add_slide"],
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Action-specific parameters. "
                        "replace_text: {old_text, new_text}. "
                        "update_cell: {sheet, cell, value}. "
                        "add_row: {sheet, values (array)}. "
                        "update_slide: {slide_index (1-based), title, body}. "
                        "add_slide: {title, body}."
                    ),
                },
            },
            "required": ["path", "action", "params"],
        },
    },
    {
        "name": "xlsx_inspect",
        "description": (
            "Understand a DATA FILE (Excel/CSV/JSON/XML) WITHOUT reading its "
            "data into chat: sheets/tables, dimensions, per-column name/type/"
            "nulls/distinct/samples, merged cells, formula count, and "
            "JOIN-KEY CANDIDATES across "
            "sheets (columns that link tables, with value overlap). ALWAYS "
            "call this FIRST for any data-file task — it prints the exact "
            "table and column names to use in xlsx_query, so never guess "
            "identifiers. Do NOT write pandas/openpyxl code via python_exec "
            "for data files — use xlsx_inspect → xlsx_query → xlsx_create/"
            "xlsx_edit instead; the data stays server-side. JSON/JSONL: each "
            "record array becomes a table (nested objects flatten to "
            "a.b columns); XML: each repeated element becomes a table. Pass "
            "paths=[...] "
            "to profile several files in one call (e.g. to compare exports). "
            "Multi-table sheets are split into one table per block; merged "
            "two-row headers compose to 'Q1 / Umsatz' names. Pass deep=true "
            "for a data-quality audit: duplicate rows, numeric outliers, "
            "orphan join keys (values missing on one side), and a formula/"
            "dependency map of what the workbook computes. Legacy .xls/.ods "
            "files are read too (converted transparently)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .xlsx/.xlsm/.csv/.json/.jsonl/.xml file"},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Several files at once (alternative to path)"},
                "sheet": {"type": "string", "description": "Restrict to one sheet/table (default: all)"},
                "deep": {"type": "boolean", "description": "Add data-quality checks (duplicates, outliers, orphan keys) + formula map"},
            },
        },
    },
    {
        "name": "xlsx_query",
        "description": (
            "Run ONE read-only SQL SELECT against tabular data from .xlsx/"
            ".csv/.json/.xml files. Each sheet (JSON array / repeated XML "
            "element) becomes a SQLite table — use the table/column names "
            "EXACTLY as "
            "xlsx_inspect printed them. Filtering, JOINs across sheets, GROUP "
            "BY, aggregates — all without writing code; the data never enters "
            "the chat. CROSS-FORMAT joins work too (e.g. a CSV export against "
            "a JSON API dump in one SELECT). Returns up to 50 result rows as "
            "a table plus the total "
            "row count; pass out='name.csv' to save the FULL result as an "
            "artifact the user can download. Pass paths=[fileA, fileB] to "
            "query several files in one session (tables are then prefixed "
            "with the file stem — xlsx_inspect with the same paths shows the "
            "names). Only SELECT/WITH is allowed. PIPELINES: pass "
            "save_as='name' to store the FULL result for this session and "
            "reference it later as path 'result:name' (in xlsx_query, "
            "xlsx_diff, or an xlsx_create/xlsx_edit source.file) — no "
            "re-query needed. Example: sql=\"SELECT o.nr, SUM(t.stueck) FROM "
            "orders o JOIN teilausf t ON t.nr = o.nr GROUP BY o.nr\"."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .xlsx/.csv/.json/.xml file (or 'result:<name>' for a stored result)"},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Several files in one SQL session (alternative to path)"},
                "sql": {"type": "string", "description": "One SELECT statement. Use table/column names from xlsx_inspect verbatim."},
                "out": {"type": "string", "description": "Optional relative .csv filename — writes the full result to your artifact folder"},
                "save_as": {"type": "string", "description": "Store the full result under this name for later 'result:<name>' references"},
                "sheet": {"type": "string", "description": "Load only this sheet/table — required for files over 30 MB, and .xls/.ods work too"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "xlsx_create",
        "description": (
            "Create a professionally styled Excel workbook from a compact JSON "
            "spec — styled header row, freeze panes, column widths, number "
            "formats, banded rows are applied automatically (house style "
            "preset). CRITICAL: for data that exists in a file, do NOT copy "
            "rows into the spec — point the sheet at source:{file, sheet?, "
            "sql?} and the server moves the data itself (sql may reshape it "
            "first; source.file also takes .csv/.json/.xml — e.g. turn a "
            "JSON export into a styled workbook in one call). Inline rows "
            "are ONLY for small, newly-authored tables. "
            "Never write openpyxl/pandas code for this. Spec: {sheets:[{name, "
            "columns?:[{name, format?: text|int|number|eur|percent|date, "
            "width?}], rows?:[[...]] | source:{file, sheet?, sql?} | "
            "master_detail:{key, master:{source, columns?}, detail:{source, "
            "columns?}}, totals?:[col], banded?, autofilter?, charts?:[{type: "
            "bar|line|pie, labels, series:[col], title?}], conditional?:"
            "[{columns:[col], rule: 'color_scale'|'data_bars'|{lt|gt|eq, "
            "fill}}], print?:{orientation?, fit_width?, repeat_header?}}], "
            "style?}. Column spec also takes choices:[...] for a dropdown "
            "(data validation). Chart types: bar|line|pie|area|scatter, plus "
            "stacked:true and secondary:[col] (right-hand Y axis). "
            "master_detail builds a grouped master→detail "
            "sheet from two sources + a join key (subtotals?:[detail col] adds "
            "a =SUM row per group). PIVOT: a sheet with pivot:{rows:'<col>', "
            "cols?:'<col>', values:'<col>', agg?: sum|count|avg|min|max} + "
            "source builds a cross-tab. TEMPLATE FILL: spec.template={file} "
            "copies an existing styled workbook and writes ONLY data into it "
            "— per sheet {name (existing sheet), anchor: 'B5' or named_range, "
            "rows|source}; the template's styling/formulas stay untouched (use "
            "for corporate report templates). source.file also accepts "
            "'result:<name>' from xlsx_query save_as. recalc:true computes "
            "formula values right away (LibreOffice) so a follow-up "
            "xlsx_query can read them. Sheets beyond 100k rows switch the "
            "workbook to a STREAMING writer automatically (constant memory; "
            "header style/freeze/widths/totals kept, banded rows/number "
            "formats/charts/conditional skipped — noted in the result). "
            "Example minimal spec: "
            "{\"sheets\":[{\"name\":\"Daten\",\"source\":{\"file\":\"in.xlsx\","
            "\"sql\":\"SELECT * FROM orders WHERE stueck > 0\"}}]}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative output filename, e.g. `orders_combined.xlsx` (lands in your artifact folder)"},
                "spec": {"type": "object", "description": "The workbook spec (see tool description for the grammar)"},
                "recalc": {"type": "boolean", "description": "Compute formula values immediately after writing (needs LibreOffice)"},
            },
            "required": ["path", "spec"],
        },
    },
    {
        "name": "xlsx_edit",
        "description": (
            "Change an EXISTING workbook in your artifact folder while "
            "preserving its formatting and formulas (assumes headers in row "
            "1). Ops: append_rows {sheet, rows|source} (new rows inherit the "
            "last row's style; source:{file, sheet?, sql?} pulls rows "
            "server-side) · add_column {sheet, name, formula?|values?, "
            "format?} — formula like '=B{row}*C{row}' is filled down per row "
            "· update_cells {sheet, where:{column, equals|contains|lt|gt}, "
            "set:{column: value}} · add_sheet (same shape as an xlsx_create "
            "sheet) · rename_sheet {from, to} · delete_sheet {name} · "
            "set_format {sheet, columns, format}. Use edit_document only for "
            "single-cell tweaks; use xlsx_create (with source) to derive a "
            "NEW file from an attachment. Pass recalc:true (top level of "
            "spec or args) to compute formula values immediately "
            "(LibreOffice) so xlsx_query can read them. Example: {\"ops\":[{"
            "\"op\":\"add_column\",\"sheet\":\"Daten\",\"name\":\"Wert\","
            "\"formula\":\"=B{row}*C{row}\",\"format\":\"eur\"}]}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workbook in your artifact folder to modify"},
                "spec": {"type": "object", "description": "{ops: [{op, ...}]} — see tool description for the op grammar"},
                "recalc": {"type": "boolean", "description": "Compute formula values immediately after the edit (needs LibreOffice)"},
            },
            "required": ["path", "spec"],
        },
    },
    {
        "name": "xlsx_diff",
        "description": (
            "Compare two DATA FILES (.xlsx/.csv/.json/.xml — sides may be "
            "DIFFERENT formats, e.g. yesterday's CSV vs today's JSON export) "
            "deterministically and report what "
            "changed — sheets/columns present on one side only, and per sheet "
            "the changed rows. ALWAYS pass key='<column>' when the rows have "
            "an ID column (keyed compare: added/removed/changed rows with "
            "per-cell old → new values); composite keys as comma-separated "
            "'KUNDE,DATUM'; without a key the compare is positional. Returns "
            "a summary (detail capped at 50 rows). out='diff.xlsx' saves a "
            "HIGHLIGHTED workbook (changed cells yellow with the old value as "
            "comment, added rows green, removed rows red at the bottom) — the "
            "best deliverable for review; out='diff.csv' saves the flat "
            "change list instead. compare='formulas' diffs the formula "
            "strings instead of values (finds edited/broken formulas); "
            "compare='formats' diffs the cell FORMATTING (number format, "
            "bold/italic, font/fill colour) while still matching rows by "
            "value key — finds re-coloured/re-formatted cells with identical "
            "values. "
            "Accepts 'result:<name>' stored results as either side. Use this "
            "for 'vergleiche Datei A mit B / was hat sich geändert' instead "
            "of reading both files into chat."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "Old/left file (.xlsx/.xls/.ods/.csv/.json/.xml or result:<name>)"},
                "path_b": {"type": "string", "description": "New/right file (.xlsx/.xls/.ods/.csv/.json/.xml or result:<name>)"},
                "key": {"type": "string", "description": "ID column on both sides (comma-separated for composite keys) — enables the keyed row compare"},
                "sheet": {"type": "string", "description": "Compare only this sheet/table"},
                "out": {"type": "string", "description": "Optional relative filename — .xlsx = highlighted diff workbook, .csv = flat change list"},
                "compare": {"type": "string", "description": "'formulas' = diff formula strings; 'formats' = diff cell formatting (rows still matched by value key)"},
            },
            "required": ["path_a", "path_b"],
        },
    },
    {
        "name": "text_diff",
        "description": (
            "Compare two TEXT files (source code, configs, SQL, markdown, "
            "XML-as-text, logs …) deterministically — unified diff with "
            "context plus counts (+added/-removed lines, hunks, similarity "
            "%). Use this instead of reading both files into chat for "
            "'vergleiche Datei A mit B'. mode='json' compares JSON/JSONL "
            "STRUCTURALLY instead (path → value: added/removed/changed "
            "paths, object-key order ignored) — right for nested configs "
            "where a text diff drowns in reordering noise. out='diff.html' "
            "saves a side-by-side review page as artifact (out='diff.txt' = "
            "the raw patch). For TABULAR data (xlsx/csv/json record lists) "
            "use xlsx_diff — it matches rows by key."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "Old/left file"},
                "path_b": {"type": "string", "description": "New/right file"},
                "mode": {"type": "string", "description": "'json' = structural JSON compare (path → value) instead of line diff"},
                "context": {"type": "integer", "description": "Unified-diff context lines (default 3)"},
                "out": {"type": "string", "description": "Optional relative filename — .html = side-by-side page, .txt/.diff = raw patch"},
            },
            "required": ["path_a", "path_b"],
        },
    },
    {
        "name": "ocr_inspect",
        "description": (
            "Profile a scanned image or PDF WITHOUT running full OCR: page "
            "count, pixel dimensions, detected orientation/rotation and script "
            "(via tesseract OSD), and a rough word-count/confidence probe. "
            "ALWAYS call this FIRST for a scan/photo task to decide the language "
            "and whether OCR is worthwhile (a native digital PDF has selectable "
            "text — use read_document instead; OCR is for SCANS/PHOTOS where "
            "read_document returns little). Runs LOCALLY (tesseract), no LLM, no "
            "cloud, deterministic. Then use ocr_extract / ocr_fields / "
            "ocr_tables / ocr_region."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the image (.png/.jpg/.tif/.bmp/.webp) or .pdf"},
                "lang": {"type": "string", "description": "Tesseract language(s), e.g. 'deu+eng' (default) or 'eng'"},
                "pages": {"type": "string", "description": "For PDFs: pages to probe, e.g. '1' or '1-3' (default: page 1)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "ocr_extract",
        "description": (
            "Read the text out of a scanned image or PDF DETERMINISTICALLY — "
            "local tesseract OCR, NO LLM, NO cloud. Use this instead of relying "
            "on the model to 'look at' an attached scan/photo and re-type "
            "numbers (it misreads amounts). Returns reading-order text plus a "
            "mean_confidence score. mode='text' (plain), 'layout' (keeps "
            "paragraph/block breaks) or 'markdown' (layout + per-page headers). "
            "For a long document pass out='text.txt' to save the FULL extract as "
            "an artifact (the chat preview is capped). For digital PDFs with "
            "real selectable text, use read_document — OCR is for scans/photos. "
            "For structured values (invoice no., total) prefer ocr_fields; for "
            "gridded tables prefer ocr_tables. "
            "If the deterministic read comes back nearly empty (a photographed "
            "document, a blurred phone snap), the result ALSO carries a "
            "`model_read` field: the same image as read by a vision OCR model. "
            "That one is UNVERIFIED and can invent plausible names/numbers — "
            "`text` is evidence, `model_read` is a lead. Never quote a value "
            "from `model_read` as fact without confirming it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the image or .pdf to OCR"},
                "mode": {"type": "string", "description": "'text' | 'layout' | 'markdown' (default 'text')"},
                "lang": {"type": "string", "description": "Tesseract language(s), e.g. 'deu+eng' (default)"},
                "pages": {"type": "string", "description": "For PDFs: '1-3,5' (default: all pages, capped at 50)"},
                "out": {"type": "string", "description": "Optional relative .txt filename — saves the FULL text to your artifact folder"},
                "model_fallback": {"type": "boolean", "description": "Default true. When the deterministic read is nearly empty, also return a vision-model reading as a separate, clearly-flagged `model_read` field. Set false for a strictly deterministic result."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "ocr_region",
        "description": (
            "OCR only a RECTANGULAR REGION of a page — for 'just the stamp "
            "top-right', 'only the total at the bottom', 'the handwritten note "
            "in the corner'. bbox=[x, y, width, height] in pixels (unit='px', "
            "default) or as percent of the page (unit='pct'). `text` is "
            "deterministic local tesseract, no LLM. Use ocr_inspect first to "
            "learn the page pixel size. Cheaper and more precise than OCR-ing "
            "the whole page when you only need one area. "
            "The result ALSO carries `model_read` — the same crop as read by "
            "the vision OCR model, UNVERIFIED and able to invent text on an "
            "unreadable crop. `text` is evidence, `model_read` is a lead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the image or .pdf"},
                "bbox": {"type": "array", "items": {"type": "number"}, "description": "[x, y, width, height] — top-left origin"},
                "unit": {"type": "string", "description": "'px' (default) or 'pct' (percent of page dimensions)"},
                "page": {"type": "integer", "description": "For PDFs: 1-based page number (default 1)"},
                "lang": {"type": "string", "description": "Tesseract language(s), default 'deu+eng'"},
                "model_fallback": {"type": "boolean", "description": "Default true. Also return the vision model's reading of the crop as a separate, flagged `model_read`. Set false for a strictly deterministic result."},
            },
            "required": ["path", "bbox"],
        },
    },
    {
        "name": "ocr_fields",
        "description": (
            "Extract STRUCTURED FIELDS from a scan/PDF deterministically: the "
            "server OCRs the document, then applies YOUR per-field REGEX to the "
            "recognised text — no LLM guessing, repeatable. Ideal for invoices, "
            "receipts, forms: give fields=[{name, pattern}] where pattern is a "
            "Python regex with ONE capture group for the value. Returns "
            "validated JSON {name: value|null} and lists which fields did not "
            "match. Example: fields=[{\"name\":\"rechnungsnr\",\"pattern\":"
            "\"Rechnung\\\\s*Nr\\\\.?\\\\s*([\\\\w-]+)\"}, {\"name\":\"betrag\","
            "\"pattern\":\"(\\\\d[\\\\d.,]*)\\\\s*EUR\"}]. Matching is "
            "case-insensitive and multi-line. "
            "Where the deterministic OCR finds NOTHING for a field, the pattern "
            "is retried against the vision model's reading — the `source` map "
            "then marks that field 'model_unverified' instead of 'ocr'. Such a "
            "value can be INVENTED on an unreadable image: verify it against "
            "the picture (ocr_region) before quoting it as fact. Set "
            "model_fallback=false for strictly deterministic values only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the image or .pdf"},
                "fields": {
                    "type": "array",
                    "description": "[{name, pattern}] — pattern is a regex; use one capture group for the value",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "pattern": {"type": "string"},
                        },
                        "required": ["name", "pattern"],
                    },
                },
                "lang": {"type": "string", "description": "Tesseract language(s), default 'deu+eng'"},
                "model_fallback": {"type": "boolean", "description": "Default true. Retry a field the deterministic OCR could not find against the vision model's reading (marked 'model_unverified' in `source`). Set false for deterministic values only."},
                "pages": {"type": "string", "description": "For PDFs: '1-3' (default: all pages)"},
            },
            "required": ["path", "fields"],
        },
    },
    {
        "name": "ocr_tables",
        "description": (
            "Extract a TABLE from a scanned image or PDF deterministically: OCR "
            "word positions are clustered into columns (by x-position) and rows "
            "(by text line) and emitted as CSV — no LLM. Pass out='name.csv' to "
            "save the full table as an artifact you can then open with "
            "xlsx_inspect / xlsx_query (OCR → spreadsheet pipeline). Best for "
            "gridded tables, statements, price lists; for free-flowing text use "
            "ocr_extract instead. Column detection is geometric, so very skewed "
            "scans may split columns — check the preview."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the image or .pdf"},
                "out": {"type": "string", "description": "Optional relative .csv filename — saves the full table to your artifact folder"},
                "lang": {"type": "string", "description": "Tesseract language(s), default 'deu+eng'"},
                "pages": {"type": "string", "description": "For PDFs: '1-3' (default: all pages)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "mrz_verify",
        "description": (
            "Verify an ID document's machine-readable zone (MRZ) SERVER-SIDE: "
            "parses TD1/TD2/TD3 and checks ALL ICAO-9303 check digits "
            "deterministically (weights 7,3,1). Returns PII-FREE verdicts — "
            "per-field checksum true/false, all_valid, doc type, issuer, "
            "nationality, expiry state vs today, age — never the document "
            "number, name or raw birth date. ALWAYS use this instead of "
            "computing MRZ check digits yourself: your own arithmetic can be "
            "wrong on pseudonymised values and would produce FALSE forgery "
            "indications. Prefer `path` (the document image/PDF on disk — the "
            "server reads the raw pixels); pass `text` only when no file is "
            "available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the document image/PDF/text file (preferred — server reads raw data)"},
                "text": {"type": "string", "description": "Raw MRZ line(s) — fallback only when no file path is available"},
                "lang": {"type": "string", "description": "Tesseract language(s), default 'deu+eng'"},
            },
        },
    },
    {
        "name": "doc_dates_check",
        "description": (
            "Compute date RELATIONS server-side — validity vs today, renewal "
            "gaps, validity spans, photo-vs-document date offsets — instead "
            "of doing date arithmetic yourself (which breaks on pseudonymised "
            "values). Each source is a named date: pass a literal `date` "
            "value, or a `path` plus `select` (exif_datetime for photo "
            "capture time, mrz_dob / mrz_expiry read from the document's MRZ, "
            "file_mtime). Returns per-source past/future vs today and "
            "pairwise gaps in days plus a human form ('10y - 1d', '9 days'). "
            "Birth dates are returned as age only, never raw."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sources": {
                    "type": "array",
                    "description": "[{name, date | path[, select]}] — select: exif_datetime | mrz_dob | mrz_expiry | file_mtime",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Label for this date, e.g. 'old_expiry', 'new_issue', 'photo_taken'"},
                            "date": {"type": "string", "description": "Literal date value (ISO, EU dot, US slash, '5 FEB 1947', EXIF '2026:07:02')"},
                            "path": {"type": "string", "description": "File to read the date from (with select)"},
                            "select": {"type": "string", "description": "exif_datetime | mrz_dob | mrz_expiry | file_mtime"},
                        },
                        "required": ["name"],
                    },
                },
                "pairs": {
                    "type": "array",
                    "description": "Optional explicit pairs [{a, b}] by source name; default: consecutive gaps in chronological order",
                    "items": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "string"},
                            "b": {"type": "string"},
                        },
                        "required": ["a", "b"],
                    },
                },
                "lang": {"type": "string", "description": "Tesseract language(s), default 'deu+eng'"},
            },
            "required": ["sources"],
        },
    },
    {
        "name": "identity_consistency",
        "description": (
            "Compare identity fields ACROSS documents server-side: extracts "
            "each source's MRZ identity and filename name-form, normalises "
            "names across case/order/initials/MRZ-form (fuzzy for OCR garble) "
            "and reports MATCH VERDICTS — how many distinct persons the "
            "sources describe, whether birth dates match (as equality + age, "
            "never the raw date), how many distinct document numbers exist "
            "and the expiry chain. Use this for 'are the personal details "
            "consistent across these documents?' instead of comparing "
            "(possibly pseudonymised) values yourself. Pass 2-8 file paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "description": "2-8 document paths (images/PDFs/text files) to compare",
                    "items": {"type": "string"},
                },
                "lang": {"type": "string", "description": "Tesseract language(s), default 'deu+eng'"},
            },
            "required": ["paths"],
        },
    },
    {
        "name": "delegate_task",
        "description": (
            "Delegate a task to another agent. Runs in a background thread with its own context. "
            "By default waits for result (wait=true). Set wait=false for async execution, "
            "then use task_status to poll for completion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Target agent ID (e.g. 'research', 'health')"},
                "task": {"type": "string", "description": "Task description for the target agent"},
                "wait": {"type": "boolean", "description": "Wait for result (default: true). Set false for async."},
                "model": {"type": "string", "description": "Override model for this task (optional)"},
            },
            "required": ["agent", "task"],
        },
    },
    {
        "name": "task_status",
        "description": (
            "Check status of background tasks. Call with task_id to check a specific task, "
            "or without to list all tasks. Returns status (running/completed/cancelled/error) and result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to check (optional, lists all if empty)"},
            },
            "required": [],
        },
    },
    {
        "name": "task_cancel",
        "description": "Cancel a running background task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to cancel"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "run_background_task",
        # Description validated in eval/fanout_probe.py against Mistral medium/small
        # + gemma-4-26b: the worked example + "do NOT make a separate summary task"
        # line drove follow_up/group_id reliability to ~15/15 on cloud models.
        "description": (
            "Spin off a long-running, high-output piece of work (deep research, "
            "multi-source synthesis, a big sweep) as a DETACHED background task so "
            "it doesn't block the conversation. Runs as YOU — same agent, model, "
            "tools — in its own context. Returns IMMEDIATELY with a task id; you do "
            "NOT get the result in this turn — it is delivered to you automatically "
            "once finished. The user sees live progress in the 'Hintergrundaufgaben' "
            "panel and can stop it.\n\n"
            "FAN-OUT (parallel): when a request covers SEVERAL INDEPENDENT SUBJECTS "
            "that can be researched at the same time (e.g. three vendors, five "
            "topics, two separate documents), make ONE call PER SUBJECT and give "
            "every one of those calls the SAME group_id (a short string you pick, "
            "e.g. 'g1'). Fan out across independent SUBJECTS — not aspects of one "
            "subject.\n\n"
            "THE COMBINE STEP: put what to do once ALL parts finish (compare, write "
            "the report, recommend) into follow_up. Do NOT create a separate task "
            "for the summary/comparison — follow_up IS the combine step; its result "
            "comes back to you in one delivery.\n\n"
            "EXAMPLE — \"compare A, B, C and recommend one\" → exactly THREE calls, "
            "each {group_id:'g1', follow_up:'Compare A/B/C and recommend one'}, with "
            "prompts 'Research A …' / 'Research B …' / 'Research C …'. (NOT a fourth "
            "summary call.)\n\n"
            "Each prompt is run by a FRESH agent that does NOT see this conversation "
            "— make every prompt fully self-contained (name the exact subject). Use "
            "background tasks ONLY for genuinely long work; for a quick lookup, do it "
            "inline. After spawning, acknowledge to the user that you've started and "
            "STOP — do not try to use the results now."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short label shown in the panel (e.g. 'Marktanalyse E-Bikes')"},
                "prompt": {"type": "string", "description": "The full, self-contained instruction for the background run — it does NOT see this conversation, so include all needed context."},
                "group_id": {"type": "string", "description": "REQUIRED whenever you make more than one call for the same request: pick a short string and use the IDENTICAL value on every call of the fan-out. Omit for a standalone single task."},
                "follow_up": {"type": "string", "description": "The combine/synthesis instruction carried out after ALL tasks in the group finish (e.g. 'compare the results and recommend one'). Set this instead of making a separate summary task."},
            },
            "required": ["title", "prompt"],
        },
    },
    {
        "name": "retry_background_task",
        "description": (
            "Retry ONE failed background task — allowed EXACTLY ONCE per task "
            "(server-enforced) and only for tasks that failed on their own "
            "(status error, timeout or empty). Use it when a delivered "
            "background result reports an API error, a timeout or an empty "
            "answer and the work is still needed. Pass `model` to rerun on a "
            "DIFFERENT model when the failure looks model-related (refusal, "
            "empty answer, repeated API errors on that provider); omit it to "
            "rerun on the same model for transient errors. NOT for tasks the "
            "USER cancelled — a deliberate stop is the user's decision: use "
            "the partial result or ask the user. If the retry is refused or "
            "has already been used, do the work directly in this turn or "
            "report the failure — never loop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The id of the failed task, as given in the delivered result block."},
                "model": {"type": "string", "description": "Optional: run the retry on this model instead (exact model id). Use when the failure looks model-related."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "use_skill",
        "description": (
            "Load a skill's instructions into context. Skills provide specialized knowledge "
            "for specific tasks (e.g. github, docker, swift). Call this BEFORE performing a task "
            "that matches a skill. The skill's instructions will be returned as text — follow them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "description": "Name of the skill to load"},
            },
            "required": ["skill"],
        },
    },
    {
        "name": "find_skills",
        "description": (
            "Search the current user's PERSONAL skills — reusable procedures the user (or their "
            "team) saved from past chats — for ones matching the task at hand. These are NOT listed "
            "up front, so call this whenever a task looks like something the user has done before "
            "(a recurring analysis, check, or workflow). Returns matching skills ranked by relevance "
            "as [{slug, name, description, score}]; then call use_skill(skill=\"<slug>\") to load the "
            "best match's instructions. Returns an empty list if the user has no matching skill — "
            "that's normal, just proceed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string",
                         "description": "A short description of the task you're about to do, "
                                        "in the user's language — used to rank the user's skills."},
            },
            "required": ["task"],
        },
    },
    {
        "name": "helpdesk_session_info",
        "description": (
            "Helpdesk only. Get metadata and the recent messages of the chat session the user "
            "opened the helpdesk from — title, model, project, thinking level, and the last few "
            "user/assistant turns. Use it to answer questions about what is happening in THIS session."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "helpdesk_user_context",
        "description": (
            "Helpdesk only. Get the current user's profile and account preferences — greeting name, "
            "role/job, communication preferences, and the auto-maintained profile summary. Use it to "
            "personalise help and address the user appropriately."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "helpdesk_config",
        "description": (
            "Helpdesk only. Read the LIVE settings relevant to the user's question (read-only; API keys "
            "are never returned). This is the source of truth for anything about models, costs, plans "
            "and limits — prefer it over the guide whenever the answer depends on what is actually "
            "configured RIGHT NOW. You get the facts as JSON; YOU analyse them and give the answer.\n\n"
            "Use it for questions like: what does model X cost · which model is best for coding / "
            "research / writing (compare the per-task capability scores) · which model is fastest (tps) · "
            "which models run locally (free, and data stays on the device) · which models a subscription "
            "already covers so they cost nothing extra · what is my spending limit and how much have I "
            "used · which model does a background job use.\n\n"
            "Sections:\n"
            "• models — every model the user may pick, each with: cost ($ per 1M tokens), capability "
            "0-100 PER TASK TYPE (coding, math, research, analysis, reporting, creative, orchestration, "
            "agentic, fast), measured speed (tokens/sec), is_local, context window, and billing "
            "(coding_plan + billed_at_zero). THE section for 'which model should I use for …'. Defaults "
            "to enabled models; pass enabled_only=false for the full catalogue.\n"
            "• coding_plans — billing accounts: 'flat' = subscription (calls really cost $0; the price "
            "fields are only the list price for comparison), 'credit' = prepaid API balance.\n"
            "• quotas — the user's own spending limits ($/day, $/cycle) and current usage.\n"
            "• providers — provider list (local-vs-cloud, plan default; keys masked).\n"
            "• cost_rates — the editable price table + which models have NO price.\n"
            "• service_models — which model each background job uses.\n\n"
            "When recommending a model, weigh capability against cost AND tell the user whether it is "
            "local (free, private) or cloud, and whether a subscription already covers it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["models", "coding_plans", "quotas", "providers",
                             "cost_rates", "service_models"],
                    "description": "Which settings section to read.",
                },
                "enabled_only": {
                    "type": "boolean",
                    "description": ("section='models' only: true (default) = only models the user can "
                                    "actually pick; false = the full catalogue including disabled ones."),
                },
            },
            "required": ["section"],
        },
    },
    {
        "name": "helpdesk_user_activity",
        "description": (
            "Helpdesk only. List what the user has done in this system: their recent chat sessions, "
            "their projects, their scheduled tasks, AND their code-mode terminal chats. The "
            "`terminal_chats` list covers the bottom-workspace terminal chats of code projects "
            "(excluded from the normal session list); each entry has a `live` flag and "
            "`terminal_chats_live_now` counts those with a turn streaming RIGHT NOW — use these to "
            "answer whether a terminal chat is currently active. Use it to give concrete, "
            "personalised tips that refer to the user's actual work instead of generic instructions."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "schedule_list",
        "description": "List all scheduled tasks with their status, next run time, and configuration.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_nodes",
        "description": (
            "List all registered remote nodes with their status, hostname, OS, tags, "
            "allowed tools, and resource usage. Use this to check what remote nodes are "
            "available before routing commands to them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "think",
        "description": (
            "Use the tool to think about something. It will not obtain new "
            "information or change anything, but just append the thought to the log. "
            "Use it after a tool result to reason about what you found before acting: "
            "check the result against the relevant policy or constraint, note which "
            "rule applies, and decide the next step. Especially valuable in "
            "policy-heavy, multi-step tasks where a wrong action has consequences."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string", "description": "A thought to think about."},
            },
            "required": ["thought"],
        },
    },
    {
        # VERBATIM from the upstream MCP server (modelcontextprotocol/servers,
        # src/sequentialthinking/index.ts) so the eval measures the ORIGINAL
        # mechanism, not an abbreviation. The 11-point "You should:" list is what
        # actually drives multi-call decomposition — dropping it (as an earlier
        # draft did) made the model call the tool once instead of stepping through
        # numbered thoughts. Kept in full despite prompt-bloat concerns because the
        # whole point of this tool is the procedural guidance.
        "name": "sequential_thinking",
        "description": (
            "A detailed tool for dynamic and reflective problem-solving through thoughts.\n"
            "This tool helps analyze problems through a flexible thinking process that can adapt and evolve.\n"
            "Each thought can build on, question, or revise previous insights as understanding deepens.\n"
            "\n"
            "When to use this tool:\n"
            "- Breaking down complex problems into steps\n"
            "- Planning and design with room for revision\n"
            "- Analysis that might need course correction\n"
            "- Problems where the full scope might not be clear initially\n"
            "- Problems that require a multi-step solution\n"
            "- Tasks that need to maintain context over multiple steps\n"
            "- Situations where irrelevant information needs to be filtered out\n"
            "\n"
            "Key features:\n"
            "- You can adjust total_thoughts up or down as you progress\n"
            "- You can question or revise previous thoughts\n"
            "- You can add more thoughts even after reaching what seemed like the end\n"
            "- You can express uncertainty and explore alternative approaches\n"
            "- Not every thought needs to build linearly - you can branch or backtrack\n"
            "- Generates a solution hypothesis\n"
            "- Verifies the hypothesis based on the Chain of Thought steps\n"
            "- Repeats the process until satisfied\n"
            "- Provides a correct answer\n"
            "\n"
            "Parameters explained:\n"
            "- thought: Your current thinking step, which can include:\n"
            "  * Regular analytical steps\n"
            "  * Revisions of previous thoughts\n"
            "  * Questions about previous decisions\n"
            "  * Realizations about needing more analysis\n"
            "  * Changes in approach\n"
            "  * Hypothesis generation\n"
            "  * Hypothesis verification\n"
            "- nextThoughtNeeded: True if you need more thinking, even if at what seemed like the end\n"
            "- thoughtNumber: Current number in sequence (can go beyond initial total if needed)\n"
            "- totalThoughts: Current estimate of thoughts needed (can be adjusted up/down)\n"
            "- isRevision: A boolean indicating if this thought revises previous thinking\n"
            "- revisesThought: If is_revision is true, which thought number is being reconsidered\n"
            "- branchFromThought: If branching, which thought number is the branching point\n"
            "- branchId: Identifier for the current branch (if any)\n"
            "- needsMoreThoughts: If reaching end but realizing more thoughts needed\n"
            "\n"
            "You should:\n"
            "1. Start with an initial estimate of needed thoughts, but be ready to adjust\n"
            "2. Feel free to question or revise previous thoughts\n"
            "3. Don't hesitate to add more thoughts if needed, even at the \"end\"\n"
            "4. Express uncertainty when present\n"
            "5. Mark thoughts that revise previous thinking or branch into new paths\n"
            "6. Ignore information that is irrelevant to the current step\n"
            "7. Generate a solution hypothesis when appropriate\n"
            "8. Verify the hypothesis based on the Chain of Thought steps\n"
            "9. Repeat the process until satisfied with the solution\n"
            "10. Provide a single, ideally correct answer as the final output\n"
            "11. Only set nextThoughtNeeded to false when truly done and a satisfactory answer is reached"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string", "description": "Your current thinking step"},
                "nextThoughtNeeded": {"type": "boolean", "description": "Whether another thought step is needed"},
                "thoughtNumber": {"type": "integer", "description": "Current thought number", "minimum": 1},
                "totalThoughts": {"type": "integer", "description": "Estimated total thoughts needed", "minimum": 1},
                "isRevision": {"type": "boolean", "description": "Whether this revises previous thinking"},
                "revisesThought": {"type": "integer", "description": "Which thought is being reconsidered", "minimum": 1},
                "branchFromThought": {"type": "integer", "description": "Branching point thought number", "minimum": 1},
                "branchId": {"type": "string", "description": "Branch identifier"},
                "needsMoreThoughts": {"type": "boolean", "description": "If more thoughts are needed"},
            },
            "required": ["thought", "nextThoughtNeeded", "thoughtNumber", "totalThoughts"],
        },
    },
    {
        # Trimmed port of waldzellai/model-enhancement-servers
        # metacognitiveMonitoring — flattened to gemma-manageable string arrays
        # (the original's nested object arrays are beyond weak local models'
        # tool-call reliability, see the v9.295.2 field-bleed incident). Like
        # think/sequential_thinking this is a NO-OP: the value is the forced
        # fact/inference/speculation split + the model following its own
        # recommendation. The procedural "You should:" list is the behavioral
        # trigger (Beschreibungs-Falle, v9.295.0) — do NOT abbreviate it.
        "name": "calibrate",
        "description": (
            "A tool for calibrating your answer against the evidence you actually found.\n"
            "It does not obtain new information and changes nothing — it forces an honest\n"
            "self-assessment before you answer: which statements are backed by documents\n"
            "you read, which are inferences, which are unsupported guesses, and what is\n"
            "missing.\n"
            "\n"
            "When to use this tool:\n"
            "- After searching/reading and BEFORE writing your final answer\n"
            "- Questions where the available documents might not contain the answer\n"
            "- Compliance/policy questions where a wrong guess has consequences\n"
            "- Whenever you are about to state something you did not actually read\n"
            "\n"
            "Parameters explained:\n"
            "- task: the question you are answering, in your own words\n"
            "- facts: statements DIRECTLY supported by documents you read this\n"
            "  conversation — name the source document for each\n"
            "- inferences: statements you derive from facts, but that no document states directly\n"
            "- speculation: statements supported by NO retrieved document (world knowledge, guesses)\n"
            "- gaps: what the question asks for that you could NOT find in the documents\n"
            "- confidence: 0.0-1.0 — how confident you are that the retrieved documents answer the question\n"
            "- recommendation: 'answer' (facts cover the question), 'answer_with_caveats'\n"
            "  (facts cover only part), or 'refuse' (the documents do not answer it)\n"
            "\n"
            "You should:\n"
            "1. Search and read the relevant documents FIRST — then calibrate\n"
            "2. Put a statement into facts ONLY if you actually read it in a document this conversation, and name the source\n"
            "3. Be honest: an empty facts list is a valid and useful result\n"
            "4. List every part of the question you found nothing about under gaps\n"
            "5. Recommend 'refuse' when facts is empty or the gaps cover the core of the question\n"
            "6. Recommend 'answer_with_caveats' when facts cover part of the question but real gaps remain\n"
            "7. Follow your own recommendation in the final answer: on 'refuse', state clearly that the documents do not answer the question and do NOT guess; on 'answer_with_caveats', name the gaps explicitly\n"
            "8. Never state anything in the final answer that you listed under speculation"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The question being answered, in your own words"},
                "facts": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Statements directly supported by documents read this conversation, each with its source",
                },
                "inferences": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Statements derived from facts but not directly stated in any document",
                },
                "speculation": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Statements supported by no retrieved document",
                },
                "gaps": {
                    "type": "array", "items": {"type": "string"},
                    "description": "What the question asks for that was not found in the documents",
                },
                "confidence": {
                    "type": "number", "minimum": 0, "maximum": 1,
                    "description": "Confidence (0.0-1.0) that the retrieved documents answer the question",
                },
                "recommendation": {
                    "type": "string", "enum": ["answer", "answer_with_caveats", "refuse"],
                    "description": "What the final answer should do, given facts and gaps",
                },
            },
            "required": ["task", "facts", "gaps", "confidence", "recommendation"],
        },
    },
    {
        "name": "schedule_history",
        "description": "Get execution history for scheduled tasks. Shows status, results, and timestamps.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Filter by schedule name (optional)"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
            "required": [],
        },
    },
    {
        "name": "mcp_connect",
        "description": (
            "Connect to an MCP server at runtime. Discovers tools from the server and makes them "
            "available as mcp_<name>_<tool> tools. Use transport='sse' for HTTP servers, 'stdio' for local commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "MCP server URL (for SSE) or command (for stdio)"},
                "name": {"type": "string", "description": "Friendly name for this connection"},
                "transport": {"type": "string", "description": "Transport type: 'sse' (default) or 'stdio'", "enum": ["sse", "stdio"]},
                "persist": {"type": "boolean", "description": "Save to mcp.json for reconnect on restart (default: false)"},
            },
            "required": ["url", "name"],
        },
    },
    {
        "name": "mcp_disconnect",
        "description": "Disconnect from a runtime MCP server. Its tools will no longer be available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the MCP server to disconnect"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "mcp_servers",
        "description": "List all connected MCP servers with their tools and status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "code_search",
        "description": (
            "Search the code knowledge graph to FIND code — use this INSTEAD OF grep/glob "
            "for discovering functions, classes, and routes in a large codebase. Three modes "
            "(combine as needed): (1) query='natural language or keywords' — BM25 full-text "
            "ranking with camelCase splitting, best for natural-language discovery; "
            "(2) name_pattern='.*regex.*' — match symbol names; (3) semantic_query=['kw1','kw2'] "
            "— embedding (vector) search, MUST be an array of keywords; results that score well "
            "on ALL keywords rank highest, so keep the keyword set tight and on-topic. "
            "Optional label ('Function'/'Method'/'Class') and limit. When you don't know the "
            "symbol's name, start with query (BM25) or semantic_query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language / keyword BM25 search"},
                "name_pattern": {"type": "string", "description": "Regex over symbol names"},
                "semantic_query": {"type": "array", "items": {"type": "string"},
                                   "description": "Array of keywords for embedding search (NOT a single string)"},
                "label": {"type": "string", "description": "Filter by node label: Function, Method, Class"},
                "limit": {"type": "integer", "description": "Max results (default 200)"},
            },
            "required": [],
        },
    },
    {
        "name": "code_trace",
        "description": (
            "Trace call relationships through the code graph — find CALLERS or CALLEES of a "
            "function/method. Use INSTEAD OF grep for 'who calls X' / 'what does X call' / "
            "impact analysis. direction: 'inbound' (callers, default), 'outbound' (callees), "
            "or 'both'. mode: 'calls' (default) or 'data_flow'. depth optional."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "function_name": {"type": "string", "description": "Function/method name (or qualified name)"},
                "direction": {"type": "string", "enum": ["inbound", "outbound", "both"],
                              "description": "inbound=callers (default), outbound=callees"},
                "mode": {"type": "string", "enum": ["calls", "data_flow"],
                         "description": "calls (default) follows CALLS edges"},
                "depth": {"type": "integer", "description": "Max hops (default 1)"},
            },
            "required": ["function_name"],
        },
    },
    {
        "name": "code_query",
        "description": (
            "Run a read-only Cypher query over the code knowledge graph for complex/multi-hop "
            "structural questions BM25/trace can't express. Node labels: Function, Method, Class, "
            "Variable, File, Module, Route. Edge types include CALLS, IMPORTS, INHERITS. "
            "Example: MATCH (c)-[:CALLS]->(f:Method) WHERE f.name='start' RETURN c.name, c.file_path"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Cypher query (read-only; MATCH/WHERE/RETURN)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "code_snippet",
        "description": (
            "Read the source code for a function/class/symbol from the code graph. Pass the "
            "qualified_name from a code_search result (or a short symbol name). "
            "include_neighbors=true also returns directly related symbols."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "qualified_name": {"type": "string", "description": "Qualified name (from code_search) or short name"},
                "include_neighbors": {"type": "boolean", "description": "Also return related symbols"},
            },
            "required": ["qualified_name"],
        },
    },
    {
        "name": "ast_grep_search",
        "description": (
            "Structural (AST-aware) code search: find code by SYNTAX PATTERN, not text. "
            "Use this when grep/search_files would drown in false positives — the pattern "
            "matches the parse tree, so formatting/whitespace/argument names don't matter. "
            "Pattern syntax: literal code with metavariables — $A matches ONE node "
            "(expression/identifier), $$$ matches any number of nodes. Examples: "
            "'str($A)' = every call to str with one argument; 'def $F($$$): $$$' = every "
            "function definition; 'if $C: return $R' = guard clauses; "
            "'$OBJ.get($K, $D)' = dict.get with default. "
            "You should: (1) state the pattern as REAL code of the target language, "
            "(2) pass lang when the root mixes languages (python, javascript, typescript, "
            "rust, go, java, …), (3) narrow root to a subdirectory for large repos. "
            "Root defaults to the code-mode project's working directory."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Structural pattern (real code with $METAVARS, $$$ for any-number)"},
                "root": {"type": "string", "description": "Directory or file to search (default: project working dir)"},
                "lang": {"type": "string", "description": "Language hint, e.g. python/javascript/typescript/go/rust"},
                "max_results": {"type": "integer", "description": "Max matches returned (default 50)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "ast_grep_replace",
        "description": (
            "Structural (AST-aware) refactoring: rewrite every match of a syntax pattern. "
            "SAFE BY DEFAULT — without apply=true this is a DRY-RUN that returns a preview "
            "(file, line, old text, replacement) and changes NOTHING. Workflow you should "
            "follow: (1) call without apply → inspect the preview, (2) if and ONLY if the "
            "preview is exactly what you want, repeat the SAME call with apply=true. "
            "The rewrite may reuse the pattern's metavariables: pattern='str($A)' with "
            "rewrite='repr($A)' turns str(x) into repr(x) everywhere. Mass rewrites over "
            "500 matches are refused — narrow the pattern or root. Modified files are "
            "tracked (code index updates automatically)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Structural pattern (real code with $METAVARS)"},
                "rewrite": {"type": "string", "description": "Replacement code; may reference the pattern's $METAVARS"},
                "root": {"type": "string", "description": "Directory or file to rewrite (default: project working dir)"},
                "lang": {"type": "string", "description": "Language hint, e.g. python/javascript/typescript/go/rust"},
                "apply": {"type": "boolean", "description": "false (default) = dry-run preview; true = write the changes"},
            },
            "required": ["pattern", "rewrite"],
        },
    },
    {
        "name": "git_command",
        "description": (
            "Execute git operations with structured output. Actions:\n"
            "- status: working tree status (modified, staged, untracked files)\n"
            "- diff: show changes (optional file path, staged=true for staged only)\n"
            "- log: commit history (limit, author, since, path filters)\n"
            "- branch: list/create/switch branches (name, create=true, switch=true)\n"
            "- commit: create commit (message required, files=[] to stage specific files, all=true for -a)\n"
            "- stash: stash/pop/list (sub_action: save/pop/list/drop)\n"
            "- blame: annotate file lines (path, line_start, line_end)\n"
            "- show: show commit details (ref)\n"
            "- tag: list/create tags (name, message)\n"
            "- remote: list remotes or show remote info"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "diff", "log", "branch", "commit", "stash", "blame", "show", "tag", "remote"],
                    "description": "Git operation to perform",
                },
                "message": {"type": "string", "description": "Commit/tag message"},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Specific files to stage/diff/blame"},
                "path": {"type": "string", "description": "File path for diff/blame/log"},
                "name": {"type": "string", "description": "Branch/tag name"},
                "ref": {"type": "string", "description": "Commit ref for show/diff (default: HEAD)"},
                "limit": {"type": "integer", "description": "Max entries for log (default: 20)"},
                "author": {"type": "string", "description": "Filter log by author"},
                "since": {"type": "string", "description": "Filter log since date (e.g., '1 week ago')"},
                "staged": {"type": "boolean", "description": "Show only staged changes for diff"},
                "create": {"type": "boolean", "description": "Create new branch/tag"},
                "switch": {"type": "boolean", "description": "Switch to branch"},
                "all": {"type": "boolean", "description": "Stage all changes for commit (-a)"},
                "sub_action": {"type": "string", "description": "Sub-action for stash (save/pop/list/drop)"},
                "line_start": {"type": "integer", "description": "Start line for blame"},
                "line_end": {"type": "integer", "description": "End line for blame"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "git_worktree",
        "description": (
            "Manage git-worktree LANES for the code-mode project: isolated checkouts under "
            "<working_dir>/.worktrees/<slug> on branch brain/<slug>, for risky or parallel "
            "work (refactors, dependency upgrades, experiments) that must not touch the main "
            "tree. You should: (1) ASK THE USER before creating or removing a lane (state "
            "slug, base and purpose), (2) after creating, do the work with normal file tools "
            "INSIDE the lane path and commit there, (3) review with action='diff' (base...branch "
            "+ uncommitted files), (4) NEVER merge automatically — integration is the user's "
            "deliberate terminal action (git merge brain/<slug>); offer the diff as the review. "
            "Actions: create {slug, base?, purpose?} · list · diff {slug, base?} · "
            "remove {slug, force?} (refuses a dirty lane without force; force discards). "
            "The lane dir is ignored via .git/info/exclude, never via the user's .gitignore. "
            "Only available in code-mode projects (uses the project's working directory)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "create | list | diff | remove"},
                "slug": {"type": "string", "description": "Lane name, ^[a-z0-9][a-z0-9_-]{0,39}$ (e.g. refactor-auth)"},
                "base": {"type": "string", "description": "Base ref for create/diff (default HEAD / the lane's recorded base)"},
                "purpose": {"type": "string", "description": "One-line purpose, stored in the lane registry"},
                "force": {"type": "boolean", "description": "remove only: discard uncommitted lane changes (ask the user first)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "github_command",
        "description": (
            "Interact with GitHub via the gh CLI. Requires gh to be installed and authenticated. Actions:\n"
            "- pr_list: list open PRs (limit, state, author)\n"
            "- pr_create: create PR (title, body, base, head, draft)\n"
            "- pr_view: view PR details (number)\n"
            "- pr_merge: merge a PR (number, method=merge|squash|rebase)\n"
            "- pr_review: list PR reviews/comments (number)\n"
            "- issue_list: list issues (limit, state, labels)\n"
            "- issue_create: create issue (title, body, labels)\n"
            "- issue_view: view issue details (number)\n"
            "- repo_view: show repo info\n"
            "- release_list: list releases\n"
            "- workflow_list: list GitHub Actions workflows\n"
            "- workflow_run: view workflow run status (run_id)\n"
            "- api: raw GitHub API call (endpoint, method)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pr_list", "pr_create", "pr_view", "pr_merge", "pr_review",
                             "issue_list", "issue_create", "issue_view",
                             "repo_view", "release_list", "workflow_list", "workflow_run", "api"],
                    "description": "GitHub operation to perform",
                },
                "number": {"type": "integer", "description": "PR or issue number"},
                "title": {"type": "string", "description": "PR/issue title"},
                "body": {"type": "string", "description": "PR/issue body"},
                "base": {"type": "string", "description": "Base branch for PR (default: main)"},
                "head": {"type": "string", "description": "Head branch for PR"},
                "draft": {"type": "boolean", "description": "Create PR as draft"},
                "method": {"type": "string", "description": "Merge method (merge/squash/rebase)"},
                "state": {"type": "string", "description": "Filter by state (open/closed/all)"},
                "labels": {"type": "string", "description": "Comma-separated labels"},
                "author": {"type": "string", "description": "Filter by author"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
                "run_id": {"type": "string", "description": "Workflow run ID"},
                "endpoint": {"type": "string", "description": "API endpoint for raw call (e.g., repos/{owner}/{repo}/issues)"},
                "api_method": {"type": "string", "description": "HTTP method for API call (GET/POST/PATCH)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "tool_search",
        "description": (
            "Search for available tools by name or description. Use this when you need a "
            "tool that isn't in your current tool list. Every returned tool is immediately "
            "callable: call it directly in your next step, exactly like your other tools. "
            "Only callable tools are returned (disabled tools never appear)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query to match against tool names and descriptions"},
                "max_results": {"type": "integer", "description": "Maximum results to return (default: 5)"},
            },
            "required": ["query"],
        },
    },
    # --- Worker Subagent Tools (v8.0.0) ---
    {
        "name": "get_artifact_detail",
        "description": (
            "Retrieve the raw content of a worker artifact. Use this to inspect "
            "the full output of a tool that was executed by a worker subagent. "
            "Optionally filter by a search query to extract only relevant sections."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "The artifact filename (from the worker envelope's artifacts[].artifact_id)"},
                "query": {"type": "string", "description": "Optional search term to extract only matching lines with context"},
                "offset": {"type": "integer", "description": "Character offset to start reading from (default: 0)"},
                "limit": {"type": "integer", "description": "Maximum characters to return (default: 16384)"},
            },
            "required": ["artifact_id"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user one or more clarifying questions that cannot be decided from available context. "
            "The chat pauses until the user answers. Use sparingly — prefer making reasonable decisions autonomously. "
            "When the user explicitly asks you to pose questions to them (e.g. \"ask me 5 questions about X\", "
            "\"interview me\", \"quiz me\"), pass them all in the `questions` array in a single call — "
            "this renders one interactive answer card in the UI with all questions at once. "
            "For a single clarifying question, either pass `question` (string) or a 1-item `questions` array. "
            "Returns {\"answers\": {<question>: <answer>, ...}} for a batch, or {\"answer\": str} for a single question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "Batch of 1-8 questions to ask the user. Each item: {question: str, options?: [str]}. Use this to ask multiple questions in one UI card.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "The question text"},
                            "options": {"type": "array", "items": {"type": "string"}, "description": "Optional multiple-choice options for this question"},
                        },
                        "required": ["question"],
                    },
                    "minItems": 1,
                    "maxItems": 8,
                },
                "question": {"type": "string", "description": "Single question text (alternative to `questions`). Use `questions` for multi-question batches."},
                "options": {"type": "array", "items": {"type": "string"}, "description": "Optional multiple-choice options (only used with single `question`)"},
                "context_summary": {"type": "string", "description": "Brief context so the user understands why these questions are being asked"},
                "timeout_seconds": {"type": "integer", "description": "Seconds to wait for an answer before aborting (default: 300)"},
            },
        },
    },
    {
        "name": "generate_image",
        "description": (
            "Generate a PHOTO/ILLUSTRATION from a text prompt using Mistral's native image "
            "generation service — for pictures, scenes, artwork, logos, mockups, photo-realistic "
            "or artistic visuals. Use this when the user wants an IMAGE to look at. "
            "The generated image is saved to the session artifact folder and shown in the Artifacts panel. "
            "Be descriptive: include subject, mood, style, lighting, and composition details. "
            "DO NOT use this for DIAGRAMS, CHARTS, ORG CHARTS, FLOWCHARTS, TIMELINES, MIND MAPS, or "
            "anything whose value is EXACT TEXT/LABELS/NUMBERS/CONNECTIONS: a diffusion image model "
            "CANNOT render legible, correct text — names, percentages and labels come out as garbled "
            "fake glyphs. For ALL diagrams/charts use **render_diagram** instead (Mermaid source → a "
            "real chart file with EXACT text). This includes when the user asks for a diagram 'as PNG' "
            "or 'as an image FILE' — that is render_diagram (format=png), NOT generate_image; 'PNG' "
            "does not mean this tool. Only when no file is needed (a quick in-CHAT diagram) write a "
            "```mermaid fenced block directly in your reply (e.g. `graph TD; A[Parent] --> B[Child]`) — "
            "the chat renders it live. generate_image is ONLY for photos/illustrations/artwork."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed description of the image to generate. Include subject, style, mood, lighting, composition.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                    "description": "Image aspect ratio. Use 16:9 for banners/landscapes, 9:16 for mobile/stories, 1:1 for square posts, 4:3 for classic format. Default: 1:1",
                },
                "style": {
                    "type": "string",
                    "description": "Optional style hint, e.g. 'photorealistic', 'flat illustration', 'minimalist', 'cinematic', 'watercolor'. Appended to the prompt.",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "render_diagram",
        "description": (
            "Render a DIAGRAM to a real image file (SVG/PNG/PDF) from Mermaid source — "
            "use this for org charts, flowcharts, structure/relationship charts, "
            "timelines, sequence/class/ER/state diagrams, gantt charts, mind maps, "
            "pie charts, quadrant/sankey/C4. The text/labels/numbers come out EXACT and "
            "legible (unlike generate_image, which garbles text). "
            "The result is saved to the session artifact folder and returned with `path` + "
            "`embed` snippets. "
            "PROFESSIONAL REPORTS: this is the way to put good-looking, data-accurate "
            "diagrams into a report. Workflow — call render_diagram to produce the image, "
            "then embed it in the document you build with write_document (PDF/DOCX/HTML) or "
            "Markdown. The default format is PNG (high-DPI), which embeds correctly in "
            "EVERY target — PDF, DOCX and HTML — so just take the default for reports; "
            "SVG is NOT embeddable in PDF or DOCX (only choose format=svg for an HTML-only "
            "report where vector zoom matters). For a quick in-CHAT diagram (no file needed) "
            "just write a ```mermaid fenced block instead — the chat renders it live. Use "
            "render_diagram when you need a FILE (report, download, embedding)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Mermaid diagram source, e.g. 'graph TD; A[Parent] --> B[Child]'. Any Mermaid 11 diagram type (flowchart/sequence/class/state/er/gantt/pie/journey/gitgraph/mindmap/timeline/quadrant/sankey/C4/block).",
                },
                "format": {
                    "type": "string",
                    "enum": ["svg", "png", "pdf"],
                    "description": "Output format. DEFAULT png = raster at high DPI (scale 4 / width 2000), embeds correctly in PDF, DOCX AND HTML — use it for any report/document. svg = vector, crisp/zoomable but NOT embeddable in PDF or DOCX (only for HTML-only reports). pdf = standalone diagram file. Default: png.",
                },
                "scale": {
                    "type": "number",
                    "description": "PNG/PDF only: device-pixel-ratio multiplier for resolution (1–5, default 4 = high-DPI/print-crisp). Raise to 5 for very dense charts that must stay legible when printed; ignored for svg (vector).",
                },
                "width": {
                    "type": "integer",
                    "description": "PNG/PDF only: base canvas width in px before scaling (400–6000, default 2000). Increase for wide org charts so labels don't cramp; ignored for svg.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional title — used for the artifact filename and as the image alt text.",
                },
                "theme": {
                    "type": "string",
                    "enum": ["default", "dark", "forest", "neutral"],
                    "description": "Mermaid theme. Default: 'default'. Use 'neutral' for a clean corporate report look.",
                },
                "background": {
                    "type": "string",
                    "enum": ["white", "transparent"],
                    "description": "Background. Default 'white' (good for documents); 'transparent' to overlay.",
                },
                "style": {
                    "type": "string",
                    "description": "Optional doc style preset name (skills/doc-styles/<name>) — inherits its mermaid theme/background so the diagram matches the report it goes into. Explicit theme/background override it.",
                },
            },
            "required": ["code"],
        },
    },
]

# Build OpenAI-compatible format automatically
TOOL_DEFINITIONS_OPENAI = []
for _td in TOOL_DEFINITIONS:
    TOOL_DEFINITIONS_OPENAI.append({
        "type": "function",
        "function": {
            "name": _td["name"],
            "description": _td["description"],
            "parameters": {
                "type": _td["input_schema"]["type"],
                "properties": _td["input_schema"]["properties"],
                "required": _td["input_schema"].get("required", []),
            },
        },
    })

# Tool name → definition index for fast lookup
_TOOL_DEF_INDEX = {td["name"]: td for td in TOOL_DEFINITIONS}
_TOOL_DEF_OPENAI_INDEX = {td["function"]["name"]: td for td in TOOL_DEFINITIONS_OPENAI}
