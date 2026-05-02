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

    try:
        resolved_model = cc.gdpr_pick_model_for_background(
            model, [user_content], purpose="kg_extract")
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
    raw = None
    last_err = ""
    for attempt in range(3):
        try:
            raw = cc._run_delegate(
                messages=[{"role": "user", "content": user_content}],
                model=resolved_model,
                system_prompt=system_prompt,
                tools=False,
                cancel_token=cancel_token,
                inference_params={
                    "temperature": inference_temperature,
                    "max_tokens": inference_max_tokens,
                },
            )
        except Exception as e:
            last_err = f"llm_error: {type(e).__name__}: {e}"
            raw = None
        # Detect "connection refused" string both at exception layer and in
        # the delegate's stringified error path.
        is_conn_refused = (
            ("Connection refused" in last_err)
            or (isinstance(raw, str)
                and raw.startswith("Delegation error:")
                and "Connection refused" in raw))
        if is_conn_refused and attempt < 2:
            import time as _time
            _time.sleep(0.8 + attempt * 1.2)  # 0.8s, 2.0s
            last_err = ""
            continue
        break

    if last_err:
        return [], last_err
    if raw is None or (isinstance(raw, str) and raw.startswith("Delegation error:")):
        return [], (raw or "delegate returned None")

    parsed = cc._extract_json_from_llm(raw, expect_array=True)
    if not isinstance(parsed, list):
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

    def _write_triples_to_kg(triples: list[dict], sf: str, did: str) -> int:
        """Write triples to MemPalace's KG with provenance, return count.
        Falls back to legacy signature on MemPalace < 3.3.3."""
        nonlocal last_error
        written = 0
        for t in triples:
            try:
                kg.add_triple(
                    subject=t["subject"], predicate=t["predicate"],
                    obj=t["object"], confidence=t["confidence"],
                    source_closet=None, source_file=sf,
                    source_drawer_id=did, adapter_name=adapter_name,
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
                    result.errors += 1
            except Exception as e:
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

    try:
        if chunking_mode == "source_file":
            # Iterate distinct source files, read each from disk, paragraph-
            # chunk at source_chunk_chars, extract per chunk. Cursor key is
            # encoded as <representative_drawer_id>#<chunk_index> so the
            # existing schema works without migration.
            for src in _iter_wing_source_files(palace_path, wing, source_prefix):
                if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
                    last_error = "cancelled"
                    break
                sf = src["source_file"]
                rep_did = src["representative_drawer_id"]

                if skip_code and _is_code_path(sf):
                    # Skip the whole source. Record one cursor row keyed on
                    # the rep drawer id so it's not retried.
                    cursor_key = f"{rep_did}#0"
                    if cursor_key not in already:
                        _progress_record(chats_db_path, wing, cursor_key,
                                         sf, adapter_name, 0,
                                         error="skipped: code")
                    result.drawers_skipped += len(src["drawer_ids"])
                    continue

                # Source-change detection. If on-disk (mtime, size) differs
                # from what we recorded last cycle, the file was edited —
                # purge old triples for this source AND drop chunk-progress
                # rows so re-extraction targets the new content. Sources we
                # can't stat (drawer's source_file isn't an absolute path,
                # e.g. chat-sync mirror drawers) skip this check.
                cur_mt = cur_sz = 0
                if sf and os.path.isfile(sf):
                    try:
                        st = os.stat(sf)
                        cur_mt = int(st.st_mtime)
                        cur_sz = int(st.st_size)
                    except OSError:
                        cur_mt = cur_sz = 0
                prev = source_state_cursor.get(sf)
                if cur_mt and (prev is None or prev != (cur_mt, cur_sz)):
                    # First-cycle ever for this source: prev is None — no
                    # invalidation needed (nothing to purge), just record.
                    # Subsequent cycles with a real diff: invalidate before
                    # re-extracting.
                    if prev is not None:
                        try:
                            t_del, p_del = _invalidate_source_in_kg(
                                palace_path, chats_db_path, wing, sf,
                                adapter_name)
                            if t_del or p_del:
                                print(f"{log_prefix} invalidated source "
                                      f"{sf}: triples={t_del} "
                                      f"progress={p_del}", flush=True)
                            # Drop chunk entries from the in-memory `already`
                            # set so this cycle re-extracts the new content.
                            stale_keys = {k for k in already
                                          if k.startswith(rep_did + "#")}
                            already -= stale_keys
                        except Exception as e:
                            print(f"{log_prefix} invalidate {sf} failed: "
                                  f"{type(e).__name__}: {e}", flush=True)
                    # Record/update cursor so next cycle compares correctly.
                    try:
                        _source_state_record(chats_db_path, wing, sf,
                                             cur_mt, cur_sz)
                    except Exception:
                        pass

                # Read the source file from disk. If absent (e.g. user
                # removed an input folder mid-cycle), fall back to per-drawer
                # for this source so we don't lose its triples entirely.
                file_text = ""
                read_err = ""
                if sf and os.path.isfile(sf):
                    try:
                        with open(sf, "r", encoding="utf-8", errors="replace") as f:
                            file_text = f.read()
                    except OSError as e:
                        read_err = f"read failed: {type(e).__name__}: {e}"
                else:
                    read_err = "source file not on disk"

                # If we couldn't read the file, fall back to per-drawer for
                # this source so we still get *some* triples.
                if read_err:
                    if progress_cb:
                        try:
                            progress_cb("source_unreadable", source_file=sf,
                                        error=read_err)
                        except Exception:
                            pass
                    for drawer in _iter_wing_drawers(palace_path, wing, sf):
                        result.drawers_seen += 1
                        did = drawer["id"]
                        if did in already:
                            result.drawers_skipped += 1
                            continue
                        content = drawer["content"]
                        if not content.strip():
                            _progress_record(chats_db_path, wing, did, sf,
                                             adapter_name, 0,
                                             error="skipped: empty")
                            result.drawers_skipped += 1
                            continue
                        triples, err = extract_triples_from_drawer(
                            content=content, source_file=sf, drawer_id=did,
                            model=model, profile=profile,
                            max_triples=max_triples_per_drawer,
                            max_drawer_chars=max_drawer_chars,
                            min_confidence=min_confidence,
                            cancel_token=cancel_token)
                        if err:
                            result.errors += 1
                            last_error = err
                            _progress_record(chats_db_path, wing, did, sf,
                                             adapter_name, 0,
                                             error=err[:240])
                            continue
                        written = _write_triples_to_kg(triples, sf, did)
                        result.drawers_processed += 1
                        result.triples_extracted += written
                        _progress_record(chats_db_path, wing, did, sf,
                                         adapter_name, written, error="")
                    continue

                # Strip the converter's frontmatter so it doesn't dilute
                # the LLM's attention.
                file_text = _strip_brain_frontmatter(file_text)
                chunks = _chunk_text_paragraphs(file_text, source_chunk_chars)
                if not chunks:
                    cursor_key = f"{rep_did}#0"
                    _progress_record(chats_db_path, wing, cursor_key, sf,
                                     adapter_name, 0,
                                     error="skipped: empty after chunking")
                    result.drawers_skipped += len(src["drawer_ids"])
                    continue

                # Each drawer in this source counts as "seen" for the
                # cycle stats (so the daemon log shows realistic numbers).
                # We process by chunk-index, not by drawer.
                result.drawers_seen += len(src["drawer_ids"])

                for ci, chunk_text in enumerate(chunks):
                    if cancel_token is not None and getattr(cancel_token, "is_set", lambda: False)():
                        last_error = "cancelled"
                        break
                    cursor_key = f"{rep_did}#{ci}"
                    if cursor_key in already:
                        result.drawers_skipped += 1
                        continue
                    if not chunk_text.strip():
                        _progress_record(chats_db_path, wing, cursor_key, sf,
                                         adapter_name, 0,
                                         error="skipped: empty")
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
                        drawer_id=rep_did,  # provenance attaches to rep
                        model=model, profile=profile,
                        max_triples=max_triples_per_drawer,
                        max_drawer_chars=max_drawer_chars,
                        min_confidence=min_confidence,
                        cancel_token=cancel_token)

                    if err:
                        result.errors += 1
                        last_error = err
                        _progress_record(chats_db_path, wing, cursor_key, sf,
                                         adapter_name, 0, error=err[:240])
                        if progress_cb:
                            try:
                                progress_cb("error", drawer_id=cursor_key,
                                            error=err)
                            except Exception:
                                pass
                        continue

                    written = _write_triples_to_kg(triples, sf, rep_did)
                    result.drawers_processed += 1
                    result.triples_extracted += written
                    _progress_record(chats_db_path, wing, cursor_key, sf,
                                     adapter_name, written, error="")
                    if progress_cb:
                        try:
                            progress_cb("processed", drawer_id=cursor_key,
                                        triples=written,
                                        running_total=result.triples_extracted)
                        except Exception:
                            pass
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
            where.append("source_file LIKE ? || '%'")
            params.append(source_prefix)
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
                where.append("source_file LIKE ? || '%'")
                params.append(source_prefix)
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
                # Drawer doesn't trace back to a real file — skip from
                # incremental tracking. (Chat-sync mirror drawers, etc.)
                skipped_no_disk += 1
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
