# Extracted from claude_cli.py — TaskRunner, WorkflowEngine, delegation tools
#
# Cross-module deps (all resolved from claude_cli.py at runtime):
#   AGENTS_DIR, _thread_local, _current_agent, _delegate_fallback_model
#   AgentConfig, MemoryStore, CancelToken
#   _run_delegate, get_inference_params
#   _get_agent_team_info, _get_delegation_scope, build_agent_registry, list_agents
#   _err, _ok

import datetime
import os
import threading
import uuid as _uuid

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # Graceful fallback — suggest pip3 install pyyaml


# --- Background Task Runner ---


class TaskRunner:
    """Manages background agent tasks with status tracking and cancellation."""

    def __init__(self):
        self._tasks: dict[str, dict] = {}  # task_id -> task_info
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def submit(self, agent_id: str, task: str, model: str | None = None) -> str:
        """Submit a task to run in a background thread. Returns task_id."""
        task_id = _uuid.uuid4().hex[:8]
        cancel_flag = threading.Event()
        # Capture caller's user_id and team ids for MemPalace wing scoping in the child thread
        caller_user_id = getattr(_thread_local, "current_user_id", "") or ""
        caller_team_ids = list(getattr(_thread_local, "current_team_ids", []) or [])

        with self._lock:
            self._tasks[task_id] = {
                "id": task_id,
                "agent": agent_id,
                "task": task,
                "model": model,
                "status": "running",
                "result": None,
                "error": None,
                "submitted_at": datetime.datetime.now().isoformat(),
                "finished_at": None,
            }
            self._cancel_flags[task_id] = cancel_flag

        thread = threading.Thread(
            target=self._run_task,
            args=(task_id, agent_id, task, model, cancel_flag, caller_user_id, caller_team_ids),
            daemon=True)
        self._threads[task_id] = thread
        thread.start()
        return task_id

    def get_status(self, task_id: str) -> dict | None:
        with self._lock:
            return self._tasks.get(task_id, {}).copy() if task_id in self._tasks else None

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [t.copy() for t in self._tasks.values()]

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self._tasks:
                return False
            if self._tasks[task_id]["status"] != "running":
                return False
            self._cancel_flags[task_id].set()
            self._tasks[task_id]["status"] = "cancelled"
            self._tasks[task_id]["finished_at"] = datetime.datetime.now().isoformat()
            return True

    def get_result(self, task_id: str) -> dict | None:
        """Get result, blocking until complete if still running. Timeout 0.1s poll."""
        if task_id not in self._threads:
            return self.get_status(task_id)
        # Wait for thread to finish (with timeout so we don't block forever)
        self._threads[task_id].join(timeout=300)
        return self.get_status(task_id)

    def _run_task(self, task_id: str, agent_id: str, task: str,
                  model: str | None, cancel_flag: threading.Event,
                  caller_user_id: str = "",
                  caller_team_ids: list | None = None):
        caller_team_ids = caller_team_ids or []
        """Execute a task in a background thread."""
        target = AgentConfig(agent_id)
        target_memory = MemoryStore(agent_id, base_dir=target.memory_dir)

        if not model:
            model = target.preferred_model or _delegate_fallback_model or "claude-opus-4-5-20251101"

        import platform
        cwd = os.getcwd()
        os_name = platform.system()
        soul = target.soul
        tools_guide = target.tools_guide

        from datetime import datetime as _dt
        system_prompt = (
            f"{soul}\n\n"
            f"You are agent '{agent_id}' running a background task.\n"
            f"Current date and time: {_dt.now().strftime('%Y-%m-%d %H:%M %Z').strip()}\n"
            f"Current working directory: {cwd}\n"
            f"Operating system: {os_name}\n\n"
            "Complete the task and provide a concise result summary.\n"
        )
        # Inject team context
        team_info = _get_agent_team_info(agent_id)
        if team_info:
            if team_info["is_head"]:
                peers = [m for m in team_info["members"] if m != agent_id]
                system_prompt += (
                    f"\nTEAM: You are the head of team '{team_info['name']}'. "
                    f"Your team members: {', '.join(peers)}\n"
                    "Delegate sub-tasks to your team members when appropriate.\n"
                    "Use memory_shared(scope='team') for team-level shared knowledge.\n"
                )
            else:
                peers = [m for m in team_info["members"] if m != agent_id and m != team_info["head"]]
                system_prompt += (
                    f"\nTEAM: You are a member of team '{team_info['name']}'.\n"
                    f"Team head: {team_info['head']}\n"
                )
                if peers:
                    system_prompt += f"Team peers: {', '.join(peers)}\n"
                system_prompt += "Use memory_shared(scope='team') for team-level shared knowledge.\n"

        if tools_guide:
            system_prompt += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"

        # Build team-aware agent registry for this delegate
        agent_registry = build_agent_registry(for_agent_id=agent_id)
        if agent_registry:
            system_prompt += f"\n\n{agent_registry}\n"

        messages = [{"role": "user", "content": task}]

        result_text = ""
        status = "completed"
        try:
            # Store delegate agent context in thread-local (thread-safe, no global mutation)
            _thread_local.delegate_agent_id = agent_id
            _thread_local.current_agent = target
            _thread_local.memory_store = target_memory
            _thread_local.current_user_id = caller_user_id
            _thread_local.current_team_ids = caller_team_ids
            if cancel_flag.is_set():
                status = "cancelled"
            else:
                delegate_inf = get_inference_params(model, target.config.get("model_purpose"))
                result_text = _run_delegate(messages, model, system_prompt,
                                            memory_store=target_memory,
                                            inference_params=delegate_inf) or ""
                if cancel_flag.is_set():
                    status = "cancelled"
        except Exception as e:
            result_text = str(e)
            status = "error"
        finally:
            # Clean up thread-local state
            _thread_local.delegate_agent_id = None
            _thread_local.current_agent = None
            _thread_local.memory_store = None
            _thread_local.current_user_id = ""
            _thread_local.current_team_ids = []

        with self._lock:
            self._tasks[task_id]["status"] = status
            self._tasks[task_id]["result"] = result_text
            self._tasks[task_id]["finished_at"] = datetime.datetime.now().isoformat()
            if status == "error":
                self._tasks[task_id]["error"] = result_text
            # Clean up thread reference
            self._threads.pop(task_id, None)
            self._cancel_flags.pop(task_id, None)


# Global task runner
_task_runner: TaskRunner | None = None


# --- Workflow Engine ---


class WorkflowEngine:
    """Manages workflow definitions stored as YAML files per agent."""

    @staticmethod
    def _workflows_dir(agent_id: str) -> str:
        return os.path.join(AGENTS_DIR, agent_id, "workflows")

    @staticmethod
    def list_workflows(agent_id: str) -> list[dict]:
        """Scan agents/<name>/workflows/*.yaml and return summaries."""
        wdir = WorkflowEngine._workflows_dir(agent_id)
        if not os.path.isdir(wdir):
            return []
        results = []
        for fname in sorted(os.listdir(wdir)):
            if not fname.endswith((".yaml", ".yml")):
                continue
            wf = WorkflowEngine.get_workflow(agent_id, fname.rsplit(".", 1)[0])
            if wf:
                results.append({
                    "name": wf.get("name", fname),
                    "file": fname,
                    "description": wf.get("description", ""),
                    "stages": len(wf.get("stages", [])),
                    "variables": [v.get("name", "") for v in wf.get("variables", [])],
                })
        return results

    @staticmethod
    def get_workflow(agent_id: str, name: str) -> dict | None:
        """Parse a workflow YAML file. Returns dict or None."""
        if not _yaml:
            return None
        wdir = WorkflowEngine._workflows_dir(agent_id)
        for ext in (".yaml", ".yml"):
            fpath = os.path.join(wdir, name + ext)
            if os.path.exists(fpath):
                try:
                    with open(fpath, "r") as f:
                        return _yaml.safe_load(f)
                except Exception:
                    return None
        return None

    @staticmethod
    def save_workflow(agent_id: str, name: str, definition: dict | str) -> str:
        """Write a workflow YAML file. Returns the file path."""
        if not _yaml:
            raise RuntimeError("PyYAML is not installed. Run: pip3 install pyyaml")
        wdir = WorkflowEngine._workflows_dir(agent_id)
        os.makedirs(wdir, exist_ok=True)
        fpath = os.path.join(wdir, name + ".yaml")
        with open(fpath, "w") as f:
            if isinstance(definition, str):
                f.write(definition)
            else:
                _yaml.dump(definition, f, default_flow_style=False, sort_keys=False)
        return fpath

    @staticmethod
    def delete_workflow(agent_id: str, name: str) -> bool:
        """Remove a workflow file. Returns True if deleted."""
        wdir = WorkflowEngine._workflows_dir(agent_id)
        for ext in (".yaml", ".yml"):
            fpath = os.path.join(wdir, name + ext)
            if os.path.exists(fpath):
                os.remove(fpath)
                return True
        return False


class WorkflowExecution:
    """Runs a workflow: sequential stage execution with approval gates."""

    def __init__(self, workflow: dict, variables: dict, agent_id: str,
                 model: str | None = None, execution_id: str | None = None):
        self.workflow = workflow
        self.variables = variables or {}
        self.agent_id = agent_id
        self.model = model
        self.execution_id = execution_id or _uuid.uuid4().hex[:10]
        self.status = "pending"  # pending / running / waiting_approval / completed / failed / cancelled
        self.current_stage_idx = -1
        self.current_stage_name = ""
        self.stage_results: dict[str, dict] = {}  # stage_name -> {status, output, elapsed}
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None
        self._cancel = threading.Event()
        self._approval_event = threading.Event()
        self._approval_result: str | None = None  # "approved" or "rejected"
        self._thread: threading.Thread | None = None

    @property
    def stages(self) -> list[dict]:
        return self.workflow.get("stages", [])

    def _substitute(self, text: str) -> str:
        """Replace {{variable}} and {{stages.X.output}} placeholders."""
        if not text:
            return text
        import re
        # Replace user variables: {{var_name}}
        for k, v in self.variables.items():
            text = text.replace("{{" + k + "}}", str(v))
        # Replace stage references: {{stages.X.output}}, {{stages.X.status}}
        def _stage_ref(m):
            stage_name = m.group(1)
            field = m.group(2)
            sr = self.stage_results.get(stage_name, {})
            return str(sr.get(field, f"[{stage_name}.{field} not available]"))
        text = re.sub(r"\{\{stages\.(\w+)\.(\w+)\}\}", _stage_ref, text)
        return text

    def _build_context(self) -> str:
        """Build accumulated context string from all completed stages."""
        parts = []
        for stage in self.stages:
            sname = stage.get("name", "")
            sr = self.stage_results.get(sname)
            if sr and sr.get("status") == "completed" and sr.get("output"):
                parts.append(f"=== Stage '{sname}' result ===\n{sr['output']}")
        return "\n\n".join(parts)

    def run(self):
        """Start the workflow in a background thread."""
        self.status = "running"
        self.started_at = datetime.datetime.now().isoformat()
        self._thread = threading.Thread(
            target=self._execute, daemon=True,
            name=f"workflow-{self.execution_id}")
        self._thread.start()

    def _execute(self):
        """Sequential stage execution."""
        try:
            for idx, stage in enumerate(self.stages):
                if self._cancel.is_set():
                    self.status = "cancelled"
                    self.finished_at = datetime.datetime.now().isoformat()
                    return

                sname = stage.get("name", f"stage_{idx}")
                stype = stage.get("type", "prompt")
                self.current_stage_idx = idx
                self.current_stage_name = sname

                if stype == "approval":
                    self._run_approval_stage(sname, stage)
                    if self._cancel.is_set() or self._approval_result == "rejected":
                        if self._approval_result == "rejected":
                            self.stage_results[sname] = {
                                "status": "rejected", "output": "Approval rejected by user.",
                                "elapsed": 0,
                            }
                            self.status = "failed"
                            self.error = f"Approval rejected at stage '{sname}'"
                        else:
                            self.status = "cancelled"
                        self.finished_at = datetime.datetime.now().isoformat()
                        return
                else:
                    self._run_prompt_stage(sname, stage)

                # Check if stage failed
                sr = self.stage_results.get(sname, {})
                if sr.get("status") == "error":
                    self.status = "failed"
                    self.error = f"Stage '{sname}' failed: {sr.get('output', 'unknown error')}"
                    self.finished_at = datetime.datetime.now().isoformat()
                    return

            self.status = "completed"
            self.finished_at = datetime.datetime.now().isoformat()

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            self.finished_at = datetime.datetime.now().isoformat()

    def _run_prompt_stage(self, sname: str, stage: dict):
        """Execute a prompt stage using _run_delegate."""
        start = datetime.datetime.now()
        prompt_template = stage.get("prompt", "")
        prompt = self._substitute(prompt_template)

        # Build context from previous stages
        context = self._build_context()
        full_prompt = prompt
        if context:
            full_prompt = f"Previous workflow results:\n{context}\n\n---\n\nCurrent task:\n{prompt}"

        # Resolve agent and model
        target_agent_id = stage.get("agent", self.agent_id)
        target = AgentConfig(target_agent_id)
        target_memory = MemoryStore(target_agent_id, base_dir=target.memory_dir)

        stage_model = self.model or target.preferred_model or _delegate_fallback_model or "claude-sonnet-4-6"

        import platform
        cwd = os.getcwd()
        os_name = platform.system()
        soul = target.soul
        tools_guide = target.tools_guide

        system_prompt = (
            f"{soul}\n\n"
            f"You are agent '{target_agent_id}' executing workflow stage '{sname}'.\n"
            f"Current date and time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"Current working directory: {cwd}\n"
            f"Operating system: {os_name}\n\n"
            "Complete the task and provide a concise result summary.\n"
        )
        if tools_guide:
            system_prompt += f"\n--- TOOL USAGE GUIDE ---\n{tools_guide}"

        # Tool restriction (set via thread-local if stage specifies allowed tools)
        restricted_tools = stage.get("tools")

        self.stage_results[sname] = {"status": "running", "output": "", "elapsed": 0}

        messages = [{"role": "user", "content": full_prompt}]

        try:
            _thread_local.delegate_agent_id = target_agent_id
            _thread_local.current_agent = target
            _thread_local.memory_store = target_memory
            if restricted_tools:
                _thread_local.workflow_allowed_tools = set(restricted_tools)

            cancel_token = CancelToken()
            # Link our cancel event to the cancel token
            def _watch_cancel():
                self._cancel.wait()
                cancel_token.cancel()
            watcher = threading.Thread(target=_watch_cancel, daemon=True)
            watcher.start()

            delegate_inf = get_inference_params(stage_model, target.config.get("model_purpose"))
            result_text = _run_delegate(
                messages, stage_model, system_prompt,
                memory_store=target_memory,
                cancel_token=cancel_token,
                inference_params=delegate_inf,
            ) or ""

            elapsed = (datetime.datetime.now() - start).total_seconds()
            if self._cancel.is_set():
                self.stage_results[sname] = {"status": "cancelled", "output": result_text, "elapsed": elapsed}
            else:
                self.stage_results[sname] = {"status": "completed", "output": result_text, "elapsed": elapsed}

        except Exception as e:
            elapsed = (datetime.datetime.now() - start).total_seconds()
            self.stage_results[sname] = {"status": "error", "output": str(e), "elapsed": elapsed}
        finally:
            _thread_local.delegate_agent_id = None
            _thread_local.current_agent = None
            _thread_local.memory_store = None
            _thread_local.workflow_allowed_tools = None

    def _run_approval_stage(self, sname: str, stage: dict):
        """Pause for human approval."""
        message = self._substitute(stage.get("message", "Approval required."))
        self.stage_results[sname] = {
            "status": "waiting_approval", "output": message, "elapsed": 0,
        }
        self.status = "waiting_approval"
        self._approval_event.clear()
        self._approval_result = None

        # Wait until approved, rejected, or cancelled
        while not self._approval_event.is_set() and not self._cancel.is_set():
            self._approval_event.wait(timeout=1.0)

        if self._cancel.is_set():
            self.stage_results[sname] = {"status": "cancelled", "output": message, "elapsed": 0}
            return

        if self._approval_result == "approved":
            self.stage_results[sname] = {"status": "completed", "output": "Approved.", "elapsed": 0}
            self.status = "running"
        # "rejected" handled by caller

    def approve(self):
        """Approve the current approval gate."""
        self._approval_result = "approved"
        self._approval_event.set()

    def reject(self):
        """Reject the current approval gate."""
        self._approval_result = "rejected"
        self._approval_event.set()

    def cancel(self):
        """Cancel the workflow execution."""
        self._cancel.set()
        self._approval_event.set()  # Unblock approval wait

    def to_dict(self) -> dict:
        """Serialize execution state."""
        stages_info = []
        for idx, stage in enumerate(self.stages):
            sname = stage.get("name", f"stage_{idx}")
            sr = self.stage_results.get(sname, {})
            stages_info.append({
                "name": sname,
                "type": stage.get("type", "prompt"),
                "status": sr.get("status", "pending"),
                "output": sr.get("output", ""),
                "elapsed": sr.get("elapsed", 0),
            })
        return {
            "execution_id": self.execution_id,
            "workflow_name": self.workflow.get("name", ""),
            "agent": self.agent_id,
            "model": self.model,
            "status": self.status,
            "current_stage": self.current_stage_name,
            "current_stage_idx": self.current_stage_idx,
            "total_stages": len(self.stages),
            "stages": stages_info,
            "variables": self.variables,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


# Global workflow execution registry
_workflow_executions: dict[str, WorkflowExecution] = {}
_workflow_lock = threading.Lock()


def workflow_start(agent_id: str, workflow_name: str, variables: dict,
                   model: str | None = None) -> WorkflowExecution:
    """Start a workflow execution. Returns the execution object."""
    if not _yaml:
        raise RuntimeError("PyYAML is not installed. Run: pip3 install pyyaml")
    wf = WorkflowEngine.get_workflow(agent_id, workflow_name)
    if not wf:
        raise ValueError(f"Workflow '{workflow_name}' not found for agent '{agent_id}'")
    execution = WorkflowExecution(wf, variables, agent_id, model)
    with _workflow_lock:
        _workflow_executions[execution.execution_id] = execution
    execution.run()
    return execution


def workflow_get_execution(execution_id: str) -> WorkflowExecution | None:
    with _workflow_lock:
        return _workflow_executions.get(execution_id)


def workflow_list_executions() -> list[dict]:
    with _workflow_lock:
        return [ex.to_dict() for ex in _workflow_executions.values()]


def workflow_cleanup_old(max_age_hours: int = 24):
    """Remove completed/failed executions older than max_age_hours."""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=max_age_hours)
    with _workflow_lock:
        to_remove = []
        for eid, ex in _workflow_executions.items():
            if ex.status in ("completed", "failed", "cancelled") and ex.finished_at:
                try:
                    finished = datetime.datetime.fromisoformat(ex.finished_at)
                    if finished < cutoff:
                        to_remove.append(eid)
                except (ValueError, TypeError):
                    pass
        for eid in to_remove:
            del _workflow_executions[eid]


def tool_delegate_task(args: dict) -> str:
    """Delegate a task to another agent — runs in a background thread."""
    agent_id = args.get("agent", "")
    task = args.get("task", "")
    wait = args.get("wait", True)
    if not agent_id or not task:
        return _err("delegate_task: agent and task are required")

    available = list_agents()
    if agent_id not in available:
        return _err(f"delegate_task: agent '{agent_id}' not found. Available: {', '.join(available)}")

    # Team-aware delegation scoping (prefer thread-local for concurrent requests)
    caller_id = getattr(_thread_local, "delegate_agent_id", None)
    if not caller_id:
        agent = getattr(_thread_local, 'current_agent', None) or _current_agent
        caller_id = agent.agent_id if agent else None
    if caller_id:
        scope = _get_delegation_scope(caller_id)
        if agent_id not in scope:
            return _err(f"delegate_task: '{caller_id}' cannot delegate to '{agent_id}'. Allowed: {', '.join(scope)}")

    if not _task_runner:
        return _err("Task runner not initialized")

    task_id = _task_runner.submit(agent_id, task, args.get("model"))

    if wait:
        # Synchronous: wait for result
        result = _task_runner.get_result(task_id)
        if result and result.get("status") == "completed":
            return _ok({
                "task_id": task_id,
                "agent": agent_id,
                "task": task,
                "response": result.get("result", ""),
            })
        elif result:
            return _err(f"delegate_task: {result.get('status')} — {result.get('error', '')}")
        return _err("delegate_task: no result")
    else:
        # Async: return task_id immediately
        return _ok({
            "task_id": task_id,
            "agent": agent_id,
            "task": task,
            "status": "running",
            "message": f"Task submitted. Use task_status(task_id='{task_id}') to check progress.",
        })


def tool_task_status(args: dict) -> str:
    """Check status of a background task."""
    if not _task_runner:
        return _err("Task runner not initialized")
    task_id = args.get("task_id", "")
    if task_id:
        status = _task_runner.get_status(task_id)
        if not status:
            return _err(f"Task '{task_id}' not found")
        # Truncate long results
        if status.get("result") and len(status["result"]) > 2000:
            status["result"] = status["result"][:2000] + "..."
        return _ok(status)
    else:
        # List all tasks
        tasks = _task_runner.list_tasks()
        for t in tasks:
            if t.get("result") and len(t["result"]) > 200:
                t["result"] = t["result"][:200] + "..."
        return _ok({"tasks": tasks, "count": len(tasks)})


def tool_task_cancel(args: dict) -> str:
    """Cancel a running background task."""
    if not _task_runner:
        return _err("Task runner not initialized")
    task_id = args.get("task_id", "")
    if not task_id:
        return _err("task_cancel: task_id is required")
    if _task_runner.cancel(task_id):
        return _ok({"task_id": task_id, "status": "cancelled"})
    return _err(f"Cannot cancel task '{task_id}' — not found or not running")
