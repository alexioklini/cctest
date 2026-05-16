"""Sidecar subprocess supervisor.

Lives in `server_lib/` so the singleton can be safely imported from both
server.py (which usually runs as `__main__`) and from handler mixins via
`from server_lib import sidecar_supervisor`. Importing `server` directly
from a handler creates a second module instance (because Python treats
`__main__` and `server` as distinct), which would give every consumer its
own un-started copy of the supervisor.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request


class SidecarSupervisor:
    """Owns the sidecar subprocess: spawn, monitor (wait + /health probe),
    auto-restart with a crash circuit breaker, and manual restart.

    Lifecycle:
      start(server_config)  -> read config, validate paths, spawn supervisor
                                + health-probe daemon threads. Idempotent.
      status()              -> dict with running/pid/uptime/health/breaker.
      restart(reason)       -> SIGTERM current proc (3s grace, then SIGKILL).
                                Clears the circuit breaker so manual restarts
                                can recover from it.
    """

    CRASH_WINDOW_SEC = 60.0
    CRASH_LIMIT = 3
    HEALTH_INTERVAL_SEC = 15.0
    HEALTH_FAIL_LIMIT = 3

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._pid = 0
        self._started_at = 0.0
        self._last_exit_rc: int | None = None
        self._last_exit_at = 0.0
        self._crash_window: list[float] = []
        self._breaker_open = False
        self._last_health_ok = False
        self._last_health_at = 0.0
        self._health_fail_streak = 0
        self._enabled = False
        self._venv_python_abs = ""
        self._sidecar_script = ""
        self._sidecar_port = 8421
        self._repo_root = ""
        self._server_config: dict | None = None  # kept by ref, re-read live

    # ----- public API -----

    def status(self) -> dict:
        with self._lock:
            proc = self._proc
            running = bool(proc is not None and proc.poll() is None)
            cfg = (self._server_config or {}).get("sidecar") or {}
            return {
                "enabled": self._enabled,
                "running": running,
                "pid": self._pid if running else 0,
                "started_at": self._started_at if running else 0,
                "last_exit_rc": self._last_exit_rc,
                "last_exit_at": self._last_exit_at,
                "crash_count_60s": len([t for t in self._crash_window
                                         if time.time() - t < self.CRASH_WINDOW_SEC]),
                "crash_limit": self.CRASH_LIMIT,
                "breaker_open": self._breaker_open,
                "last_health_ok": self._last_health_ok,
                "last_health_at": self._last_health_at,
                "url": cfg.get("url", "http://127.0.0.1:8421"),
            }

    def restart(self, reason: str = "manual") -> dict:
        with self._lock:
            if not self._enabled:
                return {"ok": False,
                        "error": "supervisor disabled (sidecar.auto_start=false)"}
            proc = self._proc
            self._breaker_open = False
            self._crash_window.clear()
            self._health_fail_streak = 0
            pid = self._pid
        print(f"[sidecar] restart requested ({reason}) — terminating pid={pid}",
              flush=True)
        if proc is not None:
            try:
                proc.terminate()
            except Exception as e:
                print(f"[sidecar] terminate failed: {type(e).__name__}: {e}",
                      flush=True)
            try:
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # If the supervisor loop had exited (breaker was open), restart it.
        if not any(t.name == "sidecar-supervisor" and t.is_alive()
                   for t in threading.enumerate()):
            threading.Thread(target=self._supervisor_loop, daemon=True,
                             name="sidecar-supervisor").start()
        return {"ok": True}

    def start(self, server_config: dict):
        self._server_config = server_config
        cfg_sc = server_config.get("sidecar") or {}
        if not cfg_sc.get("auto_start", False):
            print("[sidecar] auto_start disabled in config — supervisor not starting",
                  flush=True)
            return

        url = (cfg_sc.get("url") or "http://127.0.0.1:8421").rstrip("/")
        try:
            from urllib.parse import urlparse
            self._sidecar_port = urlparse(url).port or 8421
        except Exception:
            self._sidecar_port = 8421

        venv_python = cfg_sc.get("venv_python") or ".venv_sidecar/bin/python"
        # Repo root is two levels up from this file: server_lib/sidecar_supervisor.py
        self._repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        self._venv_python_abs = (venv_python if os.path.isabs(venv_python)
                                  else os.path.join(self._repo_root, venv_python))
        self._sidecar_script = os.path.join(self._repo_root, "sidecar", "sidecar.py")

        if not os.path.isfile(self._venv_python_abs):
            print(f"[sidecar] FATAL: venv python not found at {self._venv_python_abs}",
                  flush=True)
            print(f"[sidecar]   create it:  python3 -m venv .venv_sidecar && "
                  f".venv_sidecar/bin/pip install anthropic", flush=True)
            return
        if not os.path.isfile(self._sidecar_script):
            print(f"[sidecar] FATAL: sidecar.py missing at {self._sidecar_script}",
                  flush=True)
            return

        with self._lock:
            self._enabled = True
        threading.Thread(target=self._supervisor_loop, daemon=True,
                         name="sidecar-supervisor").start()
        threading.Thread(target=self._health_loop, daemon=True,
                         name="sidecar-health").start()

    # ----- internal loops -----

    def _supervisor_loop(self):
        while True:
            with self._lock:
                if self._breaker_open:
                    print(f"[sidecar] CIRCUIT BREAKER OPEN — halting auto-restart. "
                          f"Use General Settings → Sidecar → Restart to recover.",
                          flush=True)
                    return
            try:
                print(f"[sidecar] launching {self._venv_python_abs} "
                      f"{self._sidecar_script} --port {self._sidecar_port}",
                      flush=True)
                proc = subprocess.Popen(
                    [self._venv_python_abs, self._sidecar_script,
                     "--port", str(self._sidecar_port)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=self._repo_root, bufsize=1,
                    universal_newlines=True,
                )
                with self._lock:
                    self._proc = proc
                    self._pid = proc.pid
                    self._started_at = time.time()
                    self._health_fail_streak = 0

                def _pump_logs(p):
                    try:
                        for line in p.stdout:
                            sys.stdout.write(line)
                            sys.stdout.flush()
                    except Exception:
                        pass

                threading.Thread(target=_pump_logs, args=(proc,),
                                 daemon=True, name="sidecar-log-pump").start()
                rc = proc.wait()
                now = time.time()
                with self._lock:
                    self._proc = None
                    self._pid = 0
                    self._last_exit_rc = rc
                    self._last_exit_at = now
                    self._crash_window.append(now)
                    self._crash_window[:] = [t for t in self._crash_window
                                              if now - t < self.CRASH_WINDOW_SEC]
                    crashes = len(self._crash_window)
                print(f"[sidecar] subprocess exited rc={rc}  "
                      f"recent_crashes={crashes}/{self.CRASH_LIMIT}", flush=True)
                if crashes >= self.CRASH_LIMIT:
                    with self._lock:
                        self._breaker_open = True
                    print(f"[sidecar] CIRCUIT BREAKER OPEN — "
                          f"{crashes} crashes in {int(self.CRASH_WINDOW_SEC)}s.",
                          flush=True)
                    return
                time.sleep(2.0)
            except Exception as e:
                print(f"[sidecar] supervisor exception: {type(e).__name__}: {e}",
                      flush=True)
                time.sleep(5.0)

    def _health_loop(self):
        while True:
            time.sleep(self.HEALTH_INTERVAL_SEC)
            with self._lock:
                proc = self._proc
                enabled = self._enabled
                breaker = self._breaker_open
                cfg = (self._server_config or {}).get("sidecar") or {}
            if not enabled or breaker:
                continue
            if proc is None or proc.poll() is not None:
                continue
            url = cfg.get("url", "http://127.0.0.1:8421").rstrip("/") + "/health"
            ok = False
            try:
                urllib.request.urlopen(url, timeout=3.0).read()
                ok = True
            except Exception:
                ok = False
            with self._lock:
                self._last_health_ok = ok
                self._last_health_at = time.time()
                if ok:
                    self._health_fail_streak = 0
                else:
                    self._health_fail_streak += 1
                    fails = self._health_fail_streak
            if not ok and fails >= self.HEALTH_FAIL_LIMIT:
                print(f"[sidecar] health-probe failed {fails}× — "
                      f"process pid={proc.pid} appears wedged. Terminating.",
                      flush=True)
                try:
                    proc.terminate()
                except Exception:
                    pass


# Module-level singleton. Both server.py and handlers/admin.py import this
# instance via `from server_lib.sidecar_supervisor import sidecar_supervisor`,
# which Python resolves to the same module identity regardless of whether
# `server` was loaded as `__main__` or as a regular import.
sidecar_supervisor = SidecarSupervisor()
