---
name: github
description: "Interact with GitHub using the gh CLI — PRs, issues, CI runs, API queries."
last_recalled: 2026-03-31
---

# GitHub Skill

Use the `gh` CLI to interact with GitHub.

## Pull Requests

```bash
gh pr list --repo owner/repo
gh pr checks 55 --repo owner/repo
gh pr create --title "title" --body "body"
gh pr merge 55 --squash
```

## Issues

```bash
gh issue list --repo owner/repo --state open
gh issue create --title "title" --body "body"
gh issue close 55
```

## CI / Workflow Runs

```bash
gh run list --repo owner/repo --limit 10
gh run view <run-id> --repo owner/repo
gh run view <run-id> --repo owner/repo --log-failed
```

## API Queries

```bash
gh api repos/owner/repo/pulls/55 --jq '.title, .state'
gh api repos/owner/repo/actions/runs --jq '.workflow_runs[:5] | .[].name'
```

## Tips

- Use `--json` + `--jq` for structured output
- Use `--repo owner/repo` when not in a git directory
- Use `gh auth status` to check authentication
- Use `git --no-pager` for log/diff to avoid pager
