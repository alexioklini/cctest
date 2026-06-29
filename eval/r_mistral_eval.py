#!/usr/bin/env python3
"""r_mistral_eval.py — does mistral-small-latest ANALYZE / IMPROVE / REFACTOR /
EXTEND / REWRITE real-world R scripts well, USING the project's synced R-analysis
data via tools, judged by mistral-medium?

The R analog of eval/sql_mistral_eval.py. Same RESEARCH-eval conventions (read-only
models, no production-code changes), same harness shape:

  * provider resolution + the JUDGE call follow eval/moa_eval.py + eval/judge_mistral.py
    (read config.json, resolve each provider directly, POST /chat/completions — no
    running Brain/sidecar needed).
  * the AGENTIC loop: the candidate is handed tool schemas, emits tool calls, and a
    LOCAL HTTP stub EXECUTES them for real against the synced R corpus, feeding
    results back until the model writes a final answer. That local execution is how
    the model "uses the sync data".

The 3 tools exposed to the candidate (so it can reach the SYNCED R project data):
  * r_analyze   — runs a named analysis over the corpus via the REPO's real analyzer
                  engine.tools.r_analysis (r_functions / r_dataflow / r_sources /
                  r_globals). This is the exact code the code-mode workspace ships.
                  r_globals matters most: the corpus is Base-R with heavy GLOBAL STATE,
                  so checking r_globals before refactoring is the rewarded behavior.
  * r_grep      — substring search across every .R/.r file in the corpus.
  * r_read_file — read another .R file in full (e.g. the source()-ed helper functions).

The task set + the 4-axis rubric are pinned in eval/r_mistral_tasks.json (5 task
types). Cases are SAMPLED deterministically (sorted targets, fixed seed) at the
FUNCTION/SCRIPT level — only 6 files exist, so a per-file split is too coarse: each
type gets a mix of whole-script targets AND individual-function targets (a function
body extracted by name from the verified analyzer's function inventory). Cases are
written to the results dir (cases.json) for reproducibility.

Per [[feedback_eval_single_run_noise]] a single run is noisy; reps default to 1
(this eval is expensive) but --reps N is accepted. Output is line-buffered
(flush=True) per [[feedback_evals_line_buffered]] so a backgrounded run streams live.

Usage:
  python3 -u eval/r_mistral_eval.py --dry-run            # build cases, start stub,
                                                          # verify a tool call, NO LLM
  python3 -u eval/r_mistral_eval.py                      # full run (costs $$$)
  python3 -u eval/r_mistral_eval.py --only analyze --per-type 2
  python3 -u eval/r_mistral_eval.py --reps 3
  python3 -u eval/r_mistral_eval.py 20260629T120000Z     # pin results dir name
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
sys.path.insert(0, REPO_ROOT)  # so `engine.tools.r_analysis` imports read-only
CONFIG = os.path.join(REPO_ROOT, "config.json")
RESULTS = os.path.join(REPO_ROOT, "eval", "results")
TASKS_PATH = os.path.join(REPO_ROOT, "eval", "r_mistral_tasks.json")

CANDIDATE_MODEL = "mistral-small-latest"
# the tasks file pins the judge id; fall back to a sibling id present in this
# config.json (mirrors judge_mistral.py's tolerant resolution).
JUDGE_MODEL_DEFAULT = "mistral-medium"
JUDGE_MODEL_FALLBACK = "mistral-medium-latest"

# Default to the in-repo corpus (eval/r_corpus) so the eval is reproducible from
# a clean checkout; override with R_CORPUS to point at another tree.
CORPUS = os.environ.get(
    "R_CORPUS",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "r_corpus"),
)

R_EXCERPT_CAP = 6000
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
    """Resolve the judge model, falling back to a sibling id if the pinned one isn't
    in this config (the tasks file pins mistral-medium)."""
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

def _read_file_text(fp: str) -> str:
    try:
        return open(fp, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""


def _walk_corpus():
    """Yield (relpath, abspath) for every .R/.r file under the corpus, sorted."""
    root = os.path.abspath(CORPUS)
    out = []
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if fn.lower().endswith((".r",)):
                ap = os.path.join(dp, fn)
                out.append((os.path.relpath(ap, root), ap))
    out.sort(key=lambda t: t[0])
    return out


# function-def regex reused from engine.tools.r_analysis (Base-R `name <- function(args)`)
_RE_FUNC = re.compile(r"^\s*([A-Za-z.][\w.]*)\s*(?:<-|=)\s*function\s*\(([^)]*)\)", re.M)


def _func_bodies(text: str):
    """Yield (name, line, nargs, body) per top-level function def — mirrors
    engine.tools.r_analysis._func_bodies (body = def line up to next top-level def)."""
    defs = list(_RE_FUNC.finditer(text))
    for i, m in enumerate(defs):
        name = m.group(1)
        nargs = len([a for a in m.group(2).split(",") if a.strip()])
        line = text[:m.start()].count("\n") + 1
        end = defs[i + 1].start() if i + 1 < len(defs) else len(text)
        body = text[m.start():end]
        yield name, line, nargs, body


# ── deterministic case builder ────────────────────────────────────────────────

# per-type synthesized extra instruction for 'extend' (needs a new parameter+use)
_EXTEND_INSTRUCTIONS = [
    "Ergänze einen zusätzlichen Parameter `stichtag` (Default = der bisher fest "
    "verdrahtete bzw. ein sinnvoller Standardwert) und verwende ihn an den passenden "
    "Stellen. Nutze den vorhandenen Funktions-/Daten-Kontext aus dem Projekt.",
    "Ergänze einen zusätzlichen Parameter `szenario` mit Default `\"base\"` und nutze "
    "ihn, um zwischen Szenarien zu unterscheiden, ohne die Kern-Semantik zu ändern.",
    "Ergänze einen zusätzlichen Parameter `verbose = FALSE`, der bei TRUE Zwischen-"
    "ergebnisse meldet (message/print), und verwende ihn an sinnvollen Stellen.",
    "Ergänze einen zusätzlichen Parameter `output_path` (Default = der bisher fest "
    "verdrahtete Pfad) und nutze ihn für den Datei-Export. Prüfe vorher den Daten-Fluss.",
    "Ergänze einen zusätzlichen Parameter `currency` (Default `\"EUR\"`) und gib ihn "
    "im Ergebnis aus bzw. nutze ihn in der Berechnung, wo es fachlich passt.",
]


def _line_count_text(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def _file_targets():
    """All whole-script targets: [(target, code, n_lines)] sorted by relpath."""
    out = []
    for rel, ap in _walk_corpus():
        txt = _read_file_text(ap)
        out.append((rel, txt, _line_count_text(txt)))
    return out


def _function_targets():
    """All individual-function targets across the corpus: [(target, code, n_lines)]
    where target = 'relfile::funcname' and code = the function body. Deterministic:
    sorted by (relfile, funcname, line). Reuses the analyzer's function-finding."""
    out = []
    for rel, ap in _walk_corpus():
        txt = _read_file_text(ap)
        for name, line, _nargs, body in _func_bodies(txt):
            out.append((f"{rel}::{name}", body, _line_count_text(body), line))
    # sort by target (file::func) then line — fully deterministic, no RNG
    out.sort(key=lambda t: (t[0], t[3]))
    return [(t[0], t[1], t[2]) for t in out]


def _pick_for_type(ttype, file_tgts, func_tgts, n, seed):
    """Deterministically choose n targets for a task type: a mix of whole-script and
    individual-function targets. Determinism: fixed-seed stride per type, no RNG.

    Whole scripts are best for analyze/rewrite (structure-level reasoning); function
    targets dominate improve/refactor/extend (surgical edits). We always include at
    least one whole-script and pull the rest from functions, spread by a per-type
    stride so types don't all land on the same targets."""
    type_offset = (seed + sum(ord(c) for c in ttype)) % 997

    # how many whole scripts vs functions for this type (clamped to availability)
    want_files = {"analyze": 2, "rewrite": 2}.get(ttype, 1)
    want_files = min(want_files, len(file_tgts), max(1, n - 1) if n > 1 else n)

    chosen = []
    if file_tgts and want_files:
        step = max(1, len(file_tgts) // want_files)
        for i in range(want_files):
            idx = (type_offset + i * step) % len(file_tgts)
            chosen.append(("script", file_tgts[idx]))

    need_funcs = max(0, n - len(chosen))
    if func_tgts and need_funcs:
        # spread the function picks: start each type at a distinct base index (so
        # types don't all collide on the same function) and stride by 3 so a single
        # type spans several distinct functions. Deterministic — derived from the
        # fixed seed + type name, no RNG.
        base = type_offset % len(func_tgts)
        for i in range(need_funcs):
            idx = (base + i * 3) % len(func_tgts)
            chosen.append(("function", func_tgts[idx]))

    # de-dup by target name preserving order, then clamp to n
    seen, uniq = set(), []
    for kind, (target, code, nlines) in chosen:
        if target in seen:
            continue
        seen.add(target)
        uniq.append((kind, target, code, nlines))
    return uniq[:n]


def build_cases(per_type=3, only=None, limit=None, seed=1337):
    file_tgts = _file_targets()
    func_tgts = _function_targets()
    if not file_tgts:
        raise SystemExit(f"no .R/.r files under R_CORPUS={CORPUS}")
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
        picks = _pick_for_type(ttype, file_tgts, func_tgts, per_type, seed)
        for i, (kind, target, code, nlines) in enumerate(picks):
            excerpt = code[:R_EXCERPT_CAP]
            truncated = len(code) > R_EXCERPT_CAP
            extra = ""
            if ttype == "extend":
                extra = _EXTEND_INSTRUCTIONS[i % len(_EXTEND_INSTRUCTIONS)]
            cases.append({
                "id": f"{ttype}_{i+1:02d}",
                "type": ttype,
                "instruction": task_types[ttype],
                "extra_instruction": extra,
                "target": target,
                "target_kind": kind,             # 'script' | 'function'
                "n_lines": nlines,
                "code_excerpt": excerpt,
                "code_truncated": truncated,
            })
    if limit:
        cases = cases[:limit]
    return cases, tasks_spec


# ── the 3 tools the candidate may call (executed for real by the local stub) ──

TOOLS = [
    {"type": "function", "function": {
        "name": "r_analyze",
        "description": (
            "Run a named analysis over the SYNCED R project to understand the data + "
            "code landscape. analysis_id one of: r_functions (every function with "
            "file/line/args/call-count — duplicates marked ⚠), r_dataflow (which script "
            "reads/writes which CSV/xlsx data file), r_sources (the source() dependency "
            "graph between scripts), r_globals (each function by length + how many "
            "top-level/GLOBAL names it references — the global-state coupling). The "
            "corpus is Base-R with heavy global state, so CHECK r_globals before any "
            "refactor."),
        "parameters": {"type": "object", "properties": {
            "analysis_id": {"type": "string",
                            "enum": ["r_functions", "r_dataflow", "r_sources", "r_globals"]}},
            "required": ["analysis_id"]}}},
    {"type": "function", "function": {
        "name": "r_grep",
        "description": (
            "Case-insensitive substring search across every .R file in the synced R "
            "project. Use it to find where a function/variable/global is defined or "
            "used, or example patterns. Returns up to 30 matching lines as "
            "file:line:text."),
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "r_read_file",
        "description": (
            "Read another .R file from the synced R project in full (for cross-file "
            "context, e.g. the source()-ed helper functions in ECL_Hilfsfunktion.R). "
            "relpath is the project-relative path as returned by r_grep/r_analyze."),
        "parameters": {"type": "object", "properties": {
            "relpath": {"type": "string"}}, "required": ["relpath"]}}},
]


def _exec_r_analyze(args):
    from engine.tools import r_analysis
    aid = args.get("analysis_id", "")
    res = r_analysis.r_analyze(aid, os.path.abspath(CORPUS))
    if "error" in res:
        return json.dumps(res)
    # trim rows to keep the tool result compact (top 25)
    rows = res.get("rows", [])[:25]
    return json.dumps({"columns": res.get("columns"), "rows": rows,
                       "shown": len(rows)}, ensure_ascii=False)


def _exec_r_grep(args):
    pattern = (args.get("pattern") or "").lower()
    if not pattern:
        return json.dumps({"error": "empty pattern"})
    hits = []
    for rel, ap in _walk_corpus():
        txt = _read_file_text(ap)
        for lineno, line in enumerate(txt.splitlines(), start=1):
            if pattern in line.lower():
                hits.append({"file": rel, "line": lineno,
                             "text": line.strip()[:200]})
                if len(hits) >= 30:
                    break
        if len(hits) >= 30:
            break
    return json.dumps({"pattern": args.get("pattern"), "matches": hits,
                       "count": len(hits)}, ensure_ascii=False)


def _exec_r_read_file(args):
    relpath = args.get("relpath") or ""
    root = os.path.abspath(CORPUS)
    ap = os.path.abspath(os.path.join(root, relpath))
    # guard: stay inside the corpus
    if not ap.startswith(root + os.sep):
        return json.dumps({"error": "path outside corpus"})
    if not os.path.isfile(ap):
        return json.dumps({"error": f"not found: {relpath}"})
    code = _read_file_text(ap)
    return json.dumps({"file": relpath, "code": code[:R_EXCERPT_CAP],
                       "truncated": len(code) > R_EXCERPT_CAP},
                      ensure_ascii=False)


_TOOL_EXEC = {
    "r_analyze": _exec_r_analyze,
    "r_grep": _exec_r_grep,
    "r_read_file": _exec_r_read_file,
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


# ── the candidate agentic loop (OpenAI tool-calling, like sql_mistral_eval) ───

_CAND_SYSTEM = (
    "Du bist ein R-Experte und arbeitest in einem Code-Workspace, der ein Repository "
    "von R-Skripten bereits INDEXIERT (gesynct) hat. Es handelt sich um Base-R "
    "Quant-Risk-Skripte (IFRS-9 ECL/PIT/TTC) mit HEAVY GLOBAL STATE: viele Variablen "
    "leben global, Hilfsfunktionen werden über source() geladen und teils dupliziert. "
    "Du hast Werkzeuge, um diese gesyncten Daten zu nutzen: r_analyze (r_functions = "
    "Funktionsinventar inkl. Duplikate, r_dataflow = welche Datei liest/schreibt was, "
    "r_sources = source()-Abhängigkeiten, r_globals = Funktionen nach Größe + Global-"
    "Kopplung), r_grep (Volltextsuche über alle .R-Dateien) und r_read_file (eine "
    "andere .R-Datei als Kontext lesen, z. B. die Hilfsfunktionen). NUTZE diese "
    "Werkzeuge, um den echten Projekt-Kontext (Funktionen, globaler Zustand, Daten-"
    "Fluss, verwandte Skripte) heranzuziehen, statt zu raten — insbesondere PRÜFE vor "
    "jedem Refactor die Global-Kopplung über r_globals. Antworte am Ende konkret und "
    "mit fertigem, lauffähigem R-Code, wo gefordert."
)


def _build_case_prompt(case):
    parts = [
        f"Aufgabe ({case['type'].upper()}): {case['instruction']}",
    ]
    if case["extra_instruction"]:
        parts.append(f"Zusätzliche Anforderung: {case['extra_instruction']}")
    kind = "Funktion" if case["target_kind"] == "function" else "Skript"
    parts.append(
        f"\nZiel ({kind}): {case['target']} ({case['n_lines']} Zeilen"
        + (", Auszug gekürzt" if case["code_truncated"] else "") + ")")
    parts.append("\nR-Code:\n```r\n" + case["code_excerpt"] + "\n```")
    parts.append(
        "\nNutze bei Bedarf die Werkzeuge (r_analyze / r_grep / r_read_file), um den "
        "gesyncten Projekt-Kontext einzubeziehen (Global-Kopplung, Hilfsfunktionen, "
        "Daten-Fluss), und liefere dann deine vollständige Antwort.")
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

_RUBRIC_AXES = ["correctness", "uses_synced_data", "r_quality", "completeness"]


def _build_judge_prompt(case, rubric, answer, tool_calls):
    rubric_lines = "\n".join(f"- {ax} (0-4): {rubric[ax]}" for ax in _RUBRIC_AXES)
    tool_summary = ", ".join(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)[:80]})"
                             for c in tool_calls) or "(keine Werkzeugaufrufe)"
    extra = f"\nZusätzliche Anforderung: {case['extra_instruction']}" if case["extra_instruction"] else ""
    kind = "Funktion" if case["target_kind"] == "function" else "Skript"
    return (
        "Du bewertest die Antwort eines R-Assistenten auf eine Coding-Aufgabe über "
        "eine reale R-Skript-Sammlung (Base-R, IFRS-9 ECL/PIT/TTC, heavy global "
        "state, source()-Hilfsfunktionen, teils dupliziert).\n\n"
        f"# Aufgabe ({case['type']})\n{case['instruction']}{extra}\n\n"
        f"# Ziel ({kind})\n{case['target']} ({case['n_lines']} Zeilen)\n\n"
        f"# R-Code (Auszug)\n```r\n{case['code_excerpt'][:3000]}\n```\n\n"
        f"# Vom Modell tatsächlich genutzte Sync-Werkzeuge\n{tool_summary}\n\n"
        f"# Antwort des Modells\n{answer}\n\n"
        "# Bewertungs-Rubrik (jede Achse 0-4, ganze Zahlen)\n"
        f"{rubric_lines}\n\n"
        "Bewerte streng. 'uses_synced_data' bewertet, ob das Modell den Projekt-/Index-"
        "Kontext über die Werkzeuge tatsächlich genutzt hat (insbesondere die Global-"
        "Kopplung via r_globals vor einem Refactor geprüft hat), nicht nur die nackte "
        "Datei gelesen hat. Gib AUSSCHLIESSLICH dieses JSON-Objekt aus, ohne Prosa, "
        "ohne Markdown-Fences:\n"
        '{"correctness": <0-4>, "uses_synced_data": <0-4>, "r_quality": <0-4>, '
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
    fields = ["id", "type", "target_kind", "target", "rep", "tool_used", "n_tool_calls",
              "correctness", "uses_synced_data", "r_quality", "completeness",
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

    lines = ["# R-Mistral eval — summary",
             "",
             f"Candidate: `{CANDIDATE_MODEL}`  ·  cases: {len(rows)}",
             "",
             "| type | n | correctness | uses_synced_data | r_quality | completeness | total | tool-use rate |",
             "|------|--:|------------:|-----------------:|----------:|-------------:|------:|--------------:|"]
    all_scored = [r for r in rows if not r.get("error")]
    for ttype in sorted(by_type):
        grp = [r for r in by_type[ttype] if not r.get("error")]
        n = len(by_type[ttype])
        tu = _mean([1.0 if r.get("tool_used") else 0.0 for r in by_type[ttype]])
        lines.append(
            f"| {ttype} | {n} | {_mean([r.get('correctness') for r in grp]):.2f} | "
            f"{_mean([r.get('uses_synced_data') for r in grp]):.2f} | "
            f"{_mean([r.get('r_quality') for r in grp]):.2f} | "
            f"{_mean([r.get('completeness') for r in grp]):.2f} | "
            f"{_mean([r.get('total') for r in grp]):.2f} | {tu*100:.0f}% |")
    lines.append("")
    overall_tool = _mean([1.0 if r.get("tool_used") else 0.0 for r in rows])
    lines.append(
        "**Overall** — "
        f"correctness {_mean([r.get('correctness') for r in all_scored]):.2f}, "
        f"uses_synced_data {_mean([r.get('uses_synced_data') for r in all_scored]):.2f}, "
        f"r_quality {_mean([r.get('r_quality') for r in all_scored]):.2f}, "
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
    ap.add_argument("--per-type", type=int, default=3, help="cases sampled per task type")
    ap.add_argument("--judge-model", default=JUDGE_MODEL_DEFAULT)
    ap.add_argument("--candidate", default=None,
                    help="candidate model id (config.json). Default: " + CANDIDATE_MODEL)
    ap.add_argument("--dry-run", action="store_true",
                    help="build cases + start stub + verify it, NO LLM calls")
    args = ap.parse_args()

    # Let --candidate override the module default (used by the summary + resolver).
    if args.candidate:
        CANDIDATE_MODEL = args.candidate

    sys.stdout.reconfigure(line_buffering=True)  # live progress (feedback_evals_line_buffered)

    stamp = args.stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.path.join(RESULTS, f"r_mistral_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    print(f"[r-eval] corpus: {CORPUS}", flush=True)
    cases, tasks_spec = build_cases(per_type=args.per_type, only=args.only,
                                    limit=args.limit)
    rubric = tasks_spec["rubric"]
    with open(os.path.join(out_dir, "cases.json"), "w", encoding="utf-8") as f:
        json.dump(cases, f, indent=2, ensure_ascii=False)
    print(f"[r-eval] built {len(cases)} cases -> {out_dir}/cases.json", flush=True)

    srv, stub_url = start_stub()
    print(f"[r-eval] tool stub: {stub_url}", flush=True)

    if args.dry_run:
        print("\n[DRY RUN] cases:", flush=True)
        for c in cases:
            print(f"  {c['id']:14s} {c['target_kind']:8s} {c['n_lines']:>4}L  {c['target']}",
                  flush=True)
        # verify the stub executes a real tool against the synced corpus
        print("\n[DRY RUN] verifying tool stub against the synced R corpus ...", flush=True)
        try:
            res = _call_stub(stub_url, "r_analyze", {"analysis_id": "r_globals"})
            parsed = json.loads(res)
            nrows = len(parsed.get("rows", []))
            print(f"  r_analyze(r_globals) ok: {nrows} rows, columns={parsed.get('columns')}",
                  flush=True)
            funcs = json.loads(_call_stub(stub_url, "r_analyze", {"analysis_id": "r_functions"}))
            print(f"  r_analyze(r_functions) ok: {len(funcs.get('rows', []))} rows", flush=True)
            grep = json.loads(_call_stub(stub_url, "r_grep", {"pattern": "function"}))
            print(f"  r_grep('function') ok: {grep.get('count')} matches", flush=True)
            rd = json.loads(_call_stub(stub_url, "r_read_file",
                                       {"relpath": "ECL_Hilfsfunktion.R"}))
            print(f"  r_read_file(ECL_Hilfsfunktion.R) ok: {len(rd.get('code', ''))} chars",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  STUB VERIFY FAILED: {e}", flush=True)
            return 1
        print("\n[DRY RUN] done — no LLM calls made.", flush=True)
        return 0

    config = _load_config()
    api_key, base_url, base_model = _resolve_provider(CANDIDATE_MODEL, config)
    judge_id, (jkey, jbase, jmodel) = _resolve_judge(args.judge_model, config)
    print(f"[r-eval] candidate={CANDIDATE_MODEL} base={base_url}", flush=True)
    print(f"[r-eval] judge={judge_id} (base_model={jmodel})", flush=True)

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
                   "target_kind": case["target_kind"], "target": case["target"],
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
            print(f"[{done}/{total}] {cid:16s} {case['type']:9s} {case['target_kind']:8s} "
                  f"tools={len(tool_calls)} {score}  ({time.time()-t0:.1f}s)", flush=True)
            # rewrite summaries after every case so a crash keeps a usable summary
            _write_summaries(out_dir, rows)

    csv_path, md_path = _write_summaries(out_dir, rows)
    print(f"\n[r-eval] done in {time.time()-t_start:.0f}s", flush=True)
    print(f"[r-eval] summary: {md_path}", flush=True)
    print(f"[r-eval] csv:     {csv_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
