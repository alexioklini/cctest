"""Persistent Jupyter kernels (Python + R) — Quant-Workbench Phase A.

One kernel per chat session (key = session_id), max N concurrent, idle-reaped.
Kernels are OS subprocesses launched via jupyter_client with generated
kernelspecs: the Python kernel runs the SERVER interpreter (sys.executable)
with PYTHONPATH pointed at the quant venv — byte-parity with python_exec's
interpreter semantics — and the R kernel runs IRkernel from the system R
library. Kernels die WITH Brain (no restart recovery, like the in-process
loop): shutdown_all() is wired into server.py's finally + an atexit backstop.

Cancel escalation (the register_tool_process analogon): SessionKernel exposes
`cancel_escalate()` — first call interrupts the kernel (SIGINT →
KeyboardInterrupt, kernel + state survive), second call SIGKILLs the kernel
process group. `engine.tool_exec.kill_tool_process` dispatches on the method's
presence, so the existing per-tool cancel plumbing reaches kernels unchanged.

No top-level `import brain` (engine invariant) and no jupyter_client import at
module load — the dependency is lazy so a missing install degrades to a clear
tool error instead of breaking engine import.
"""

import atexit
import base64
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Kernelspecs are generated per boot (machine-specific argv) into a dir we own.
_SPEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".venv_quant", "brain-kernelspecs")

_LANG_SPECS = {"python": "brainpy", "r": "brainr"}

# Grace period between interrupt and hard kill on the timeout path.
_INTERRUPT_GRACE_S = 5.0


def _ensure_kernelspecs(venv_path: str) -> None:
    """Write the two kernelspec dirs (idempotent, rewritten each start so a
    moved interpreter/venv never leaves a stale argv behind)."""
    import json
    specs = {
        "brainpy": {
            "argv": [sys.executable, "-m", "ipykernel_launcher",
                     "-f", "{connection_file}"],
            "display_name": "Brain Python", "language": "python",
        },
    }
    r_bin = shutil.which("R")
    if r_bin:
        specs["brainr"] = {
            "argv": [r_bin, "--slave", "-e", "IRkernel::main()",
                     "--args", "{connection_file}"],
            "display_name": "Brain R", "language": "R",
        }
    for name, spec in specs.items():
        d = os.path.join(_SPEC_DIR, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "kernel.json"), "w") as f:
            json.dump(spec, f)


class SessionKernel:
    """One live kernel bound to one chat session."""

    def __init__(self, session_id: str, lang: str, km, kc):
        self.session_id = session_id
        self.lang = lang                      # 'python' | 'r'
        self.km = km                          # jupyter_client.KernelManager
        self.kc = kc                          # blocking client, channels started
        self.started_at = time.time()
        self.last_used = time.time()
        self.exec_count = 0
        self.exec_lock = threading.Lock()     # one execution at a time
        self._exec_active = False
        self._cancel_count = 0                # per-execution escalation state

    # --- introspection ------------------------------------------------------

    @property
    def pid(self) -> int | None:
        try:
            return self.km.provisioner.process.pid
        except Exception:
            return None

    def is_alive(self) -> bool:
        try:
            return bool(self.km.is_alive())
        except Exception:
            return False

    def rss_mb(self) -> int | None:
        pid = self.pid
        if not pid:
            return None
        try:
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5)
            kb = int((out.stdout or "0").strip() or 0)
            return kb // 1024
        except Exception:
            return None

    # --- cancel escalation (per-tool kill analogon) --------------------------

    def cancel_escalate(self) -> bool:
        """First call: interrupt (KeyboardInterrupt, kernel survives). Second
        call: SIGKILL the kernel's process group. Called by kill_tool_process
        from the cancel HTTP path — a different thread than the exec loop."""
        self._cancel_count += 1
        if self._cancel_count <= 1:
            try:
                self.km.interrupt_kernel()
                return True
            except Exception:
                return False
        return self._hard_kill()

    def _hard_kill(self) -> bool:
        pid = self.pid
        try:
            if pid:
                from engine.tool_exec import kill_process_tree
                kill_process_tree(pid)
            return True
        except Exception:
            return False


class KernelManager:
    """Session-keyed kernel pool: lazy start, LRU eviction, idle reaping."""

    def __init__(self):
        self._lock = threading.Lock()
        self._kernels: dict[str, SessionKernel] = {}

    # --- lifecycle -----------------------------------------------------------

    def get(self, session_id: str) -> SessionKernel | None:
        with self._lock:
            k = self._kernels.get(session_id)
        if k is not None and not k.is_alive():
            self.shutdown(session_id)
            return None
        return k

    def get_or_start(self, session_id: str, lang: str, cwd: str,
                     venv_path: str = "", max_kernels: int = 3) -> SessionKernel:
        """Return the session's kernel, starting it lazily. Raises RuntimeError
        with a model-readable message on lang mismatch / full pool / missing
        runtime."""
        lang = (lang or "python").lower()
        if lang not in _LANG_SPECS:
            raise RuntimeError(f"unsupported kernel lang '{lang}' (python|r)")
        existing = self.get(session_id)
        if existing is not None:
            if existing.lang != lang:
                raise RuntimeError(
                    f"this session's kernel runs lang='{existing.lang}' — call "
                    f"kernel_restart with lang='{lang}' to switch (state is lost)")
            return existing

        # LRU eviction when the pool is full (never evict a running exec).
        with self._lock:
            if len(self._kernels) >= max_kernels:
                idle = [k for k in self._kernels.values() if not k._exec_active]
                if not idle:
                    raise RuntimeError(
                        f"all {max_kernels} kernel slots are busy — retry later")
                victim = min(idle, key=lambda k: k.last_used)
                self._kernels.pop(victim.session_id, None)
            else:
                victim = None
        if victim is not None:
            self._shutdown_kernel(victim)

        k = self._start(session_id, lang, cwd, venv_path)
        with self._lock:
            self._kernels[session_id] = k
        return k

    def _start(self, session_id: str, lang: str, cwd: str,
               venv_path: str) -> SessionKernel:
        try:
            from jupyter_client.kernelspec import KernelSpecManager
            from jupyter_client.manager import KernelManager as JKernelManager
        except ImportError as e:
            raise RuntimeError(f"jupyter_client not installed on the server: {e}")
        _ensure_kernelspecs(venv_path)
        spec_name = _LANG_SPECS[lang]
        if not os.path.isdir(os.path.join(_SPEC_DIR, spec_name)):
            raise RuntimeError("R kernel unavailable — R/IRkernel not installed")
        ksm = KernelSpecManager()
        ksm.kernel_dirs = [_SPEC_DIR] + list(ksm.kernel_dirs)
        km = JKernelManager(kernel_name=spec_name, kernel_spec_manager=ksm)
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if lang == "python" and venv_path and os.path.isdir(venv_path):
            env["PYTHONPATH"] = venv_path + (
                (os.pathsep + env["PYTHONPATH"]) if env.get("PYTHONPATH") else "")
        os.makedirs(cwd, exist_ok=True)
        km.start_kernel(cwd=cwd, env=env)
        kc = km.client()
        kc.start_channels()
        try:
            kc.wait_for_ready(timeout=60)
        except Exception as e:
            try:
                kc.stop_channels()
                km.shutdown_kernel(now=True)
            except Exception:
                pass
            raise RuntimeError(f"kernel failed to become ready: {e}")
        return SessionKernel(session_id, lang, km, kc)

    def restart(self, session_id: str, lang: str, cwd: str,
                venv_path: str = "", max_kernels: int = 3) -> SessionKernel:
        self.shutdown(session_id)
        return self.get_or_start(session_id, lang, cwd, venv_path, max_kernels)

    def shutdown(self, session_id: str) -> bool:
        with self._lock:
            k = self._kernels.pop(session_id, None)
        if k is None:
            return False
        self._shutdown_kernel(k)
        return True

    def _shutdown_kernel(self, k: SessionKernel) -> None:
        try:
            k.kc.stop_channels()
        except Exception:
            pass
        try:
            k.km.shutdown_kernel(now=True)
        except Exception:
            # Belt and braces: the process must not outlive the entry.
            k._hard_kill()

    def shutdown_all(self) -> None:
        with self._lock:
            kernels = list(self._kernels.values())
            self._kernels.clear()
        for k in kernels:
            self._shutdown_kernel(k)

    def reap_idle(self, idle_timeout_s: int = 1200) -> int:
        """Shut down kernels idle for longer than the timeout. Returns count."""
        now = time.time()
        with self._lock:
            stale = [sid for sid, k in self._kernels.items()
                     if not k._exec_active and now - k.last_used > idle_timeout_s]
        n = 0
        for sid in stale:
            if self.shutdown(sid):
                n += 1
        return n

    # --- execution -----------------------------------------------------------

    def execute(self, session_id: str, code: str, timeout: float = 120.0,
                is_cancelled=None) -> dict:
        """Run code on the session's kernel. Returns
        {ok, text, images: [png bytes], error, timed_out, interrupted, killed,
        duration_s, exec_count}. The kernel survives interrupts; a hard kill
        (2nd cancel / unresponsive after timeout+grace) drops the pool entry."""
        k = self.get(session_id)
        if k is None:
            raise RuntimeError("no kernel for this session")
        with k.exec_lock:
            k._exec_active = True
            k._cancel_count = 0
            k.last_used = time.time()
            try:
                return self._execute_locked(k, code, timeout, is_cancelled)
            finally:
                k._exec_active = False
                k.last_used = time.time()
                if not k.is_alive():
                    # Hard-killed or crashed mid-exec: drop the dead entry so
                    # the next kernel_exec starts fresh transparently.
                    self.shutdown(k.session_id)

    def _execute_locked(self, k: SessionKernel, code: str,
                        timeout: float, is_cancelled=None) -> dict:
        start = time.time()
        kc = k.kc
        msg_id = kc.execute(code)
        k.exec_count += 1
        text_parts: list[str] = []
        images: list[bytes] = []
        error = None
        timed_out = False
        interrupted_at = None
        deadline = start + timeout
        while True:
            try:
                msg = kc.get_iopub_msg(timeout=0.5)
            except queue.Empty:
                if not k.is_alive():
                    return self._result(k, start, text_parts, images,
                                        error or "kernel died during execution",
                                        timed_out, killed=True)
                now = time.time()
                if interrupted_at is not None and now - interrupted_at > _INTERRUPT_GRACE_S:
                    # Interrupt had no effect (e.g. blocked in a C extension)
                    # → hard kill after the grace period.
                    k._hard_kill()
                    return self._result(
                        k, start, text_parts, images,
                        f"kernel unresponsive after interrupt — killed "
                        f"(timeout {timeout:.0f}s)", timed_out, killed=True)
                if now > deadline and interrupted_at is None:
                    timed_out = True
                    interrupted_at = now
                    try:
                        k.km.interrupt_kernel()
                    except Exception:
                        k._hard_kill()
                # Chat-Stopp (same seam ask_user polls): interrupt once — the
                # kernel + its state survive, the execution aborts with
                # KeyboardInterrupt. An unresponsive interrupt escalates to a
                # hard kill via the grace check above.
                elif (interrupted_at is None and is_cancelled is not None
                        and is_cancelled()):
                    interrupted_at = now
                    k._cancel_count = max(k._cancel_count, 1)
                    try:
                        k.km.interrupt_kernel()
                    except Exception:
                        k._hard_kill()
                continue
            if msg.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            mtype, content = msg["msg_type"], msg["content"]
            if mtype == "stream":
                text_parts.append(content.get("text", ""))
            elif mtype in ("execute_result", "display_data"):
                data = content.get("data", {})
                if "image/png" in data:
                    try:
                        images.append(base64.b64decode(data["image/png"]))
                    except Exception:
                        pass
                elif "text/plain" in data:
                    text_parts.append(data["text/plain"] + "\n")
            elif mtype == "error":
                error = _ANSI_RE.sub("", "\n".join(content.get("traceback", [])))
            elif mtype == "status" and content.get("execution_state") == "idle":
                break
        interrupted = bool(k._cancel_count == 1 or timed_out)
        if timed_out and error is None:
            error = f"execution timed out after {timeout:.0f}s — kernel was interrupted (state preserved)"
        return self._result(k, start, text_parts, images, error,
                            timed_out, interrupted=interrupted)

    @staticmethod
    def _result(k: SessionKernel, start: float, text_parts, images, error,
                timed_out: bool, interrupted: bool = False,
                killed: bool = False) -> dict:
        return {
            "ok": error is None and not killed,
            "text": "".join(text_parts),
            "images": images,
            "error": error,
            "timed_out": timed_out,
            "interrupted": interrupted,
            "killed": killed,
            "duration_s": round(time.time() - start, 3),
            "exec_count": k.exec_count,
        }

    # --- status --------------------------------------------------------------

    def status(self, session_id: str, with_names: bool = False) -> dict:
        k = self.get(session_id)
        if k is None:
            return {"alive": False, "session_id": session_id}
        st = {
            "alive": True,
            "session_id": session_id,
            "lang": k.lang,
            "pid": k.pid,
            "uptime_s": round(time.time() - k.started_at, 1),
            "idle_s": round(time.time() - k.last_used, 1),
            "rss_mb": k.rss_mb(),
            "exec_count": k.exec_count,
            "busy": k._exec_active,
        }
        if with_names and not k._exec_active:
            names = self._introspect_names(k)
            if names is not None:
                st["names"] = names
        return st

    def _introspect_names(self, k: SessionKernel) -> str | None:
        """Top-level user names: Python via user_expressions (no history bump),
        R via a tiny real ls() execution (IRkernel ignores user_expressions)."""
        if not k.exec_lock.acquire(timeout=2):
            return None
        try:
            if k.lang == "python":
                expr = ("', '.join(sorted(n for n in globals() if not "
                        "n.startswith('_') and n not in ('In','Out','exit',"
                        "'quit','open','get_ipython')))")
                k.kc.execute("", silent=False, store_history=False,
                             user_expressions={"names": expr})
                deadline = time.time() + 10
                while time.time() < deadline:
                    try:
                        reply = k.kc.get_shell_msg(timeout=2)
                    except queue.Empty:
                        return None
                    ue = reply.get("content", {}).get("user_expressions", {})
                    if ue:
                        raw = ue.get("names", {}).get("data", {}).get("text/plain", "")
                        return raw.strip("'\"")
                return None
            r = self._execute_locked(k, "cat(paste(sort(ls()), collapse=', '))", 15)
            return r["text"].strip() if r["ok"] else None
        except Exception:
            return None
        finally:
            k.exec_lock.release()

    def overview(self) -> list[dict]:
        with self._lock:
            sids = list(self._kernels.keys())
        return [self.status(sid) for sid in sids]


kernel_manager = KernelManager()

# Backstop for exit paths that skip server.py's finally (the primary shutdown
# call). Idempotent — shutdown_all on an empty pool is a no-op.
atexit.register(kernel_manager.shutdown_all)
