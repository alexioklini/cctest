#!/usr/bin/env python3
"""sql_mistral_eval.py — does mistral-small-latest ANALYZE / IMPROVE / REFACTOR /
EXTEND / REWRITE real-world SQL well, USING the project's synced data via tools,
judged by mistral-medium-3.5?

This is a RESEARCH eval (read-only models, no production-code changes). It mirrors
the conventions of the other eval harnesses in this dir:

  * provider resolution + the JUDGE call follow eval/moa_eval.py + eval/judge_mistral.py
    (read config.json, resolve each provider directly, POST /chat/completions — no
    running Brain/sidecar needed).
  * the AGENTIC loop follows eval/codegraph_e2e.py: the candidate is handed tool
    schemas, emits tool calls, and a LOCAL HTTP stub EXECUTES them for real against
    the synced corpus, feeding results back until the model writes a final answer.
    That local execution is how the model "uses the sync data".

The 3 tools exposed to the candidate (so it can reach the SYNCED project data):
  * sql_analyze  — runs a named analysis over the corpus via the REPO's real analyzer
                   engine.tools.sql_analysis (sql_tables / sql_complex / sql_servers /
                   sql_objects). This is the exact code the code-mode workspace ships.
  * sql_grep     — substring search across every .sql/.dbq query in the corpus
                   (find related queries / tables / columns by name).
  * sql_read_file— read another query file in full for cross-query context.

The task set + the 4-axis rubric are pinned in eval/sql_mistral_tasks.json (5 task
types). Cases are SAMPLED deterministically (sorted by path, fixed seed) across
complexity tiers — incl. the 103-join ESG_Abfrage_Privat.dbq and an OPENQUERY/DB2
file — and written to the results dir (cases.json) for reproducibility.

Per [[feedback_eval_single_run_noise]] a single run is noisy; reps default to 1
(this eval is expensive) but --reps N is accepted. Output is line-buffered
(flush=True) per [[feedback_evals_line_buffered]] so a backgrounded run streams live.

Usage:
  python3 -u eval/sql_mistral_eval.py --limit 1 --dry-run     # build cases, start
                                                              # stub, NO LLM calls
  python3 -u eval/sql_mistral_eval.py                         # full run (costs $$$)
  python3 -u eval/sql_mistral_eval.py --only analyze --limit 2
  python3 -u eval/sql_mistral_eval.py --reps 3
  python3 -u eval/sql_mistral_eval.py 20260629T120000Z        # pin results dir name
"""
import argparse
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)  # so `engine.tools.sql_analysis` imports read-only
CONFIG = os.path.join(REPO_ROOT, "config.json")
RESULTS = os.path.join(REPO_ROOT, "eval", "results")
TASKS_PATH = os.path.join(REPO_ROOT, "eval", "sql_mistral_tasks.json")

CANDIDATE_MODEL = "mistral-small-latest"
# the tasks file pins the provider-scoped judge id; fall back to the bare id which
# IS present in this config.json (mirrors judge_mistral.py's tolerant resolution).
JUDGE_MODEL_DEFAULT = "mistral-vibe/mistral-medium-3.5"
JUDGE_MODEL_FALLBACK = "mistral-medium-3.5"

CORPUS = os.environ.get(
    "SQL_CORPUS",
    # The corpus lives under eval/sql/ (real bank SQL/.dbq + result exports;
    # gitignored — local only). Override with SQL_CORPUS=<path> if it moves.
    os.path.join(REPO_ROOT, "eval", "sql"),
)

SQL_EXCERPT_CAP = 6000
MAX_ROUNDS = 6


# ── config / provider resolution (mirrors moa_eval.py / judge_mistral.py) ─────

def _load_config() -> dict:
    with open(CONFIG) as f:
        return json.load(f)


def _resolve_provider(model_id: str, config: dict):
    """Returns (api_key, base_url, base_model_id). Tolerant of provider-scoped ids."""
    models = config.get("models", {})
    providers = config.get("providers", {})
    if model_id not in models:
        raise KeyError(f"model {model_id!r} not in config.json[models]")
    m = models[model_id]
    provider_name = m.get("provider")
    base_model = m.get("base_model_id") or model_id.rsplit("/", 1)[-1]
    if not provider_name or provider_name not in providers:
        raise KeyError(
            f"provider {provider_name!r} for {model_id!r} not in config.json[providers]")
    p = providers[provider_name]
    return p["api_key"], p["base_url"].rstrip("/"), base_model


def _resolve_judge(model_id: str, config: dict):
    """Resolve the judge model, falling back to the bare id if the provider-scoped
    one isn't in this config (the tasks file pins mistral-vibe/...)."""
    try:
        return model_id, _resolve_provider(model_id, config)
    except KeyError:
        if model_id != JUDGE_MODEL_FALLBACK:
            return JUDGE_MODEL_FALLBACK, _resolve_provider(JUDGE_MODEL_FALLBACK, config)
        raise


def _approx_tokens(s: str) -> int:
    return max(1, len(s or "") // 4)


def call_chat(api_key, base_url, base_model, messages, *, tools=None,
              timeout=240.0, temperature=0.2, max_tokens=2048):
    """One non-streaming OpenAI chat-completion. Returns the parsed response dict."""
    body = {"model": base_model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base_url + "/chat/completions", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from {base_url}: {err_body[:500]}")


# ── corpus helpers ────────────────────────────────────────────────────────────

def _extract_sql_from_dbq(text: str) -> str:
    """A .dbq is an IBM query-tool XML export; the SQL lives in <Body>…</Body>."""
    bodies = re.findall(r"<Body>(.*?)</Body>", text, re.S)
    return "\n\n".join(b.strip() for b in bodies if b.strip())


def _read_file_text(fp: str) -> str:
    try:
        return open(fp, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""


def _sql_text_for(fp: str) -> str:
    """Return the SQL for a corpus file (.dbq → unwrap <Body>, else raw)."""
    txt = _read_file_text(fp)
    if fp.lower().endswith(".dbq"):
        return _extract_sql_from_dbq(txt) or txt
    return txt


def _walk_corpus():
    """Yield (relpath, abspath) for every .sql/.dbq file under the corpus, sorted."""
    root = os.path.abspath(CORPUS)
    out = []
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if fn.lower().endswith((".sql", ".dbq")):
                ap = os.path.join(dp, fn)
                out.append((os.path.relpath(ap, root), ap))
    out.sort(key=lambda t: t[0])
    return out


# ── deterministic case builder ────────────────────────────────────────────────

# explicit anchors we always want represented (large / OPENQUERY / DB2 tiers)
_ANCHOR_LARGE = "ESG_Abfrage_Privat.dbq"           # the 103-join monster
_ANCHOR_OPENQUERY = "AUW109_Risikoanalyse_Kundenprodukte.sql"  # OPENQUERY/DB2

# per-type synthesized extra instruction for 'extend' (needs an extra column/filter)
_EXTEND_INSTRUCTIONS = [
    "Ergänze eine zusätzliche Spalte für das Erstellungs-/Buchungsdatum (bzw. das "
    "nächstliegende vorhandene Datumsfeld) und filtere zusätzlich auf die letzten "
    "12 Monate. Nutze den vorhandenen Tabellen-/Spalten-Kontext.",
    "Ergänze eine zusätzliche Spalte mit der Kunden-/Personennummer (KNDN/IPID o. ä.) "
    "und filtere optional auf aktive Datensätze. Nutze den vorhandenen Spalten-Kontext.",
    "Ergänze eine zusätzliche Filterung auf einen Länder-/Währungscode-Parameter "
    "(@Land bzw. @CCY) und gib das Feld zusätzlich in der Ausgabe aus.",
    "Ergänze eine zusätzliche aggregierte Spalte (COUNT/SUM über die Hauptentität) "
    "und gruppiere das Ergebnis entsprechend, ohne die Kern-Semantik zu ändern.",
    "Ergänze eine zusätzliche Spalte mit dem Risiko-/Status-Kennzeichen der Hauptentität "
    "und filtere auf Datensätze, bei denen dieses Kennzeichen gesetzt ist.",
]


def _line_count(fp: str) -> int:
    try:
        with open(fp, encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _tier(n_lines: int) -> str:
    if n_lines < 30:
        return "small"
    if n_lines < 200:
        return "medium"
    return "large"


def _pick_for_type(ttype, files, n, seed):
    """Deterministically choose n files for a task type, spread across tiers and
    always including the two anchors among the 'large' picks where applicable.

    Determinism: we sort by path then index by a fixed-seed stride per type — no RNG.
    """
    by_tier = {"small": [], "medium": [], "large": []}
    anchor_paths = {}
    for rel, ap in files:
        base = os.path.basename(rel)
        by_tier[_tier(_line_count(ap))].append((rel, ap))
        if base == _ANCHOR_LARGE:
            anchor_paths["large"] = (rel, ap)
        if base == _ANCHOR_OPENQUERY:
            anchor_paths["openquery"] = (rel, ap)

    chosen = []
    # PREPEND the anchors so they survive the [:n] clamp. The ESG .dbq (103 joins)
    # and the AUW109 OPENQUERY/DB2 file are the deliberately-hard tiers the tasks
    # file requires represented; they matter most in the structure-heavy types, so
    # anchor 'analyze' + 'rewrite' (NB the ESG .dbq is only 17 lines of XML so it
    # is NOT caught by the line-count 'large' tier — it must be forced in by name).
    if ttype in ("analyze", "rewrite"):
        for key in ("large", "openquery"):
            a = anchor_paths.get(key)
            if a:
                chosen.append(a)

    # target spread: ~2 small, ~2 medium, ~1 large (clamped to availability)
    want = {"small": 2, "medium": 2, "large": 1}
    # stride offset varies per type so types don't all pick the same files
    type_offset = (seed + sum(ord(c) for c in ttype)) % 997
    for tier in ("small", "medium", "large"):
        pool = by_tier[tier]
        if not pool:
            continue
        k = min(want[tier], len(pool))
        step = max(1, len(pool) // k)
        for i in range(k):
            idx = (type_offset + i * step) % len(pool)
            chosen.append(pool[idx])

    # de-dup preserving order (anchors first), then clamp to n
    seen, uniq = set(), []
    for rel, ap in chosen:
        if rel in seen:
            continue
        seen.add(rel)
        uniq.append((rel, ap))
    return uniq[:n]


def build_cases(per_type=5, only=None, limit=None, seed=1337):
    files = _walk_corpus()
    if not files:
        raise SystemExit(f"no .sql/.dbq files under SQL_CORPUS={CORPUS}")
    with open(TASKS_PATH) as f:
        tasks_spec = json.load(f)
    task_types = tasks_spec["task_types"]

    types = list(task_types.keys())
    if only:
        types = [t for t in types if t == only]
        if not types:
            raise SystemExit(f"unknown --only {only!r}; known: {list(task_types)}")

    cases = []
    for ttype in types:
        picks = _pick_for_type(ttype, files, per_type, seed)
        for i, (rel, ap) in enumerate(picks):
            sql = _sql_text_for(ap)
            excerpt = sql[:SQL_EXCERPT_CAP]
            truncated = len(sql) > SQL_EXCERPT_CAP
            extra = ""
            if ttype == "extend":
                extra = _EXTEND_INSTRUCTIONS[i % len(_EXTEND_INSTRUCTIONS)]
            cases.append({
                "id": f"{ttype}_{i+1:02d}",
                "type": ttype,
                "instruction": task_types[ttype],
                "extra_instruction": extra,
                "file": rel,
                "n_lines": _line_count(ap),
                "tier": _tier(_line_count(ap)),
                "sql_excerpt": excerpt,
                "sql_truncated": truncated,
            })
    if limit:
        cases = cases[:limit]
    return cases, tasks_spec


# ── the 3 tools the candidate may call (executed for real by the local stub) ──

TOOLS = [
    {"type": "function", "function": {
        "name": "sql_analyze",
        "description": (
            "Run a named analysis over the SYNCED SQL project to understand the data "
            "landscape. analysis_id one of: sql_tables (most-referenced tables across "
            "all queries), sql_complex (queries ranked by join/SELECT count — the "
            "complex/refactor candidates), sql_servers (OPENQUERY linked-server "
            "dependencies), sql_objects (stored procedures & views inventory)."),
        "parameters": {"type": "object", "properties": {
            "analysis_id": {"type": "string",
                            "enum": ["sql_tables", "sql_complex", "sql_servers", "sql_objects"]}},
            "required": ["analysis_id"]}}},
    {"type": "function", "function": {
        "name": "sql_grep",
        "description": (
            "Case-insensitive substring search across every query file in the synced "
            "SQL project. Use it to find related queries, where a table/column is used, "
            "or example patterns for a join. Returns up to 30 matching files with a "
            "snippet line each."),
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "sql_read_file",
        "description": (
            "Read another query file from the synced SQL project in full (for cross-"
            "query context, e.g. how a sibling query joins the same tables). relpath is "
            "the project-relative path as returned by sql_grep/sql_analyze."),
        "parameters": {"type": "object", "properties": {
            "relpath": {"type": "string"}}, "required": ["relpath"]}}},
]


def _exec_sql_analyze(args):
    from engine.tools import sql_analysis
    aid = args.get("analysis_id", "")
    res = sql_analysis.sql_analyze(aid, os.path.abspath(CORPUS))
    if "error" in res:
        return json.dumps(res)
    # trim rows to keep the tool result compact (top 25)
    rows = res.get("rows", [])[:25]
    return json.dumps({"columns": res.get("columns"), "rows": rows,
                       "shown": len(rows), "sqlglot": res.get("sqlglot")},
                      ensure_ascii=False)


def _exec_sql_grep(args):
    pattern = (args.get("pattern") or "").lower()
    if not pattern:
        return json.dumps({"error": "empty pattern"})
    hits = []
    for rel, ap in _walk_corpus():
        txt = _sql_text_for(ap)
        low = txt.lower()
        pos = low.find(pattern)
        if pos == -1:
            continue
        # snippet: the line containing the first hit
        line_start = low.rfind("\n", 0, pos) + 1
        line_end = low.find("\n", pos)
        if line_end == -1:
            line_end = len(txt)
        snippet = txt[line_start:line_end].strip()[:200]
        hits.append({"file": rel, "snippet": snippet})
        if len(hits) >= 30:
            break
    return json.dumps({"pattern": args.get("pattern"), "matches": hits,
                       "count": len(hits)}, ensure_ascii=False)


def _exec_sql_read_file(args):
    relpath = args.get("relpath") or ""
    root = os.path.abspath(CORPUS)
    ap = os.path.abspath(os.path.join(root, relpath))
    # guard: stay inside the corpus
    if not ap.startswith(root + os.sep):
        return json.dumps({"error": "path outside corpus"})
    if not os.path.isfile(ap):
        return json.dumps({"error": f"not found: {relpath}"})
    sql = _sql_text_for(ap)[:SQL_EXCERPT_CAP]
    return json.dumps({"file": relpath, "sql": sql,
                       "truncated": len(_sql_text_for(ap)) > SQL_EXCERPT_CAP},
                      ensure_ascii=False)


_TOOL_EXEC = {
    "sql_analyze": _exec_sql_analyze,
    "sql_grep": _exec_sql_grep,
    "sql_read_file": _exec_sql_read_file,
}


# ── local HTTP stub that executes the tools for real ──────────────────────────

class _StubState:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []  # list of {name, args} for the current case

    def reset(self):
        with self.lock:
            self.calls = []


_STUB = _StubState()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        name = body.get("name", "")
        args = body.get("args", {}) or {}
        fn = _TOOL_EXEC.get(name)
        if fn is None:
            result = json.dumps({"error": f"unknown tool {name}"})
        else:
            try:
                result = fn(args)
            except Exception as e:  # noqa: BLE001 — never 500 the stub
                result = json.dumps({"error": f"{type(e).__name__}: {e}"})
        with _STUB.lock:
            _STUB.calls.append({"name": name, "args": args})
        out = json.dumps({"result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def start_stub():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, "http://127.0.0.1:%d/" % srv.server_address[1]


def _call_stub(stub_url, name, args):
    data = json.dumps({"name": name, "args": args}).encode()
    req = urllib.request.Request(stub_url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())["result"]


# ── the candidate agentic loop (OpenAI tool-calling, like codegraph_e2e) ──────

_CAND_SYSTEM = (
    "Du bist ein SQL-Experte und arbeitest in einem Code-Workspace, der ein großes "
    "Repository von SQL-Abfragen (T-SQL / IBM DB2-iSeries über OPENQUERY, plus .dbq-"
    "Exporte) bereits INDEXIERT (gesynct) hat. Du hast Werkzeuge, um diese gesyncten "
    "Daten zu nutzen: sql_analyze (Tabellen-Hotspots, komplexeste Abfragen, Linked-"
    "Server, Prozeduren/Views), sql_grep (Volltextsuche über alle Abfragen) und "
    "sql_read_file (eine andere Abfrage als Kontext lesen). NUTZE diese Werkzeuge, um "
    "den echten Projekt-Kontext (Tabellen, Spalten, verwandte Abfragen) heranzuziehen, "
    "statt zu raten. Antworte am Ende konkret und mit fertigem SQL, wo gefordert."
)


def _build_case_prompt(case):
    parts = [
        f"Aufgabe ({case['type'].upper()}): {case['instruction']}",
    ]
    if case["extra_instruction"]:
        parts.append(f"Zusätzliche Anforderung: {case['extra_instruction']}")
    parts.append(
        f"\nDatei: {case['file']} (Tier: {case['tier']}, {case['n_lines']} Zeilen"
        + (", Auszug gekürzt" if case["sql_truncated"] else "") + ")")
    parts.append("\nSQL:\n```sql\n" + case["sql_excerpt"] + "\n```")
    parts.append(
        "\nNutze bei Bedarf die Werkzeuge (sql_analyze / sql_grep / sql_read_file), "
        "um den gesyncten Projekt-Kontext einzubeziehen, und liefere dann deine "
        "vollständige Antwort.")
    return "\n".join(parts)


def run_candidate(case, api_key, base_url, base_model, stub_url, *,
                  max_rounds=MAX_ROUNDS, temperature=0.2):
    """Full agentic loop: model may call tools (executed by the stub) up to
    max_rounds, then writes a final answer. Returns (final_text, tool_calls, error)."""
    _STUB.reset()
    messages = [
        {"role": "system", "content": _CAND_SYSTEM},
        {"role": "user", "content": _build_case_prompt(case)},
    ]
    final_text, err = "", None
    for _round in range(max_rounds):
        try:
            resp = call_chat(api_key, base_url, base_model, messages,
                             tools=TOOLS, temperature=temperature, max_tokens=2048)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            break
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message", {}) or {}
        tool_calls = msg.get("tool_calls") or []
        # append the assistant message verbatim so tool_call_id linkage stays valid
        messages.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            **({"tool_calls": tool_calls} if tool_calls else {}),
        })
        if not tool_calls:
            final_text = msg.get("content") or ""
            break
        for tc in tool_calls:
            fn = (tc.get("function") or {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                result = _call_stub(stub_url, name, args)
            except Exception as e:  # noqa: BLE001
                result = json.dumps({"error": f"stub: {type(e).__name__}: {e}"})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result,
            })
    else:
        # hit max_rounds without a tool-free final — ask once for a final answer
        try:
            messages.append({"role": "user", "content":
                             "Bitte gib jetzt deine vollständige finale Antwort ohne "
                             "weitere Werkzeugaufrufe."})
            resp = call_chat(api_key, base_url, base_model, messages,
                             temperature=temperature, max_tokens=2048)
            final_text = ((resp.get("choices") or [{}])[0]
                          .get("message", {}).get("content", "")) or ""
        except Exception as e:  # noqa: BLE001
            err = err or f"{type(e).__name__}: {e}"
    with _STUB.lock:
        calls = list(_STUB.calls)
    return final_text, calls, err


# ── judge (mirrors judge_mistral.py) ──────────────────────────────────────────

_RUBRIC_AXES = ["correctness", "uses_synced_data", "sql_quality", "completeness"]


def _build_judge_prompt(case, rubric, answer, tool_calls):
    rubric_lines = "\n".join(f"- {ax} (0-4): {rubric[ax]}" for ax in _RUBRIC_AXES)
    tool_summary = ", ".join(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)[:80]})"
                             for c in tool_calls) or "(keine Werkzeugaufrufe)"
    extra = f"\nZusätzliche Anforderung: {case['extra_instruction']}" if case["extra_instruction"] else ""
    return (
        "Du bewertest die Antwort eines SQL-Assistenten auf eine Coding-Aufgabe über "
        "eine reale SQL-Abfragensammlung (Bank, T-SQL + IBM DB2/OPENQUERY).\n\n"
        f"# Aufgabe ({case['type']})\n{case['instruction']}{extra}\n\n"
        f"# Datei\n{case['file']} (Tier: {case['tier']}, {case['n_lines']} Zeilen)\n\n"
        f"# SQL (Auszug)\n```sql\n{case['sql_excerpt'][:3000]}\n```\n\n"
        f"# Vom Modell tatsächlich genutzte Sync-Werkzeuge\n{tool_summary}\n\n"
        f"# Antwort des Modells\n{answer}\n\n"
        "# Bewertungs-Rubrik (jede Achse 0-4, ganze Zahlen)\n"
        f"{rubric_lines}\n\n"
        "Bewerte streng. 'uses_synced_data' bewertet, ob das Modell den Projekt-/Index-"
        "Kontext über die Werkzeuge tatsächlich genutzt hat (nicht nur die nackte Datei). "
        "Gib AUSSCHLIESSLICH dieses JSON-Objekt aus, ohne Prosa, ohne Markdown-Fences:\n"
        '{"correctness": <0-4>, "uses_synced_data": <0-4>, "sql_quality": <0-4>, '
        '"completeness": <0-4>, "justification": "<1-2 Sätze>"}')


def parse_judge_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.MULTILINE)
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object found. head={text[:200]!r}")
    return json.loads(m.group(0))


def run_judge(case, rubric, answer, tool_calls, api_key, base_url, base_model):
    prompt = _build_judge_prompt(case, rubric, answer, tool_calls)
    messages = [
        {"role": "system", "content":
         "You are a precise scoring judge. Output ONLY the requested JSON object."},
        {"role": "user", "content": prompt},
    ]
    last_err = None
    for _attempt in range(2):
        try:
            resp = call_chat(api_key, base_url, base_model, messages,
                             temperature=0.0, max_tokens=600)
            content = ((resp.get("choices") or [{}])[0]
                       .get("message", {}).get("content", "")) or ""
            if not content.strip():
                last_err = "empty content"
                continue
            j = parse_judge_json(content)
            for ax in _RUBRIC_AXES:
                j[ax] = int(round(float(j.get(ax, 0))))
            j["total"] = sum(j[ax] for ax in _RUBRIC_AXES)
            return j
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    raise RuntimeError(f"judge failed after 2 attempts: {last_err}")


# ── summary writers ───────────────────────────────────────────────────────────

def _write_summaries(out_dir, rows):
    """rows = per-case dicts with type, scores per axis, total, tool_used, error."""
    csv_path = os.path.join(out_dir, "summary.csv")
    fields = ["id", "type", "tier", "file", "rep", "tool_used", "n_tool_calls",
              "correctness", "uses_synced_data", "sql_quality", "completeness",
              "total", "error"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

    # aggregate per type
    by_type = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)

    def _mean(vals):
        vals = [v for v in vals if isinstance(v, (int, float))]
        return sum(vals) / len(vals) if vals else float("nan")

    lines = ["# SQL-Mistral eval — summary",
             "",
             f"Candidate: `{CANDIDATE_MODEL}`  ·  cases: {len(rows)}",
             "",
             "| type | n | correctness | uses_synced_data | sql_quality | completeness | total | tool-use rate |",
             "|------|--:|------------:|-----------------:|------------:|-------------:|------:|--------------:|"]
    all_scored = [r for r in rows if not r.get("error")]
    for ttype in sorted(by_type):
        grp = [r for r in by_type[ttype] if not r.get("error")]
        n = len(by_type[ttype])
        tu = _mean([1.0 if r.get("tool_used") else 0.0 for r in by_type[ttype]])
        lines.append(
            f"| {ttype} | {n} | {_mean([r.get('correctness') for r in grp]):.2f} | "
            f"{_mean([r.get('uses_synced_data') for r in grp]):.2f} | "
            f"{_mean([r.get('sql_quality') for r in grp]):.2f} | "
            f"{_mean([r.get('completeness') for r in grp]):.2f} | "
            f"{_mean([r.get('total') for r in grp]):.2f} | {tu*100:.0f}% |")
    lines.append("")
    overall_tool = _mean([1.0 if r.get("tool_used") else 0.0 for r in rows])
    lines.append(
        "**Overall** — "
        f"correctness {_mean([r.get('correctness') for r in all_scored]):.2f}, "
        f"uses_synced_data {_mean([r.get('uses_synced_data') for r in all_scored]):.2f}, "
        f"sql_quality {_mean([r.get('sql_quality') for r in all_scored]):.2f}, "
        f"completeness {_mean([r.get('completeness') for r in all_scored]):.2f}, "
        f"total {_mean([r.get('total') for r in all_scored]):.2f} / 16")
    lines.append(f"**Tool-use rate** (model called a sync-data tool): {overall_tool*100:.0f}%")
    n_err = sum(1 for r in rows if r.get("error"))
    if n_err:
        lines.append(f"**Errors:** {n_err} case(s) failed (see per-case json).")
    md_path = os.path.join(out_dir, "summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return csv_path, md_path


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    global CANDIDATE_MODEL
    ap = argparse.ArgumentParser()
    ap.add_argument("stamp", nargs="?", default=None,
                    help="results-dir suffix (default: UTC timestamp derived at startup)")
    ap.add_argument("--reps", type=int, default=1, help="reps per case (eval is expensive)")
    ap.add_argument("--only", default=None, help="restrict to one task type")
    ap.add_argument("--limit", type=int, default=None, help="cap total cases built")
    ap.add_argument("--per-type", type=int, default=5, help="cases sampled per task type")
    ap.add_argument("--judge-model", default=JUDGE_MODEL_DEFAULT)
    ap.add_argument("--candidate", default=None,
                    help="candidate model id (config.json), e.g. gemma-4-12B-it-qat-4bit, "
                         "codestral-2508. Default: " + CANDIDATE_MODEL)
    ap.add_argument("--dry-run", action="store_true",
                    help="build cases + start stub + verify it, NO LLM calls")
    args = ap.parse_args()

    # Let --candidate override the module default (used by the summary + resolver).
    if args.candidate:
        CANDIDATE_MODEL = args.candidate

    sys.stdout.reconfigure(line_buffering=True)  # live progress (feedback_evals_line_buffered)

    stamp = args.stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.path.join(RESULTS, f"sql_mistral_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    print(f"[sql-eval] corpus: {CORPUS}", flush=True)
    cases, tasks_spec = build_cases(per_type=args.per_type, only=args.only,
                                    limit=args.limit)
    rubric = tasks_spec["rubric"]
    with open(os.path.join(out_dir, "cases.json"), "w", encoding="utf-8") as f:
        json.dump(cases, f, indent=2, ensure_ascii=False)
    print(f"[sql-eval] built {len(cases)} cases -> {out_dir}/cases.json", flush=True)

    srv, stub_url = start_stub()
    print(f"[sql-eval] tool stub: {stub_url}", flush=True)

    if args.dry_run:
        print("\n[DRY RUN] cases:", flush=True)
        for c in cases:
            print(f"  {c['id']:14s} {c['tier']:6s} {c['n_lines']:>5}L  {c['file']}",
                  flush=True)
        # verify the stub executes a real tool against the synced corpus
        print("\n[DRY RUN] verifying tool stub (sql_analyze sql_tables) ...", flush=True)
        try:
            res = _call_stub(stub_url, "sql_analyze", {"analysis_id": "sql_tables"})
            parsed = json.loads(res)
            nrows = len(parsed.get("rows", []))
            print(f"  sql_analyze ok: {nrows} rows, columns={parsed.get('columns')}",
                  flush=True)
            grep = json.loads(_call_stub(stub_url, "sql_grep", {"pattern": "OPENQUERY"}))
            print(f"  sql_grep ok: {grep.get('count')} files match 'OPENQUERY'", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  STUB VERIFY FAILED: {e}", flush=True)
            return 1
        print("\n[DRY RUN] done — no LLM calls made.", flush=True)
        return 0

    config = _load_config()
    api_key, base_url, base_model = _resolve_provider(CANDIDATE_MODEL, config)
    judge_id, (jkey, jbase, jmodel) = _resolve_judge(args.judge_model, config)
    print(f"[sql-eval] candidate={CANDIDATE_MODEL} base={base_url}", flush=True)
    print(f"[sql-eval] judge={judge_id} (base_model={jmodel})", flush=True)

    rows = []
    t_start = time.time()
    total = len(cases) * args.reps
    done = 0
    for case in cases:
        for rep in range(args.reps):
            done += 1
            cid = f"{case['id']}#r{rep}" if args.reps > 1 else case["id"]
            t0 = time.time()
            answer, tool_calls, cand_err = run_candidate(
                case, api_key, base_url, base_model, stub_url)
            tool_used = bool(tool_calls)
            row = {"id": case["id"], "rep": rep, "type": case["type"],
                   "tier": case["tier"], "file": case["file"],
                   "tool_used": tool_used, "n_tool_calls": len(tool_calls)}
            judge = None
            if cand_err:
                row["error"] = f"candidate: {cand_err}"
            else:
                try:
                    judge = run_judge(case, rubric, answer, tool_calls,
                                      jkey, jbase, jmodel)
                    for ax in _RUBRIC_AXES:
                        row[ax] = judge[ax]
                    row["total"] = judge["total"]
                except Exception as e:  # noqa: BLE001
                    row["error"] = f"judge: {e}"
            rows.append(row)
            # persist this case immediately (crash-safe partial results)
            per_case = {"case": case, "rep": rep, "final_answer": answer,
                        "tool_calls": tool_calls, "candidate_error": cand_err,
                        "judge": judge, "elapsed_s": round(time.time() - t0, 1)}
            with open(os.path.join(out_dir, f"{cid.replace('#', '_')}.json"),
                      "w", encoding="utf-8") as f:
                json.dump(per_case, f, indent=2, ensure_ascii=False)
            score = (f"total={row.get('total')}" if "total" in row
                     else f"ERR {row.get('error', '')[:60]}")
            print(f"[{done}/{total}] {cid:16s} {case['type']:9s} {case['tier']:6s} "
                  f"tools={len(tool_calls)} {score}  ({time.time()-t0:.1f}s)", flush=True)
            # rewrite summaries after every case so a crash keeps a usable summary
            _write_summaries(out_dir, rows)

    csv_path, md_path = _write_summaries(out_dir, rows)
    print(f"\n[sql-eval] done in {time.time()-t_start:.0f}s", flush=True)
    print(f"[sql-eval] summary: {md_path}", flush=True)
    print(f"[sql-eval] csv:     {csv_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
