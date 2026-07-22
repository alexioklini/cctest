#!/usr/bin/env python3
"""Brain-vs-Opus eval runner over the KG-Real-Policies corpus.

Usage:
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py --only R1_multilogin,F1_geldwaesche
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py --skip-gold     # rerun only Brain + judge against last gold
  BRAIN_USER=admin BRAIN_PASS=admin python3 eval/run.py --disciplines full  # override config
"""

import argparse
import csv
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _http_post_json(url: str, body: dict, headers: dict, timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"error": body[:500]}
        return e.code, parsed


def brain_login(base_url: str, username: str, password: str) -> str:
    code, body = _http_post_json(
        base_url.rstrip("/") + "/v1/auth/login",
        {"username": username, "password": password},
        headers={},
        timeout=10,
    )
    if code != 200 or "token" not in body:
        raise SystemExit(f"Brain login failed ({code}): {body}")
    return body["token"]


def brain_create_session(base_url: str, token: str, agent: str, project: str, model: str | None) -> str:
    # skip_warmup=True: don't auto-fire _trigger_warmup on session create.
    # Eval batches one session per question; the per-session prefill races
    # the actual chat call on the same provider queue and on gemma-4-26B
    # can truncate replies to empty after the first tool round.
    body: dict = {"agent": agent, "project": project, "skip_warmup": True}
    if model:
        body["model"] = model
    code, resp = _http_post_json(
        base_url.rstrip("/") + "/v1/sessions",
        body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    if code != 200 or "session_id" not in resp:
        raise RuntimeError(f"create_session failed ({code}): {resp}")
    return resp["session_id"]


def brain_chat(base_url: str, token: str, session_id: str, message: str, timeout: float,
               thinking: str | None = None, model: str | None = None,
               anonymise: bool = False) -> dict:
    """POST /v1/chat and drain SSE until 'done'. Returns the done-event data plus
    a list of tool-call summaries lifted from 'tool_*' events.

    `model` is passed per-turn — REQUIRED for routing directives ('auto',
    'auto-cloud', 'auto-local', 'moa'): the send handler only routes/fans-out
    when the directive arrives as the composer model of the turn; a directive
    that only sits on the session (create-time) is not re-evaluated.

    `anonymise=True` sets `gdpr_action=anonymise` on the turn, so the session
    gets a mapping and every retrieval result (mempalace_query / KG) carrying
    PII is pseudonymised before the model sees it. Test-2 (project PII) knob."""
    url = base_url.rstrip("/") + "/v1/chat"
    body = {"session_id": session_id, "message": message}
    if model:
        body["model"] = model
    if thinking and thinking != "none":
        body["thinking"] = thinking
    if anonymise:
        body["gdpr_action"] = "anonymise"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "text/event-stream")

    final: dict = {}
    tools: list[dict] = []
    errors: list[str] = []
    start = time.time()

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        event_name = None
        data_buf: list[str] = []
        for raw in resp:
            if time.time() - start > timeout:
                raise TimeoutError(f"brain_chat timed out after {timeout}s")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith(":"):
                continue  # keepalive comment
            if line == "":
                if event_name and data_buf:
                    payload_str = "\n".join(data_buf)
                    try:
                        payload = json.loads(payload_str)
                    except Exception:
                        payload = {"_raw": payload_str}
                    if event_name == "done":
                        final = payload
                        break
                    if event_name == "error":
                        errors.append(payload.get("message", payload_str))
                    if event_name in ("tool_use", "tool_result", "tool_round"):
                        tools.append({"event": event_name, "data": payload})
                event_name, data_buf = None, []
                continue
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                data_buf.append(line[6:])
    if not final:
        raise RuntimeError(f"brain_chat ended without 'done' event. errors={errors}")
    final["_tool_events"] = tools
    final["_errors"] = errors
    return final


def run_claude_code_gold(question: str, mcp_config_abs: str, model: str,
                         max_turns: int, system_prompt_files: list[str],
                         timeout: float) -> dict:
    """Spawn `claude -p` against vanilla mempalace MCP. Returns parsed JSON output.

    `system_prompt_files` is a list of files to concatenate into a single
    --append-system-prompt block. The eval always passes the gold_context file
    (so Opus knows which palace/wing to query); a disciplines file may be
    appended on top in citation_only/full modes.
    """
    cmd = [
        "claude",
        "-p",
        "--model", model,
        "--mcp-config", mcp_config_abs,
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
        "--output-format", "json",
        "--max-turns", str(max_turns),
        "--no-session-persistence",
    ]
    parts = [_read_text(p) for p in system_prompt_files if p]
    if parts:
        cmd += ["--append-system-prompt", "\n\n---\n\n".join(parts)]
    cmd.append(question)

    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    out = proc.stdout.strip()
    if not out:
        raise RuntimeError(f"claude -p returned empty stdout. stderr={proc.stderr[:1000]}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude -p stdout not JSON: {e}. head={out[:500]!r} stderr={proc.stderr[:500]!r}")


def extract_text_from_claude_json(blob: dict) -> str:
    """The --output-format=json shape varies across versions. Common shape:
    {type:result, subtype:success|error_*, is_error:bool, result:str|None, ...}.
    On error_max_turns / api_error / etc., result is None — surface a marker so
    the judge sees a real failure rather than a UUID dredged from session_id."""
    if blob.get("is_error") or blob.get("subtype", "").startswith("error"):
        st = blob.get("subtype", "error")
        return f"[CLAUDE_CODE_ERROR: subtype={st} terminal_reason={blob.get('terminal_reason','')} num_turns={blob.get('num_turns','?')}]"
    for key in ("result", "response", "output", "message"):
        v = blob.get(key)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            t = v.get("text") or v.get("content")
            if isinstance(t, str) and t.strip():
                return t
            if isinstance(t, list):
                parts = [b.get("text", "") for b in t if isinstance(b, dict)]
                joined = "\n".join(p for p in parts if p)
                if joined.strip():
                    return joined
    return "[CLAUDE_CODE_EMPTY: no result field present]"


def _build_judge_prompt(question_obj: dict, gold_text: str, brain_text: str, rubric: str) -> str:
    expected_docs = ", ".join(question_obj.get("expected_docs", [])) or "(none — refusal expected)"
    return (
        f"# Eval rubric\n\n{rubric}\n\n"
        f"---\n\n"
        f"# Question\n\n{question_obj['question']}\n\n"
        f"**Bucket:** {question_obj.get('bucket','')}\n"
        f"**Expected docs:** {expected_docs}\n"
        f"**Expected to refuse:** {bool(question_obj.get('expected_refuse', False))}\n\n"
        f"---\n\n"
        f"# Gold answer (Claude Code + Opus + vanilla MemPalace)\n\n{gold_text}\n\n"
        f"---\n\n"
        f"# Brain answer (Brain agent as deployed)\n\n{brain_text}\n\n"
        f"---\n\n"
        f"Score both answers per the rubric. Output the JSON object only — no prose, no markdown fences."
    )


def run_judge_mistral(question_obj: dict, gold_text: str, brain_text: str,
                      rubric: str, judge_model: str, timeout: float) -> dict:
    """Call Mistral (or any OpenAI-compatible) provider directly via urllib.
    Reads provider config from <repo>/config.json — same source-of-truth Brain uses."""
    with open(os.path.join(REPO_ROOT, "config.json")) as f:
        cfg = json.load(f)
    models = cfg.get("models", {})
    providers = cfg.get("providers", {})
    if judge_model not in models:
        raise RuntimeError(f"judge model {judge_model!r} not found in config.json[models]")
    m = models[judge_model]
    provider_name = m.get("provider")
    base_model = m.get("base_model_id") or judge_model.rsplit("/", 1)[-1]
    if not provider_name or provider_name not in providers:
        raise RuntimeError(f"provider {provider_name!r} for {judge_model!r} not in config.json[providers]")
    p = providers[provider_name]
    api_key = p["api_key"]
    base_url = p["base_url"].rstrip("/")

    prompt = _build_judge_prompt(question_obj, gold_text, brain_text, rubric)
    body = {
        "model": base_model,
        "messages": [
            {"role": "system", "content": "You are a precise scoring judge. Output ONLY the requested JSON object — no prose, no markdown fences, no explanation outside the JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 2000,
    }
    data = json.dumps(body).encode("utf-8")

    last_err = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(base_url + "/chat/completions", data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {api_key}")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                outer = json.loads(resp.read().decode("utf-8"))
            content = (outer.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if not content.strip():
                last_err = "empty content"
                continue
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
            mm = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if not mm:
                last_err = f"no JSON object in content. head={content[:200]!r}"
                continue
            return json.loads(mm.group(0))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = f"HTTP {e.code}: {err_body[:300]}"
        except json.JSONDecodeError as e:
            last_err = f"JSON decode: {e}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"mistral judge failed after 2 attempts: {last_err}")


def run_judge(question_obj: dict, gold_text: str, brain_text: str,
              rubric: str, judge_model: str, timeout: float) -> dict:
    """Spawn `claude -p` with no MCP, no tools, fed the rubric + both answers."""
    prompt = _build_judge_prompt(question_obj, gold_text, brain_text, rubric)

    cmd = [
        "claude",
        "-p",
        "--model", judge_model,
        "--output-format", "json",
        "--max-turns", "3",
        "--no-session-persistence",
        "--strict-mcp-config",
    ]
    # Pipe prompt via stdin to avoid argparse swallowing it after a flag like --tools.
    # Up to 2 attempts — Opus -p occasionally returns just a session uuid
    # instead of the result text on the first try; retry once with the same prompt.
    last_err = None
    for attempt in range(2):
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                              input=prompt, timeout=timeout)
        if not proc.stdout.strip():
            last_err = f"empty stdout. stderr={proc.stderr[:300]}"
            continue
        try:
            judge_outer = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            last_err = f"outer not JSON: {e}; head={proc.stdout[:300]!r}"
            continue
        judge_text = extract_text_from_claude_json(judge_outer)
        judge_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", judge_text.strip(), flags=re.MULTILINE)
        m = re.search(r"\{.*\}", judge_text, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError as e:
                last_err = f"inner not JSON: {e}; head={m.group(0)[:300]!r}"
                continue
        last_err = f"no JSON object in judge text. head={judge_text[:300]!r}"
    raise RuntimeError(f"judge failed after 2 attempts: {last_err}")


def discipline_path(cfg: dict, mode: str) -> str | None:
    if mode == "none":
        return None
    files = cfg.get("disciplines_files", {})
    rel = files.get(mode)
    if not rel:
        raise SystemExit(f"unknown disciplines mode {mode!r}; valid: none, " + ", ".join(files))
    return os.path.join(REPO_ROOT, rel)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="eval/config.json")
    ap.add_argument("--only", help="comma-separated question ids to run")
    ap.add_argument("--skip-gold", action="store_true",
                    help="reuse gold/* from --reuse-results dir (must be set)")
    ap.add_argument("--skip-brain", action="store_true",
                    help="reuse brain/* from --reuse-results dir")
    ap.add_argument("--reuse-results", help="path to a previous results dir to reuse gold/brain answers")
    ap.add_argument("--disciplines", choices=["none", "citation_only", "full"],
                    help="override claude_code.disciplines from config")
    ap.add_argument("--brain-model", help="override brain.model from config")
    ap.add_argument("--anonymise", action="store_true",
                    help="Test-2: run the Brain side with gdpr_action=anonymise "
                         "(project retrieval PII pseudonymised before the model). "
                         "Gold side is unaffected (reuse Opus gold as-is).")
    ap.add_argument("--thinking", choices=["none", "low", "medium", "high"], default=None,
                    help="enable thinking for Brain chat requests (mistral_blocks format: only 'high' is valid)")
    ap.add_argument("--judge-model", default=None,
                    help="override judge model (e.g. claude-sonnet-4-6); provider=claude_code routes via claude -p")
    ap.add_argument("--label", default="", help="extra label appended to results dir name")
    ap.add_argument("--no-judge", action="store_true",
                    help="skip the Claude Code judge call; collect answers only. Use eval/judge_mistral.py to score afterwards.")
    ap.add_argument("--parallel", type=int, default=None,
                    help="number of questions to run concurrently (default: config.parallel or 1)")
    args = ap.parse_args()

    cfg_path = os.path.join(REPO_ROOT, args.config) if not os.path.isabs(args.config) else args.config
    cfg = _load_json(cfg_path)

    user = os.environ.get("BRAIN_USER")
    pwd = os.environ.get("BRAIN_PASS")
    if not user or not pwd:
        print("ERROR: set BRAIN_USER and BRAIN_PASS env vars.", file=sys.stderr)
        return 2

    # Verify external commands
    if not shutil.which("claude"):
        print("ERROR: 'claude' CLI not on PATH.", file=sys.stderr)
        return 2

    questions = _load_json(os.path.join(REPO_ROOT, cfg["questions_file"]))["questions"]
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
        if not questions:
            print(f"ERROR: --only filtered out everything. Known ids: {[q['id'] for q in _load_json(os.path.join(REPO_ROOT, cfg['questions_file']))['questions']]}", file=sys.stderr)
            return 2

    rubric = _read_text(os.path.join(REPO_ROOT, cfg["rubric_file"]))
    mcp_config_abs = os.path.join(REPO_ROOT, cfg["mcp_config"])
    discipline_mode = args.disciplines or cfg["claude_code"].get("disciplines", "none")
    discipline_file = discipline_path(cfg, discipline_mode)
    gold_context_rel = cfg.get("gold_context_file")
    gold_context_file = os.path.join(REPO_ROOT, gold_context_rel) if gold_context_rel else None
    gold_prompt_files = [p for p in (gold_context_file, discipline_file) if p]

    cc_cfg = cfg["claude_code"]
    brain_cfg = cfg["brain"]
    judge_cfg = cfg["judge"]
    brain_model = args.brain_model or brain_cfg.get("model")

    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    suffix = f"_{args.label}" if args.label else ""
    results_dir = os.path.join(REPO_ROOT, cfg["results_dir"], f"{ts}_disc-{discipline_mode}{suffix}")
    _ensure_dir(results_dir)

    # Snapshot the inputs so the run is fully reproducible
    for src_rel in [cfg["questions_file"], cfg["rubric_file"]]:
        src = os.path.join(REPO_ROOT, src_rel)
        shutil.copy2(src, os.path.join(results_dir, os.path.basename(src)))
    if discipline_file:
        shutil.copy2(discipline_file, os.path.join(results_dir, "disciplines_active.md"))
    if gold_context_file and os.path.exists(gold_context_file):
        shutil.copy2(gold_context_file, os.path.join(results_dir, "gold_context.md"))

    run_meta = {
        "timestamp": ts,
        "config": cfg,
        "discipline_mode": discipline_mode,
        "brain_model_override": brain_model,
        "brain_thinking": args.thinking,
        "judge_model_override": args.judge_model,
        "questions_run": [q["id"] for q in questions],
        "reuse_results": args.reuse_results,
    }
    with open(os.path.join(results_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump(run_meta, f, indent=2, ensure_ascii=False)

    print(f"[eval] results → {results_dir}")
    print(f"[eval] disciplines = {discipline_mode}")

    # Login to Brain once; create per-question session below
    print("[eval] brain: login")
    brain_token = brain_login(brain_cfg["base_url"], user, pwd)

    parallel = args.parallel if args.parallel is not None else cfg.get("parallel", 1)
    print(f"[eval] parallel = {parallel}")

    _print_lock = threading.Lock()

    def _log(qid: str, msg: str) -> None:
        with _print_lock:
            print(f"  [{qid}] {msg}")

    def run_question(i: int, q: dict) -> dict:
        qid = q["id"]
        qdir = os.path.join(results_dir, qid)
        _ensure_dir(qdir)
        with _print_lock:
            print(f"\n[eval] [{i}/{len(questions)}] {qid} — {q['bucket']}")

        # Persist the question for human review
        with open(os.path.join(qdir, "question.json"), "w", encoding="utf-8") as f:
            json.dump(q, f, indent=2, ensure_ascii=False)

        skip_gold = args.skip_gold

        # ----- Gold (Claude Code) -----
        gold_path = os.path.join(qdir, "gold.json")
        if skip_gold and args.reuse_results:
            src = os.path.join(args.reuse_results, qid, "gold.json")
            if os.path.exists(src):
                shutil.copy2(src, gold_path)
                _log(qid, f"gold: reused from {src}")
            else:
                _log(qid, f"gold: --skip-gold but no source at {src}; running fresh")
                skip_gold = False
        if not (skip_gold and os.path.exists(gold_path)):
            t0 = time.time()
            try:
                gold_blob = run_claude_code_gold(
                    q["question"], mcp_config_abs, cc_cfg["model"],
                    cc_cfg["max_turns"], gold_prompt_files,
                    cc_cfg["timeout_seconds"],
                )
                gold_blob["_elapsed_s"] = round(time.time() - t0, 2)
                with open(gold_path, "w", encoding="utf-8") as f:
                    json.dump(gold_blob, f, indent=2, ensure_ascii=False)
                _log(qid, f"gold: ok ({gold_blob['_elapsed_s']}s)")
            except Exception as e:
                err = {"error": str(e), "elapsed_s": round(time.time() - t0, 2)}
                with open(gold_path, "w", encoding="utf-8") as f:
                    json.dump(err, f, indent=2, ensure_ascii=False)
                _log(qid, f"gold: FAILED — {e}")
        gold_blob = _load_json(gold_path)
        gold_text = extract_text_from_claude_json(gold_blob) if "error" not in gold_blob else f"[GOLD ERROR: {gold_blob['error']}]"

        # ----- Brain -----
        brain_path = os.path.join(qdir, "brain.json")
        if args.skip_brain and args.reuse_results:
            src = os.path.join(args.reuse_results, qid, "brain.json")
            if os.path.exists(src):
                shutil.copy2(src, brain_path)
                _log(qid, f"brain: reused from {src}")
        if not (args.skip_brain and os.path.exists(brain_path)):
            t0 = time.time()
            try:
                sid = brain_create_session(brain_cfg["base_url"], brain_token,
                                           brain_cfg["agent"], brain_cfg["project"],
                                           brain_model)
                done = brain_chat(brain_cfg["base_url"], brain_token, sid,
                                  q["question"], brain_cfg["timeout_seconds"],
                                  thinking=args.thinking, model=brain_model,
                                  anonymise=args.anonymise)
                done["_session_id"] = sid
                done["_elapsed_s"] = round(time.time() - t0, 2)
                with open(brain_path, "w", encoding="utf-8") as f:
                    json.dump(done, f, indent=2, ensure_ascii=False)
                _log(qid, f"brain: ok ({done['_elapsed_s']}s, model={done.get('model')})")
            except Exception as e:
                err = {"error": str(e), "elapsed_s": round(time.time() - t0, 2)}
                with open(brain_path, "w", encoding="utf-8") as f:
                    json.dump(err, f, indent=2, ensure_ascii=False)
                _log(qid, f"brain: FAILED — {e}")
        brain_blob = _load_json(brain_path)
        brain_text = brain_blob.get("text", "") if "error" not in brain_blob else f"[BRAIN ERROR: {brain_blob['error']}]"

        # ----- Judge -----
        judge_path = os.path.join(qdir, "judge.json")
        if args.no_judge:
            judge = {"skipped": True}
        elif not gold_text.strip() or not brain_text.strip():
            judge = {"error": "missing answers; skipping judge",
                     "gold_empty": not bool(gold_text.strip()),
                     "brain_empty": not bool(brain_text.strip())}
        else:
            try:
                effective_judge_model = args.judge_model or judge_cfg["model"]
                judge_provider = (judge_cfg.get("provider") or "claude_code").lower()
                if args.judge_model:
                    # Route by the override model's actual provider: a model that
                    # exists in config.json[models] runs via the mistral/OpenAI-shape
                    # judge path; otherwise it's a claude-cli model (claude -p).
                    try:
                        _bcfg = json.load(open(os.path.join(REPO_ROOT, "config.json")))
                        if args.judge_model in (_bcfg.get("models") or {}):
                            judge_provider = "mistral"
                        else:
                            judge_provider = "claude_code"
                    except Exception:
                        judge_provider = "claude_code"
                if judge_provider == "mistral":
                    judge = run_judge_mistral(q, gold_text, brain_text, rubric,
                                              effective_judge_model, judge_cfg["timeout_seconds"])
                else:
                    judge = run_judge(q, gold_text, brain_text, rubric,
                                      effective_judge_model, judge_cfg["timeout_seconds"])
                _log(qid, f"judge: gold={judge.get('gold',{}).get('total','?')} "
                          f"brain={judge.get('brain',{}).get('total','?')} "
                          f"winner={judge.get('comparison',{}).get('winner','?')}")
            except Exception as e:
                judge = {"error": str(e)}
                _log(qid, f"judge: FAILED — {e}")
        with open(judge_path, "w", encoding="utf-8") as f:
            json.dump(judge, f, indent=2, ensure_ascii=False)

        return {
            "id": qid,
            "bucket": q.get("bucket", ""),
            "expected_refuse": q.get("expected_refuse", False),
            "gold_total": _g(judge, "gold.total"),
            "brain_total": _g(judge, "brain.total"),
            "delta": _delta(judge),
            "winner": _g(judge, "comparison.winner"),
            "gold_retrieval": _g(judge, "gold.retrieval"),
            "brain_retrieval": _g(judge, "brain.retrieval"),
            "gold_precision": _g(judge, "gold.precision"),
            "brain_precision": _g(judge, "brain.precision"),
            "gold_citation": _g(judge, "gold.citation"),
            "brain_citation": _g(judge, "brain.citation"),
            "gold_refusal": _g(judge, "gold.refusal"),
            "brain_refusal": _g(judge, "brain.refusal"),
            "gold_composition": _g(judge, "gold.composition"),
            "brain_composition": _g(judge, "brain.composition"),
            "judge_summary": _g(judge, "comparison.summary") or judge.get("error", ""),
        }

    # Run questions — parallel or sequential
    q_index = {q["id"]: i for i, q in enumerate(questions)}
    rows_by_id: dict[str, dict] = {}

    if parallel > 1:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futures = {ex.submit(run_question, i, q): q["id"] for i, q in enumerate(questions, 1)}
            for fut in as_completed(futures):
                qid = futures[fut]
                try:
                    rows_by_id[qid] = fut.result()
                except Exception as e:
                    with _print_lock:
                        print(f"  [{qid}] UNCAUGHT — {e}")
    else:
        for i, q in enumerate(questions, 1):
            rows_by_id[q["id"]] = run_question(i, q)

    summary_rows = [rows_by_id[q["id"]] for q in questions if q["id"] in rows_by_id]

    # ----- Summary CSV + MD -----
    csv_path = os.path.join(results_dir, "summary.csv")
    if summary_rows:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            w.writeheader()
            w.writerows(summary_rows)

    md_path = os.path.join(results_dir, "summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Eval results — {ts} (disciplines: {discipline_mode})\n\n")
        f.write(_render_summary_md(summary_rows))

    print(f"\n[eval] done. summary: {md_path}")
    return 0


def _g(d: dict, dotted: str):
    """Safe dotted-path getter; '' on miss."""
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part, "")
        if cur == "":
            return ""
    return cur


def _delta(judge: dict):
    g = _g(judge, "gold.total")
    b = _g(judge, "brain.total")
    try:
        return round(float(b) - float(g), 2)
    except (TypeError, ValueError):
        return ""


def _render_summary_md(rows: list[dict]) -> str:
    if not rows:
        return "_no rows_\n"
    out = ["| id | bucket | gold | brain | Δ | winner | summary |",
           "|----|--------|-----:|------:|--:|--------|---------|"]
    valid_g, valid_b = [], []
    wins = {"gold": 0, "brain": 0, "tie": 0, "?": 0}
    for r in rows:
        g, b, d = r["gold_total"], r["brain_total"], r["delta"]
        if isinstance(g, (int, float)): valid_g.append(g)
        if isinstance(b, (int, float)): valid_b.append(b)
        wins[r["winner"] or "?"] = wins.get(r["winner"] or "?", 0) + 1
        out.append(f"| {r['id']} | {r['bucket']} | {g} | {b} | {d} | {r['winner']} | {(r['judge_summary'] or '')[:90]} |")
    out.append("")
    if valid_g and valid_b:
        out.append(f"**Means** — gold: {sum(valid_g)/len(valid_g):.2f}, brain: {sum(valid_b)/len(valid_b):.2f}, "
                   f"Δ_brain−gold: {(sum(valid_b)/len(valid_b)) - (sum(valid_g)/len(valid_g)):+.2f}")
    out.append(f"**Wins** — gold: {wins.get('gold',0)}, brain: {wins.get('brain',0)}, tie: {wins.get('tie',0)}, errors: {wins.get('?',0)}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
