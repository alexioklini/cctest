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
