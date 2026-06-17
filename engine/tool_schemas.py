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
            "session's artifact folder and auto-promotes to the Artifacts panel; "
            "do NOT write to an absolute path (it won't appear there) unless the "
            "user explicitly gave you one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write to"},
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
        "name": "transcribe_audio",
        "description": (
            "Transcribe an audio file (.wav/.mp3/.m4a/.flac/.ogg) to text. "
            "Routes to any model in the models config flagged with the 'audio' capability — "
            "cloud Voxtral, local Whisper, or anything else added with that flag. Falls back to "
            "the configured local fallback when GDPR server_block is on or the cloud call errors. "
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
            "For unknown extensions, falls back to plain text read."
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
            ".docx (headings, tables, bold/italic, images), .xlsx (markdown tables to "
            "sheets), .pptx (# sections to slides, images), .pdf (formatted PDF via "
            "reportlab, images), .html (self-contained styled web report, images inlined "
            "as base64). For an HTML report ALWAYS use this tool, NOT write_file with "
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
            "PDF); in .pptx an image-only slide section becomes a centered full-slide picture."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Output file path (extension determines format)"},
                "content": {"type": "string", "description": "Markdown content to convert into the document"},
                "style": {"type": "string", "description": "Optional style preset name (a file in the agent's skills/doc-styles/, e.g. 'corporate') — sets fonts/colors/layout deterministically. Omit for the built-in default look."},
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
            "PPTX: update_slide (slide_index, title, body), add_slide (title, body)."
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
        "name": "helpdesk_user_activity",
        "description": (
            "Helpdesk only. List what the user has done in this system: their recent chat sessions, "
            "their projects, and their scheduled tasks. Use it to give concrete, personalised tips "
            "that refer to the user's actual work instead of generic instructions."
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
        "name": "code_graph_build",
        "description": (
            "Build or rebuild the code structure graph for a directory. Parses source files "
            "using Tree-sitter AST parsing to extract functions, classes, imports, and call "
            "relationships. Supports Python, JavaScript, TypeScript, Go, Rust, Java, and more. "
            "Use incremental=true (default) to only re-parse changed files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to parse (absolute path)"},
                "incremental": {"type": "boolean", "description": "Only re-parse changed files (default: true)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "code_graph_query",
        "description": (
            "Query the code structure graph for structural relationships. Find callers/callees "
            "of a function, imports, inheritance, test coverage, and more. Build the graph first "
            "with code_graph_build."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["callers_of", "callees_of", "imports_of", "importers_of",
                             "tests_for", "inheritors_of", "children_of", "file_summary"],
                    "description": "Type of structural query",
                },
                "target": {"type": "string", "description": "Qualified name or function/class name to query"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
            "required": ["query_type", "target"],
        },
    },
    {
        "name": "code_graph_impact",
        "description": (
            "Blast-radius analysis: given a list of changed files, find all functions, classes, "
            "and files that could be affected. Uses BFS traversal of the code graph. "
            "Build the graph first with code_graph_build."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of changed file paths",
                },
                "depth": {"type": "integer", "description": "Max traversal depth (default: 2)"},
            },
            "required": ["files"],
        },
    },
    {
        "name": "code_graph_enhance",
        "description": (
            "Enhance the code graph with LLM-generated summaries, architecture layer classification, "
            "and a guided tour. Actions: 'all' (default), 'summaries' (LLM descriptions per function/class), "
            "'layers' (classify as api/service/data/ui/util/test), 'tour' (dependency-ordered walkthrough)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["all", "summaries", "layers", "tour"],
                    "description": "What to generate (default: all)",
                },
                "batch_size": {"type": "integer", "description": "Max files to summarize per run (default: 20)"},
                "root_dir": {"type": "string", "description": "Root directory for tour (default: last build dir)"},
            },
            "required": [],
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
            "tool that isn't in your current tool list. Returns matching tool schemas that "
            "will be available on subsequent turns. Useful when MCP tools are deferred."
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
        "name": "worker_status",
        "description": "Get current state of running or completed worker subagents. Use this to inform the user what a background task is doing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Specific worker ID. Omit for all workers in this session."},
            },
        },
    },
    {
        "name": "worker_abort",
        "description": "Abort a running worker subagent. Idempotent — aborting an already-aborted worker returns success.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID to abort"},
                "reason": {"type": "string", "description": "Reason for aborting (logged and shown to user)"},
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "worker_pause",
        "description": "Pause a running worker at its next safepoint without terminating it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID to pause"},
                "reason": {"type": "string", "description": "Reason for pausing"},
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "worker_resume",
        "description": "Resume a paused worker without adding input.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID to resume"},
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "worker_send",
        "description": "Send additional context or instructions to a running or paused worker. If paused, also resumes the worker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID to send to"},
                "message": {"type": "string", "description": "Message content to inject into the worker's context"},
                "role": {"type": "string", "enum": ["user", "system"], "description": "Message role (default: user)"},
            },
            "required": ["worker_id", "message"],
        },
    },
    {
        "name": "worker_ask_user",
        "description": (
            "Ask the user one or more questions that cannot be decided from available context. "
            "The worker will pause until answered. Only available inside a worker subagent. "
            "Use sparingly — prefer making reasonable decisions autonomously. "
            "When the user explicitly asks you to pose questions to them (e.g. \"ask me 5 questions\", "
            "\"interview me\", \"quiz me\"), pass them all in the `questions` array in a single call — "
            "this renders one interactive answer card in the UI with all questions at once. "
            "For a single clarifying question, either pass `question` (string) or a 1-item `questions` array."
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
            "fake glyphs. For those, output a **Mermaid diagram** as a ```mermaid fenced code block "
            "directly in your reply (flowchart/graph for org charts & relationships, e.g. "
            "`graph TD; A[Wiener Privatbank SE] --> B[Tochter GmbH]`). The chat renders ```mermaid "
            "blocks as crisp diagrams with exact text — no image tool needed."
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
            "Markdown: use SVG for crisp HTML/PDF, PNG for DOCX. For a quick in-CHAT diagram "
            "(no file needed) just write a ```mermaid fenced block instead — the chat renders "
            "it live. Use render_diagram when you need a FILE (report, download, embedding)."
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
                    "description": "Output format. svg = crisp/scalable, resolution-independent (PREFER it for HTML & PDF embedding and whenever the diagram may be reused/zoomed); png = raster, rendered at high DPI (best for DOCX, which can't embed SVG); pdf = standalone. Default: svg.",
                },
                "scale": {
                    "type": "number",
                    "description": "PNG/PDF only: device-pixel-ratio multiplier for resolution (1–5, default 3 = high-DPI/retina-crisp). Raise to 4–5 for very dense charts that must stay legible when printed; ignored for svg (vector).",
                },
                "width": {
                    "type": "integer",
                    "description": "PNG/PDF only: base canvas width in px before scaling (400–6000, default 1600). Increase for wide org charts so labels don't cramp; ignored for svg.",
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
