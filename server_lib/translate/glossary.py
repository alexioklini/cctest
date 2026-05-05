"""Glossary storage — JSON files under agents/main/glossaries/.

Glossaries are exact-match term lists injected into the translation system
prompt. Not MemPalace (semantic search) — translation needs literal lookup.

Schema:
{
  "name": "DZ-Bank-DE-EN",
  "description": "Bank-spezifische Begriffe DE→EN",
  "source": "de",                  # optional, ISO 639-1
  "target": "en",                  # optional, ISO 639-1
  "entries": [
    {"src": "Eigenkapitalquote", "tgt": "equity ratio"},
    ...
  ],
  "do_not_translate": ["BaFin", "DZ Bank", "MaRisk"]
}
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

# Resolve once at import — server_lib lives next to brain.py at the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GLOSSARY_DIR = os.path.join(_REPO_ROOT, "agents", "main", "glossaries")

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(name: str) -> str:
    s = (name or "").strip().lower().replace(" ", "-")
    s = _SLUG_RE.sub("-", s).strip("-")
    return s or "glossary"


def _path_for(slug: str) -> str:
    safe = _slugify(slug)
    return os.path.join(GLOSSARY_DIR, f"{safe}.json")


def list_glossaries() -> list[dict]:
    """Return [{slug, name, description, source, target, entry_count}, ...]."""
    if not os.path.isdir(GLOSSARY_DIR):
        return []
    out = []
    for fn in sorted(os.listdir(GLOSSARY_DIR)):
        if not fn.endswith(".json"):
            continue
        slug = fn[:-5]
        try:
            with open(os.path.join(GLOSSARY_DIR, fn), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        out.append({
            "slug": slug,
            "name": data.get("name") or slug,
            "description": data.get("description") or "",
            "source": data.get("source") or "",
            "target": data.get("target") or "",
            "entry_count": len(data.get("entries") or []),
            "do_not_translate_count": len(data.get("do_not_translate") or []),
        })
    return out


def load_glossary(slug: str) -> dict | None:
    p = _path_for(slug)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        data["slug"] = _slugify(slug)
        return data
    except Exception:
        return None


def save_glossary(data: dict) -> dict:
    """Validate + write atomically. Returns the saved (normalised) object.

    Raises ValueError on invalid input.
    """
    if not isinstance(data, dict):
        raise ValueError("glossary payload must be an object")
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("glossary 'name' is required")
    slug = _slugify(data.get("slug") or name)

    entries_in = data.get("entries") or []
    if not isinstance(entries_in, list):
        raise ValueError("'entries' must be an array")
    entries: list[dict] = []
    for e in entries_in:
        if not isinstance(e, dict):
            continue
        src = (e.get("src") or "").strip()
        tgt = (e.get("tgt") or "").strip()
        if not src or not tgt:
            continue
        entries.append({"src": src, "tgt": tgt})

    dnt_in = data.get("do_not_translate") or []
    if not isinstance(dnt_in, list):
        dnt_in = []
    do_not_translate = [str(x).strip() for x in dnt_in if str(x).strip()]

    normalised = {
        "slug": slug,
        "name": name,
        "description": (data.get("description") or "").strip(),
        "source": (data.get("source") or "").strip().lower()[:2],
        "target": (data.get("target") or "").strip().lower()[:2],
        "entries": entries,
        "do_not_translate": do_not_translate,
    }

    os.makedirs(GLOSSARY_DIR, exist_ok=True)
    target_path = _path_for(slug)
    fd, tmp = tempfile.mkstemp(prefix=".glossary-", suffix=".json", dir=GLOSSARY_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(normalised, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target_path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
    return normalised


def delete_glossary(slug: str) -> bool:
    p = _path_for(slug)
    if not os.path.exists(p):
        return False
    try:
        os.unlink(p)
        return True
    except Exception:
        return False


def glossary_to_system_block(g: dict | None, max_entries: int = 200) -> str:
    """Format a glossary as an instruction block to append to the translation
    system prompt. Returns '' if nothing to inject."""
    if not g:
        return ""
    entries = (g.get("entries") or [])[:max_entries]
    dnt = g.get("do_not_translate") or []
    if not entries and not dnt:
        return ""
    lines = ["", "GLOSSARY (use these translations exactly when the source term appears):"]
    for e in entries:
        lines.append(f'  "{e["src"]}" → "{e["tgt"]}"')
    if dnt:
        lines.append("")
        lines.append("DO NOT TRANSLATE these terms — keep verbatim in the output:")
        for t in dnt:
            lines.append(f"  - {t}")
    return "\n".join(lines)
