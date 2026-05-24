"""Managed-subprocess supervisors.

`ProcessSupervisor` is the generic engine: spawn, monitor (wait + HTTP health
probe), auto-restart with a crash circuit breaker, and manual restart. The
sidecar and the bundled SearXNG instance are both thin subclasses that only
describe HOW to launch their process and WHERE to health-probe it.

Lives in `server_lib/` so the singletons can be safely imported from both
server.py (which usually runs as `__main__`) and from handler mixins via
`from server_lib import sidecar_supervisor`. Importing `server` directly
from a handler creates a second module instance (because Python treats
`__main__` and `server` as distinct), which would give every consumer its
own un-started copy of a supervisor.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request


class ProcessSupervisor:
    """Owns a managed subprocess: spawn, monitor (wait + HTTP health probe),
    auto-restart with a crash circuit breaker, and manual restart.

    Subclasses override the four hooks below to describe their process;
    everything else (the loops, the breaker, the locking, the status dict)
    is shared. Subclasses MUST NOT duplicate any loop logic.

    Hooks:
      _resolve(cfg)  -> validate paths/config, set self._argv/_cwd/_env/
                        _health_url. Return False to abort start (logs its
                        own reason). Called once inside start().
      (label/config-key/default-url come from class attributes.)

    Lifecycle:
      start(server_config)  -> read config, _resolve(), spawn supervisor
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

    # ----- subclass-customised identity -----
    LABEL = "process"          # log prefix + thread-name stem
    CONFIG_KEY = ""            # server_config sub-block this supervisor reads
    DEFAULT_URL = ""           # fallback base url when config omits it
    PREFIX_LOGS = False        # tag each subprocess stdout line with [LABEL]

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
        self._repo_root = ""
        self._argv: list[str] = []          # set by _resolve()
        self._proc_env: dict | None = None   # set by _resolve(); None = inherit
        self._proc_cwd = ""                  # set by _resolve()
        self._health_url = ""                # set by _resolve()
        self._server_config: dict | None = None  # kept by ref, re-read live

    # ----- subclass hooks -----

    def _resolve(self, cfg: dict) -> bool:
        """Validate config + populate self._argv/_proc_cwd/_proc_env/
        _health_url. Return False (after logging why) to abort start."""
        raise NotImplementedError

    # ----- public API -----

    def status(self) -> dict:
        with self._lock:
            proc = self._proc
            running = bool(proc is not None and proc.poll() is None)
            cfg = (self._server_config or {}).get(self.CONFIG_KEY) or {}
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
                "url": cfg.get("url", self.DEFAULT_URL),
            }

    def restart(self, reason: str = "manual") -> dict:
        with self._lock:
            if not self._enabled:
                return {"ok": False,
                        "error": f"supervisor disabled ({self.CONFIG_KEY}.auto_start=false)"}
            proc = self._proc
            self._breaker_open = False
            self._crash_window.clear()
            self._health_fail_streak = 0
            pid = self._pid
        print(f"[{self.LABEL}] restart requested ({reason}) — terminating pid={pid}",
              flush=True)
        if proc is not None:
            try:
                proc.terminate()
            except Exception as e:
                print(f"[{self.LABEL}] terminate failed: {type(e).__name__}: {e}",
                      flush=True)
            try:
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # If the supervisor loop had exited (breaker was open), restart it.
        sup_name = f"{self.LABEL}-supervisor"
        if not any(t.name == sup_name and t.is_alive()
                   for t in threading.enumerate()):
            threading.Thread(target=self._supervisor_loop, daemon=True,
                             name=sup_name).start()
        return {"ok": True}

    def start(self, server_config: dict):
        self._server_config = server_config
        cfg = server_config.get(self.CONFIG_KEY) or {}
        if not cfg.get("auto_start", False):
            print(f"[{self.LABEL}] auto_start disabled in config — supervisor not starting",
                  flush=True)
            return

        # Repo root is two levels up from this file: server_lib/<this>.py
        self._repo_root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))

        if not self._resolve(cfg):
            return  # _resolve logged the reason

        with self._lock:
            self._enabled = True
        threading.Thread(target=self._supervisor_loop, daemon=True,
                         name=f"{self.LABEL}-supervisor").start()
        threading.Thread(target=self._health_loop, daemon=True,
                         name=f"{self.LABEL}-health").start()

    # ----- internal loops -----

    def _supervisor_loop(self):
        while True:
            with self._lock:
                if self._breaker_open:
                    print(f"[{self.LABEL}] CIRCUIT BREAKER OPEN — halting auto-restart. "
                          f"Use General Settings → Restart to recover.",
                          flush=True)
                    return
            try:
                print(f"[{self.LABEL}] launching {' '.join(self._argv)}", flush=True)
                proc = subprocess.Popen(
                    self._argv,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=self._proc_cwd, bufsize=1,
                    universal_newlines=True,
                    env=self._proc_env,
                )
                with self._lock:
                    self._proc = proc
                    self._pid = proc.pid
                    self._started_at = time.time()
                    self._health_fail_streak = 0

                label = self.LABEL
                prefix = self.PREFIX_LOGS

                def _pump_logs(p):
                    try:
                        for line in p.stdout:
                            if prefix and not line.startswith(f"[{label}]"):
                                sys.stdout.write(f"[{label}] {line}")
                            else:
                                sys.stdout.write(line)
                            sys.stdout.flush()
                    except Exception:
                        pass

                threading.Thread(target=_pump_logs, args=(proc,),
                                 daemon=True, name=f"{self.LABEL}-log-pump").start()
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
                print(f"[{self.LABEL}] subprocess exited rc={rc}  "
                      f"recent_crashes={crashes}/{self.CRASH_LIMIT}", flush=True)
                if crashes >= self.CRASH_LIMIT:
                    with self._lock:
                        self._breaker_open = True
                    print(f"[{self.LABEL}] CIRCUIT BREAKER OPEN — "
                          f"{crashes} crashes in {int(self.CRASH_WINDOW_SEC)}s.",
                          flush=True)
                    return
                time.sleep(2.0)
            except Exception as e:
                print(f"[{self.LABEL}] supervisor exception: {type(e).__name__}: {e}",
                      flush=True)
                time.sleep(5.0)

    def _health_loop(self):
        while True:
            time.sleep(self.HEALTH_INTERVAL_SEC)
            with self._lock:
                proc = self._proc
                enabled = self._enabled
                breaker = self._breaker_open
                url = self._health_url
            if not enabled or breaker:
                continue
            if proc is None or proc.poll() is not None:
                continue
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
                print(f"[{self.LABEL}] health-probe failed {fails}× — "
                      f"process pid={proc.pid} appears wedged. Terminating.",
                      flush=True)
                try:
                    proc.terminate()
                except Exception:
                    pass


class SidecarSupervisor(ProcessSupervisor):
    """Sidecar: a Python script run under a dedicated venv. Health = /health."""

    LABEL = "sidecar"
    CONFIG_KEY = "sidecar"
    DEFAULT_URL = "http://127.0.0.1:8421"

    def _resolve(self, cfg: dict) -> bool:
        url = (cfg.get("url") or self.DEFAULT_URL).rstrip("/")
        try:
            from urllib.parse import urlparse
            port = urlparse(url).port or 8421
        except Exception:
            port = 8421

        venv_python = cfg.get("venv_python") or ".venv_sidecar/bin/python"
        venv_python_abs = (venv_python if os.path.isabs(venv_python)
                           else os.path.join(self._repo_root, venv_python))
        sidecar_script = os.path.join(self._repo_root, "sidecar", "sidecar.py")

        if not os.path.isfile(venv_python_abs):
            print(f"[{self.LABEL}] FATAL: venv python not found at {venv_python_abs}",
                  flush=True)
            print(f"[{self.LABEL}]   create it:  python3 -m venv .venv_sidecar && "
                  f".venv_sidecar/bin/pip install anthropic", flush=True)
            return False
        if not os.path.isfile(sidecar_script):
            print(f"[{self.LABEL}] FATAL: sidecar.py missing at {sidecar_script}",
                  flush=True)
            return False

        self._argv = [venv_python_abs, sidecar_script, "--port", str(port)]
        self._proc_cwd = self._repo_root
        self._proc_env = None  # inherit
        self._health_url = url + "/health"
        return True


class SearxngSupervisor(ProcessSupervisor):
    """Bundled SearXNG metasearch instance, run under its own venv via
    `python -m searx.webapp` with SEARXNG_SETTINGS_PATH pointing at our
    override file. Health = the index page (200 OK)."""

    LABEL = "searxng"
    CONFIG_KEY = "searxng"
    DEFAULT_URL = "http://127.0.0.1:8088"
    PREFIX_LOGS = True

    def _resolve(self, cfg: dict) -> bool:
        url = (cfg.get("url") or self.DEFAULT_URL).rstrip("/")

        venv_python = cfg.get("venv_python") or ".venv_searxng/bin/python"
        venv_python_abs = (venv_python if os.path.isabs(venv_python)
                           else os.path.join(self._repo_root, venv_python))
        settings_path = cfg.get("settings_path") or "searxng_settings.yml"
        settings_abs = (settings_path if os.path.isabs(settings_path)
                        else os.path.join(self._repo_root, settings_path))
        searxng_pkg = os.path.join(self._repo_root, "searxng", "searx", "__init__.py")

        if not os.path.isfile(venv_python_abs):
            print(f"[{self.LABEL}] FATAL: venv python not found at {venv_python_abs}",
                  flush=True)
            print(f"[{self.LABEL}]   create it:  python3.13 -m venv .venv_searxng && "
                  f"cd searxng && ../.venv_searxng/bin/pip install -r requirements.txt "
                  f"&& ../.venv_searxng/bin/pip install -e . --no-build-isolation",
                  flush=True)
            return False
        if not os.path.isfile(searxng_pkg):
            print(f"[{self.LABEL}] FATAL: searxng checkout missing at "
                  f"{os.path.dirname(searxng_pkg)}", flush=True)
            return False
        if not os.path.isfile(settings_abs):
            print(f"[{self.LABEL}] FATAL: settings file missing at {settings_abs}",
                  flush=True)
            return False

        env = os.environ.copy()
        env["SEARXNG_SETTINGS_PATH"] = settings_abs

        self._argv = [venv_python_abs, "-m", "searx.webapp"]
        # cwd inside the checkout so `searx.webapp` resolves and the working
        # dir matches what the dev server expects.
        self._proc_cwd = os.path.join(self._repo_root, "searxng")
        self._proc_env = env
        self._health_url = url + "/"
        return True


# Module-level singletons. Both server.py and handlers import these instances
# via `from server_lib.sidecar_supervisor import sidecar_supervisor`, which
# Python resolves to the same module identity regardless of whether `server`
# was loaded as `__main__` or as a regular import.
sidecar_supervisor = SidecarSupervisor()
searxng_supervisor = SearxngSupervisor()
