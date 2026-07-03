"""Official leaderboard scores for the model benchmark (capability side).

As of v9.275.0 the CAPABILITY percentage per (model x task_type) cell comes
from PUBLIC leaderboard data instead of the self-authored prompt+judge
mini-benchmark; only SPEED (tps) is still measured internally by the seed run
in `engine/model_bench.py`. Two sources, queried in a per-task preference
chain (`TASK_SOURCE_MAP`):

  - Artificial Analysis (https://artificialanalysis.ai) — per-model
    intelligence/coding/math/agentic indices via their free Data API.
    Needs `config.json -> benchmark_official.artificialanalysis_api_key`
    (free key, 1000 req/day). Their ToS require attribution — the models-tab
    GUI shows the source line. Without a key the source is skipped.
  - LMArena (https://lmarena.ai) — human-preference Elo per category from the
    official HuggingFace dataset `lmarena-ai/leaderboard-dataset` (CC-BY-4.0,
    no auth), read via the datasets-server rows/filter API.

Raw scores live on incompatible scales (AA index ~0-70, Arena Elo ~1200-1480),
so capability = the model's PERCENTILE within the FULL leaderboard
distribution for that metric ("beats N% of all listed models", 0-100). This
is pool-independent (adding/removing config models never shifts anyone's
score), comparable across the two sources, and calibrated for the router's
floor semantics (brain._BENCH_CAPABILITY_FLOOR=50, complexity-shifted ±20):
a mid-field commercial model sits ~55, frontier ~90. Deliberately NOT min-max
over the configured pool — that pins the weakest configured model at the
bottom even when the pool is tightly clustered, starving it of all routes.

Model identity is resolved by normalized-name matching (provider prefix,
`-latest`, quantization/instruct suffixes and date tails stripped) with an
explicit per-model override: `config.json -> models.<id>.official_names =
{"artificialanalysis": "...", "lmarena": "..."}` wins over any heuristic.
The matched official name + raw value are stored on the measured cell so a
mismatch is visible (GUI tooltip) and correctable.

Fetched payloads are cached on disk (default 24h TTL) so repeated benchmark
runs don't hammer the sources; a fetch failure falls back to the stale cache
(better than nothing), and a model absent from every source falls back to the
internal prompt+judge benchmark (handled by the caller).

No `import brain` (cycle-free, unit-testable): pure fetch + match + math.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse

AA_API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
_LMARENA_FILTER_URL = "https://datasets-server.huggingface.co/filter"
_LMARENA_DATASET = "lmarena-ai/leaderboard-dataset"
_LMARENA_CONFIG = "text_style_control"

# task_type -> ordered (source, metric) preference chain; first source with a
# value for the model wins. AA indices carry the checkable-skill tasks
# (their coding/math indices aggregate LiveCodeBench/SciCode/AIME/MATH-500);
# LMArena human-preference Elo carries the taste/format tasks it splits out
# (creative writing, instruction following, multi-turn). `fast` uses overall
# quality — its ranking is dominated by tps/cost anyway (floor semantics).
TASK_SOURCE_MAP: dict[str, list[tuple[str, str]]] = {
    "coding":        [("aa", "artificial_analysis_coding_index"), ("lmarena", "coding")],
    "math":          [("aa", "artificial_analysis_math_index"), ("lmarena", "math")],
    "research":      [("aa", "artificial_analysis_intelligence_index"), ("lmarena", "hard_prompts")],
    "analysis":      [("aa", "artificial_analysis_intelligence_index"), ("lmarena", "hard_prompts")],
    "reporting":     [("lmarena", "instruction_following"), ("aa", "artificial_analysis_intelligence_index")],
    "creative":      [("lmarena", "creative_writing"), ("aa", "artificial_analysis_intelligence_index")],
    "orchestration": [("aa", "artificial_analysis_agentic_index"), ("lmarena", "multi_turn"),
                      ("aa", "artificial_analysis_intelligence_index")],
    "agentic":       [("aa", "artificial_analysis_agentic_index"), ("lmarena", "multi_turn"),
                      ("aa", "artificial_analysis_intelligence_index")],
    "fast":          [("lmarena", "overall"), ("aa", "artificial_analysis_intelligence_index")],
}

_LMARENA_CATEGORIES = sorted({m for chain in TASK_SOURCE_MAP.values()
                              for s, m in chain if s == "lmarena"})

_SOURCE_LABEL = {"aa": "artificialanalysis", "lmarena": "lmarena"}


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _http_get_json(url: str, headers: dict | None = None, timeout: float = 30.0):
    import httpx
    r = httpx.get(url, headers=headers or {}, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def fetch_artificialanalysis(api_key: str) -> list[dict]:
    """One call returns every model with evaluations + pricing + speed."""
    payload = _http_get_json(AA_API_URL, headers={"x-api-key": api_key})
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise ValueError(f"unexpected AA response shape: {type(data).__name__}")
    return data


def fetch_lmarena(categories: list[str] | None = None) -> dict[str, dict[str, float]]:
    """{category: {model_name: elo_rating}} from the latest leaderboard split,
    via the datasets-server /filter API (paginated, no auth)."""
    out: dict[str, dict[str, float]] = {}
    for cat in categories or _LMARENA_CATEGORIES:
        ratings: dict[str, float] = {}
        offset = 0
        while True:
            qs = urllib.parse.urlencode({
                "dataset": _LMARENA_DATASET, "config": _LMARENA_CONFIG,
                "split": "latest", "where": f"\"category\"='{cat}'",
                "offset": offset, "length": 100,
            })
            payload = _http_get_json(f"{_LMARENA_FILTER_URL}?{qs}")
            rows = payload.get("rows") or []
            for r in rows:
                row = r.get("row") or {}
                name = row.get("model_name")
                rating = row.get("rating")
                if name and isinstance(rating, (int, float)):
                    ratings[str(name)] = float(rating)
            offset += len(rows)
            if not rows or offset >= int(payload.get("num_rows_total") or 0):
                break
        out[cat] = ratings
    return out


# ── Disk cache ───────────────────────────────────────────────────────────────

def _load_cache(cache_path: str) -> dict:
    try:
        with open(cache_path) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def fetch_sources(*, api_key: str | None, cache_path: str,
                  ttl_hours: float = 24.0, force: bool = False) -> tuple[dict, list[str]]:
    """Return {"aa": [...]|None, "lmarena": {...}|None} using the disk cache
    per source (TTL), refetching stale entries. A fetch failure keeps the
    stale cached payload (recorded in the returned errors list)."""
    cache = _load_cache(cache_path)
    errors: list[str] = []
    now = time.time()
    max_age = max(0.0, float(ttl_hours)) * 3600

    def _cached(key):
        ent = cache.get(key) or {}
        return ent.get("data"), float(ent.get("fetched_at") or 0)

    result: dict = {}
    dirty = False

    aa_data, aa_ts = _cached("aa")
    if api_key:
        if force or aa_data is None or now - aa_ts > max_age:
            try:
                aa_data = fetch_artificialanalysis(api_key)
                cache["aa"] = {"fetched_at": now, "data": aa_data}
                dirty = True
            except Exception as e:
                errors.append(f"Artificial Analysis: {e}")
    else:
        if aa_data is None:
            errors.append("Artificial Analysis: kein API-Key konfiguriert (benchmark_official.artificialanalysis_api_key)")
    result["aa"] = aa_data

    lm_data, lm_ts = _cached("lmarena")
    if force or lm_data is None or now - lm_ts > max_age:
        try:
            lm_data = fetch_lmarena()
            cache["lmarena"] = {"fetched_at": now, "data": lm_data}
            dirty = True
        except Exception as e:
            errors.append(f"LMArena: {e}")
    result["lmarena"] = lm_data

    if dirty:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w") as f:
                json.dump(cache, f)
        except Exception as e:
            errors.append(f"cache write: {e}")
    return result, errors


# ── Name matching ────────────────────────────────────────────────────────────

# Tokens that never distinguish a leaderboard identity from a config id:
# release aliases, chat/instruct variants, quantization/runtime suffixes.
_STRIP_TOKENS = {
    "latest", "instruct", "it", "chat", "preview",
    "4bit", "8bit", "bf16", "fp16", "fp8", "int8", "mlx", "gguf", "awq",
    "gptq", "dwq", "mxfp4", "qat",
}


def _norm(name: str) -> str:
    s = (name or "").lower()
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    s = s.replace("_", "-").replace(".", "-").replace(" ", "-")
    parts = [p for p in s.split("-") if p and p not in _STRIP_TOKENS]
    return "-".join(parts)


_DATE_TOKEN = re.compile(r"^\d{4}$")  # YYMM release tails (2506, 2508, ...)


def _family(norm: str) -> tuple[str, int]:
    """Split a normalized name into (family, date): trailing 4-digit release
    tokens are the date (0 if none). Version/size tokens (3.1, 24b) stay in
    the family — stripping them would merge distinct models (qwen2.5-7b vs
    qwen2.5-72b)."""
    parts = norm.split("-")
    date = 0
    while parts and _DATE_TOKEN.match(parts[-1]):
        date = max(date, int(parts[-1]))
        parts.pop()
    return "-".join(parts), date


def _num(v):
    """Metric values may arrive as number or {value: n}-ish dicts."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, dict):
        for k in ("value", "score", "normalized"):
            if isinstance(v.get(k), (int, float)):
                return float(v[k])
    return None


def _build_aa_index(aa_data: list[dict]) -> tuple[dict[str, dict], dict[str, list[float]]]:
    """({norm_name: {name, metrics}}, {metric: [all values]}) — indexed under
    both slug and display name; duplicate keys keep the entry with the higher
    intelligence index. The value lists span EVERY listed model (percentile
    base)."""
    index: dict[str, dict] = {}
    dists: dict[str, list[float]] = {}
    aa_metrics = {mk for ch in TASK_SOURCE_MAP.values() for src, mk in ch if src == "aa"}
    for m in aa_data or []:
        evals = m.get("evaluations") or {}
        metrics = {k: n for k in aa_metrics if (n := _num(evals.get(k))) is not None}
        if not metrics:
            continue
        for k, v in metrics.items():
            dists.setdefault(k, []).append(v)
        entry = {"name": m.get("name") or m.get("slug") or m.get("id") or "?",
                 "metrics": metrics}
        ii = metrics.get("artificial_analysis_intelligence_index", 0)
        for key in {_norm(m.get("slug") or ""), _norm(m.get("name") or "")}:
            if not key:
                continue
            old = index.get(key)
            if old is None or ii > old["metrics"].get("artificial_analysis_intelligence_index", 0):
                index[key] = entry
    return index, dists


def _build_lmarena_index(lm_data: dict[str, dict[str, float]]) -> tuple[dict[str, dict], dict[str, list[float]]]:
    """({norm_name: {name, metrics}}, {category: [all elo values]}) with
    metrics = {category: elo}."""
    index: dict[str, dict] = {}
    dists: dict[str, list[float]] = {}
    for cat, ratings in (lm_data or {}).items():
        dists[cat] = list(ratings.values())
        for name, elo in ratings.items():
            key = _norm(name)
            if not key:
                continue
            entry = index.setdefault(key, {"name": name, "metrics": {}})
            entry["metrics"][cat] = elo
    return index, dists


def _match_model(mid: str, mcfg: dict, index: dict[str, dict],
                 source_label: str) -> dict | None:
    """Resolve one config model against one source index. Explicit
    `official_names` override > exact normalized match > family match
    (same name modulo a trailing release-date token; newest date wins)."""
    override = ((mcfg.get("official_names") or {}).get(source_label) or "").strip()
    if override:
        return index.get(_norm(override))  # explicit: exact or nothing
    candidates = [c for c in (_norm(mcfg.get("base_model_id") or ""), _norm(mid)) if c]
    for c in candidates:
        if c in index:
            return index[c]
    for c in candidates:
        fam, _ = _family(c)
        if not fam:
            continue
        hits = [(k, v) for k, v in index.items() if _family(k)[0] == fam]
        if hits:
            return max(hits, key=lambda kv: _family(kv[0])[1])[1]
    return None


# ── Normalization + assembly ─────────────────────────────────────────────────

def _percentile(dist: list[float], v: float) -> int:
    """Share of the full leaderboard this value beats (mid-rank for ties),
    0-100. Percentile — NOT pool min-max — so scores are pool-independent and
    a tightly-clustered config pool doesn't get artificially stretched."""
    if not dist:
        return 0
    below = sum(1 for x in dist if x < v)
    ties = sum(1 for x in dist if x == v)
    return int(round((below + 0.5 * ties) / len(dist) * 100))


def compute_official_benchmarks(models_cfg: dict, *, api_key: str | None,
                                cache_path: str, ttl_hours: float = 24.0,
                                force: bool = False) -> tuple[dict, dict]:
    """Main entry. Returns (table, meta):

    table: {model_id: {task_type: {"capability": int(0-100 percentile),
                                    "source": "artificialanalysis"|"lmarena",
                                    "raw": float, "official_name": str}}}
           — only cells with official data; missing cells mean the caller
           should fall back to the internal prompt+judge benchmark.
    meta:  {"errors": [...], "matched": {model_id: {source: official_name}}}
    """
    sources, errors = fetch_sources(api_key=api_key, cache_path=cache_path,
                                    ttl_hours=ttl_hours, force=force)
    aa_index, aa_dists = _build_aa_index(sources.get("aa") or [])
    lm_index, lm_dists = _build_lmarena_index(sources.get("lmarena") or {})
    indexes = {"aa": aa_index, "lmarena": lm_index}
    dists = {"aa": aa_dists, "lmarena": lm_dists}

    enabled = {mid: cfg for mid, cfg in (models_cfg or {}).items()
               if (cfg or {}).get("enabled", True)}
    matched: dict[str, dict] = {}
    for mid, cfg in enabled.items():
        for src, label in _SOURCE_LABEL.items():
            hit = _match_model(mid, cfg or {}, indexes[src], label)
            if hit:
                matched.setdefault(mid, {})[src] = hit

    table: dict[str, dict] = {}
    for mid, hits in matched.items():
        for task, chain in TASK_SOURCE_MAP.items():
            for src, metric in chain:
                raw = hits.get(src, {}).get("metrics", {}).get(metric) if src in hits else None
                if raw is None:
                    continue
                table.setdefault(mid, {})[task] = {
                    "capability": _percentile(dists[src].get(metric) or [], raw),
                    "source": _SOURCE_LABEL[src],
                    "raw": round(raw, 2),
                    "official_name": hits[src]["name"],
                }
                break

    meta = {
        "errors": errors,
        "matched": {mid: {src: hits[src]["name"] for src in hits}
                    for mid, hits in matched.items()},
        "aa_models": len(aa_index),
        "lmarena_models": len(lm_index),
    }
    return table, meta
