#!/usr/bin/env python3
"""Minimal Mistral agentic loop for eval-harness experiments.

NO Brain dependency. Direct OpenAI-compatible HTTP call. Three tools:
  - mempalace_query: vector search via direct ChromaDB query (mirrors Brain's
    chroma-direct path, no closet boost / no reranker — minimal retrieval)
  - read_document: full file read (markdown / text / pdf-as-markdown — relies
    on the .brain-extracted/<name>.<ext>.md companions which are plain markdown)
  - read_file: generic file read with optional offset/limit

Usage:
  python3 eval/harness/run.py \\
      --question "Wie ist der Umgang mit Multilogin-Berechtigungen geregelt?" \\
      --system-prompt eval/harness/system_prompt.md \\
      --output /tmp/run1.json

Defaults:
  --base-url, --api-key, --model are read from <repo>/config.json's
  `mistral-vibe` provider entry. Override with flags.
  --palace-path defaults to /Users/alexander/.mempalace/brain.
  --wing defaults to project__f201b24ff6a2 (KG-Real-Policies).
  --max-rounds 15. --temperature 0.2. --top-p 0.85.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Config + provider resolution ----------

def _load_brain_config() -> dict:
    with open(os.path.join(REPO_ROOT, "config.json")) as f:
        return json.load(f)


def resolve_vibe_defaults() -> dict:
    """Look up base_url, api_key, base_model_id for mistral-vibe / mistral-medium-3.5
    from <repo>/config.json. Used when CLI flags omit them."""
    cfg = _load_brain_config()
    providers = cfg.get("providers", {})
    models = cfg.get("models", {})
    vibe = providers.get("mistral-vibe") or {}
    base_url = (vibe.get("base_url") or "").rstrip("/")
    api_key = vibe.get("api_key") or ""
    # Prefer the provider-scoped id if it exists, otherwise the unscoped one
    model_id = "mistral-vibe/mistral-medium-3.5"
    m = models.get(model_id) or models.get("mistral-medium-3.5") or {}
    base_model = m.get("base_model_id") or "mistral-medium-3.5"
    return {"base_url": base_url, "api_key": api_key, "base_model": base_model}


# ---------- MemPalace + file tools ----------

def _ensure_mempalace_importable() -> None:
    """Add the mempalace venv site-packages to sys.path. Idempotent."""
    cfg = _load_brain_config().get("mempalace", {}) or {}
    site = cfg.get("venv_site_packages", "")
    if site and os.path.isdir(site) and site not in sys.path:
        sys.path.insert(0, site)


def tool_mempalace_query(args: dict, palace_path: str, default_wing: str) -> dict:
    """Direct ChromaDB query against the palace. Same retrieval shape as
    Brain's tool_mempalace_query (chroma-direct + filename-token boost) but
    nothing else — no reranker, no closet boost, no user/team scoping."""
    _ensure_mempalace_importable()
    try:
        from mempalace.palace import get_collection
        from mempalace.searcher import build_where_filter
    except ImportError as e:
        return {"error": f"mempalace not importable: {e}"}

    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "mempalace_query: 'query' is required"}
    wing = args.get("wing") or default_wing
    room = args.get("room") or None
    n_results = args.get("n_results") or 5
    try:
        n_results = max(1, min(25, int(n_results)))
    except (TypeError, ValueError):
        n_results = 5

    try:
        col = get_collection(palace_path, create=False)
        if col is None:
            return {"error": f"palace collection not found at {palace_path}"}
        where = build_where_filter(wing, room) if (wing or room) else None
        kwargs = {
            "query_texts": [query],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        res = col.query(**kwargs)
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        out = []
        for doc, meta, dist in zip(docs, metas, dists):
            meta = meta or {}
            sim = max(0.0, 1.0 - float(dist or 0.0))
            row = {
                "wing": meta.get("wing", ""),
                "room": meta.get("room", ""),
                "source_file": meta.get("source_file", ""),
                "similarity": round(sim, 3),
                "text": (doc or "")[:1000],
            }
            # If source_file lives under .brain-extracted/, the original
            # binary is the same path with the .md suffix stripped.
            sf = row["source_file"]
            if sf:
                row["read_path"] = sf
                if ".brain-extracted/" in sf and sf.endswith(".md"):
                    row["read_path_original"] = sf[:-3]
            out.append(row)
        return _filename_boost(query, out)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _filename_boost(query: str, results: list) -> dict:
    """Re-rank results so drawers whose filename literally contains query
    tokens move up. Mirrors Brain's filename-token boost — important for
    questions like 'IT-Morgencheck' where pure vector retrieval underranks
    the perfectly-named file."""
    def tokenise(text: str) -> set:
        base = set(re.findall(r"\w{3,}", text.lower(), flags=re.UNICODE))
        cs_split = re.sub(r"(?<=[a-zäöü])(?=[A-ZÄÖÜ])", " ", text)
        sep_split = re.sub(r"[^A-Za-zÄÖÜäöüß]+", " ", cs_split).lower()
        base |= set(t for t in sep_split.split() if len(t) >= 3)
        return base

    qtoks = tokenise(query)
    if not qtoks:
        return {"results": results}

    def filename_tokens(name: str) -> set:
        name = re.sub(r"\.(pdf|docx|pptx|xlsx|xlsm|eml|msg)\.md$", r".\1",
                      name, flags=re.IGNORECASE)
        name = re.sub(r"(?<=[a-zäöü])(?=[A-ZÄÖÜ])", " ", name)
        name = name.lower()
        name = re.sub(r"[^a-zäöüß]+", " ", name)
        parts = [p for p in name.split() if len(p) >= 2]
        toks = set(p for p in parts if len(p) >= 3)
        for i in range(len(parts) - 1):
            pair = parts[i] + parts[i + 1]
            if len(pair) >= 3:
                toks.add(pair)
        return toks

    for r in results:
        sf = r.get("source_file") or ""
        bn = sf.split("/")[-1] if sf else ""
        if not bn:
            continue
        fn_toks = filename_tokens(bn)
        if not fn_toks:
            continue
        hits = len(qtoks & fn_toks)
        if hits:
            bonus = min(0.30, hits * 0.10)
            r["similarity"] = round(min(1.0, r["similarity"] + bonus), 3)
            r["filename_boost"] = round(bonus, 3)
    results.sort(key=lambda x: x.get("similarity", 0.0), reverse=True)
    return {"results": results}


def tool_read_document(args: dict) -> dict:
    """Plain file read for any path. The .brain-extracted/*.md companions
    are plain markdown — no PDF parsing needed for typical workflows."""
    path = (args.get("path") or args.get("source_file") or "").strip()
    if not path:
        return {"error": "read_document: 'path' is required"}
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    if not os.path.exists(path):
        return {"error": f"file not found: {path}"}
    if os.path.isdir(path):
        return {"error": f"is a directory: {path}"}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        # Cap the returned content to avoid blowing the model's context
        cap = int(args.get("max_chars") or 60000)
        if len(content) > cap:
            head = content[: int(cap * 0.7)]
            tail = content[-int(cap * 0.3):]
            content = head + "\n\n[... truncated, full file is " + str(len(content)) + " chars ...]\n\n" + tail
        return {"path": path, "content": content, "size": os.path.getsize(path)}
    except OSError as e:
        return {"error": f"OSError: {e}"}


def tool_read_file(args: dict) -> dict:
    """Generic file read with optional offset/limit by line count."""
    path = (args.get("path") or "").strip()
    if not path:
        return {"error": "read_file: 'path' is required"}
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    if not os.path.exists(path):
        return {"error": f"file not found: {path}"}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        offset = max(0, int(args.get("offset") or 0))
        limit = int(args.get("limit") or 2000)
        chunk = lines[offset: offset + limit]
        return {
            "path": path,
            "content": "".join(chunk),
            "total_lines": len(lines),
            "offset": offset,
            "limit": limit,
            "returned_lines": len(chunk),
        }
    except OSError as e:
        return {"error": f"OSError: {e}"}


# ---------- Tool definitions for the LLM ----------

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "mempalace_query",
            "description": "Semantic search over the indexed document corpus. Returns ranked drawer snippets (~800 chars each) with source file paths. Use 2-4 content-bearing keywords from the question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (2-4 keywords work best)."},
                    "n_results": {"type": "integer", "description": "Number of results to return. Default 5, max 25."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "Read a full source file. Pass the drawer's `read_path` (the .brain-extracted/<name>.<ext>.md companion) verbatim as `path`. Returns up to 60000 chars of content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file."},
                    "max_chars": {"type": "integer", "description": "Optional override for content cap (default 60000)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file by line range. Use for partial reads of large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "Line offset (0-indexed)."},
                    "limit": {"type": "integer", "description": "Max lines to return."},
                },
                "required": ["path"],
            },
        },
    },
]


def dispatch_tool(name: str, args: dict, palace_path: str, default_wing: str) -> dict:
    if name == "mempalace_query":
        return tool_mempalace_query(args, palace_path, default_wing)
    if name == "read_document":
        return tool_read_document(args)
    if name == "read_file":
        return tool_read_file(args)
    return {"error": f"unknown tool: {name}"}


# ---------- LLM call ----------

def call_llm(base_url: str, api_key: str, model: str, messages: list,
             temperature: float, top_p: float, timeout: float = 180.0) -> dict:
    body = {
        "model": model,
        "messages": messages,
        "tools": TOOL_DEFS,
        "tool_choice": "auto",
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": 4096,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base_url + "/chat/completions", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {base_url}: {err_body[:600]}")


# ---------- Agentic loop ----------

def run_loop(question: str, system_prompt: str, base_url: str, api_key: str,
             model: str, temperature: float, top_p: float, max_rounds: int,
             palace_path: str, default_wing: str, verbose: bool = True) -> dict:
    """Run a single Q&A through the agentic loop. Returns a transcript dict."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    transcript = {
        "question": question,
        "model": model,
        "base_url": base_url,
        "temperature": temperature,
        "top_p": top_p,
        "system_prompt_chars": len(system_prompt),
        "rounds": [],
        "final_answer": "",
        "tool_calls_total": 0,
        "elapsed_total_s": 0.0,
        "stop_reason": "",
    }
    t0 = time.time()
    for r in range(max_rounds):
        round_t0 = time.time()
        if verbose:
            print(f"\n=== round {r+1}/{max_rounds} ===")
        resp = call_llm(base_url, api_key, model, messages, temperature, top_p)
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        finish = choice.get("finish_reason", "")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        usage = resp.get("usage", {})

        round_record = {
            "round": r + 1,
            "elapsed_s": round(time.time() - round_t0, 2),
            "finish_reason": finish,
            "content_excerpt": (content or "")[:400],
            "content_chars": len(content or ""),
            "tool_calls": [],
            "usage": usage,
        }

        # Append the assistant message back into the history (with tool_calls
        # if any). Most OpenAI-compat servers require this for the tool-result
        # turn to validate.
        assistant_msg = {"role": "assistant", "content": content or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            transcript["final_answer"] = content
            transcript["stop_reason"] = finish or "no_tool_calls"
            transcript["rounds"].append(round_record)
            if verbose:
                print(f"  finish_reason={finish}  content_chars={len(content or '')}")
                print(f"  -> final answer ({len(content or '')} chars)")
            break

        # Execute each tool call and append a tool message with its result.
        for tc in tool_calls:
            tname = (tc.get("function") or {}).get("name") or "unknown"
            try:
                targs = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                targs = {}
            transcript["tool_calls_total"] += 1
            t_t0 = time.time()
            result = dispatch_tool(tname, targs, palace_path, default_wing)
            t_elapsed = round(time.time() - t_t0, 2)
            # Compact the result for both transcript and the message we feed back
            result_str = json.dumps(result, ensure_ascii=False)
            # Truncate huge results before sending to model so we don't blow
            # context on a single read_document
            if len(result_str) > 80000:
                result_str = result_str[:78000] + ' ... ", "_truncated": true}'
            round_record["tool_calls"].append({
                "name": tname,
                "args": targs,
                "elapsed_s": t_elapsed,
                "result_chars": len(result_str),
                "result_excerpt": result_str[:400],
            })
            if verbose:
                arg_preview = json.dumps(targs, ensure_ascii=False)
                if len(arg_preview) > 120:
                    arg_preview = arg_preview[:120] + "..."
                print(f"  tool_call: {tname}({arg_preview})  -> {len(result_str)}c in {t_elapsed}s")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id") or "",
                "name": tname,
                "content": result_str,
            })

        transcript["rounds"].append(round_record)

        if finish == "stop":
            # Defensive — shouldn't happen alongside tool_calls but some
            # providers do this. Drain by going one more round.
            pass
    else:
        transcript["stop_reason"] = "max_rounds"
        if verbose:
            print(f"\n!! hit max_rounds ({max_rounds}) without final answer")

    transcript["elapsed_total_s"] = round(time.time() - t0, 2)
    return transcript


# ---------- Main ----------

def main() -> int:
    defaults = resolve_vibe_defaults()
    ap = argparse.ArgumentParser(description="Minimal Mistral agentic loop for prompt experiments")
    ap.add_argument("--question", required=True, help="The user question to ask")
    ap.add_argument("--system-prompt", default=os.path.join(os.path.dirname(__file__), "system_prompt.md"),
                    help="Path to system prompt file")
    ap.add_argument("--model", default=defaults["base_model"], help="Model id passed to the API")
    ap.add_argument("--base-url", default=defaults["base_url"], help="OpenAI-compatible base URL (without /chat/completions)")
    ap.add_argument("--api-key", default=defaults["api_key"], help="Bearer token (default: from config.json mistral-vibe provider)")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--top-p", type=float, default=0.85)
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--palace-path", default="/Users/alexander/.mempalace/brain")
    ap.add_argument("--wing", default="project__f201b24ff6a2",
                    help="Default MemPalace wing for queries (KG-Real-Policies = project__f201b24ff6a2)")
    ap.add_argument("--output", help="Optional JSON output path (full transcript). Default: stdout summary only.")
    ap.add_argument("--quiet", action="store_true", help="Suppress per-round progress lines")
    args = ap.parse_args()

    if not args.api_key:
        print("ERROR: no --api-key and config.json mistral-vibe provider has none either", file=sys.stderr)
        return 2
    if not args.base_url:
        print("ERROR: no --base-url and config.json mistral-vibe provider has none either", file=sys.stderr)
        return 2

    with open(args.system_prompt, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    print(f"[harness] model={args.model}  T={args.temperature}  top_p={args.top_p}  max_rounds={args.max_rounds}")
    print(f"[harness] palace={args.palace_path}  wing={args.wing}")
    print(f"[harness] system_prompt={args.system_prompt} ({len(system_prompt)} chars)")
    print(f"[harness] question: {args.question}")

    transcript = run_loop(
        question=args.question,
        system_prompt=system_prompt,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        temperature=args.temperature,
        top_p=args.top_p,
        max_rounds=args.max_rounds,
        palace_path=args.palace_path,
        default_wing=args.wing,
        verbose=not args.quiet,
    )

    print("\n" + "=" * 70)
    print(f"FINAL ANSWER ({len(transcript['final_answer'])} chars):")
    print("=" * 70)
    print(transcript["final_answer"])
    print("\n" + "=" * 70)
    print(f"summary: {transcript['stop_reason']}, "
          f"{len(transcript['rounds'])} rounds, "
          f"{transcript['tool_calls_total']} tool calls, "
          f"{transcript['elapsed_total_s']}s")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(transcript, f, indent=2, ensure_ascii=False)
        print(f"transcript: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
