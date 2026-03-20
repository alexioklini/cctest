# Feature Proposal: Custom Agent Workflows

**Status:** Proposed
**Author:** Brain Agent Team
**Date:** 2026-03-20
**Effort:** Large (2-3 weeks)
**Priority:** High

---

## Problem

Complex tasks require multiple steps with dependencies, approvals, and conditional logic.
Currently, users must either manually orchestrate multi-step tasks by sending sequential
messages, or rely on the LLM to figure out sequencing on its own. This leads to several
issues:

1. **Non-deterministic execution** -- the agent may skip steps, reorder them, or forget
   intermediate results depending on context window pressure and model behavior.
2. **No approval gates** -- there is no way to pause execution mid-task and require human
   review before proceeding (e.g., before deploying code or sending an email).
3. **No reusability** -- a carefully orchestrated multi-step process cannot be saved and
   re-run. Users type the same complex instructions repeatedly.
4. **No visibility** -- during long-running delegated tasks, users cannot see which stage
   the agent is in or how far along the process is.
5. **No conditional branching** -- if step 2 fails, there is no built-in way to route to
   an error-handling step instead of blindly continuing.

## Proposed Solution

Introduce **workflow definition files** (`workflow.yaml` or `workflow.json`) that define
multi-step blueprints. Each workflow has named stages with:

- **Sequential and parallel execution** -- stages run in order by default, or in parallel
  when declared as a group.
- **Approval gates** -- stages that pause and wait for human approval before continuing.
- **Conditional branching** -- route to different stages based on the output of a previous
  stage (success/failure, pattern matching on output).
- **Agent assignment** -- each stage can target a specific agent or use the default.
- **Variable passing** -- output from one stage feeds into the next as context.
- **Timeout and retry** -- per-stage timeout with optional retry count.

### Workflow Definition Format

```text
# agents/main/workflows/code-review.yaml

name: Code Review
description: Automated code review pipeline with human approval gate
trigger: manual                    # manual | schedule | webhook
default_agent: Coder

stages:
  analyze:
    prompt: |
      Analyze the code changes in {{repo_path}}.
      List all modified files and summarize what changed.
    agent: Researcher
    timeout: 120

  review:
    prompt: |
      Review the following changes for:
      - Security vulnerabilities
      - Performance issues
      - Code style violations
      Context from analysis: {{stages.analyze.output}}
    agent: Coder
    depends_on: [analyze]
    timeout: 300

  suggest:
    prompt: |
      Based on the review findings, generate specific code
      suggestions with diff format for each issue found.
      Review findings: {{stages.review.output}}
    depends_on: [review]
    timeout: 180

  approve:
    type: approval                 # pauses for human review
    message: |
      Code review complete. {{stages.suggest.output}}
      Review suggestions above and approve to apply changes.
    depends_on: [suggest]

  apply:
    prompt: |
      Apply the approved code suggestions to the repository.
      Suggestions: {{stages.suggest.output}}
    depends_on: [approve]
    on_failure: rollback
    timeout: 120

  rollback:
    prompt: |
      Revert any changes made during the apply stage.
      Restore original files.
    trigger: on_failure
```

### Deploy Workflow Example

```text
# agents/main/workflows/deploy.yaml

name: Deploy Pipeline
description: Lint, test, build, approve, deploy
trigger: manual
default_agent: Coder

variables:
  branch:
    description: Branch to deploy
    default: main
  environment:
    description: Target environment
    default: staging

stages:
  lint:
    prompt: |
      Run linting on branch {{branch}}.
      Execute: cd /project && npm run lint
      Report any issues found.
    timeout: 60

  test:
    prompt: |
      Run the test suite on branch {{branch}}.
      Execute: cd /project && npm test
      Report pass/fail counts and any failures.
    depends_on: [lint]
    timeout: 300
    on_failure: notify_failure

  build:
    prompt: |
      Build the project for {{environment}}.
      Execute: cd /project && npm run build -- --env={{environment}}
      Report build size and any warnings.
    depends_on: [test]
    timeout: 180

  approval:
    type: approval
    message: |
      Build complete for {{environment}}.
      - Lint: {{stages.lint.status}}
      - Tests: {{stages.test.status}}
      - Build: {{stages.build.status}}
      Approve to proceed with deployment?
    depends_on: [build]

  deploy:
    prompt: |
      Deploy build artifacts to {{environment}}.
      Execute the deployment script and verify health checks.
    depends_on: [approval]
    timeout: 600

  notify_failure:
    prompt: |
      Tests failed. Send a summary of failures to the team.
      Test output: {{stages.test.output}}
    trigger: on_failure
```

### Parallel Execution

```text
  # Stages in a parallel group run concurrently
  parallel_checks:
    type: parallel
    stages: [lint, typecheck, security_scan]

  lint:
    prompt: Run ESLint on the codebase.
    group: parallel_checks

  typecheck:
    prompt: Run TypeScript type checking.
    group: parallel_checks

  security_scan:
    prompt: Run npm audit and report vulnerabilities.
    group: parallel_checks

  merge_results:
    prompt: Combine results from all checks.
    depends_on: [parallel_checks]
```

## Web UI Mockups

### Workflow Editor (Visual Pipeline)

```text
+-----------------------------------------------------------------------+
|  Workflows                                              [+ New]       |
+-----------------------------------------------------------------------+
|                                                                       |
|  code-review.yaml                                                     |
|  ~~~~~~~~~~~~~~~~                                                     |
|                                                                       |
|  +-----------+     +-----------+     +-----------+                    |
|  | analyze   |---->| review    |---->| suggest   |                    |
|  | Researcher|     | Coder     |     | Coder     |                    |
|  | 120s      |     | 300s      |     | 180s      |                    |
|  +-----------+     +-----------+     +-----------+                    |
|                                             |                         |
|                                             v                         |
|                                      +-----------+                    |
|                                      | approve   |                    |
|                                      | GATE      |                    |
|                                      +-----------+                    |
|                                             |                         |
|                                             v                         |
|                                      +-----------+     +-----------+  |
|                                      | apply     |--X->| rollback  |  |
|                                      | Coder     |fail | Coder     |  |
|                                      +-----------+     +-----------+  |
|                                                                       |
|  [Edit YAML]  [Run Workflow]  [Delete]                                |
+-----------------------------------------------------------------------+
```

### Workflow Execution View (Running)

```text
+-----------------------------------------------------------------------+
|  Running: Deploy Pipeline                          [Cancel] [Pause]   |
|  Branch: main | Environment: staging                                  |
+-----------------------------------------------------------------------+
|                                                                       |
|  [==========] analyze    DONE     12s   Researcher                    |
|  [==========] review     DONE     48s   Coder                         |
|  [======>   ] suggest    RUNNING  23s   Coder                         |
|  [          ] approve    PENDING  --    GATE                          |
|  [          ] apply      PENDING  --    Coder                         |
|                                                                       |
|  Progress: 2/5 stages complete | Elapsed: 1m 23s                      |
|                                                                       |
|  --- Stage Output: suggest ---                                        |
|  Generating code suggestions based on review findings...              |
|  Found 3 issues to address:                                           |
|  1. SQL injection in user_search (security)                           |
|  2. N+1 query in dashboard load (performance)                         |
|  3. Missing error handling in upload handler                          |
|  ...                                                                  |
+-----------------------------------------------------------------------+
```

### Approval Gate Popup

```text
+-----------------------------------------------------------------------+
|                                                                       |
|  +---------------------------------------------------------------+   |
|  |                    Approval Required                           |   |
|  +---------------------------------------------------------------+   |
|  |                                                               |   |
|  |  Workflow: Deploy Pipeline                                    |   |
|  |  Stage: approval (4 of 5)                                    |   |
|  |                                                               |   |
|  |  Build complete for staging.                                  |   |
|  |  - Lint: PASSED (0 warnings)                                 |   |
|  |  - Tests: PASSED (142/142)                                   |   |
|  |  - Build: SUCCESS (2.3 MB, 0 warnings)                      |   |
|  |                                                               |   |
|  |  Approve to proceed with deployment?                          |   |
|  |                                                               |   |
|  |  +--------------------------------------------------+        |   |
|  |  | Optional note:                                    |        |   |
|  |  | Looks good, ship it.                              |        |   |
|  |  +--------------------------------------------------+        |   |
|  |                                                               |   |
|  |           [ Reject ]          [ Approve ]                     |   |
|  +---------------------------------------------------------------+   |
|                                                                       |
+-----------------------------------------------------------------------+
```

### Workflow List in Settings

```text
+-----------------------------------------------------------------------+
|  Settings > Workflows                                                 |
+-----------------------------------------------------------------------+
|                                                                       |
|  +-------------------+----------+---------+----------+-------------+  |
|  | Name              | Stages   | Trigger | Agent    | Last Run    |  |
|  +-------------------+----------+---------+----------+-------------+  |
|  | Code Review       | 6        | manual  | Coder    | 2h ago OK   |  |
|  | Deploy Pipeline   | 5        | manual  | Coder    | 1d ago OK   |  |
|  | Daily Digest      | 3        | daily   | Reporter | 6h ago OK   |  |
|  | PR Triage         | 4        | webhook | main     | 30m ago ERR |  |
|  +-------------------+----------+---------+----------+-------------+  |
|                                                                       |
|  [+ New Workflow]  [Import]  [Templates]                              |
+-----------------------------------------------------------------------+
```

## TUI Mockups

### Running a Workflow

```text
$ /workflow run code-review --repo-path=/Users/alex/project

  Workflow: Code Review
  =====================

  Stage 1/5: analyze (Researcher)
  > Analyzing code changes in /Users/alex/project...
  > Found 12 modified files across 4 directories.
  > Summary: Auth module refactor, new API endpoints, test updates.
  DONE (14s)

  Stage 2/5: review (Coder)
  > Reviewing changes for security, performance, style...
  > Found 2 security issues, 1 performance concern, 3 style violations.
  DONE (52s)

  Stage 3/5: suggest (Coder)
  > Generating fix suggestions...
  > 6 suggestions prepared with diffs.
  DONE (31s)

  Stage 4/5: approve (APPROVAL GATE)
  +---------------------------------------------------------+
  | Review 6 code suggestions above. Approve to apply?      |
  | [a]pprove  [r]eject  [v]iew details                     |
  +---------------------------------------------------------+
  > a

  Stage 5/5: apply (Coder)
  > Applying 6 code suggestions...
  > 5/6 applied successfully. 1 skipped (conflict).
  DONE (18s)

  Workflow complete. 5/5 stages passed. Total: 2m 35s
```

### Listing Workflows

```text
$ /workflow list

  Workflows (agents/main/workflows/)
  ===================================
  code-review.yaml    6 stages   manual   Last: 2h ago (OK)
  deploy.yaml         5 stages   manual   Last: 1d ago (OK)
  daily-digest.yaml   3 stages   daily    Last: 6h ago (OK)

$ /workflow status

  Running Workflows
  =================
  deploy.yaml   Stage 3/5 (build)   Running   1m 12s   main/Coder
```

## Implementation Plan

### Phase 1: Core Engine (Week 1)

1. **Workflow parser** -- load and validate `workflow.yaml` files from
   `agents/<name>/workflows/` directory.
2. **Workflow executor** -- new module in `claude_cli.py` that runs stages sequentially,
   handles `depends_on` ordering, and passes variables between stages.
3. **Stage runner** -- wraps existing `_run_delegate` or direct chat to execute each stage
   prompt with the assigned agent.
4. **Variable interpolation** -- resolve `{{stages.X.output}}` and `{{variables.Y}}`
   placeholders in prompts.
5. **State persistence** -- store workflow execution state in SQLite (`workflow_runs` table)
   so runs survive server restarts.

### Phase 2: Gates and Branching (Week 1-2)

1. **Approval gates** -- stage type that pauses execution and emits an SSE event to
   connected clients requesting approval.
2. **Conditional routing** -- `on_failure` and `on_success` fields that redirect to
   different stages based on outcome.
3. **Parallel execution** -- run grouped stages in threads, collect all outputs, continue
   when all complete.
4. **Timeout and retry** -- per-stage watchdog thread (reuses existing scheduler pattern).

### Phase 3: API Endpoints (Week 2)

1. `GET /v1/workflows` -- list workflows for an agent
2. `POST /v1/workflows/run` -- trigger a workflow with variables
3. `GET /v1/workflows/runs` -- list active and past runs
4. `GET /v1/workflows/runs/<id>` -- run status with per-stage details
5. `POST /v1/workflows/runs/<id>/approve` -- approve or reject a gate
6. `POST /v1/workflows/runs/<id>/cancel` -- cancel a running workflow
7. `POST /v1/workflows` -- save/update a workflow definition

### Phase 4: Web UI (Week 2-3)

1. **Workflow list** in agent settings with create/edit/delete.
2. **Visual pipeline editor** -- drag-and-drop stage ordering (stretch goal; start with
   YAML editor).
3. **Execution view** -- real-time progress via SSE with per-stage status indicators.
4. **Approval popup** -- modal triggered by SSE event when a gate is reached.
5. **Run history** -- table of past executions with status, duration, output.

### Phase 5: TUI (Week 3)

1. `/workflow list` -- list available workflows.
2. `/workflow run <name>` -- execute a workflow, show stage progression.
3. `/workflow status` -- show running workflows.
4. `/workflow cancel <run-id>` -- cancel a running workflow.
5. Interactive approval prompts in the terminal.

## How Workflows Differ from Just Asking the Agent

| Aspect | Ad-hoc (current) | Workflows (proposed) |
|---|---|---|
| Execution order | LLM decides, may vary | Deterministic, defined in YAML |
| Human approval | Not possible mid-task | Built-in approval gates |
| Reusability | Must retype instructions | Save once, run many times |
| Visibility | Opaque until completion | Real-time stage progress |
| Error handling | LLM may ignore errors | Explicit on_failure routing |
| Parallel execution | Sequential by default | Declared parallel groups |
| Reproducibility | Non-deterministic | Same stages every time |
| Auditability | Chat history only | Structured run logs per stage |

## Template Library

Ship a set of built-in workflow templates that users can install or customize:

- **Code Review** -- analyze, review, suggest, approve, apply
- **Deploy Pipeline** -- lint, test, build, approve, deploy
- **Daily Digest** -- fetch emails, check calendar, summarize, deliver
- **Research Report** -- search, gather sources, analyze, draft, review, publish
- **PR Triage** -- fetch PRs, categorize, assign priority, notify team
- **Incident Response** -- detect, diagnose, notify, remediate, postmortem

Templates stored in a `workflow-templates/` directory or fetchable from ClawHub.

## Benefits

1. **Reliability** -- deterministic multi-step execution that does not drift with model
   behavior or context window pressure.
2. **Safety** -- approval gates prevent agents from taking irreversible actions without
   human oversight.
3. **Efficiency** -- parallel stage groups reduce total execution time for independent
   tasks.
4. **Reusability** -- define once, run many times with different variables.
5. **Observability** -- real-time progress tracking and structured logs for every run.
6. **Composability** -- workflows can delegate individual stages to specialized agents,
   leveraging the existing team structure.

## Effort Estimate

| Component | Estimate |
|---|---|
| Workflow parser + executor | 3 days |
| Variable interpolation + state | 2 days |
| Approval gates + branching | 2 days |
| API endpoints | 1 day |
| Web UI (list, execution, approval) | 3 days |
| TUI commands | 1 day |
| Template library | 1 day |
| Testing + polish | 2 days |
| **Total** | **~15 days** |

## Open Questions

1. Should workflows be agent-scoped or global? Proposal: stored per-agent in
   `agents/<name>/workflows/`, but can be shared via templates.
2. Should workflow runs appear in regular chat history or in a separate view?
3. How to handle workflows that span multiple hours (e.g., waiting for approval overnight)?
   Proposal: persist state in SQLite, resume on server restart.
4. Should there be a visual YAML editor in the Web UI, or rely on a proper drag-and-drop
   pipeline builder? Proposal: start with YAML editor, add visual builder later.
5. Integration with the scheduler -- should workflows be triggerable on a schedule?
   Proposal: yes, via `trigger: schedule` with cron-like syntax.
