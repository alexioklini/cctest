# ast-grep tools (v9.310.0) — structural (AST-aware) code search + rewrite.
#
# Idea 3/4 from the oh-my-opencode-slim analysis: pattern-based structural
# search ("all calls str($A)") and refactoring with dry-run preview, backed by
# the ast-grep binary (https://ast-grep.github.io, ~25 languages).
#
# HOST DEPENDENCY like yt-dlp/crawl4ai: the `ast-grep` binary is NOT in
# requirements — installed per machine (brew install ast-grep). Missing binary
# → clear German error, never a crash. Every invocation is a subprocess with a
# hard timeout (the only reliably killable unit — see the PDF-hang lesson).
#
# Group: code_graph (undeferred in code-mode via apply_domain_context, deferred
# for the interactive purpose via tool_settings — same gating as code_search).
#
# brain.py re-exports both via `from engine.tools.ast_grep_tools import (...)`.

from __future__ import annotations

import json
import os
import shutil
import subprocess

from engine.context import get_request_context
from engine.tool_exec import _ok, _err

_AST_GREP_TIMEOUT_S = 60
_AST_GREP_MAX_RESULTS = 50
_AST_GREP_MAX_APPLY = 500  # refuse a mass-rewrite beyond this many matches


def _ast_grep_bin() -> str:
    """Resolve the ast-grep binary: PATH first, then the Homebrew default.
    Empty string = not installed."""
    return (shutil.which("ast-grep")
            or (os.path.exists("/opt/homebrew/bin/ast-grep")
                and "/opt/homebrew/bin/ast-grep") or "")


def _ast_grep_root(args: dict):
    """Resolve the search root: explicit `root` arg, else the code-mode
    project's working_dir from the request context. Returns (root, err)."""
    root = (args.get("root") or "").strip()
    if not root:
        root = get_request_context().working_dir or ""
    if not root:
        return "", _err("ast_grep: kein Suchpfad — `root` angeben (oder in "
                        "einem Code-Projekt arbeiten, dann gilt dessen "
                        "Arbeitsverzeichnis)")
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root) and not os.path.isfile(root):
        return "", _err(f"ast_grep: Pfad existiert nicht: {root}")
    return root, None


def _ast_grep_run(pattern: str, root: str, lang: str = "",
                  rewrite: str = "", update_all: bool = False):
    """One ast-grep invocation. Returns (matches, err). With update_all the
    files are MODIFIED in place (no JSON output); otherwise --json=stream
    matches are parsed."""
    bin_ = _ast_grep_bin()
    if not bin_:
        return None, _err("ast_grep: das ast-grep-Binary ist auf diesem Server "
                          "nicht installiert (brew install ast-grep) — bis "
                          "dahin search_files/execute_command nutzen")
    cmd = [bin_, "run", "--pattern", pattern]
    if lang:
        cmd += ["--lang", lang]
    if rewrite:
        cmd += ["--rewrite", rewrite]
    if update_all:
        cmd += ["--update-all"]
    else:
        cmd += ["--json=stream"]
    cmd.append(root)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_AST_GREP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, _err(f"ast_grep: Timeout nach {_AST_GREP_TIMEOUT_S}s — "
                          f"Pattern oder root eingrenzen")
    if p.returncode != 0 and (p.stderr or "").strip():
        # ast-grep errors (bad pattern / unknown lang) land on stderr.
        return None, _err(f"ast_grep: {p.stderr.strip()[:600]}")
    if update_all:
        return [], None
    matches = []
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        m = {
            "file": d.get("file", ""),
            "line": (d.get("range", {}).get("start", {}).get("line", 0) or 0) + 1,
            "text": (d.get("text") or "")[:500],
            "context": (d.get("lines") or "")[:500],
        }
        if "replacement" in d:
            m["replacement"] = (d.get("replacement") or "")[:500]
        matches.append(m)
    return matches, None


def tool_ast_grep_search(args: dict) -> str:
    """Structural AST search: find code by syntax pattern, not text."""
    pattern = (args.get("pattern") or "").strip()
    if not pattern:
        return _err("ast_grep_search: pattern ist erforderlich")
    root, err = _ast_grep_root(args)
    if err:
        return err
    lang = (args.get("lang") or "").strip()
    limit = int(args.get("max_results") or _AST_GREP_MAX_RESULTS)
    matches, err = _ast_grep_run(pattern, root, lang=lang)
    if err:
        return err
    total = len(matches)
    return _ok({
        "root": root, "pattern": pattern, "total_matches": total,
        "matches": matches[:limit],
        **({"note": f"{total} Treffer, nur die ersten {limit} gezeigt — "
                    f"max_results erhöhen oder Pattern eingrenzen"}
           if total > limit else {}),
    })


def tool_ast_grep_replace(args: dict) -> str:
    """Structural AST rewrite with dry-run preview (default) or apply."""
    import brain as _brain
    pattern = (args.get("pattern") or "").strip()
    rewrite = args.get("rewrite")
    if not pattern or rewrite is None:
        return _err("ast_grep_replace: pattern und rewrite sind erforderlich")
    root, err = _ast_grep_root(args)
    if err:
        return err
    lang = (args.get("lang") or "").strip()
    apply_ = bool(args.get("apply", False))

    # ALWAYS scan first (also in apply mode): the preview list is what the
    # apply reports, and the match count gates the mass-rewrite refusal.
    matches, err = _ast_grep_run(pattern, root, lang=lang, rewrite=str(rewrite))
    if err:
        return err
    if not matches:
        return _ok({"root": root, "pattern": pattern, "total_matches": 0,
                    "applied": False, "note": "keine Treffer — nichts zu ersetzen"})
    if not apply_:
        return _ok({
            "root": root, "pattern": pattern, "total_matches": len(matches),
            "applied": False, "preview": matches[:_AST_GREP_MAX_RESULTS],
            "note": ("DRY-RUN — noch nichts geändert. Vorschau prüfen, dann "
                     "denselben Aufruf mit apply=true wiederholen."),
        })
    if len(matches) > _AST_GREP_MAX_APPLY:
        return _err(f"ast_grep_replace: {len(matches)} Treffer > "
                    f"{_AST_GREP_MAX_APPLY} — Mass-Rewrite verweigert; Pattern "
                    f"oder root eingrenzen")
    _, err = _ast_grep_run(pattern, root, lang=lang, rewrite=str(rewrite),
                           update_all=True)
    if err:
        return err
    changed = sorted({m["file"] for m in matches})
    agent = get_request_context().current_agent or getattr(_brain, "_current_agent", None)
    agent_id = agent.agent_id if agent else "main"
    for f in changed:
        try:
            _brain._after_file_write(
                f if os.path.isabs(f) else os.path.join(root, f),
                "modified", agent_id)
        except Exception:
            pass
    return _ok({
        "root": root, "pattern": pattern, "applied": True,
        "replacements": len(matches), "files_changed": changed,
    })
