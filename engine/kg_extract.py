"""
kg_extract.py — LLM-based knowledge-graph triple extraction post-pass for
MemPalace.

Runs *after* `mempalace.miner.mine()` has filed drawers in a wing. For each
fresh drawer that hasn't been processed yet, calls a configurable LLM with a
profile-specific prompt and writes the resulting triples into MemPalace's
KG (`KnowledgeGraph.add_triple` with `source_file`, `source_drawer_id`,
`adapter_name` provenance — RFC 002 §5.5 fields).

Validated 2026-04-26 against a real German banking policy PDF. Default
model: `gemma-4-e4b-it-4bit` (local, German-capable, runs alongside the
26B chat warmpool without GPU-RAM conflict).

Public surface:
    Profile          — dataclass: name + system_prompt + predicates
    PROFILES         — registry of built-in profiles
    extract_triples_from_drawer(content, source_file, drawer_id, model, profile, ...) -> list[dict]
    run_kg_post_pass(palace_path, wing, source_prefix, adapter_name, ...) -> RunResult
    init_kg_progress_schema(db_path) — idempotent CREATE TABLE for cursor + log
    list_kg_extraction_log(db_path, wing=None, limit=N) -> list[dict]

Schema added to chats.db (idempotent):
    kg_extraction_progress(palace_wing, source_drawer_id, processed_at, triples)
    kg_extraction_log(id, palace_wing, adapter_name, source_prefix,
                      started_at, finished_at, drawers_seen, drawers_processed,
                      triples_extracted, errors, error_msg, model)

Skip rules:
    - Code files (extension match) — skipped, code graph handles those
    - Drawers already in kg_extraction_progress — skipped (idempotent re-runs)
    - max_drawer_chars enforced; oversized drawers truncated with marker

Wing scoping is enforced by every public function: source_prefix MUST be
non-empty, callers MUST pass the project's input-folder path or `ingest-`
prefix. The agent-facing tools added in claude_cli.py do their own scoping
on top of this layer.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

# These imports happen lazily at first use so this module can be imported
# without the mempalace venv on sys.path (e.g. by tests that don't need
# extraction). Brain's server.py runs `_ensure_mempalace_importable` before
# calling us.


# ── Profile registry ─────────────────────────────────────────────────────────

NORMATIVE_PREDICATES = [
    "requires", "forbids", "permits", "defines", "cites",
    "applies_to", "effective_from", "supersedes", "responsible_party",
    "condition", "exception", "penalty",
]

NORMATIVE_PROMPT = """You are extracting structured claims from normative
documents — internal policies, external regulations and laws, technical
specifications, standards (ISO/DIN/RFC/EBA/BaFin), contracts, and SOPs.
The content is often in German; it may also appear in English or other
languages. Treat all of these the same way: extract what the document
asserts as binding, defining, referencing, scoping, or excepting.

OUTPUT a strict JSON array of triples. Nothing else — no prose, no markdown
fences. Empty array `[]` if the chunk contains no extractable normative
content.

Each triple:
{
  "subject":    "<entity, role, system, document, regulation — verbatim in source language>",
  "predicate":  "<one of the controlled predicates below; lowercase snake_case>",
  "object":     "<value, condition, period, party, citation — verbatim in source language>",
  "confidence": <float 0.0-1.0>,
  "span":       "<short verbatim quote from the chunk supporting this triple, max 200 chars>"
}

CONTROLLED PREDICATES (use exactly these when applicable):
  requires           — X must do Y / Y must happen / Y is mandatory for X
                       (German: muss, sind verpflichtet, hat ... zu, ist erforderlich)
  forbids            — X must not do Y
                       (German: darf nicht, ist untersagt, ausgeschlossen)
  permits            — X may do Y / Y is allowed for X
                       (German: darf, ist zulässig, kann)
  defines            — term X means Y / X is defined as Y
                       (German: ist definiert als, im Sinne ... versteht man)
  cites              — this document references another regulation or standard
                       (German: gemäß §..., nach Artikel ..., siehe DIN ..., laut)
  applies_to         — scope: who/what the rule covers (role, system, country,
                       department, transaction type)
  effective_from     — date or version when the rule becomes binding
                       (German: gilt ab, in Kraft seit, wirksam ab)
  supersedes         — this rule replaces an older rule or version
                       (German: ersetzt, ablöst, anstelle von)
  responsible_party  — who must comply or enforce
                       (German: verantwortlich ist, obliegt, zuständig)
  condition          — under what circumstance the rule applies
                       (German: sofern, wenn, im Falle, bei)
  exception          — explicit carveout from a rule
                       (German: außer, ausgenommen, unbeschadet)
  penalty            — consequence of non-compliance
                       (German: Bußgeld, Sanktion, Strafe, Verstoß)

If the relation is normative but doesn't fit any of the above, invent a
predicate in lowercase snake_case English. Do NOT translate the predicate
to German — predicates stay English so triples join across languages.

QUALITY RULES:
- Extract obligations and references, not narrative or background.
- Skip table-of-contents lines, page headers/footers, signature blocks.
- Subject and object stay in the SOURCE language (German stays German).
- Each triple should be defensible from the `span` quote alone.
- Prefer specific over generic.
- If the chunk is pure boilerplate, return [].
- Return at most %MAX_TRIPLES% triples per chunk; if more exist, pick the
  most important ones.

EXAMPLE:
Source (German policy fragment):
  "Mitarbeiter sind verpflichtet, personenbezogene Daten gemäß Art. 17
  DSGVO spätestens 30 Tage nach Ablauf der gesetzlichen Aufbewahrungsfrist
  zu löschen."
Triples:
[
  {"subject":"Mitarbeiter","predicate":"requires","object":"Löschung personenbezogener Daten","confidence":0.95,"span":"Mitarbeiter sind verpflichtet, personenbezogene Daten ... zu löschen"},
  {"subject":"Löschung personenbezogener Daten","predicate":"cites","object":"Art. 17 DSGVO","confidence":0.95,"span":"gemäß Art. 17 DSGVO"},
  {"subject":"Löschung personenbezogener Daten","predicate":"effective_from","object":"30 Tage nach Ablauf der gesetzlichen Aufbewahrungsfrist","confidence":0.85,"span":"spätestens 30 Tage nach Ablauf der gesetzlichen Aufbewahrungsfrist"}
]
"""

GENERIC_PROMPT = """You are extracting structured (subject, predicate, object)
triples from arbitrary text. The content may be in any language.

OUTPUT a strict JSON array of triples. Nothing else. Empty array `[]` if no
useful triples are present.

Each triple:
{
  "subject": "<entity verbatim in source language>",
  "predicate": "<short snake_case English verb>",
  "object": "<value verbatim in source language>",
  "confidence": <0.0-1.0>,
  "span": "<short verbatim quote, max 200 chars>"
}

Predicates are open but stay in lowercase English snake_case. Subjects and
objects remain in the source language. Skip headers/footers/boilerplate.
Return at most %MAX_TRIPLES% triples per chunk.
"""


@dataclass
class Profile:
    name: str
    system_prompt: str
    predicates: list[str] = field(default_factory=list)


PROFILES: dict[str, Profile] = {
    "normative": Profile(
        name="normative", system_prompt=NORMATIVE_PROMPT,
        predicates=NORMATIVE_PREDICATES,
    ),
    "generic": Profile(
        name="generic", system_prompt=GENERIC_PROMPT, predicates=[],
    ),
}


# ── Re-chunking for source-file mode ─────────────────────────────────────────

# Paragraph-aware chunker — same shape as kg_prompt_eval.chunk_text. The miner
# emits ~700-char drawers tuned for vector retrieval; for LLM claim extraction
# that's too small (chunks often start mid-sentence and the model conservatively
# returns []). Re-chunking the source file at 3500 chars with paragraph
# boundaries restores the prompt-eval triple density (~9 triples per substantial
# chunk on real bank-policy content).

DEFAULT_SOURCE_CHUNK_CHARS = 3500


def _chunk_text_paragraphs(text: str, max_chars: int) -> list[str]:
    """Split `text` into chunks ≤ max_chars at paragraph boundaries.
    Falls through to hard-slicing only when a single paragraph exceeds the
    cap (rare in prose; common in code-as-text).
    """
    if not text:
        return []
    out: list[str] = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    buf: list[str] = []
    n = 0
    for p in paragraphs:
        plen = len(p) + 2
        if n + plen > max_chars and buf:
            out.append("\n\n".join(buf))
            buf, n = [], 0
        if plen > max_chars:
            for i in range(0, len(p), max_chars):
                out.append(p[i:i + max_chars])
            continue
        buf.append(p)
        n += plen
    if buf:
        out.append("\n\n".join(buf))
    return out


def _strip_brain_frontmatter(text: str) -> str:
    """Remove `<!-- brain-source: ... -->` lines that the converter writes
    so the LLM doesn't waste attention on metadata."""
    lines = text.splitlines()
    drop_until = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("<!-- brain-source"):
            drop_until = i + 1
            continue
        if s == "" and drop_until == i:
            drop_until = i + 1
            continue
        break
    return "\n".join(lines[drop_until:]).lstrip()


# ── Code-extension skip list (mirrors _ARTIFACT_INTERMEDIATE_EXTS in claude_cli) ──

CODE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".kts", ".swift",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh",
    ".cs", ".rb", ".pl", ".pm", ".php",
    ".sh", ".bash", ".zsh", ".fish",
    ".json", ".jsonl", ".ndjson",
    ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".tsv", ".log",
    ".sql", ".graphql", ".proto", ".lock",
}


def _is_code_path(path: str) -> bool:
    if not path:
        return False
    return os.path.splitext(path)[1].lower() in CODE_EXTS


def _anchored_prefix_sql(column: str, prefix: str) -> tuple[str, list]:
    """Build a path-boundary-ANCHORED prefix match for `column` so a directory
    prefix `/data/proj1` matches `/data/proj1` itself and `/data/proj1/<...>`
    but NOT the sibling `/data/proj1extra/...`. A bare `LIKE prefix || '%'`
    leaks across sibling dirs sharing a name prefix — a real cross-scope DELETE
    risk in kg_purge_for_scope. We match: exact `= prefix`, OR `LIKE prefix+sep`
    + '%'. Filename-prefix patterns (e.g. `ingest-<h>-`, `weburl-`) that already
    end in a separator-like boundary still work since `= prefix` covers exact
    and the sep-join covers children. Returns (sql_fragment, params)."""
    sep = os.sep
    p = prefix.rstrip(sep)
    return (f"({column} = ? OR {column} LIKE ? || '%')", [p, p + sep])


# ── Cursor / log schema in chats.db ──────────────────────────────────────────

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_INITIALIZED: set[str] = set()


def init_kg_progress_schema(db_path: str) -> None:
    """Idempotent CREATE TABLE for the per-drawer cursor and the run log.
    Safe to call repeatedly; cached per db_path so the second call is free.
    """
    with _SCHEMA_LOCK:
        if db_path in _SCHEMA_INITIALIZED:
            return
        conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kg_extraction_progress (
                    palace_wing       TEXT NOT NULL,
                    source_drawer_id  TEXT NOT NULL,
                    source_file       TEXT,
                    adapter_name      TEXT,
                    processed_at      REAL NOT NULL,
                    triples           INTEGER NOT NULL DEFAULT 0,
                    error             TEXT DEFAULT '',
                    PRIMARY KEY (palace_wing, source_drawer_id)
                );
                CREATE INDEX IF NOT EXISTS idx_kg_progress_wing
                    ON kg_extraction_progress(palace_wing);
                CREATE INDEX IF NOT EXISTS idx_kg_progress_source
                    ON kg_extraction_progress(palace_wing, source_file);

                CREATE TABLE IF NOT EXISTS kg_extraction_log (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    palace_wing       TEXT NOT NULL,
                    adapter_name      TEXT NOT NULL,
                    source_prefix     TEXT NOT NULL,
                    profile           TEXT NOT NULL DEFAULT '',
                    model             TEXT NOT NULL DEFAULT '',
                    started_at        REAL NOT NULL,
                    finished_at       REAL,
                    drawers_seen      INTEGER NOT NULL DEFAULT 0,
                    drawers_processed INTEGER NOT NULL DEFAULT 0,
                    drawers_skipped   INTEGER NOT NULL DEFAULT 0,
                    triples_extracted INTEGER NOT NULL DEFAULT 0,
                    errors            INTEGER NOT NULL DEFAULT 0,
                    error_msg         TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_kg_log_wing
                    ON kg_extraction_log(palace_wing, started_at);

                -- Per-source mtime/size cursor used to detect file changes
                -- between cycles. When (mtime, size) shifts, the daemon
                -- purges old triples + chunk-progress rows for that
                -- source_file before re-extracting (otherwise old triples
                -- remain orphaned with stale source_drawer_ids).
                CREATE TABLE IF NOT EXISTS kg_extraction_source_state (
                    palace_wing   TEXT NOT NULL,
                    source_file   TEXT NOT NULL,
                    mtime         INTEGER NOT NULL DEFAULT 0,
                    size          INTEGER NOT NULL DEFAULT 0,
                    updated_at    REAL NOT NULL,
                    PRIMARY KEY (palace_wing, source_file)
                );
                CREATE INDEX IF NOT EXISTS idx_kg_src_state_wing
                    ON kg_extraction_source_state(palace_wing);
            """)
            conn.commit()
        finally:
            conn.close()
        _SCHEMA_INITIALIZED.add(db_path)


def _progress_get(db_path: str, palace_wing: str) -> set[str]:
    """Return the set of source_drawer_ids already processed for this wing."""
    init_kg_progress_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT source_drawer_id FROM kg_extraction_progress "
            "WHERE palace_wing = ?", (palace_wing,)).fetchall()
        return {r[0] for r in rows if r and r[0]}
    finally:
        conn.close()


def _progress_record(db_path: str, palace_wing: str, drawer_id: str,
                     source_file: str, adapter_name: str, triples: int,
                     error: str = "") -> None:
    init_kg_progress_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO kg_extraction_progress "
            "(palace_wing, source_drawer_id, source_file, adapter_name, "
            " processed_at, triples, error) VALUES (?,?,?,?,?,?,?)",
            (palace_wing, drawer_id, source_file or "", adapter_name or "",
             time.time(), int(triples), error or ""))
        conn.commit()
    finally:
        conn.close()


def _source_state_get(db_path: str, palace_wing: str
                       ) -> dict[str, tuple[int, int]]:
    """Return {source_file: (mtime, size)} cursor for the wing."""
    init_kg_progress_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT source_file, mtime, size FROM kg_extraction_source_state "
            "WHERE palace_wing = ?", (palace_wing,)).fetchall()
        return {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows if r[0]}
    finally:
        conn.close()


def _source_state_record(db_path: str, palace_wing: str, source_file: str,
                          mtime: int, size: int) -> None:
    init_kg_progress_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO kg_extraction_source_state "
            "(palace_wing, source_file, mtime, size, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (palace_wing, source_file, int(mtime), int(size), time.time()))
        conn.commit()
    finally:
        conn.close()


def _invalidate_source_in_kg(palace_path: str, chats_db_path: str,
                              palace_wing: str, source_file: str,
                              adapter_name: str) -> tuple[int, int]:
    """Purge KG triples + extraction-progress rows for one source_file.
    Used when (mtime, size) changes between cycles — old triples carry
    stale source_drawer_ids and would orphan otherwise. Returns (triples,
    progress) deleted counts.

    Uses an EXACT source_file match (not LIKE prefix) so we don't
    accidentally invalidate sibling files in the same folder. If the KG
    schema has `adapter_name` (3.3.3+) we additionally filter by it.
    """
    if not source_file:
        return 0, 0
    # ORDER MATTERS (crash-safety): delete the PROGRESS cursor BEFORE the triples.
    # A crash between the two leaves progress-gone-but-triples-present, which is
    # SELF-HEALING — the source-state cursor isn't committed until re-extraction
    # finishes (see _commit_source_state), so the next run still sees the source
    # as changed, re-invalidates (re-deletes both), and re-extracts cleanly. The
    # reverse order (the old bug) could leave triples-gone-but-progress-present →
    # next run skips re-extraction → the file is stuck with ZERO triples forever.
    progress_deleted = 0
    init_kg_progress_schema(chats_db_path)
    conn = sqlite3.connect(chats_db_path, timeout=10, check_same_thread=False)
    try:
        cur = conn.execute(
            "DELETE FROM kg_extraction_progress "
            "WHERE palace_wing = ? AND source_file = ?",
            (palace_wing, source_file))
        progress_deleted = cur.rowcount or 0
        conn.commit()
    finally:
        conn.close()

    triples_deleted = 0
    kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
    if os.path.isfile(kg_path):
        conn = sqlite3.connect(kg_path, timeout=10, check_same_thread=False)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(triples)")}
            params: list = [source_file]
            sql = "DELETE FROM triples WHERE source_file = ?"
            if "adapter_name" in cols and adapter_name:
                sql += " AND adapter_name = ?"
                params.append(adapter_name)
            cur = conn.execute(sql, params)
            triples_deleted = cur.rowcount or 0
            conn.commit()
            # Orphan entity cleanup.
            conn.execute(
                "DELETE FROM entities WHERE id NOT IN "
                "(SELECT subject FROM triples UNION SELECT object FROM triples)")
            conn.commit()
        finally:
            conn.close()
    return triples_deleted, progress_deleted


def _log_start(db_path: str, palace_wing: str, adapter_name: str,
               source_prefix: str, profile: str, model: str) -> int:
    init_kg_progress_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        cur = conn.execute(
            "INSERT INTO kg_extraction_log "
            "(palace_wing, adapter_name, source_prefix, profile, model, started_at) "
            "VALUES (?,?,?,?,?,?)",
            (palace_wing, adapter_name, source_prefix, profile, model,
             time.time()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _log_finish(db_path: str, log_id: int, *, drawers_seen: int,
                drawers_processed: int, drawers_skipped: int,
                triples_extracted: int, errors: int,
                error_msg: str = "") -> None:
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        conn.execute(
            "UPDATE kg_extraction_log SET finished_at=?, drawers_seen=?, "
            "drawers_processed=?, drawers_skipped=?, triples_extracted=?, "
            "errors=?, error_msg=? WHERE id=?",
            (time.time(), drawers_seen, drawers_processed, drawers_skipped,
             triples_extracted, errors, error_msg, log_id))
        conn.commit()
    finally:
        conn.close()


def list_kg_extraction_log(db_path: str, wing: str | None = None,
                           limit: int = 100) -> list[dict]:
    init_kg_progress_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        if wing:
            rows = conn.execute(
                "SELECT * FROM kg_extraction_log WHERE palace_wing=? "
                "ORDER BY started_at DESC LIMIT ?", (wing, int(limit))
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kg_extraction_log "
                "ORDER BY started_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def kg_source_states_for_wing(db_path: str, wing: str) -> dict[str, dict]:
    """Aggregate the per-source-file KG state for a wing from the progress
    cursor, for per-document UI badges. Returns
        {realpath(source_file): {"triples": int, "kg": "kg"|"skipped"|"empty",
                                 "skip_reason": str}}.
    A source_file is 'skipped' if ANY of its chunk rows carries a
    'kg_skipped:' error (the GDPR/classification skip-gate); 'kg' if it has
    ≥1 extracted triple; else 'empty' (processed but no extractable relations).
    `error` rows that are NOT skip-markers don't appear here as a state — a
    real extraction failure leaves NO progress row (cursor not advanced), so
    the file simply reads as not-yet-extracted, which is correct.
    Keyed by realpath so it matches the folder-tree walk's realpath check."""
    init_kg_progress_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT source_file, triples, error FROM kg_extraction_progress "
            "WHERE palace_wing = ? AND source_file != ''", (wing,)).fetchall()
    finally:
        conn.close()
    def _accumulate(out, key, tri, err):
        cur = out.setdefault(key, {"triples": 0, "kg": "empty", "skip_reason": ""})
        cur["triples"] += int(tri or 0)
        e = (err or "")
        if e.startswith("kg_skipped:"):
            cur["kg"] = "skipped"
            # e.g. "kg_skipped: gdpr_anonymise" → "gdpr_anonymise"
            cur["skip_reason"] = e.split(":", 1)[1].strip()

    out: dict[str, dict] = {}
    for sf, tri, err in rows:
        if not sf:
            continue
        rp = os.path.realpath(sf)
        _accumulate(out, rp, tri, err)
        # Source files are stored under their .brain-extracted/<x>.<ext>.md
        # companion; the project source-tree walks the ORIGINAL binaries. Key
        # the state under the derived original path too so an original-file
        # lookup matches (mirrors indexed_source_files_for_wing's mapping).
        if "/.brain-extracted/" in sf and sf.endswith(".md"):
            orig = sf[:-3].replace("/.brain-extracted/", "/", 1)
            _accumulate(out, os.path.realpath(orig), tri, err)
    for rp, st in out.items():
        if st["kg"] != "skipped":
            st["kg"] = "kg" if st["triples"] > 0 else "empty"
    return out


# ── Core: per-drawer extraction ──────────────────────────────────────────────

def _truncate_for_llm(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    return head + "\n\n[... truncated for extraction; full content stored as drawer ...]"


def extract_triples_from_drawer(
    content: str,
    source_file: str,
    drawer_id: str,
    model: str,
    profile: Profile,
    *,
    max_triples: int = 12,
    max_drawer_chars: int = 6000,
    min_confidence: float = 0.5,
    inference_temperature: float = 0.0,
    inference_max_tokens: int = 8000,
    cancel_token=None,
) -> tuple[list[dict], str | None]:
    """Run one LLM extraction. Returns (triples, error_msg or None).

    Triples are normalized: lowercased predicates, stripped fields, dropped
    if confidence < min_confidence or any of subject/predicate/object missing.
    """
    if not content or not content.strip():
        return [], None

    # Lazy import — claude_cli is a heavy module.
    import brain as cc

    system_prompt = profile.system_prompt.replace(
        "%MAX_TRIPLES%", str(max_triples))
    user_content = _truncate_for_llm(content, max_drawer_chars)

    _kg_deanon = cc._identity_deanon
    try:
        resolved_model, (_pii_content,), _kg_deanon = cc.gdpr_pick_model_for_background(
            model, [user_content], purpose="kg_extract")
        user_content = _pii_content
    except cc.GDPRSkipError as e:
        # Policy 'skip' — deliberate no-op, NOT an error. Distinct marker so
        # the caller marks the chunk/doc skipped-and-done (cursor advances),
        # not failed (which would retry-loop + count as an error).
        return [], f"gdpr_skip: {e}"
    except cc.GDPRBlockedError as e:
        return [], f"gdpr_block: {e}"
    except Exception:
        resolved_model = model

    if not resolved_model:
        return [], "no model resolvable"

    # Up to 2 retries on transient connection-refused (local LLM gateway
    # might be loading the model or briefly restarting). Each retry waits a
    # short, growing backoff. Real errors (bad JSON, GDPR block, malformed
    # response) are not retried — they'd fail the same way.
    from handlers import sidecar_proxy as _sidecar_proxy
    raw = None
    last_err = ""
    for attempt in range(3):
        _res = _sidecar_proxy.background_call(
            messages=[{"role": "user", "content": user_content}],
            model=resolved_model,
            system_prompt=system_prompt,
            cost_purpose="kg_extract",
            max_tokens=inference_max_tokens,
        )
        raw = _kg_deanon(_res.get("reply") or "") or None
        _err = _res.get("error") or ""
        last_err = f"llm_error: {_err}" if _err else ""
        is_conn_refused = (
            ("Connection refused" in last_err)
            or ("Connection refused" in str(_err)))
        if is_conn_refused and attempt < 2:
            import time as _time
            _time.sleep(0.8 + attempt * 1.2)  # 0.8s, 2.0s
            last_err = ""
            continue
        break

    if last_err and not raw:
        return [], last_err
    if raw is None:
        return [], "sidecar returned no reply"

    parsed = cc._extract_json_from_llm(raw, expect_array=True)
    if not isinstance(parsed, list):
        # Model returned prose instead of JSON (e.g. "No normative content").
        # Treat as empty rather than an error so the chunk is marked done and
        # doesn't show a red dot in the UI.
        if isinstance(raw, str) and len(raw.strip()) < 500 and "[" not in raw:
            return [], None
        return [], "no JSON array in response"

    triples = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        s = (item.get("subject") or "").strip()
        p = (item.get("predicate") or "").strip().lower().replace(" ", "_")
        o = (item.get("object") or "").strip()
        if not s or not p or not o:
            continue
        try:
            conf = float(item.get("confidence", 1.0))
        except (TypeError, ValueError):
            conf = 1.0
        conf = max(0.0, min(1.0, conf))
        if conf < min_confidence:
            continue
        span = (item.get("span") or "").strip()
        if len(span) > 240:
            span = span[:240]
        triples.append({
            "subject": s, "predicate": p, "object": o,
            "confidence": conf, "span": span,
        })
        if len(triples) >= max_triples:
            break
    return triples, None


# ── Wing iteration: pull fresh drawers from MemPalace ────────────────────────

def _iter_wing_drawers(palace_path: str, wing: str, source_prefix: str
                       ) -> Iterable[dict]:
    """Yield drawer dicts {id, source_file, content, room} for every drawer
    in `wing` whose source_file startswith(source_prefix). Empty source_prefix
    is treated as "match all in wing" — caller must validate this is intended.
    """
    from mempalace.palace import get_collection as _get_drawers_col
    col = _get_drawers_col(palace_path, create=False)
    if not col:
        return
    got = col.get(where={"wing": wing}, include=["metadatas", "documents"])
    ids = got.get("ids") or []
    metas = got.get("metadatas") or []
    docs = got.get("documents") or []
    for i, did in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        doc = docs[i] if i < len(docs) else ""
        sf = (meta or {}).get("source_file", "") or ""
        if source_prefix and not sf.startswith(source_prefix):
            continue
        yield {
            "id": did,
            "source_file": sf,
            "room": (meta or {}).get("room", ""),
            "content": doc or "",
        }


def _iter_wing_source_files(palace_path: str, wing: str, source_prefix: str
                             ) -> Iterable[dict]:
    """Yield one dict per distinct source_file in `wing` matching the prefix:
        {source_file, drawer_ids: [...], representative_drawer_id}

    Order: stable (sorted by source_file). Used by source_file chunking mode
    to read each source from disk once and re-chunk it ourselves rather than
    fanning out per-drawer.

    Caller is responsible for skipping files that don't exist on disk
    (deleted between mining and extraction) — this iterator only groups
    drawer ids by source.
    """
    from mempalace.palace import get_collection as _get_drawers_col
    col = _get_drawers_col(palace_path, create=False)
    if not col:
        return
    got = col.get(where={"wing": wing}, include=["metadatas"])
    ids = got.get("ids") or []
    metas = got.get("metadatas") or []
    by_source: dict[str, list[str]] = {}
    for i, did in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        sf = (meta or {}).get("source_file", "") or ""
        if not sf:
            continue
        if source_prefix and not sf.startswith(source_prefix):
            continue
        by_source.setdefault(sf, []).append(did)
    for sf in sorted(by_source.keys()):
        drawer_ids = by_source[sf]
        yield {
            "source_file": sf,
            "drawer_ids": drawer_ids,
            "representative_drawer_id": drawer_ids[0] if drawer_ids else "",
        }


# ── Run a post-pass over a wing ──────────────────────────────────────────────

_KG_DEFAULT_CLOUD_WORKERS = 8


def _kg_resolve_workers(model: str) -> int:
    """Return the max parallel workers to use for KG extraction with `model`.

    Resolution order:
    1. config.json → mempalace.kg.parallel_workers  (explicit override)
    2. config.json → providers.<name>.max_concurrent for the model's provider
       (respects local/CLIProxy caps like oMLX=2, cliproxyapi=2)
    3. _KG_DEFAULT_CLOUD_WORKERS (8) — cloud providers with no cap set
    """
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        cfg = json.load(open(cfg_path))
    except Exception:
        return _KG_DEFAULT_CLOUD_WORKERS

    # Explicit override wins.
    explicit = cfg.get("mempalace", {}).get("kg", {}).get("parallel_workers", 0)
    if explicit and int(explicit) > 0:
        return int(explicit)

    # Resolve provider for this model, then read its max_concurrent.
    if model:
        try:
            import brain as _cc
            resolved = _cc.resolve_provider_for_model(model)
            provider_name = (resolved or {}).get("provider_name", "")
        except Exception:
            provider_name = ""
        if provider_name:
            prov_max = cfg.get("providers", {}).get(provider_name, {}).get("max_concurrent", 0)
            if prov_max and int(prov_max) > 0:
                return int(prov_max)

    return _KG_DEFAULT_CLOUD_WORKERS


@dataclass
class RunResult:
    log_id: int
    drawers_seen: int = 0
    drawers_processed: int = 0
    drawers_skipped: int = 0
    triples_extracted: int = 0
    errors: int = 0
    error_msg: str = ""
    elapsed_s: float = 0.0
    # Source files skipped because GDPR/classification would block or anonymise
    # them — extraction deliberately NOT attempted (no model swap, no
    # anonymise-then-extract-garbage). Surfaced per-document in the UI.
    gdpr_skipped: int = 0


def run_kg_post_pass(
    *,
    palace_path: str,
    wing: str,
    source_prefix: str,
    adapter_name: str,
    profile_name: str = "normative",
    model: str = "",
    chats_db_path: str,
    max_triples_per_drawer: int = 12,
    max_drawer_chars: int = 6000,
    min_confidence: float = 0.5,
    skip_code: bool = True,
    chunking_mode: str = "source_file",
    source_chunk_chars: int = DEFAULT_SOURCE_CHUNK_CHARS,
    progress_cb=None,
    cancel_token=None,
    log_prefix: str = "[kg-extract]",
    max_workers: int = 0,
) -> RunResult:
    """Extract KG triples for content in `wing` whose source_file startswith
    `source_prefix`, write into MemPalace's KG, and record progress in chats.db.

    chunking_mode="source_file" (default): group drawers by source_file, read
        the original file from disk, paragraph-chunk at `source_chunk_chars`,
        extract per chunk. Produces ~10x more triples per document because
        the LLM sees full paragraphs rather than mid-sentence drawer fragments.
        Cursor key: <representative_drawer_id>#<chunk_index>.

    chunking_mode="per_drawer": legacy mode — pass each drawer's content to
        the LLM 1:1. Kept for completeness; suffers the chunk-fragment
        quality issue described in backlog_kg_chunk_granularity.md.

    Required (refuses to run otherwise):
        - palace_path, wing, source_prefix all non-empty
        - profile_name in PROFILES
        - adapter_name non-empty (so triples can be filtered by it later)

    progress_cb: optional callable(stage, **info) for daemon UI feedback.
    """
    if not palace_path or not os.path.isdir(palace_path):
        raise ValueError("palace_path missing or not a directory")
    if not wing:
        raise ValueError("wing must be non-empty")
    if not source_prefix:
        raise ValueError(
            "source_prefix must be non-empty — cross-wing extraction is not "
            "supported in step 1 (no full-wing access for projects)")
    if not adapter_name:
        raise ValueError("adapter_name must be non-empty for provenance")
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise ValueError(
            f"unknown profile {profile_name!r}; have {sorted(PROFILES)}")

    # Lazy MemPalace KG import.
    from mempalace.knowledge_graph import KnowledgeGraph

    kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
    kg = KnowledgeGraph(db_path=kg_path)

    init_kg_progress_schema(chats_db_path)
    log_id = _log_start(chats_db_path, wing, adapter_name, source_prefix,
                        profile_name, model)

    result = RunResult(log_id=log_id)
    t0 = time.time()
    already = _progress_get(chats_db_path, wing)
    last_error = ""
    _write_lock = threading.Lock()  # guards result counters, last_error, already

    workers = max_workers if max_workers > 0 else _kg_resolve_workers(model)

    def _write_triples_to_kg(triples: list[dict], sf: str, did: str) -> int:
        """Write triples to MemPalace's KG with provenance, return count.
        Falls back to legacy signature on MemPalace < 3.3.3.
        KG has its own internal lock so concurrent calls are safe."""
        nonlocal last_error
        written = 0
        for t in triples:
            try:
                kg.add_triple(
                    subject=t["subject"], predicate=t["predicate"],
                    obj=t["object"], confidence=t["confidence"],
                    source_closet=None, source_file=sf,
                    source_drawer_id=did, adapter_name=adapter_name,
                    span=t.get("span") or None,
                )
                written += 1
            except TypeError:
                try:
                    kg.add_triple(
                        subject=t["subject"], predicate=t["predicate"],
                        obj=t["object"], confidence=t["confidence"],
                        source_closet=None, source_file=sf,
                    )
                    written += 1
                except Exception:
                    with _write_lock:
                        result.errors += 1
            except Exception as e:
                with _write_lock:
                    result.errors += 1
                    last_error = f"add_triple: {type(e).__name__}: {e}"
        return written

    # Snapshot the per-source (mtime, size) cursor for change detection.
    # When a source file's stats shift between cycles, mp_miner already
    # files new drawers (different content hashes → new drawer ids) but
    # the OLD triples in the KG still carry the now-orphan drawer id +
    # source_file. Without invalidation those orphans accumulate forever.
    source_state_cursor: dict[str, tuple[int, int]] = {}
    if chunking_mode == "source_file":
        try:
            source_state_cursor = _source_state_get(chats_db_path, wing)
        except Exception:
            source_state_cursor = {}

    def _process_source(src: dict, _already: set) -> None:
        """Process one source file: read → chunk → extract → write.
        Runs in a thread-pool worker; uses _write_lock for shared state.
        _already is the shared progress set passed explicitly to avoid Python's
        closure rule that treats any assigned name as local throughout."""
        nonlocal last_error
        if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
            return
        sf = src["source_file"]
        rep_did = src["representative_drawer_id"]

        if skip_code and _is_code_path(sf):
            cursor_key = f"{rep_did}#0"
            with _write_lock:
                if cursor_key not in _already:
                    _progress_record(chats_db_path, wing, cursor_key,
                                     sf, adapter_name, 0,
                                     error="skipped: code")
                    _already.add(cursor_key)
                result.drawers_skipped += len(src["drawer_ids"])
            return

        # Source-change detection — single-threaded concern, lock for safety.
        cur_mt = cur_sz = 0
        if sf and os.path.isfile(sf):
            try:
                st = os.stat(sf)
                cur_mt = int(st.st_mtime)
                cur_sz = int(st.st_size)
            except OSError:
                cur_mt = cur_sz = 0

        # File no longer on disk — purge its triples and skip extraction.
        if sf and not cur_mt:
            try:
                t_del, p_del = _invalidate_source_in_kg(
                    palace_path, chats_db_path, wing, sf, adapter_name)
                if t_del or p_del:
                    print(f"{log_prefix} source gone, purged "
                          f"{sf}: triples={t_del} progress={p_del}",
                          flush=True)
            except Exception:
                pass
            with _write_lock:
                result.drawers_skipped += len(src["drawer_ids"])
            return

        with _write_lock:
            prev = source_state_cursor.get(sf)
        # Pending source-state to commit AFTER re-extraction of this source
        # completes — recording it BEFORE (the old bug) meant a crash between the
        # invalidation (triples deleted) and re-extraction left the file at the
        # new mtime/size with ZERO triples permanently (next run sees prev==cur →
        # skips). Defer to `_commit_source_state()` at the end of each completion
        # path so the cursor only advances once new triples are durably written.
        _pending_state = None
        if cur_mt and (prev is None or prev != (cur_mt, cur_sz)):
            if prev is not None:
                try:
                    t_del, p_del = _invalidate_source_in_kg(
                        palace_path, chats_db_path, wing, sf, adapter_name)
                    if t_del or p_del:
                        print(f"{log_prefix} invalidated source "
                              f"{sf}: triples={t_del} "
                              f"progress={p_del}", flush=True)
                    with _write_lock:
                        stale_keys = {k for k in _already
                                      if k.startswith(rep_did + "#")}
                        _already -= stale_keys
                except Exception as e:
                    print(f"{log_prefix} invalidate {sf} failed: "
                          f"{type(e).__name__}: {e}", flush=True)
            _pending_state = (cur_mt, cur_sz)

        def _commit_source_state():
            if _pending_state is None:
                return
            try:
                _source_state_record(chats_db_path, wing, sf,
                                     _pending_state[0], _pending_state[1])
                with _write_lock:
                    source_state_cursor[sf] = _pending_state
            except Exception:
                pass

        # Read file from disk.
        file_text = ""
        read_err = ""
        if sf and os.path.isfile(sf):
            try:
                with open(sf, "r", encoding="utf-8", errors="replace") as fh:
                    file_text = fh.read()
            except OSError as e:
                read_err = f"read failed: {type(e).__name__}: {e}"
        else:
            read_err = "source file not on disk"

        # Fallback to per-drawer if file unreadable.
        if read_err:
            if progress_cb:
                try:
                    progress_cb("source_unreadable", source_file=sf,
                                error=read_err)
                except Exception:
                    pass
            for drawer in _iter_wing_drawers(palace_path, wing, sf):
                with _write_lock:
                    result.drawers_seen += 1
                did = drawer["id"]
                with _write_lock:
                    if did in _already:
                        result.drawers_skipped += 1
                        continue
                content = drawer["content"]
                if not content.strip():
                    _progress_record(chats_db_path, wing, did, sf,
                                     adapter_name, 0, error="skipped: empty")
                    with _write_lock:
                        _already.add(did)
                        result.drawers_skipped += 1
                    continue
                triples, err = extract_triples_from_drawer(
                    content=content, source_file=sf, drawer_id=did,
                    model=model, profile=profile,
                    max_triples=max_triples_per_drawer,
                    max_drawer_chars=max_drawer_chars,
                    min_confidence=min_confidence,
                    cancel_token=cancel_token)
                if err and err.startswith("gdpr_skip:"):
                    # Policy 'skip' — mark this drawer done (no retry), count once.
                    _progress_record(chats_db_path, wing, did, sf,
                                     adapter_name, 0, error="kg_skipped: gdpr_skip")
                    with _write_lock:
                        _already.add(did)
                        result.drawers_skipped += 1
                        result.gdpr_skipped += 1
                    continue
                if err:
                    with _write_lock:
                        result.errors += 1
                        last_error = err
                    # DO NOT advance the cursor on a real extraction failure —
                    # retry next cycle instead of silently locking in 0 triples.
                    # (See the per-chunk branch below for the full rationale.)
                    print(f"{log_prefix} KG extract FAILED drawer {did} "
                          f"{os.path.basename(sf)}: {err[:160]} — NOT advancing "
                          f"cursor, will retry next cycle", flush=True)
                    continue
                written = _write_triples_to_kg(triples, sf, did)
                _progress_record(chats_db_path, wing, did, sf,
                                 adapter_name, written, error="")
                with _write_lock:
                    _already.add(did)
                    result.drawers_processed += 1
                    result.triples_extracted += written
            _commit_source_state()  # per-drawer re-extract finished
            return

        file_text = _strip_brain_frontmatter(file_text)

        # ── Document-level GDPR/classification decision (whole-doc scan) ──────
        # POLICY-DRIVEN (obeys gdpr_scanner.background_pii_action) — not
        # hardwired. Scanning the FULL document here (rather than per chunk)
        # is what makes the per-rule min_occurrences gate count DISTINCT values
        # across the whole document, the agreed counting scope. On a 'skip' or
        # block/abort policy outcome the whole source is marked done (cursor
        # advances → no retry-loop), surfaced per-file as 'KG⊘'. On
        # anonymise/swap/proceed the per-chunk extraction below re-applies the
        # policy normally.
        try:
            import brain as _cc
            _cc.gdpr_pick_model_for_background(
                model, [file_text], purpose="kg_extract")
            _doc_gdpr = ""          # proceed
        except Exception as _ge:
            _name = type(_ge).__name__
            if _name == "GDPRSkipError":
                _doc_gdpr = "skip"
            elif _name in ("GDPRBlockedError", "ClassificationBlockedError"):
                _doc_gdpr = "block"
            else:
                _doc_gdpr = ""      # scanner error → fail open, proceed
        if _doc_gdpr:
            cursor_key = f"{rep_did}#0"
            _progress_record(chats_db_path, wing, cursor_key, sf,
                             adapter_name, 0,
                             error=f"kg_skipped: gdpr_{_doc_gdpr}")
            with _write_lock:
                _already.add(cursor_key)
                result.drawers_skipped += len(src["drawer_ids"])
                result.gdpr_skipped += 1
            print(f"{log_prefix} GDPR policy={_doc_gdpr} — KG extraction skipped "
                  f"for {os.path.basename(sf)} (whole-doc PII decision)", flush=True)
            if progress_cb:
                try:
                    progress_cb("gdpr_skipped", source_file=sf, reason=_doc_gdpr)
                except Exception:
                    pass
            _commit_source_state()
            return

        chunks = _chunk_text_paragraphs(file_text, source_chunk_chars)
        if not chunks:
            cursor_key = f"{rep_did}#0"
            _progress_record(chats_db_path, wing, cursor_key, sf,
                             adapter_name, 0,
                             error="skipped: empty after chunking")
            with _write_lock:
                _already.add(cursor_key)
                result.drawers_skipped += len(src["drawer_ids"])
            _commit_source_state()  # nothing to extract → state is current
            return

        with _write_lock:
            result.drawers_seen += len(src["drawer_ids"])

        for ci, chunk_text in enumerate(chunks):
            if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
                break
            cursor_key = f"{rep_did}#{ci}"
            with _write_lock:
                if cursor_key in _already:
                    result.drawers_skipped += 1
                    continue
            if not chunk_text.strip():
                _progress_record(chats_db_path, wing, cursor_key, sf,
                                 adapter_name, 0, error="skipped: empty")
                with _write_lock:
                    _already.add(cursor_key)
                    result.drawers_skipped += 1
                continue

            if progress_cb:
                try:
                    progress_cb("extracting", drawer_id=cursor_key,
                                source_file=sf,
                                drawers_seen=result.drawers_seen)
                except Exception:
                    pass

            triples, err = extract_triples_from_drawer(
                content=chunk_text, source_file=sf,
                drawer_id=rep_did,
                model=model, profile=profile,
                max_triples=max_triples_per_drawer,
                max_drawer_chars=max_drawer_chars,
                min_confidence=min_confidence,
                cancel_token=cancel_token)

            # Policy 'skip' (background_pii_action='skip') — PII found, deliberate
            # no-op. The whole document shares the same PII profile, so skip the
            # ENTIRE source file: mark every chunk done with a kg_skipped reason
            # (cursor advances → no retry-loop), count it, and return. NOT an
            # error — the doc is intentionally excluded from the KG, surfaced
            # per-file as a 'KG⊘' badge.
            if err and err.startswith("gdpr_skip:"):
                for _ci in range(len(chunks)):
                    _ck = f"{rep_did}#{_ci}"
                    _progress_record(chats_db_path, wing, _ck, sf,
                                     adapter_name, 0, error="kg_skipped: gdpr_skip")
                    with _write_lock:
                        _already.add(_ck)
                with _write_lock:
                    result.drawers_skipped += len(src["drawer_ids"])
                    result.gdpr_skipped += 1
                print(f"{log_prefix} GDPR policy=skip — KG extraction skipped "
                      f"for {os.path.basename(sf)} (PII found)", flush=True)
                if progress_cb:
                    try:
                        progress_cb("gdpr_skipped", source_file=sf, reason="skip")
                    except Exception:
                        pass
                _commit_source_state()
                return

            if err:
                with _write_lock:
                    result.errors += 1
                    last_error = err
                # DO NOT advance the cursor on a real extraction failure (model
                # error / "no reply" / timeout): persisting a progress row here
                # would mark the chunk processed-with-0-triples → skipped forever,
                # silently zeroing the KG on a provider outage (the 2026-06 policy
                # KG incident). Leaving it unrecorded means the next cycle retries.
                # Mirrors the chat-sync cursor-clamp-below-failed-writes guard.
                print(f"{log_prefix} KG extract FAILED chunk {cursor_key} "
                      f"{os.path.basename(sf)}: {err[:160]} — NOT advancing "
                      f"cursor, will retry next cycle", flush=True)
                if progress_cb:
                    try:
                        progress_cb("error", drawer_id=cursor_key, error=err)
                    except Exception:
                        pass
                continue

            written = _write_triples_to_kg(triples, sf, rep_did)
            _progress_record(chats_db_path, wing, cursor_key, sf,
                             adapter_name, written, error="")
            with _write_lock:
                _already.add(cursor_key)
                result.drawers_processed += 1
                result.triples_extracted += written
            if progress_cb:
                try:
                    progress_cb("processed", drawer_id=cursor_key,
                                triples=written,
                                running_total=result.triples_extracted)
                except Exception:
                    pass

        # All chunks of this source extracted (or skipped-as-done). Commit the
        # source-state cursor ONLY now — and not if cancelled mid-loop, so a
        # cancelled partial extraction is re-run next time rather than frozen at
        # the new mtime with missing triples.
        _cancelled = (cancel_token is not None
                      and getattr(cancel_token, "is_set", lambda: False)())
        if not _cancelled:
            _commit_source_state()

    try:
        if chunking_mode == "source_file":
            sources = list(_iter_wing_source_files(palace_path, wing,
                                                   source_prefix))
            print(f"{log_prefix} {len(sources)} source files, "
                  f"workers={workers}", flush=True)
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=workers) as executor:
                futs = {executor.submit(_process_source, src, already): src
                        for src in sources}
                for fut in concurrent.futures.as_completed(futs):
                    try:
                        fut.result()
                    except Exception as e:
                        with _write_lock:
                            result.errors += 1
                            last_error = f"worker: {type(e).__name__}: {e}"
        else:
            # Legacy per-drawer mode.
            for drawer in _iter_wing_drawers(palace_path, wing, source_prefix):
                if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
                    last_error = "cancelled"
                    break
                result.drawers_seen += 1
                did = drawer["id"]
                sf = drawer["source_file"]

                if did in already:
                    result.drawers_skipped += 1
                    continue
                if sf and not os.path.isfile(sf):
                    try:
                        _invalidate_source_in_kg(
                            palace_path, chats_db_path, wing, sf, adapter_name)
                    except Exception:
                        pass
                    result.drawers_skipped += 1
                    continue
                if skip_code and _is_code_path(sf):
                    _progress_record(chats_db_path, wing, did, sf,
                                     adapter_name, 0, error="skipped: code")
                    result.drawers_skipped += 1
                    continue
                content = drawer["content"]
                if not content or not content.strip():
                    _progress_record(chats_db_path, wing, did, sf,
                                     adapter_name, 0, error="skipped: empty")
                    result.drawers_skipped += 1
                    continue

                if progress_cb:
                    try:
                        progress_cb("extracting", drawer_id=did,
                                    source_file=sf,
                                    drawers_seen=result.drawers_seen)
                    except Exception:
                        pass

                triples, err = extract_triples_from_drawer(
                    content=content, source_file=sf, drawer_id=did,
                    model=model, profile=profile,
                    max_triples=max_triples_per_drawer,
                    max_drawer_chars=max_drawer_chars,
                    min_confidence=min_confidence, cancel_token=cancel_token)

                if err:
                    result.errors += 1
                    last_error = err
                    _progress_record(chats_db_path, wing, did, sf,
                                     adapter_name, 0, error=err[:240])
                    if progress_cb:
                        try:
                            progress_cb("error", drawer_id=did, error=err)
                        except Exception:
                            pass
                    continue

                written = _write_triples_to_kg(triples, sf, did)
                result.drawers_processed += 1
                result.triples_extracted += written
                _progress_record(chats_db_path, wing, did, sf, adapter_name,
                                 written, error="")
                if progress_cb:
                    try:
                        progress_cb("processed", drawer_id=did,
                                    triples=written,
                                    running_total=result.triples_extracted)
                    except Exception:
                        pass
    except Exception as e:
        result.errors += 1
        last_error = f"run_error: {type(e).__name__}: {e}"
    finally:
        try:
            kg.close()
        except Exception:
            pass

    result.elapsed_s = time.time() - t0
    result.error_msg = last_error
    _log_finish(chats_db_path, log_id,
                drawers_seen=result.drawers_seen,
                drawers_processed=result.drawers_processed,
                drawers_skipped=result.drawers_skipped,
                triples_extracted=result.triples_extracted,
                errors=result.errors,
                error_msg=last_error)

    print(f"{log_prefix} wing={wing} prefix={source_prefix} "
          f"seen={result.drawers_seen} new={result.drawers_processed} "
          f"skip={result.drawers_skipped} triples={result.triples_extracted} "
          f"errors={result.errors} elapsed={result.elapsed_s:.1f}s "
          f"profile={profile_name} model={model or 'auto'}",
          flush=True)
    return result


# ── KG stats helpers (used by the HTTP endpoints) ────────────────────────────

def kg_stats_for_wing(palace_path: str, source_prefix: str | None = None,
                      adapter_name: str | None = None) -> dict:
    """Aggregate KG stats scoped to a (source_prefix, adapter_name) filter.
    Returns counts + top entities by degree + top predicates by frequency.

    Note: MemPalace's KG schema doesn't have a wing column; we scope via
    source_file prefix + adapter_name. Both columns exist as of 3.3.3.
    """
    kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
    if not os.path.isfile(kg_path):
        return {"entities": 0, "triples": 0, "top_predicates": [],
                "top_entities": []}
    conn = sqlite3.connect(kg_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        where = []
        params: list = []
        if source_prefix:
            # ANCHORED at a path boundary (sibling-dir-safe; display stats but
            # mirror the purge predicate so counts match what a purge deletes).
            _frag, _p = _anchored_prefix_sql("source_file", source_prefix)
            where.append(_frag)
            params.extend(_p)
        if adapter_name:
            # adapter_name column added in 3.3.3; tolerate older schemas by
            # checking column existence.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(triples)")}
            if "adapter_name" in cols:
                where.append("adapter_name = ?")
                params.append(adapter_name)
        wsql = (" WHERE " + " AND ".join(where)) if where else ""

        total_triples = conn.execute(
            f"SELECT COUNT(*) FROM triples{wsql}", params).fetchone()[0]

        if total_triples == 0:
            return {"entities": 0, "triples": 0, "top_predicates": [],
                    "top_entities": []}

        # Distinct entities touched within our scope (subject ∪ object).
        ent_rows = conn.execute(
            f"SELECT subject AS eid FROM triples{wsql} "
            f"UNION SELECT object FROM triples{wsql}",
            params + params).fetchall()
        entity_ids = {r["eid"] for r in ent_rows if r and r["eid"]}

        pred_rows = conn.execute(
            f"SELECT predicate, COUNT(*) AS n FROM triples{wsql} "
            f"GROUP BY predicate ORDER BY n DESC LIMIT 25",
            params).fetchall()

        # Top entities by degree (in + out).
        deg_rows = conn.execute(
            f"SELECT eid, SUM(n) AS deg FROM ("
            f"  SELECT subject AS eid, COUNT(*) AS n FROM triples{wsql} GROUP BY subject "
            f"  UNION ALL "
            f"  SELECT object AS eid, COUNT(*) AS n FROM triples{wsql} GROUP BY object"
            f") GROUP BY eid ORDER BY deg DESC LIMIT 25",
            params + params).fetchall()

        # Resolve names for top entities.
        top_entities = []
        for r in deg_rows:
            eid = r["eid"]
            name_row = conn.execute(
                "SELECT name, type FROM entities WHERE id=?",
                (eid,)).fetchone()
            top_entities.append({
                "id": eid,
                "name": name_row["name"] if name_row else eid,
                "type": (name_row["type"] if name_row else "unknown") or "unknown",
                "degree": int(r["deg"]),
            })

        return {
            "entities": len(entity_ids),
            "triples": int(total_triples),
            "top_predicates": [{"predicate": r["predicate"],
                                "count": int(r["n"])} for r in pred_rows],
            "top_entities": top_entities,
        }
    finally:
        conn.close()


def kg_purge_for_scope(palace_path: str, *, source_prefix: str = "",
                       adapter_name: str = "", chats_db_path: str = "",
                       wing: str = "") -> dict:
    """Delete every triple matching the (source_prefix, adapter_name) filter.
    Also clears the matching kg_extraction_progress rows so the next run
    re-extracts. Wing is required for progress cleanup.

    Returns {triples_deleted, progress_deleted}.
    """
    # Refuse unscoped delete BEFORE touching either DB — same rule applies to
    # both the KG and the progress cursor.
    if not source_prefix and not adapter_name:
        return {"triples_deleted": 0, "progress_deleted": 0,
                "error": "refused: at least one of source_prefix or "
                         "adapter_name required"}

    kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
    triples_deleted = 0
    if os.path.isfile(kg_path):
        conn = sqlite3.connect(kg_path, timeout=10, check_same_thread=False)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(triples)")}
            where = []
            params: list = []
            if source_prefix:
                # ANCHORED at a path boundary so purging scope `/data/proj1`
                # can't also DELETE triples for the sibling `/data/proj1extra`.
                _frag, _p = _anchored_prefix_sql("source_file", source_prefix)
                where.append(_frag)
                params.extend(_p)
            if adapter_name and "adapter_name" in cols:
                where.append("adapter_name = ?")
                params.append(adapter_name)
            if where:
                wsql = " WHERE " + " AND ".join(where)
                cur = conn.execute(f"DELETE FROM triples{wsql}", params)
                triples_deleted = cur.rowcount or 0
                conn.commit()
                # Orphan entity cleanup: drop entities no longer referenced.
                conn.execute(
                    "DELETE FROM entities WHERE id NOT IN "
                    "(SELECT subject FROM triples UNION SELECT object FROM triples)")
                conn.commit()
            # else: only adapter_name was given but the column doesn't exist
            # in this older KG schema — skip KG delete (the progress cursor
            # cleanup below still runs so re-runs work).
        finally:
            conn.close()

    progress_deleted = 0
    if chats_db_path and wing:
        init_kg_progress_schema(chats_db_path)
        conn = sqlite3.connect(chats_db_path, timeout=10, check_same_thread=False)
        try:
            where = ["palace_wing = ?"]
            params = [wing]
            if source_prefix:
                where.append("source_file LIKE ? || '%'")
                params.append(source_prefix)
            if adapter_name:
                where.append("adapter_name = ?")
                params.append(adapter_name)
            wsql = " WHERE " + " AND ".join(where)
            cur = conn.execute(
                f"DELETE FROM kg_extraction_progress{wsql}", params)
            progress_deleted = cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()

    return {"triples_deleted": triples_deleted,
            "progress_deleted": progress_deleted}


# ── Incremental closet regeneration wrapper ──────────────────────────────────
#
# MemPalace's `closet_llm.regenerate_closets(palace_path, wing=...)` does a
# wing-wide rebuild: every source file in the wing gets re-LLMed every time
# it's called, even if nothing changed. For a 400-PDF project that's 400
# LLM calls per daemon cycle forever — daemon-hostile.
#
# This wrapper gates the wing-wide call on "did any source file in the wing
# actually change?" using a (palace_wing, source_file) cursor that records
# (mtime, size). If everything's unchanged, we short-circuit. If even one
# source changed, we run the full rebuild and re-record the cursor — finer-
# grained per-file regen would need an upstream `source_files=[...]` filter
# on `regenerate_closets`, which doesn't exist today.
#
# Cursor key:
#   (palace_wing, source_file) → (mtime_ns, size_bytes, processed_at, error)
#
# Sources whose source_file isn't a real on-disk path (e.g. drawers from
# the chat-sync mirror) get skipped entirely — they're handled by the
# chat-sync daemon's own closet rebuild path, not by us.

_CLOSET_SCHEMA_INITIALIZED: set[str] = set()


def init_closet_regen_schema(db_path: str) -> None:
    """Idempotent CREATE TABLE for the closet_regen_progress cursor."""
    with _SCHEMA_LOCK:
        if db_path in _CLOSET_SCHEMA_INITIALIZED:
            return
        conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS closet_regen_progress (
                    palace_wing   TEXT NOT NULL,
                    source_file   TEXT NOT NULL,
                    mtime         INTEGER NOT NULL DEFAULT 0,
                    size          INTEGER NOT NULL DEFAULT 0,
                    processed_at  REAL NOT NULL,
                    error         TEXT DEFAULT '',
                    PRIMARY KEY (palace_wing, source_file)
                );
                CREATE INDEX IF NOT EXISTS idx_closet_regen_wing
                    ON closet_regen_progress(palace_wing);
            """)
            conn.commit()
        finally:
            conn.close()
        _CLOSET_SCHEMA_INITIALIZED.add(db_path)


def _closet_progress_get(db_path: str, palace_wing: str
                          ) -> dict[str, tuple[int, int]]:
    """Return {source_file: (mtime, size)} for everything we already
    regenerated for this wing. Empty dict on cold start."""
    init_closet_regen_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        rows = conn.execute(
            "SELECT source_file, mtime, size FROM closet_regen_progress "
            "WHERE palace_wing = ?", (palace_wing,)).fetchall()
        return {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows if r[0]}
    finally:
        conn.close()


def _closet_progress_record(db_path: str, palace_wing: str,
                             source_file: str, mtime: int, size: int,
                             error: str = "") -> None:
    init_closet_regen_schema(db_path)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO closet_regen_progress "
            "(palace_wing, source_file, mtime, size, processed_at, error) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (palace_wing, source_file, int(mtime), int(size),
             time.time(), error or ""))
        conn.commit()
    finally:
        conn.close()


def _file_stat_or_zero(path: str) -> tuple[int, int]:
    """Return (mtime, size) for `path` or (0, 0) if it doesn't exist /
    isn't a real on-disk file. Sources with (0, 0) are skipped — they're
    not user files we own."""
    if not path or not os.path.isfile(path):
        return 0, 0
    try:
        st = os.stat(path)
        return int(st.st_mtime), int(st.st_size)
    except OSError:
        return 0, 0


def run_closet_regen_incremental(
    *,
    palace_path: str,
    wing: str,
    source_prefix: str,
    chats_db_path: str,
    endpoint: str,
    api_key: str,
    api_model: str,
    log_prefix: str = "[closet-regen]",
) -> dict:
    """Wing-wide LLM closet regen, gated on "did any source change?".

    Returns a small summary dict:
        {sources_seen, sources_stale, regen_triggered, elapsed_s, error?}

    If `regen_triggered` is False, no LLM calls were made. If True, upstream
    `regenerate_closets` was called once for the whole wing (it doesn't
    accept per-file filters today) and every stale source was recorded in
    the cursor with its current (mtime, size). Even unchanged sources get
    their cursor refreshed because upstream rebuilt them too.
    """
    if not palace_path or not os.path.isdir(palace_path):
        return {"error": "palace_path missing", "regen_triggered": False}
    if not wing:
        return {"error": "wing required", "regen_triggered": False}
    if not endpoint or not api_model:
        return {"error": "endpoint and api_model required",
                "regen_triggered": False}

    t0 = time.time()
    seen = 0
    stale_sources: list[str] = []
    skipped_no_disk = 0

    # Snapshot what we already regenerated for this wing.
    cursor = _closet_progress_get(chats_db_path, wing)

    # Walk distinct source_files in the wing matching prefix.
    fresh_stats: dict[str, tuple[int, int]] = {}
    try:
        for src in _iter_wing_source_files(palace_path, wing, source_prefix):
            sf = src["source_file"]
            seen += 1
            mt, sz = _file_stat_or_zero(sf)
            if mt == 0 and sz == 0:
                # File no longer on disk — treat as stale so closets that
                # reference it get rebuilt (or removed by upstream regen).
                skipped_no_disk += 1
                stale_sources.append(sf)
                continue
            fresh_stats[sf] = (mt, sz)
            prev = cursor.get(sf)
            if prev is None or prev != (mt, sz):
                stale_sources.append(sf)
    except Exception as e:
        return {"error": f"iter_wing_source_files: {type(e).__name__}: {e}",
                "regen_triggered": False, "elapsed_s": time.time() - t0}

    if not stale_sources:
        # All cached sources unchanged — short-circuit.
        return {
            "sources_seen": seen,
            "sources_stale": 0,
            "skipped_no_disk": skipped_no_disk,
            "regen_triggered": False,
            "elapsed_s": time.time() - t0,
        }

    # Run the wing-wide regen. Upstream rebuilds every source in the wing,
    # not just the stale ones — but at least we only pay this when something
    # actually changed.
    try:
        from mempalace.closet_llm import (
            LLMConfig as _ClosetLLMConfig,
            regenerate_closets as _regen,
        )
    except Exception as e:
        return {"error": f"closet_llm import: {type(e).__name__}: {e}",
                "regen_triggered": False, "elapsed_s": time.time() - t0}

    cfg = _ClosetLLMConfig(endpoint=endpoint, key=api_key, model=api_model)
    try:
        out = _regen(palace_path=palace_path, wing=wing, cfg=cfg) or {}
    except Exception as e:
        return {"error": f"regen: {type(e).__name__}: {e}",
                "regen_triggered": False,
                "sources_seen": seen,
                "sources_stale": len(stale_sources),
                "elapsed_s": time.time() - t0}

    # Record cursor for every source we observed this round (upstream
    # regenerated them all, so every cursor row is now fresh).
    for sf, (mt, sz) in fresh_stats.items():
        _closet_progress_record(chats_db_path, wing, sf, mt, sz)

    err = (out or {}).get("error", "")
    processed = (out or {}).get("processed", 0)
    elapsed = time.time() - t0
    print(
        f"{log_prefix} wing={wing} prefix={source_prefix} "
        f"sources_seen={seen} stale_pre_call={len(stale_sources)} "
        f"upstream_processed={processed} skipped_no_disk={skipped_no_disk} "
        f"elapsed={elapsed:.1f}s model={api_model} "
        f"{('err=' + err) if err else 'ok'}",
        flush=True,
    )
    return {
        "sources_seen": seen,
        "sources_stale": len(stale_sources),
        "skipped_no_disk": skipped_no_disk,
        "upstream_processed": int(processed) if processed else 0,
        "regen_triggered": True,
        "elapsed_s": elapsed,
        "error": err,
    }


def closet_regen_purge_for_scope(*, chats_db_path: str, palace_wing: str,
                                  source_prefix: str = "") -> int:
    """Drop closet_regen_progress rows for a wing, optionally filtered by
    source_file prefix. Returns count deleted. Used by the project sync
    daemon when triples / drawers are purged so a re-extract round also
    re-runs closets."""
    if not palace_wing:
        return 0
    init_closet_regen_schema(chats_db_path)
    conn = sqlite3.connect(chats_db_path, timeout=10, check_same_thread=False)
    try:
        if source_prefix:
            cur = conn.execute(
                "DELETE FROM closet_regen_progress "
                "WHERE palace_wing = ? AND source_file LIKE ? || '%'",
                (palace_wing, source_prefix))
        else:
            cur = conn.execute(
                "DELETE FROM closet_regen_progress WHERE palace_wing = ?",
                (palace_wing,))
        n = cur.rowcount or 0
        conn.commit()
        return n
    finally:
        conn.close()
