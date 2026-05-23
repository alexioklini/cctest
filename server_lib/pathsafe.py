"""Shared path-traversal validator.

Single low-level skeleton for the realpath-based path guards that were
duplicated (and DIVERGENT) across handlers/classification.py,
handlers/projects.py, and handlers/admin.py. Each call site keeps its own
EFFECTIVE policy by passing its own roots/flags — the only thing shared is
the realpath-resolution + hard-denylist skeleton.

Low-level by design: stdlib `os` only. NEVER import brain/engine here (one-way
DAG — handlers/engine/brain import FROM this module, not the reverse).

Contract: validate_path(...) returns a 2-tuple (resolved_path, error):
  - success -> (real_path: str, None)
  - failure -> (None,        error: str)
Call sites that historically returned `""`/`None` or raised adapt the tuple.
"""
from __future__ import annotations

import os

# Shared hard-denylist — sensitive system locations no caller should ever
# resolve into. Kept identical to every pre-consolidation copy.
HARD_DENY: tuple[str, ...] = (
    "/etc", "/var", "/usr", "/bin", "/sbin", "/System", "/Library/Keychains",
)


def _under(path: str, root: str) -> bool:
    """True if `path` equals `root` or sits beneath it. Both must be realpath'd."""
    return path == root or path.startswith(root + os.sep)


def validate_path(
    raw: str,
    *,
    allowed_roots: list[str] | None = None,
    apply_hard_deny: bool = True,
    extra_deny: tuple[str, ...] = (),
    deny_agents_dir: str | None = None,
    must_exist: bool = True,
    must_be_dir: bool = False,
    expand_user: bool = False,
) -> tuple[str | None, str | None]:
    """Resolve `raw` and validate it against a site-specific policy.

    Parameters (the per-site knobs):
      allowed_roots   None  -> allow-by-default (anything not denied passes).
                      list  -> ALLOWLIST: the path MUST sit under one root.
                               Roots are realpath-normalized here.
      apply_hard_deny       -> enforce HARD_DENY (+ extra_deny). Default True.
      extra_deny            -> additional denied prefixes (realpath'd here).
      deny_agents_dir       -> if given, refuse paths inside this dir
                               (the brain `agents/` tree).
      must_exist            -> path must exist on disk.
      must_be_dir           -> path must be an existing directory.
      expand_user           -> expanduser() before resolving.

    Returns (real_path, None) on success, (None, error) on rejection.
    """
    if not raw or not isinstance(raw, str):
        return None, "path required"
    try:
        p = raw.strip()
        if expand_user:
            p = os.path.expanduser(p)
        rp = os.path.realpath(p)
    except (OSError, ValueError) as e:
        return None, f"Invalid path: {e}"

    if must_be_dir:
        if not os.path.isdir(rp):
            return None, "Path is not a directory or does not exist"
    elif must_exist:
        if not os.path.exists(rp):
            return None, f"path not found: {raw}"

    if deny_agents_dir:
        agents_root = os.path.realpath(deny_agents_dir)
        if _under(rp, agents_root):
            return None, "Cannot add a folder inside the agents directory"

    if apply_hard_deny:
        for bad in (*HARD_DENY, *extra_deny):
            if _under(rp, bad):
                return None, f"refusing system path: {raw}"

    if allowed_roots is not None:
        for root in allowed_roots:
            if _under(rp, os.path.realpath(root)):
                return rp, None
        return None, f"path is outside allowed roots: {raw}"

    return rp, None
