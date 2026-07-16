# Persistent-kernel tools (Quant-Workbench Phase A): kernel_exec /
# kernel_status / kernel_restart — the stateful complement to the one-shot
# python_exec / r_exec. The kernel lifecycle lives in engine/kernels.py
# (KernelManager singleton); this module is the tool-facing wrapper that adds
# the file_tools conventions: artifact registration via (mtime,size) folder
# diff with Phase-B provenance (produced_by="kernel#N" + env snapshot), the
# GDPR stdout pass, per-tool cancel registration (the SessionKernel itself is
# the registered handle — kill_tool_process dispatches on cancel_escalate),
# and the kernel_status SSE emit for the status-bar badge.
#
# Interactive-only BY CONSTRUCTION: scheduled runs (sched-*) and background
# fan-out sub-agents share/synthesize session ids, so a session-keyed kernel
# there would be a leak surface without benefit — those callers get a clear
# error steering to python_exec/r_exec. (The tool_settings purpose seed keeps
# the tools out of restricted purposes too; this guard is the fail-loud
# backstop for purpose="interactive" background paths.)

from __future__ import annotations

import os

from engine.context import get_request_context
from engine.tool_exec import (
    _ok,
    _err,
    register_tool_process,
    unregister_tool_process,
)
from engine.tools.file_tools import (
    _artifact_watch_dir,
    _changed_files,
    _code_mode_write_base,
    _env_snapshot_py,
    _env_snapshot_r,
    _resolve_artifact_dir,
    _snapshot_dir,
)


def _session_guard():
    """Return (session_id, err_str|None): kernels are interactive-chat-only."""
    ctx = get_request_context()
    session_id = ctx.current_session_id or ""
    if not session_id:
        return "", _err("kernel_exec: requires a chat session — use python_exec/r_exec")
    if session_id.startswith("sched-") or ctx.current_bg_task:
        return "", _err(
            "kernel tools are only available in interactive chat sessions — "
            "use python_exec/r_exec for one-shot scripts here")
    return session_id, None


def _kernel_cwd() -> str | None:
    """The kernel's working directory = the session write root (same choke
    point as python_exec: a bare open('x.png','w') lands in the artifact
    folder by construction). In code mode this is the chat's output folder —
    NOT the project root, and without python_exec's per-run source symlinks
    (a persistent kernel can't scope symlinks to one run)."""
    _wr, _ = _resolve_artifact_dir()
    if not _wr:
        return None
    _wd_cm = get_request_context().working_dir
    return _code_mode_write_base(_wd_cm) if _wd_cm else _wr


def _emit_kernel_status(session_id: str) -> None:
    """Push the badge payload to the client (LiveStream via event_callback;
    handlers/chat.py passes 'kernel_status' through its tool-event filter)."""
    try:
        from engine.kernels import kernel_manager
        ecb = get_request_context().event_callback
        if ecb:
            ecb("kernel_status", kernel_manager.status(session_id))
    except Exception:
        pass


def _kernel_cfg(_brain) -> dict:
    return _brain.get_tool_config().get("kernel_exec", {})


def tool_kernel_exec(args: dict) -> str:
    """Execute code on the session's persistent kernel (state survives turns)."""
    import brain as _brain
    code = args.get("code", "")
    if not code.strip():
        return _err("kernel_exec: no code provided")
    lang = (args.get("lang") or "python").lower()
    if lang not in ("python", "r"):
        return _err("kernel_exec: lang must be 'python' or 'r'")
    session_id, guard = _session_guard()
    if guard:
        return guard

    cfg = _kernel_cfg(_brain)
    timeout = args.get("timeout", cfg.get("timeout", 120))
    max_output = cfg.get("max_output_chars", 50000)
    max_kernels = cfg.get("max_kernels", 3)
    venv_path = _brain.get_tool_config().get("python_exec", {}).get("venv_path", "")

    work_dir = _kernel_cwd()
    if not work_dir:
        return _err("kernel_exec: no session artifact folder available")
    os.makedirs(work_dir, exist_ok=True)
    watch_dir = _artifact_watch_dir(work_dir) or work_dir

    from engine.kernels import kernel_manager
    try:
        try:
            from engine.context import report_tool_progress
            report_tool_progress(phase="Läuft", note=f"Kernel ({lang})")
        except Exception:
            pass
        k = kernel_manager.get_or_start(session_id, lang, work_dir,
                                        venv_path, max_kernels)
    except RuntimeError as e:
        return _err(f"kernel_exec: {e}")

    pre_files = _snapshot_dir(watch_dir)
    # The SessionKernel IS the cancel handle: kill_tool_process sees
    # cancel_escalate and interrupts first, kills second. The chat Stopp
    # additionally reaches the exec wait loop via the same cancel seam the
    # blocking ask_user polls (interrupt → kernel + state survive).
    from engine.tools.ask_tools import _ask_turn_cancelled
    _proc_key = register_tool_process(k)
    try:
        res = kernel_manager.execute(session_id, code, timeout=timeout,
                                     is_cancelled=_ask_turn_cancelled)
    except RuntimeError as e:
        return _err(f"kernel_exec: {e}")
    finally:
        unregister_tool_process(_proc_key)

    ctx = get_request_context()
    agent = ctx.current_agent or getattr(_brain, "_current_agent", None)
    agent_id = agent.agent_id if agent else "main"
    produced_by = f"kernel#{res['exec_count']}"
    env_snap = (_env_snapshot_py(venv_path) if k.lang == "python"
                else _env_snapshot_r())

    # display_data PNGs (plt.show() / R plot()) → files BEFORE the diff, so
    # they register together with any files the code wrote itself.
    for img in res["images"]:
        counter = 1
        while os.path.exists(os.path.join(work_dir, f"kernel_plot_{counter}.png")):
            counter += 1
        try:
            with open(os.path.join(work_dir, f"kernel_plot_{counter}.png"), "wb") as f:
                f.write(img)
        except OSError:
            pass

    created = []
    changed = _changed_files(watch_dir, pre_files, exclude=set())
    if changed and agent:
        for fname, was_new in changed:
            fpath = os.path.join(watch_dir, fname)
            if os.path.isfile(fpath):
                _brain._after_file_write(
                    fpath, "created" if was_new else "modified", agent_id,
                    produced_by=produced_by, env_snapshot=env_snap)
                created.append(fname)

    output = res["text"]
    if res["error"]:
        output += ("\n--- error ---\n" if output else "") + res["error"]
    if len(output) > max_output:
        output = output[:max_output] + "\n... (truncated)"
    # Same transparent-anonymisation seam as python_exec stdout.
    output = _brain._gdpr_anon_tool_text(output, "kernel_exec:stdout")

    _emit_kernel_status(session_id)

    if res["killed"]:
        return _err(f"kernel_exec: {res['error']} — kernel state is LOST; "
                    f"the next kernel_exec starts a fresh kernel.\n{output}")

    result = {
        "exec": res["exec_count"],
        "lang": k.lang,
        "duration_s": res["duration_s"],
        "output": output,
    }
    if not res["ok"]:
        result["status"] = "error"
    if res["interrupted"]:
        result["interrupted"] = True
    if created:
        result["artifacts"] = created
    return _ok(result)


def tool_kernel_status(args: dict) -> str:
    """Report the session kernel: language, uptime, RSS, exec count, names."""
    session_id, guard = _session_guard()
    if guard:
        return guard
    from engine.kernels import kernel_manager
    st = kernel_manager.status(session_id, with_names=True)
    if not st.get("alive"):
        return _ok({"alive": False,
                    "note": "no kernel running for this session — the first "
                            "kernel_exec starts one"})
    return _ok(st)


def tool_kernel_restart(args: dict) -> str:
    """Restart (or start) the session kernel — all in-memory state is lost."""
    import brain as _brain
    session_id, guard = _session_guard()
    if guard:
        return guard
    lang = (args.get("lang") or "").lower()
    from engine.kernels import kernel_manager
    prev = kernel_manager.get(session_id)
    if not lang:
        lang = prev.lang if prev else "python"
    if lang not in ("python", "r"):
        return _err("kernel_restart: lang must be 'python' or 'r'")
    cfg = _kernel_cfg(_brain)
    venv_path = _brain.get_tool_config().get("python_exec", {}).get("venv_path", "")
    work_dir = _kernel_cwd()
    if not work_dir:
        return _err("kernel_restart: no session artifact folder available")
    try:
        kernel_manager.restart(session_id, lang, work_dir, venv_path,
                               cfg.get("max_kernels", 3))
    except RuntimeError as e:
        return _err(f"kernel_restart: {e}")
    _emit_kernel_status(session_id)
    return _ok({"restarted": True, "lang": lang,
                "note": "fresh kernel — previous state is gone"})
