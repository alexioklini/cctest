#!/usr/bin/env python3
"""
kg_prompt_eval.py — Standalone evaluator for the KG triple-extraction prompt.

Reads one or more documents, chunks them, runs the configured LLM with the
selected profile's prompt, and prints triples for human review. No DB writes.

Usage:
    python3 kg_prompt_eval.py <file_or_dir> [--profile normative|generic]
                                            [--model <model_id>]
                                            [--chunks N]   (limit chunks per file)
                                            [--max-chars N] (per chunk, default 4000)
                                            [--show-content]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Iterator

# Brain config + delegate machinery
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_cli as cc


def _bootstrap_brain_config():
    """Replicate the slice of server.py startup we need: load providers,
    load models, initialize cc._models_config so resolve_provider_for_model /
    _run_delegate work in a standalone process.
    """
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        file_cfg = json.load(f)
    providers = file_cfg.get("providers") or {}
    existing_models = file_cfg.get("models") or {}
    deleted_models = file_cfg.get("deleted_models") or []
    if providers:
        cc.init_models_config(providers, existing_models,
                              deleted_models=deleted_models)
    # Make the gdpr_scanner config visible so PII routing can work.
    if hasattr(cc, "_GDPR_CFG_CACHE"):
        try:
            cc._GDPR_CFG_CACHE.clear()  # type: ignore[attr-defined]
        except Exception:
            pass
    # Cache server config bits some helpers expect.
    if hasattr(cc, "set_gdpr_scanner_config"):
        try:
            cc.set_gdpr_scanner_config(file_cfg.get("gdpr_scanner") or {})
        except Exception:
            pass
    return file_cfg


_BRAIN_CFG = _bootstrap_brain_config()

# ── Profile definitions ──────────────────────────────────────────────────────

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
predicate in lowercase snake_case English (e.g. retention_period,
review_frequency, escalation_path). Do NOT translate the predicate to
German — predicates stay English so triples join across languages.

QUALITY RULES:
- Extract obligations and references, not narrative or background.
- Skip table-of-contents lines, page headers/footers, signature blocks.
- Subject and object stay in the SOURCE language (German stays German).
- Each triple should be defensible from the `span` quote alone.
- Prefer specific over generic: `(GDPR Art. 17, requires, deletion within
  30 days)` beats `(GDPR, mentions, deletion)`.
- If the chunk is pure boilerplate, return [].
- Return at most %MAX_TRIPLES% triples per chunk; if more exist, pick the
  most important ones.

EXAMPLES:

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

Source (English spec fragment):
  "The system MUST validate the bearer token before processing any request,
  except for the /health endpoint."
Triples:
[
  {"subject":"the system","predicate":"requires","object":"validate the bearer token","confidence":0.97,"span":"The system MUST validate the bearer token before processing any request"},
  {"subject":"validate the bearer token","predicate":"applies_to","object":"any request","confidence":0.9,"span":"before processing any request"},
  {"subject":"validate the bearer token","predicate":"exception","object":"/health endpoint","confidence":0.95,"span":"except for the /health endpoint"}
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

Predicates are open but stay in lowercase English snake_case (e.g.
mentions, describes, located_in, owned_by, succeeded_by). Subjects and
objects remain in the source language. Skip headers/footers/boilerplate.
Return at most %MAX_TRIPLES% triples per chunk.
"""

PROFILES = {
    "normative": {
        "system_prompt": NORMATIVE_PROMPT,
        "predicates": NORMATIVE_PREDICATES,
    },
    "generic": {
        "system_prompt": GENERIC_PROMPT,
        "predicates": [],
    },
}


# ── Document reading ─────────────────────────────────────────────────────────

CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
             ".c", ".cpp", ".cs", ".rb", ".kt", ".swift", ".php",
             ".sh", ".bash", ".zsh", ".pl",
             ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
             ".csv", ".tsv", ".log"}

PROSE_EXTS = {".pdf", ".md", ".markdown", ".txt", ".docx", ".html", ".htm",
              ".rst", ".adoc"}


def read_pdf(path: str) -> str:
    import fitz  # type: ignore
    out = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, 1):
            t = page.get_text()
            if t.strip():
                out.append(f"--- Page {i} ---\n{t}")
    return "\n\n".join(out)


def read_docx(path: str) -> str:
    try:
        import docx  # type: ignore
    except ImportError:
        return ""
    d = docx.Document(path)
    return "\n".join(p.text for p in d.paragraphs if p.text)


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def load_document(path: str) -> tuple[str, str]:
    """Return (text, kind). kind in {prose, code, skip}."""
    ext = os.path.splitext(path)[1].lower()
    if ext in CODE_EXTS:
        return "", "code"
    if ext == ".pdf":
        return read_pdf(path), "prose"
    if ext == ".docx":
        return read_docx(path), "prose"
    if ext in PROSE_EXTS or ext == "":
        return read_text(path), "prose"
    return "", "skip"


def chunk_text(text: str, max_chars: int) -> Iterator[str]:
    """Paragraph-aware chunking that respects max_chars hard cap.
    Splits on blank lines first; if a paragraph exceeds the cap, slice it.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    buf: list[str] = []
    n = 0
    for p in paragraphs:
        plen = len(p) + 2
        if n + plen > max_chars and buf:
            yield "\n\n".join(buf)
            buf, n = [], 0
        if plen > max_chars:
            for i in range(0, len(p), max_chars):
                yield p[i:i + max_chars]
            continue
        buf.append(p)
        n += plen
    if buf:
        yield "\n\n".join(buf)


# ── LLM call via Brain's _run_delegate ───────────────────────────────────────

def pick_model(model_arg: str) -> str:
    if model_arg:
        return model_arg
    cfg = cc._load_mempalace_config()
    kg_cfg = cfg.get("kg") or {}
    m = kg_cfg.get("extraction_model") or ""
    if m:
        return m
    # Auto-pick: prefer a German-capable local model first, then Mistral, then
    # whatever else is enabled. We fall back to gdpr_pick_model_for_background
    # only inside call_llm if this returns "".
    preference_order = [
        "gemma-4-26B-A4B-it-MLX-4bit",  # local, strong on German, free
        "mistral-vibe-cli-fast",
        "mistral-vibe-cli-latest",
        "gemini-2.5-flash",
    ]
    enabled = {k: v for k, v in (_BRAIN_CFG.get("models") or {}).items()
               if v.get("enabled", True)}
    for cand in preference_order:
        if cand in enabled:
            return cand
    if enabled:
        return next(iter(enabled.keys()))
    return ""


def call_llm(model: str, system_prompt: str, user_content: str) -> str | None:
    """Run one extraction via _run_delegate. Returns raw text or None on failure."""
    if not model:
        # Use background-pick: prefers cheap haiku/local. Pass content as the
        # PII scan target so cloud→local fallback works on PII drawers.
        try:
            model = cc.gdpr_pick_model_for_background(
                cc.SERVER_DEFAULT_MODEL if hasattr(cc, "SERVER_DEFAULT_MODEL") else "",
                [user_content], purpose="kg_extract_eval")
        except Exception:
            model = ""
    if not model:
        # Fall back to whatever the engine deems usable.
        model = cc.get_default_model() if hasattr(cc, "get_default_model") else ""
    if not model:
        print("[eval] no model resolvable; pass --model", file=sys.stderr)
        return None

    messages = [{"role": "user", "content": user_content}]
    t0 = time.time()
    try:
        out = cc._run_delegate(
            messages=messages,
            model=model,
            system_prompt=system_prompt,
            tools=False,
            inference_params={"temperature": 0.0, "max_tokens": 2000},
        )
    except Exception as e:
        print(f"[eval] _run_delegate failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    dt = time.time() - t0
    print(f"  [llm] model={model} time={dt:.1f}s", file=sys.stderr)
    return out


def parse_triples(raw: str) -> list[dict]:
    if not raw:
        return []
    parsed = cc._extract_json_from_llm(raw, expect_array=True)
    if isinstance(parsed, list):
        return [t for t in parsed if isinstance(t, dict)]
    return []


# ── Main ─────────────────────────────────────────────────────────────────────

def iter_files(target: str) -> Iterator[str]:
    if os.path.isfile(target):
        yield target
        return
    for root, _dirs, files in os.walk(target):
        for fn in sorted(files):
            yield os.path.join(root, fn)


def fmt_triple(t: dict) -> str:
    s = (t.get("subject") or "").strip()
    p = (t.get("predicate") or "").strip()
    o = (t.get("object") or "").strip()
    c = t.get("confidence")
    span = (t.get("span") or "").strip()
    c_s = f" [c={c:.2f}]" if isinstance(c, (int, float)) else ""
    head = f"  ({s})  --[{p}]-->  ({o}){c_s}"
    if span:
        head += f"\n      ↳ \"{span[:160]}\""
    return head


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="file or directory")
    ap.add_argument("--profile", default="normative", choices=list(PROFILES))
    ap.add_argument("--model", default="", help="explicit model id (else config)")
    ap.add_argument("--chunks", type=int, default=0,
                    help="max chunks per file (0=all)")
    ap.add_argument("--skip", type=int, default=0,
                    help="skip the first N chunks per file (jump past TOC/foreword)")
    ap.add_argument("--max-chars", type=int, default=4000,
                    help="max chars per chunk")
    ap.add_argument("--max-triples", type=int, default=10,
                    help="hint to the model")
    ap.add_argument("--show-content", action="store_true",
                    help="also print the chunk text before its triples")
    args = ap.parse_args()

    profile = PROFILES[args.profile]
    system_prompt = profile["system_prompt"].replace(
        "%MAX_TRIPLES%", str(args.max_triples))
    model = pick_model(args.model)

    print(f"=== KG prompt eval — profile={args.profile} model={model or 'auto'} "
          f"max_chars={args.max_chars} max_triples_hint={args.max_triples} ===\n")

    total_files = 0
    total_chunks = 0
    total_triples = 0
    predicate_counts: dict[str, int] = {}
    t_start = time.time()

    for fpath in iter_files(args.target):
        try:
            text, kind = load_document(fpath)
        except Exception as e:
            print(f"\n## {fpath}\n  [skip] read failed: {type(e).__name__}: {e}")
            continue
        if kind == "skip":
            continue
        if kind == "code":
            print(f"\n## {fpath}\n  [skip] code file — code graph handles this")
            continue
        if not text.strip():
            print(f"\n## {fpath}\n  [skip] empty after extraction")
            continue

        total_files += 1
        print(f"\n## {fpath}  ({len(text):,} chars)")

        chunks = list(chunk_text(text, args.max_chars))
        total_chunks_in_file = len(chunks)
        if args.skip > 0:
            chunks = chunks[args.skip:]
        if args.chunks > 0:
            chunks = chunks[:args.chunks]
        print(f"   total_chunks={total_chunks_in_file} "
              f"skipped={args.skip} processing={len(chunks)}")

        for ci, chunk in enumerate(chunks, 1):
            total_chunks += 1
            print(f"\n— chunk {ci}/{len(chunks)} ({len(chunk):,} chars)")
            if args.show_content:
                preview = chunk if len(chunk) <= 600 else chunk[:600] + " ..."
                print(f"  --- content ---\n  {preview}\n  --- end content ---")
            raw = call_llm(model, system_prompt, chunk)
            if raw is None:
                print("  [extraction failed]")
                continue
            triples = parse_triples(raw)
            if not triples:
                preview = (raw or "").strip()
                if preview:
                    if len(preview) > 500:
                        preview = preview[:500] + " ... [truncated]"
                    print(f"  (no triples — raw response):")
                    for ln in preview.splitlines():
                        print(f"    | {ln}")
                else:
                    print("  (no triples — empty response)")
                continue
            for t in triples:
                pred = (t.get("predicate") or "").strip()
                if pred:
                    predicate_counts[pred] = predicate_counts.get(pred, 0) + 1
                total_triples += 1
                print(fmt_triple(t))

    dt = time.time() - t_start
    print(f"\n=== summary ===")
    print(f"  files:    {total_files}")
    print(f"  chunks:   {total_chunks}")
    print(f"  triples:  {total_triples}")
    print(f"  elapsed:  {dt:.1f}s")
    if predicate_counts:
        print(f"  predicates (count):")
        for p, n in sorted(predicate_counts.items(), key=lambda kv: -kv[1]):
            print(f"    {n:4d}  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
