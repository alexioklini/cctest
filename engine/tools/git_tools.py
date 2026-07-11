# Git + GitHub CLI tools (extracted from brain.py, A3).
# Subprocess wrappers around the `git` / `gh` CLIs.
# _ok / _err are shared brain utilities imported lazily at call time to
# avoid a circular import (brain.py imports these tools near module-eval end).

import json
import os
import subprocess


def _ok(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _run_git(cmd_args: list[str], cwd: str | None = None, timeout: int = 30) -> tuple[int, str]:
    """Run a git command and return (exit_code, output)."""
    try:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["PAGER"] = "cat"
        proc = subprocess.run(
            ["git", "--no-pager"] + cmd_args,
            capture_output=True, cwd=cwd, env=env, timeout=timeout,
        )
        output = proc.stdout.decode("utf-8", errors="replace")
        if proc.stderr:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            if err and proc.returncode != 0:
                output += f"\n{err}" if output else err
        return proc.returncode, output.strip()
    except subprocess.TimeoutExpired:
        return -1, f"Git command timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "git not found — install git first"
    except Exception as e:
        return -1, str(e)


def tool_git_command(args: dict) -> str:
    """Execute git operations with structured output."""
    action = args.get("action", "")
    if not action:
        return _err("Missing action")

    if action == "status":
        code, out = _run_git(["status", "--porcelain", "-b"])
        if code != 0:
            return _err(out)
        lines = out.split("\n")
        branch = lines[0][3:] if lines and lines[0].startswith("## ") else ""
        files = {"modified": [], "staged": [], "untracked": []}
        for l in lines[1:]:
            if not l.strip():
                continue
            idx, wt = l[0], l[1]
            fname = l[3:]
            if idx in ("M", "A", "D", "R"):
                files["staged"].append(fname)
            if wt == "M":
                files["modified"].append(fname)
            elif wt == "?":
                files["untracked"].append(fname)
        return _ok({"branch": branch, **files})

    elif action == "diff":
        cmd = ["diff"]
        if args.get("staged"):
            cmd.append("--staged")
        if args.get("ref"):
            cmd.append(args["ref"])
        if args.get("path"):
            cmd.extend(["--", args["path"]])
        code, out = _run_git(cmd)
        if code != 0:
            return _err(out)
        # Truncate large diffs
        if len(out) > 30000:
            out = out[:30000] + "\n... (diff truncated)"
        return _ok({"diff": out})

    elif action == "log":
        limit = args.get("limit", 20)
        cmd = ["log", f"--max-count={limit}", "--format=%H|%an|%ae|%ai|%s"]
        if args.get("author"):
            cmd.append(f"--author={args['author']}")
        if args.get("since"):
            cmd.append(f"--since={args['since']}")
        if args.get("path"):
            cmd.extend(["--", args["path"]])
        code, out = _run_git(cmd)
        if code != 0:
            return _err(out)
        commits = []
        for line in out.split("\n"):
            if "|" in line:
                parts = line.split("|", 4)
                if len(parts) == 5:
                    commits.append({"hash": parts[0], "author": parts[1], "email": parts[2], "date": parts[3], "message": parts[4]})
        return _ok({"commits": commits, "count": len(commits)})

    elif action == "branch":
        if args.get("create") and args.get("name"):
            code, out = _run_git(["checkout", "-b", args["name"]])
            return _ok({"created": args["name"]}) if code == 0 else _err(out)
        elif args.get("switch") and args.get("name"):
            code, out = _run_git(["checkout", args["name"]])
            return _ok({"switched": args["name"]}) if code == 0 else _err(out)
        else:
            code, out = _run_git(["branch", "-a", "--format=%(refname:short)|%(objectname:short)|%(upstream:short)"])
            if code != 0:
                return _err(out)
            branches = []
            for line in out.split("\n"):
                if "|" in line:
                    parts = line.split("|")
                    branches.append({"name": parts[0], "commit": parts[1] if len(parts) > 1 else "", "upstream": parts[2] if len(parts) > 2 else ""})
            # Get current branch
            _, current = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            return _ok({"branches": branches, "current": current.strip()})

    elif action == "commit":
        message = args.get("message", "")
        if not message:
            return _err("Missing commit message")
        files = args.get("files", [])
        if files:
            code, out = _run_git(["add"] + files)
            if code != 0:
                return _err(f"Failed to stage: {out}")
        if args.get("all"):
            code, out = _run_git(["commit", "-a", "-m", message])
        else:
            code, out = _run_git(["commit", "-m", message])
        if code != 0:
            return _err(out)
        # Get the new commit hash
        _, hash_out = _run_git(["rev-parse", "--short", "HEAD"])
        return _ok({"committed": hash_out.strip(), "message": message})

    elif action == "stash":
        sub = args.get("sub_action", "save")
        if sub == "save":
            msg = args.get("message", "")
            cmd = ["stash", "push"]
            if msg:
                cmd.extend(["-m", msg])
            code, out = _run_git(cmd)
        elif sub == "pop":
            code, out = _run_git(["stash", "pop"])
        elif sub == "list":
            code, out = _run_git(["stash", "list"])
        elif sub == "drop":
            code, out = _run_git(["stash", "drop"])
        else:
            return _err(f"Unknown stash sub_action: {sub}")
        return _ok({"output": out}) if code == 0 else _err(out)

    elif action == "blame":
        path = args.get("path", "")
        if not path:
            return _err("Missing path for blame")
        cmd = ["blame", "--porcelain"]
        if args.get("line_start") and args.get("line_end"):
            cmd.extend([f"-L{args['line_start']},{args['line_end']}"])
        cmd.append(path)
        code, out = _run_git(cmd)
        if code != 0:
            return _err(out)
        if len(out) > 20000:
            out = out[:20000] + "\n... (truncated)"
        return _ok({"blame": out})

    elif action == "show":
        ref = args.get("ref", "HEAD")
        code, out = _run_git(["show", "--stat", ref])
        if code != 0:
            return _err(out)
        if len(out) > 20000:
            out = out[:20000] + "\n... (truncated)"
        return _ok({"output": out})

    elif action == "tag":
        if args.get("create") and args.get("name"):
            cmd = ["tag"]
            if args.get("message"):
                cmd.extend(["-a", args["name"], "-m", args["message"]])
            else:
                cmd.append(args["name"])
            code, out = _run_git(cmd)
            return _ok({"tagged": args["name"]}) if code == 0 else _err(out)
        else:
            code, out = _run_git(["tag", "-l", "--sort=-creatordate", "--format=%(refname:short)|%(creatordate:short)|%(subject)"])
            if code != 0:
                return _err(out)
            tags = []
            for line in out.split("\n"):
                if "|" in line:
                    parts = line.split("|", 2)
                    tags.append({"name": parts[0], "date": parts[1] if len(parts) > 1 else "", "message": parts[2] if len(parts) > 2 else ""})
            return _ok({"tags": tags})

    elif action == "remote":
        code, out = _run_git(["remote", "-v"])
        if code != 0:
            return _err(out)
        remotes = {}
        for line in out.split("\n"):
            parts = line.split()
            if len(parts) >= 2:
                remotes[parts[0]] = parts[1]
        return _ok({"remotes": remotes})

    return _err(f"Unknown git action: {action}")


def _run_gh(cmd_args: list[str], timeout: int = 30) -> tuple[int, str]:
    """Run a gh CLI command and return (exit_code, output)."""
    try:
        proc = subprocess.run(
            ["gh"] + cmd_args,
            capture_output=True, timeout=timeout,
        )
        output = proc.stdout.decode("utf-8", errors="replace")
        if proc.stderr:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            if err and proc.returncode != 0:
                output += f"\n{err}" if output else err
        return proc.returncode, output.strip()
    except FileNotFoundError:
        return -1, "gh CLI not found — install with: brew install gh"
    except subprocess.TimeoutExpired:
        return -1, f"GitHub CLI timed out after {timeout}s"
    except Exception as e:
        return -1, str(e)


def tool_github_command(args: dict) -> str:
    """Interact with GitHub via gh CLI."""
    action = args.get("action", "")
    if not action:
        return _err("Missing action")

    limit = args.get("limit", 20)

    if action == "pr_list":
        cmd = ["pr", "list", "--limit", str(limit), "--json", "number,title,author,state,headRefName,baseRefName,createdAt,url"]
        if args.get("state"):
            cmd.extend(["--state", args["state"]])
        if args.get("author"):
            cmd.extend(["--author", args["author"]])
        code, out = _run_gh(cmd)
        if code != 0:
            return _err(out)
        try:
            return _ok({"prs": json.loads(out)})
        except json.JSONDecodeError:
            return _ok({"output": out})

    elif action == "pr_create":
        title = args.get("title", "")
        if not title:
            return _err("Missing PR title")
        cmd = ["pr", "create", "--title", title]
        if args.get("body"):
            cmd.extend(["--body", args["body"]])
        if args.get("base"):
            cmd.extend(["--base", args["base"]])
        if args.get("head"):
            cmd.extend(["--head", args["head"]])
        if args.get("draft"):
            cmd.append("--draft")
        code, out = _run_gh(cmd)
        return _ok({"url": out}) if code == 0 else _err(out)

    elif action == "pr_view":
        number = args.get("number")
        if not number:
            return _err("Missing PR number")
        code, out = _run_gh(["pr", "view", str(number), "--json",
                             "number,title,body,state,author,headRefName,baseRefName,additions,deletions,files,reviews,comments,url"])
        if code != 0:
            return _err(out)
        try:
            return _ok(json.loads(out))
        except json.JSONDecodeError:
            return _ok({"output": out})

    elif action == "pr_merge":
        number = args.get("number")
        if not number:
            return _err("Missing PR number")
        method = args.get("method", "merge")
        cmd = ["pr", "merge", str(number), f"--{method}"]
        code, out = _run_gh(cmd)
        return _ok({"merged": number, "method": method}) if code == 0 else _err(out)

    elif action == "pr_review":
        number = args.get("number")
        if not number:
            return _err("Missing PR number")
        code, out = _run_gh(["pr", "view", str(number), "--json", "reviews,comments"])
        if code != 0:
            return _err(out)
        try:
            return _ok(json.loads(out))
        except json.JSONDecodeError:
            return _ok({"output": out})

    elif action == "issue_list":
        cmd = ["issue", "list", "--limit", str(limit), "--json", "number,title,author,state,labels,createdAt,url"]
        if args.get("state"):
            cmd.extend(["--state", args["state"]])
        if args.get("labels"):
            cmd.extend(["--label", args["labels"]])
        code, out = _run_gh(cmd)
        if code != 0:
            return _err(out)
        try:
            return _ok({"issues": json.loads(out)})
        except json.JSONDecodeError:
            return _ok({"output": out})

    elif action == "issue_create":
        title = args.get("title", "")
        if not title:
            return _err("Missing issue title")
        cmd = ["issue", "create", "--title", title]
        if args.get("body"):
            cmd.extend(["--body", args["body"]])
        if args.get("labels"):
            cmd.extend(["--label", args["labels"]])
        code, out = _run_gh(cmd)
        return _ok({"url": out}) if code == 0 else _err(out)

    elif action == "issue_view":
        number = args.get("number")
        if not number:
            return _err("Missing issue number")
        code, out = _run_gh(["issue", "view", str(number), "--json", "number,title,body,state,author,labels,comments,url"])
        if code != 0:
            return _err(out)
        try:
            return _ok(json.loads(out))
        except json.JSONDecodeError:
            return _ok({"output": out})

    elif action == "repo_view":
        code, out = _run_gh(["repo", "view", "--json", "name,description,url,defaultBranchRef,stargazerCount,forkCount,isPrivate,languages"])
        if code != 0:
            return _err(out)
        try:
            return _ok(json.loads(out))
        except json.JSONDecodeError:
            return _ok({"output": out})

    elif action == "release_list":
        code, out = _run_gh(["release", "list", "--limit", str(limit)])
        if code != 0:
            return _err(out)
        return _ok({"output": out})

    elif action == "workflow_list":
        code, out = _run_gh(["workflow", "list", "--json", "name,state,id"])
        if code != 0:
            return _err(out)
        try:
            return _ok({"workflows": json.loads(out)})
        except json.JSONDecodeError:
            return _ok({"output": out})

    elif action == "workflow_run":
        run_id = args.get("run_id", "")
        if not run_id:
            return _err("Missing run_id")
        code, out = _run_gh(["run", "view", run_id, "--json", "status,conclusion,name,createdAt,updatedAt,jobs"])
        if code != 0:
            return _err(out)
        try:
            return _ok(json.loads(out))
        except json.JSONDecodeError:
            return _ok({"output": out})

    elif action == "api":
        endpoint = args.get("endpoint", "")
        if not endpoint:
            return _err("Missing API endpoint")
        method = args.get("api_method", "GET")
        cmd = ["api", endpoint, "--method", method]
        code, out = _run_gh(cmd, timeout=30)
        if code != 0:
            return _err(out)
        try:
            return _ok(json.loads(out))
        except json.JSONDecodeError:
            return _ok({"output": out[:20000]})

    return _err(f"Unknown GitHub action: {action}")


# ── git worktree lanes (v9.311.0) ─────────────────────────────────────────────
# Idea 4/4 from the oh-my-opencode-slim analysis (their `worktrees` skill):
# git worktrees as ISOLATED coding lanes for code-mode projects — risky
# refactors / package upgrades / parallel work run in their own checkout under
# <working_dir>/.worktrees/<slug> on branch brain/<slug>, without touching the
# main tree. The lane dir sits INSIDE working_dir on purpose: the bottom-panel
# terminal cwd-lockdown, the file tree and all file tools cover it. It is kept
# out of git's view via .git/info/exclude (repo-PRIVATE ignore — we never edit
# the user's .gitignore).
#
# v1 scope: create / list / remove / diff. NO auto-merge — integrating a lane
# is a deliberate user action (terminal: git merge brain/<slug>), the diff
# action is the review step. Registry (purpose/base/created) in
# .worktrees/lanes.json next to the lanes.

_WORKTREE_DIRNAME = ".worktrees"
_WORKTREE_SLUG_RE = None  # compiled lazily


def _worktree_repo_root():
    """The code-mode project's working_dir, if it is a git repo.
    Returns (root, err)."""
    from engine.context import get_request_context
    wd = get_request_context().working_dir or ""
    if not wd:
        return "", _err("git_worktree: nur in einem Code-Projekt verfügbar "
                        "(kein working_dir im Kontext)")
    wd = os.path.abspath(os.path.expanduser(wd))
    if not os.path.isdir(os.path.join(wd, ".git")):
        code, out = _run_git(["rev-parse", "--show-toplevel"], cwd=wd)
        if code != 0:
            return "", _err(f"git_worktree: {wd} ist kein Git-Repository")
    return wd, None


def _worktree_slug(raw: str):
    import re
    global _WORKTREE_SLUG_RE
    if _WORKTREE_SLUG_RE is None:
        _WORKTREE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
    slug = (raw or "").strip().lower()
    if not _WORKTREE_SLUG_RE.match(slug):
        return "", _err("git_worktree: slug muss ^[a-z0-9][a-z0-9_-]{0,39}$ "
                        "erfüllen (z. B. 'refactor-auth')")
    return slug, None


def _worktree_registry_path(root: str) -> str:
    return os.path.join(root, _WORKTREE_DIRNAME, "lanes.json")


def _worktree_registry_load(root: str) -> dict:
    try:
        with open(_worktree_registry_path(root)) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _worktree_registry_save(root: str, reg: dict) -> None:
    p = _worktree_registry_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)


def _worktree_ensure_excluded(root: str) -> None:
    """Append `.worktrees/` to .git/info/exclude (repo-private, idempotent) so
    lanes never show up as untracked noise in the MAIN tree. Never touches the
    user's .gitignore."""
    try:
        info_dir = os.path.join(root, ".git", "info")
        if not os.path.isdir(info_dir):  # .git may be a file (this IS a worktree)
            return
        p = os.path.join(info_dir, "exclude")
        existing = ""
        if os.path.exists(p):
            with open(p) as f:
                existing = f.read()
        if f"{_WORKTREE_DIRNAME}/" not in existing:
            with open(p, "a") as f:
                f.write(f"\n# Brain-Agent worktree lanes\n{_WORKTREE_DIRNAME}/\n")
    except Exception:
        pass  # cosmetic only — a failed exclude never blocks the lane


def _worktree_dirty(lane_dir: str) -> list[str]:
    code, out = _run_git(["status", "--porcelain"], cwd=lane_dir)
    if code != 0:
        return []
    return [l for l in out.split("\n") if l.strip()]


def tool_git_worktree(args: dict) -> str:
    """Manage git-worktree lanes for the code-mode project."""
    action = (args.get("action") or "").strip()
    root, err = _worktree_repo_root()
    if err:
        return err

    if action == "list":
        code, out = _run_git(["worktree", "list", "--porcelain"], cwd=root)
        if code != 0:
            return _err(f"git_worktree: {out}")
        reg = _worktree_registry_load(root)
        lanes = []
        cur: dict = {}
        for line in out.split("\n") + [""]:
            if not line.strip():
                if cur.get("worktree"):
                    wt = cur["worktree"]
                    if _WORKTREE_DIRNAME + os.sep in wt or wt == root:
                        slug = os.path.basename(wt) if wt != root else "(main)"
                        meta = reg.get(slug, {})
                        lanes.append({
                            "slug": slug, "path": wt,
                            "branch": (cur.get("branch") or "").replace("refs/heads/", ""),
                            "is_main": wt == root,
                            "dirty_files": len(_worktree_dirty(wt)) if wt != root else None,
                            **({"purpose": meta.get("purpose"), "base": meta.get("base"),
                                "created": meta.get("created")} if meta else {}),
                        })
                cur = {}
                continue
            k, _, v = line.partition(" ")
            cur[k] = v or True
        return _ok({"root": root, "lanes": lanes})

    slug, err = _worktree_slug(args.get("slug", ""))
    if err and action != "list":
        return err
    lane_dir = os.path.join(root, _WORKTREE_DIRNAME, slug)
    branch = f"brain/{slug}"

    if action == "create":
        if os.path.isdir(lane_dir):
            return _err(f"git_worktree: Lane '{slug}' existiert bereits ({lane_dir})")
        base = (args.get("base") or "").strip() or "HEAD"
        _worktree_ensure_excluded(root)
        code, out = _run_git(["worktree", "add", lane_dir, "-b", branch, base],
                             cwd=root, timeout=120)
        if code != 0:
            return _err(f"git_worktree: {out}")
        reg = _worktree_registry_load(root)
        code2, base_sha = _run_git(["rev-parse", "--short", base], cwd=root)
        import datetime as _dt
        reg[slug] = {"branch": branch, "base": base,
                     "base_sha": base_sha if code2 == 0 else "",
                     "purpose": (args.get("purpose") or "").strip()[:300],
                     "created": _dt.datetime.now().isoformat(timespec="seconds")}
        _worktree_registry_save(root, reg)
        return _ok({"slug": slug, "path": lane_dir, "branch": branch, "base": base,
                    "note": ("Lane erstellt. Dort arbeiten (Dateipfade unter "
                             f"{lane_dir}), mit action='diff' reviewen; die "
                             "Integration (merge) macht der Nutzer bewusst im "
                             "Terminal — nie automatisch.")})

    if action == "diff":
        if not os.path.isdir(lane_dir):
            return _err(f"git_worktree: Lane '{slug}' existiert nicht")
        reg = _worktree_registry_load(root)
        base = (args.get("base") or (reg.get(slug) or {}).get("base") or "HEAD").strip()
        code, out = _run_git(["diff", f"{base}...{branch}", "--stat"], cwd=root, timeout=60)
        if code != 0:
            return _err(f"git_worktree: {out}")
        stat = out
        code, full = _run_git(["diff", f"{base}...{branch}"], cwd=root, timeout=60)
        if len(full) > 30000:
            full = full[:30000] + "\n... (Diff gekürzt — pro Datei mit git_command action=diff ansehen)"
        dirty = _worktree_dirty(lane_dir)
        return _ok({"slug": slug, "branch": branch, "base": base, "stat": stat,
                    "diff": full,
                    "uncommitted_in_lane": dirty[:50],
                    **({"note": "Es gibt UNCOMMITTETE Änderungen in der Lane — "
                                "der Diff zeigt nur Committetes."} if dirty else {})})

    if action == "remove":
        if not os.path.isdir(lane_dir):
            return _err(f"git_worktree: Lane '{slug}' existiert nicht")
        dirty = _worktree_dirty(lane_dir)
        force = bool(args.get("force", False))
        if dirty and not force:
            return _err(f"git_worktree: Lane '{slug}' hat {len(dirty)} "
                        f"uncommittete Änderung(en) — erst committen/verwerfen "
                        f"oder force=true (verwirft sie ENDGÜLTIG). Nur nach "
                        f"Rückfrage beim Nutzer forcen.")
        cmd = ["worktree", "remove", lane_dir]
        if force:
            cmd.append("--force")
        code, out = _run_git(cmd, cwd=root, timeout=60)
        if code != 0:
            return _err(f"git_worktree: {out}")
        reg = _worktree_registry_load(root)
        reg.pop(slug, None)
        _worktree_registry_save(root, reg)
        return _ok({"slug": slug, "removed": True,
                    "note": f"Worktree entfernt. Der Branch {branch} bleibt "
                            f"erhalten (Löschung nur bewusst durch den Nutzer: "
                            f"git branch -D {branch})."})

    return _err("git_worktree: action muss create|list|diff|remove sein")
