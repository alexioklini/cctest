#!/usr/bin/env python3
"""Anthropic-format agentic loop for eval-harness experiments.

Mirrors `eval/harness/run.py` (the OpenAI-format minimal loop) but talks to
CLIProxyAPI's Anthropic `/v1/messages` endpoint at http://localhost:8317.
No Brain dependency, no SDK install — raw HTTP, identical tool surface.

Tools exposed to the model:
  - mempalace_query     (direct ChromaDB; mirrors Brain's chroma-direct path)
  - mempalace_kg_search (direct SQLite over knowledge_graph.sqlite3)
  - read_document       (full file read up to ~60K chars)
  - read_file           (line-paginated read)
  - exa_search          (raw POST to api.exa.ai/search)
  - web_fetch           (urllib GET, HTML stripped to ~markdown via regex)
  - write_file          (only enabled with --allow-write, for scheduled-task replay)

Usage (single question):
  python3 eval/sdk_harness/run.py \
      --question "Wie ist der Umgang mit Multilogin-Berechtigungen geregelt?" \
      --system-prompt eval/sdk_harness/system_prompt_lean.md \
      --output /tmp/sdk_run1.json

Usage (scheduled-task replay):
  python3 eval/sdk_harness/run.py \
      --question "$(sqlite3 agents/main/scheduler.db 'select task from schedules where id=95')" \
      --system-prompt eval/sdk_harness/system_prompt_scheduler.md \
      --tools exa_search,web_fetch,write_file \
      --allow-write \
      --output /tmp/sched_replay.json
"""

import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Config + provider resolution ----------

def _load_brain_config() -> dict:
    with open(os.path.join(REPO_ROOT, "config.json")) as f:
        return json.load(f)


def _ensure_mempalace_importable() -> None:
    cfg = _load_brain_config().get("mempalace", {}) or {}
    site = cfg.get("venv_site_packages", "")
    if site and os.path.isdir(site) and site not in sys.path:
        sys.path.insert(0, site)


# ---------- MemPalace + file tools (lifted from eval/harness/run.py) ----------

def tool_mempalace_query(args: dict, palace_path: str, default_wing: str) -> dict:
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


def tool_mempalace_kg_search(args: dict, palace_path: str) -> dict:
    """Minimal KG search over <palace>/knowledge_graph.sqlite3.
    Returns triples whose subject/predicate/object literal contains the query
    substring (case-insensitive). Limited to 20 results."""
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "mempalace_kg_search: 'query' is required"}
    db_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
    if not os.path.isfile(db_path):
        return {"error": f"KG database not found at {db_path}"}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        like = f"%{query}%"
        rows = cur.execute(
            "SELECT subject, predicate, object, source_file, confidence "
            "FROM triples "
            "WHERE subject LIKE ? OR predicate LIKE ? OR object LIKE ? "
            "ORDER BY confidence DESC NULLS LAST LIMIT 20",
            (like, like, like),
        ).fetchall()
        conn.close()
        return {"results": [dict(r) for r in rows]}
    except sqlite3.Error as e:
        return {"error": f"sqlite: {e}"}


def tool_read_document(args: dict) -> dict:
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
        cap = int(args.get("max_chars") or 60000)
        if len(content) > cap:
            head = content[: int(cap * 0.7)]
            tail = content[-int(cap * 0.3):]
            content = head + "\n\n[... truncated, full file is " + str(len(content)) + " chars ...]\n\n" + tail
        return {"path": path, "content": content, "size": os.path.getsize(path)}
    except OSError as e:
        return {"error": f"OSError: {e}"}


def tool_read_file(args: dict) -> dict:
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


# ---------- Web tools (lifted from brain.py minus the Brain plumbing) ----------

EXA_DEFAULT_KEY = "97dbd594-f7b4-4866-9a8e-6a297e3df576"  # matches brain.py:4460 fallback


def tool_exa_search(args: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "exa_search: 'query' is required"}
    num_results = int(args.get("num_results") or 5)
    category = args.get("category")
    api_key = os.environ.get("EXA_API_KEY") or EXA_DEFAULT_KEY
    body = {"query": query, "type": "auto", "num_results": num_results}
    if category:
        body["category"] = category
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        req = urllib.request.Request(
            "https://api.exa.ai/search",
            data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8"))
        out = [{"title": r.get("title", ""), "link": r.get("url", "")}
               for r in data.get("results", [])]
        return {"query": query, "results": out, "result_count": len(out)}
    except urllib.error.HTTPError as e:
        return {"error": f"Exa HTTP {e.code}: {e.read()[:300]!r}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def tool_web_fetch(args: dict) -> dict:
    url = (args.get("url") or "").strip()
    if not url:
        return {"error": "web_fetch: 'url' is required"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            html = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    # Strip scripts/styles, then tags, then collapse whitespace — same shape
    # Brain's web_fetch returns when the heavy-mode summariser isn't used.
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", "", html)
    text = re.sub(r"(?s)<!--.*?-->", "", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    cap = int(args.get("max_chars") or 30000)
    if len(text) > cap:
        text = text[:cap] + f"\n\n[... truncated, full page is {len(text)} chars ...]"
    return {"url": url, "content": text, "chars": len(text)}


# ---------- write_file (gated, for scheduled-task replay) ----------

_WRITE_GATE = {"allow": False, "base_dir": "/tmp"}


def tool_write_file(args: dict) -> dict:
    if not _WRITE_GATE["allow"]:
        return {"error": "write_file disabled (use --allow-write)"}
    path = (args.get("path") or "").strip()
    content = args.get("content", "")
    if not path:
        return {"error": "write_file: 'path' is required"}
    # Force writes into the configured base dir to avoid escaping
    base = os.path.abspath(_WRITE_GATE["base_dir"])
    if not os.path.isabs(path):
        path = os.path.join(base, path)
    path = os.path.abspath(path)
    if not path.startswith(base + os.sep) and path != base:
        return {"error": f"write_file: path must live under {base}"}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"path": path, "bytes": len(content.encode("utf-8"))}
    except OSError as e:
        return {"error": f"OSError: {e}"}


# ---------- Tool registry + Anthropic tool schemas ----------

_TOOL_SCHEMAS = {
    "mempalace_query": {
        "name": "mempalace_query",
        "description": "Semantic search over the indexed document corpus. Returns ranked drawer snippets (~800 chars each) with source file paths. Use 2-4 content-bearing keywords from the question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (2-4 keywords work best)."},
                "n_results": {"type": "integer", "description": "Number of results to return. Default 5, max 25."},
            },
            "required": ["query"],
        },
    },
    "mempalace_kg_search": {
        "name": "mempalace_kg_search",
        "description": "Search the knowledge-graph triples. Returns subject-predicate-object triples whose any field matches the query substring (case-insensitive).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    "read_document": {
        "name": "read_document",
        "description": "Read a full source file. Pass the drawer's `read_path` verbatim as `path`. Returns up to 60000 chars of content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file."},
                "max_chars": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    "read_file": {
        "name": "read_file",
        "description": "Read a file by line range. Use for partial reads of large files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
    },
    "exa_search": {
        "name": "exa_search",
        "description": "Web search via Exa. Returns up to num_results items with title + link only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "num_results": {"type": "integer", "description": "Default 5."},
                "category": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    "web_fetch": {
        "name": "web_fetch",
        "description": "Fetch a URL and return the page as plain text. HTML is stripped to readable text, capped at 30000 chars.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["url"],
        },
    },
    "write_file": {
        "name": "write_file",
        "description": "Write content to a file. Path can be relative (resolved against the working directory) or absolute.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
}


def _dispatch(name: str, args: dict, palace_path: str, default_wing: str) -> dict:
    if name == "mempalace_query":
        return tool_mempalace_query(args, palace_path, default_wing)
    if name == "mempalace_kg_search":
        return tool_mempalace_kg_search(args, palace_path)
    if name == "read_document":
        return tool_read_document(args)
    if name == "read_file":
        return tool_read_file(args)
    if name == "exa_search":
        return tool_exa_search(args)
    if name == "web_fetch":
        return tool_web_fetch(args)
    if name == "write_file":
        return tool_write_file(args)
    return {"error": f"unknown tool: {name}"}


# ---------- Anthropic /v1/messages call (raw HTTP) ----------

def call_anthropic(base_url: str, api_key: str, model: str,
                   system: str, messages: list, tools: list,
                   max_tokens: int = 16000, temperature: float = 0.2,
                   top_p: float = 0.85, timeout: float = 240.0) -> dict:
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
        "tools": tools,
        "temperature": temperature,
        "top_p": top_p,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/v1/messages",
                                 data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {base_url}: {err[:600]}")


# ---------- Agentic loop ----------

def run_loop(question: str, system_prompt: str, base_url: str, api_key: str,
             model: str, tool_names: list, max_rounds: int,
             palace_path: str, default_wing: str,
             temperature: float = 0.2, top_p: float = 0.85,
             verbose: bool = True) -> dict:
    tools_payload = [_TOOL_SCHEMAS[t] for t in tool_names if t in _TOOL_SCHEMAS]
    messages = [{"role": "user", "content": question}]
    transcript = {
        "question": question,
        "model": model,
        "base_url": base_url,
        "tools_enabled": tool_names,
        "system_prompt_chars": len(system_prompt),
        "rounds": [],
        "final_answer": "",
        "tool_calls_total": 0,
        "elapsed_total_s": 0.0,
        "stop_reason": "",
        "usage_total": {"input_tokens": 0, "output_tokens": 0},
    }
    t0 = time.time()
    for r in range(max_rounds):
        round_t0 = time.time()
        if verbose:
            print(f"\n=== round {r+1}/{max_rounds} ===", flush=True)
        resp = call_anthropic(base_url, api_key, model, system_prompt,
                              messages, tools_payload,
                              temperature=temperature, top_p=top_p)
        usage = resp.get("usage", {})
        transcript["usage_total"]["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
        transcript["usage_total"]["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
        stop = resp.get("stop_reason", "")
        content_blocks = resp.get("content") or []
        # Capture text + tool_use blocks
        text_parts = []
        tool_uses = []
        for blk in content_blocks:
            if blk.get("type") == "text":
                text_parts.append(blk.get("text", ""))
            elif blk.get("type") == "tool_use":
                tool_uses.append(blk)
        text = "\n".join(p for p in text_parts if p)
        round_record = {
            "round": r + 1,
            "elapsed_s": round(time.time() - round_t0, 2),
            "stop_reason": stop,
            "content_chars": len(text),
            "content_excerpt": text[:400],
            "tool_calls": [],
            "usage": usage,
        }
        # Append the assistant turn back into history (Anthropic requires the
        # full content list — both text blocks AND tool_use blocks)
        messages.append({"role": "assistant", "content": content_blocks})

        if not tool_uses:
            transcript["final_answer"] = text
            transcript["stop_reason"] = stop or "no_tool_use"
            transcript["rounds"].append(round_record)
            if verbose:
                print(f"  stop_reason={stop}  text_chars={len(text)}", flush=True)
            break

        # Run every tool_use and add tool_result blocks in one user message
        result_blocks = []
        for tu in tool_uses:
            tname = tu.get("name", "?")
            targs = tu.get("input") or {}
            transcript["tool_calls_total"] += 1
            t_t0 = time.time()
            result = _dispatch(tname, targs, palace_path, default_wing)
            t_elapsed = round(time.time() - t_t0, 2)
            result_str = json.dumps(result, ensure_ascii=False)
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
                ap = json.dumps(targs, ensure_ascii=False)
                if len(ap) > 120:
                    ap = ap[:120] + "..."
                print(f"  tool_use: {tname}({ap})  -> {len(result_str)}c in {t_elapsed}s", flush=True)
            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tu.get("id", ""),
                "content": result_str,
            })
        messages.append({"role": "user", "content": result_blocks})
        transcript["rounds"].append(round_record)
    else:
        transcript["stop_reason"] = "max_rounds"
        if verbose:
            print(f"\n!! hit max_rounds ({max_rounds}) without final answer", flush=True)

    transcript["elapsed_total_s"] = round(time.time() - t0, 2)
    return transcript


# ---------- Main ----------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Anthropic-format minimal agentic loop (talks to CLIProxyAPI)")
    ap.add_argument("--question", required=True)
    ap.add_argument("--system-prompt",
                    default=os.path.join(os.path.dirname(__file__), "system_prompt_lean.md"))
    ap.add_argument("--base-url", default="http://localhost:8317")
    ap.add_argument("--api-key", default=os.environ.get("CLIPROXY_KEY", "brain-agent"))
    ap.add_argument("--model", default="mistral-medium-3.5")
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--top-p", type=float, default=0.85)
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--palace-path", default="/Users/alexander/.mempalace/brain")
    ap.add_argument("--wing", default="project__f201b24ff6a2")
    ap.add_argument("--tools",
                    default="mempalace_query,mempalace_kg_search,read_document,read_file",
                    help="Comma-separated tool names; defaults to the policy-eval set")
    ap.add_argument("--allow-write", action="store_true",
                    help="Enable write_file (needed for scheduled-task replay)")
    ap.add_argument("--write-base-dir", default="/tmp/sdk_harness_out",
                    help="write_file is forced under this directory")
    ap.add_argument("--output", help="JSON transcript output path")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.allow_write:
        _WRITE_GATE["allow"] = True
        _WRITE_GATE["base_dir"] = args.write_base_dir
        os.makedirs(args.write_base_dir, exist_ok=True)

    with open(args.system_prompt, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    tool_names = [t.strip() for t in args.tools.split(",") if t.strip()]

    print(f"[sdk-harness] model={args.model}  base_url={args.base_url}", flush=True)
    print(f"[sdk-harness] tools={tool_names}", flush=True)
    print(f"[sdk-harness] system_prompt={args.system_prompt} ({len(system_prompt)} chars)", flush=True)
    print(f"[sdk-harness] question: {args.question[:200]}{'...' if len(args.question)>200 else ''}", flush=True)

    transcript = run_loop(
        question=args.question,
        system_prompt=system_prompt,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        tool_names=tool_names,
        max_rounds=args.max_rounds,
        palace_path=args.palace_path,
        default_wing=args.wing,
        temperature=args.temperature,
        top_p=args.top_p,
        verbose=not args.quiet,
    )

    print("\n" + "=" * 70)
    print(f"FINAL ANSWER ({len(transcript['final_answer'])} chars):")
    print("=" * 70)
    print(transcript["final_answer"])
    print("\n" + "=" * 70)
    print(f"summary: stop_reason={transcript['stop_reason']}  "
          f"rounds={len(transcript['rounds'])}  "
          f"tool_calls={transcript['tool_calls_total']}  "
          f"in={transcript['usage_total']['input_tokens']}  "
          f"out={transcript['usage_total']['output_tokens']}  "
          f"{transcript['elapsed_total_s']}s")

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(transcript, f, indent=2, ensure_ascii=False)
        print(f"transcript: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
