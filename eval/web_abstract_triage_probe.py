#!/usr/bin/env python3
"""Abstract-first web triage probe (NOT product code).

Validates the v9.60.x search-tool rule rewrite: after a web search, the model
should `web_fetch(mode="abstract")` each result to triage relevance CHEAPLY,
then `web_fetch(mode="full")` ONLY the results whose abstract shows they help —
and SKIP full-reading the off-topic ones. This tests the DECISION (does the
model avoid reading a whole website the abstract already showed is useless?) and
quantifies the TOKEN SAVINGS vs the old "fetch ALL URLs in full" behavior.

How it works (zero changes to Brain; mirrors eval/fanout_probe.py):
  - Posts directly to the SIDECAR (POST :8421/turn) with the REAL production
    system prompt + full tool list (so the search/web_fetch descriptions under
    test are exactly what production sends).
  - A stateful local HTTP stub plays `tool_endpoint`: it returns scenario search
    results for searxng_search/exa_search, and per-(url,mode) canned content for
    web_fetch — a short abstract in mode=abstract, a big body in mode=full. It
    RECORDS every call so we can see which URLs the model chose to full-read.
  - Scores: (a) full-fetched the relevant url(s), (b) did NOT full-fetch the
    off-topic ones, (c) answered correctly. Token cost = sum of returned content
    lengths, compared to the naive full-fetch-everything baseline.

Run:
  python3 eval/web_abstract_triage_probe.py
  python3 eval/web_abstract_triage_probe.py --model mistral-small-latest
  python3 eval/web_abstract_triage_probe.py --only weather_noise

Sidecar must be up (:8421); creds from config.json.
"""
import argparse
import json
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIDECAR = "http://127.0.0.1:8421"
CONFIG = "config.json"

# ── Scenarios ────────────────────────────────────────────────────────────────
# Each scenario defines a search result set where SOME pages are relevant to the
# question and others are clearly off-topic. `abstract` is the ~1500-char survey
# the stub returns for mode="abstract"; `full` is the (large) body for
# mode="full". `relevant` lists the URLs that actually contain the answer — the
# model SHOULD full-fetch those and SHOULD NOT full-fetch the rest.
SCENARIOS = [
    {
        "id": "weather_noise",
        "query_hint": "current weather in Vienna today",
        "user": ("What is today's forecast high temperature for Vienna? Search the "
                 "web and answer with the number."),
        "results": [
            {"title": "Vienna weather today — hourly forecast",
             "url": "https://weather.example/vienna",
             "abstract": "Vienna, today: partly cloudy. Forecast HIGH 24°C, low 13°C. "
                         "Wind 12 km/h. Updated 06:00 local.",
             "full": "VIENNA WEATHER\n" + ("hourly table row ... " * 400) +
                     "\nForecast high today: 24°C. Low: 13°C.",
             "relevant": True},
            {"title": "History of Vienna — Wikipedia",
             "url": "https://encyclopedia.example/wiki/Vienna_history",
             "abstract": "Vienna is the capital of Austria. This article covers its "
                         "history from Roman Vindobona through the Habsburg era to "
                         "the present. No current weather data.",
             "full": "HISTORY OF VIENNA\n" + ("the habsburgs ... " * 500),
             "relevant": False},
            {"title": "Best restaurants in Vienna 2026",
             "url": "https://food.example/vienna-restaurants",
             "abstract": "A guide to the top 50 restaurants in Vienna for 2026, "
                         "covering Wiener Schnitzel, cafés and fine dining. Not about "
                         "weather.",
             "full": "VIENNA RESTAURANTS\n" + ("schnitzel ... " * 500),
             "relevant": False},
        ],
    },
    {
        # Titles are deliberately ambiguous — all three look plausibly relevant,
        # so the model CANNOT pick the right one from titles alone; only the
        # abstract reveals which page has the actual number. A naive full-fetch of
        # all three wastes tokens on two large off-topic bodies. This is the case
        # abstract triage exists for.
        "id": "ambiguous_titles",
        "query_hint": "Acme Rocket Mk3 maximum payload to LEO kg",
        "user": ("What is the maximum payload to LEO (in kg) of the Acme Rocket Mk3? "
                 "Search the web and answer with the number."),
        "results": [
            {"title": "Acme Rocket Mk3 — Official Product Page",
             "url": "https://acme.example/mk3",
             "abstract": "The Acme Rocket Mk3 is our flagship launch vehicle. Marketing "
                         "overview, pricing tiers, and a gallery. (Detailed specs are on "
                         "the separate datasheet page — this page lists no payload "
                         "figures.)",
             "full": "ACME MK3 PRODUCT PAGE\n" + ("buy now ... " * 500),
             "relevant": False},
            {"title": "Acme Rocket Mk3 — Technical Datasheet",
             "url": "https://acme.example/mk3-datasheet",
             "abstract": "Acme Rocket Mk3 technical datasheet. Maximum payload to LEO: "
                         "8200 kg. Payload to GTO: 3100 kg. Height 52 m, thrust 7.6 MN, "
                         "two stages.",
             "full": "ACME MK3 DATASHEET\n" + ("spec row ... " * 400) +
                     "\nMaximum payload to LEO: 8200 kg.",
             "relevant": True},
            {"title": "Acme Rocket Mk3 — Launch History & News",
             "url": "https://acme.example/mk3-launches",
             "abstract": "A chronological log of every Acme Mk3 launch since 2024: dates, "
                         "missions, and outcomes. No payload-capacity specification "
                         "figures here.",
             "full": "ACME MK3 LAUNCHES\n" + ("flight log ... " * 500),
             "relevant": False},
        ],
    },
    {
        # Fictional org/policy so the model CANNOT answer from prior knowledge —
        # it must use the stubbed results. (An earlier real-world HTTP-429 version
        # failed as a probe: the model recognised the question, invented its own
        # real URLs, ignored the stub, and answered from memory.)
        "id": "policy_lookup",
        "query_hint": "Zentralbank Vindobonia interner Beleg-Aufbewahrungsfrist Jahre",
        "user": ("According to the Zentralbank Vindobonia retention policy, for how "
                 "many years must internal payment vouchers (Belege) be retained? "
                 "Search the web and answer with the number of years."),
        "results": [
            {"title": "Zentralbank Vindobonia — About Us",
             "url": "https://zbv.example/about",
             "abstract": "Zentralbank Vindobonia is the fictional central bank of "
                         "Vindobonia. This page covers its mission, leadership, and "
                         "history. No retention-period figures.",
             "full": "ABOUT ZBV\n" + ("our mission ... " * 500),
             "relevant": False},
            {"title": "ZBV Records Retention Directive 12.4",
             "url": "https://zbv.example/directive-12-4",
             "abstract": "ZBV Records Retention Directive 12.4: internal payment "
                         "vouchers (Belege) must be retained for 11 years from the end "
                         "of the business year. Customer correspondence: 6 years. "
                         "Board minutes: permanent.",
             "full": "DIRECTIVE 12.4\n" + ("clause ... " * 400) +
                     "\nInternal payment vouchers: 11 years.",
             "relevant": True},
            {"title": "ZBV Annual Report 2025 — Highlights",
             "url": "https://zbv.example/annual-2025",
             "abstract": "Financial highlights and strategic priorities for ZBV in "
                         "2025: balance sheet totals, headcount, sustainability goals. "
                         "Not a records-retention document.",
             "full": "ANNUAL REPORT\n" + ("highlight ... " * 500),
             "relevant": False},
        ],
    },
]

# Which search tool to advertise the results under. Both carry the same rule;
# searxng is fine for the probe (no API key needed in the description).
SEARCH_TOOL = "searxng_search"


# ── Stateful capture + content stub ──────────────────────────────────────────
class _Stub:
    """Per-run state: the active scenario + recorded calls. The HTTP handler
    reads `current` to decide what to return."""
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []          # [{name, args}]
        self.fetch_modes = {}    # url -> set of modes fetched
        self.returned_chars = 0  # total content chars the model was handed
        self.scenario = None

    def reset(self, scenario):
        with self.lock:
            self.calls = []
            self.fetch_modes = {}
            self.returned_chars = 0
            self.scenario = scenario


_STUB = _Stub()


def _search_results_payload(scenario):
    # Mirror the real searxng/exa shape: {query, results:[{title,link}], result_count}
    results = [{"title": r["title"], "link": r["url"], "url": r["url"],
                "snippet": r["title"]} for r in scenario["results"]]
    return json.dumps({"query": scenario["query_hint"], "results": results,
                       "result_count": len(results)})


def _web_fetch_payload(scenario, url, mode):
    rec = next((r for r in scenario["results"] if r["url"] == url), None)
    if rec is None:
        return json.dumps({"url": url, "status": 404, "content": "not found",
                           "fetch_method": "raw"})
    if mode == "abstract":
        content = rec["abstract"]
        method = "markitdown+abstract"
    else:
        content = rec["full"]
        method = "markitdown"
    return json.dumps({"url": url, "status": 200, "length": len(content),
                       "content": content, "fetch_method": method})


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        name = body.get("name", "")
        args = body.get("args", {}) or {}
        with _STUB.lock:
            scenario = _STUB.scenario
            _STUB.calls.append({"name": name, "args": args})
        if name in ("searxng_search", "exa_search"):
            result = _search_results_payload(scenario)
        elif name == "web_fetch":
            url = args.get("url", "")
            mode = (args.get("mode") or "full").lower()
            result = _web_fetch_payload(scenario, url, mode)
            with _STUB.lock:
                _STUB.fetch_modes.setdefault(url, set()).add(mode)
                try:
                    _STUB.returned_chars += len(json.loads(result).get("content", ""))
                except Exception:
                    pass
        else:
            # Any other tool: benign empty result so the turn proceeds.
            result = json.dumps({"note": "ok"})
        out = json.dumps({"result": result, "is_error": False, "elapsed_ms": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _start_stub():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}/"


# ── Production context + sidecar turn (mirrors fanout_probe) ──────────────────
def _load_production_context(model):
    import brain
    from engine.context import request_context
    # build_first_turn_prefix renders the search-tool rule from brain._tool_settings
    # (populated by the server at startup). This standalone process didn't run that
    # bootstrap, so load it from config.json exactly as server.py does — otherwise
    # the abstract-first rule under test wouldn't reach the system prompt.
    if not getattr(brain, "_tool_settings", None):
        cfg = json.load(open(CONFIG))
        brain._tool_settings = cfg.get("tool_settings", {}) or {}
    with request_context(current_agent=brain.AgentConfig("main")):
        sp, tools, _ = brain.build_first_turn_prefix(
            model, "main", mcp_manager=getattr(brain, "_mcp_manager", None),
            discovered_tools=set(), is_openai_shape=False, purpose="interactive")
    return sp, tools


def _provider_creds(model):
    c = json.load(open(CONFIG))
    m = (c.get("models", {}) or {}).get(model) or {}
    prov_name = m.get("provider") or "CLIProxyAPI"
    p = c["providers"][prov_name]
    bu = (p.get("base_url") or "").rstrip("/")
    if bu.endswith("/v1"):
        bu = bu[:-3]
    return p.get("api_key") or "", bu, prov_name


def _run_scenario(model, api_key, base_url, scenario, stub_url, system, tools, timeout_s=180):
    _STUB.reset(scenario)
    import brain
    payload = {
        "model": brain.get_api_model_id(model),
        "base_url": base_url, "api_key": api_key,
        "system": system,
        "messages": [{"role": "user", "content": scenario["user"]}],
        "tools": tools,
        "max_tokens": 1500, "max_rounds": 8,
        "tool_endpoint": stub_url, "tool_endpoint_auth": "Bearer probe",
        "turn_id": uuid.uuid4().hex, "temperature": 0.1,
    }
    req = urllib.request.Request(
        f"{SIDECAR}/turn", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    final_text, err = "", None
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:
                    continue
                if ev.get("type") == "done":
                    final_text = ev.get("data", {}).get("final_text", "") or final_text
                elif ev.get("type") == "error":
                    err = ev.get("data", {}).get("message", "error")
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    with _STUB.lock:
        calls = list(_STUB.calls)
        fetch_modes = {u: set(m) for u, m in _STUB.fetch_modes.items()}
        returned_chars = _STUB.returned_chars
    return calls, fetch_modes, returned_chars, final_text, err


# ── Scoring ───────────────────────────────────────────────────────────────────
def _score(scenario, calls, fetch_modes, returned_chars, final_text):
    notes = []
    relevant = {r["url"] for r in scenario["results"] if r["relevant"]}
    offtopic = {r["url"] for r in scenario["results"] if not r["relevant"]}

    full_fetched = {u for u, modes in fetch_modes.items() if "full" in modes}
    abstract_fetched = {u for u, modes in fetch_modes.items() if "abstract" in modes}

    read_any = full_fetched | abstract_fetched

    # 1. Searched first (didn't answer from nothing).
    searched = any(c["name"] in ("searxng_search", "exa_search") for c in calls)

    # 2. Grounded in the relevant page — read it in EITHER mode. Answering from a
    #    sufficient abstract is the IDEAL outcome (the rule explicitly allows it),
    #    so a full read is not required; reaching the relevant content is.
    got_relevant = relevant.issubset(read_any) if relevant else True

    # 3. THE HEADLINE: did NOT full-read the off-topic pages. Don't read the whole
    #    website when the abstract (or the result list) showed it won't help.
    skipped_offtopic = not (full_fetched & offtopic)
    if full_fetched & offtopic:
        notes.append(f"full-read off-topic: {sorted(full_fetched & offtopic)}")

    # 4. Answer contains the expected fact. Normalise away digit-group separators
    #    (commas/spaces/thin-spaces) so "8,200" / "8 200" match "8200".
    expect = {"weather_noise": "24", "policy_lookup": "11",
              "ambiguous_titles": "8200"}.get(scenario["id"], "")
    _norm = "".join(ch for ch in (final_text or "") if ch not in ",   ")
    answered = (expect in _norm) if expect else True

    # Informational: did it triage via abstract at all? Not a hard pass criterion
    # — a model that reads only the one obviously-relevant page in full (skipping
    # the off-topic ones) ALSO satisfies the goal. We report it to distinguish the
    # two good strategies.
    used_abstract = bool(abstract_fetched)

    # Token-cost proxy: chars of content the model was handed. Baseline = the OLD
    # rule (full-fetch EVERY result).
    baseline_chars = sum(len(r["full"]) for r in scenario["results"])
    saved_frac = round(1 - (returned_chars / baseline_chars), 2) if baseline_chars else 0.0

    # PASS = grounded in the answer, skipped off-topic full-reads, answered right.
    # (Abstract use is encouraged but not mandated — skipping off-topic full-reads
    # is the invariant, whether achieved via abstract triage or selective reading.)
    passed = searched and got_relevant and skipped_offtopic and answered
    paid_off = passed and saved_frac >= 0.3
    return {
        "searched": searched, "used_abstract": used_abstract,
        "got_relevant": got_relevant, "skipped_offtopic": skipped_offtopic,
        "answered": answered, "passed": passed,
        "returned_chars": returned_chars, "baseline_chars": baseline_chars,
        "saved_frac": saved_frac, "paid_off": paid_off,
        "full_fetched": sorted(full_fetched), "abstract_fetched": sorted(abstract_fetched),
        "notes": notes,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mistral-medium-3.5")
    ap.add_argument("--only", help="comma-separated scenario ids")
    args = ap.parse_args()

    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    scenarios = SCENARIOS
    if args.only:
        want = set(args.only.split(","))
        scenarios = [s for s in SCENARIOS if s["id"] in want]

    api_key, base_url, prov = _provider_creds(args.model)
    print(f"[abstract-triage] model={args.model} provider={prov}")
    system, tools = _load_production_context(args.model)
    srv, stub_url = _start_stub()

    rows = []
    try:
        for s in scenarios:
            t0 = time.time()
            calls, fmodes, rchars, final, err = _run_scenario(
                args.model, api_key, base_url, s, stub_url, system, tools)
            if err:
                print(f"  {s['id']}: ERROR {err}")
                rows.append({"id": s["id"], "error": err})
                continue
            sc = _score(s, calls, fmodes, rchars, final)
            rows.append({"id": s["id"], **sc})
            print(f"  {s['id']}: pass={sc['passed']} paid_off={sc['paid_off']} "
                  f"saved={int(sc['saved_frac']*100)}% "
                  f"(searched={sc['searched']} abstract={sc['used_abstract']} "
                  f"got_relevant={sc['got_relevant']} skipped_offtopic={sc['skipped_offtopic']} "
                  f"answered={sc['answered']}) full={len(sc['full_fetched'])} "
                  f"abs={len(sc['abstract_fetched'])} ({round(time.time()-t0,1)}s)")
            for n in sc["notes"]:
                print(f"      ! {n}")
    finally:
        srv.shutdown()

    ok = [r for r in rows if r.get("passed")]
    paid = [r for r in rows if r.get("paid_off")]
    print(f"\n[abstract-triage] passed {len(ok)}/{len(rows)} · "
          f"paid_off (≥30% saved) {len(paid)}/{len(rows)}")
    return 0 if len(ok) == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
