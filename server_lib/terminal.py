"""
Interactive PTY terminal sessions for code-mode projects.

Each session is a real pseudo-terminal running the user's shell, started in the
project's working_dir. Output is read off the PTY in a background thread into a
ring buffer; SSE consumers stream from there (POST keystrokes in, SSE bytes
out — the server is a ThreadingHTTPServer with no WebSocket support, so SSE+POST
is the transport, mirroring the chat stream).

Directory lockdown is PRAGMATIC (per product decision): the shell starts in
working_dir; we keep it from wandering ABOVE that root by re-cd'ing back when a
`cd` would escape (a PROMPT_COMMAND guard injected at spawn). It is NOT a hard
sandbox — absolute paths still resolve with the user's own permissions.

Sessions are keyed by (agent_id, project_name) + a session id, so the SAME
sessions surface in both the project-detail view and the code-mode chat.
"""
from __future__ import annotations

import os
import signal
import struct
import threading
import time

try:  # POSIX-only — pty/termios/fcntl don't exist on Windows. The module must
    # still import there so the lazy handler imports get a clean error from
    # create() instead of a 500 ModuleNotFoundError.
    import pty
    import select
    import termios
    import fcntl
    PTY_SUPPORTED = True
except ImportError:
    pty = select = termios = fcntl = None
    PTY_SUPPORTED = False

# Per-session output ring buffer cap (bytes). xterm re-renders from the live
# stream; this only bounds memory + the replay a reconnecting tab receives.
_BUFFER_CAP = 256 * 1024
_MAX_SESSIONS_PER_PROJECT = 8
_IDLE_KILL_SECONDS = 60 * 60  # reap sessions with no consumer + no output 1h


class PtySession:
    def __init__(self, sid: str, agent_id: str, project: str, cwd: str):
        self.id = sid
        self.agent_id = agent_id
        self.project = project
        self.cwd = cwd
        self.created_at = time.time()
        self.last_active = time.time()
        self._buf = bytearray()
        self._abs_base = 0  # absolute byte offset of buf[0] (grows as we trim)
        self._lock = threading.Lock()
        self._subscribers: list = []  # list of threading.Event to wake on data
        self._closed = False
        self.pid = None
        self.fd = None
        self._start()

    # -- lifecycle ----------------------------------------------------------
    def _start(self):
        root = self.cwd
        shell = os.environ.get("SHELL") or "/bin/bash"
        shbase = os.path.basename(shell)
        # Build the per-session rc dir in the PARENT (before fork) so cleanup is
        # unambiguous and the child just consumes it.
        try:
            self._rcdir = self._rc_dir(shbase, root)
        except Exception:
            self._rcdir = None
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                os.chdir(root)
            except OSError:
                pass
            env = dict(os.environ)
            env["TERM"] = "xterm-256color"
            env.pop("NO_COLOR", None)
            env["BRAIN_TERMINAL"] = "1"
            env["__BRAIN_ROOT"] = root
            # Directory-lockdown guard lives in the per-session rc dir (built in
            # the parent); an `exec shell -i` would NOT inherit functions, so we
            # point the interactive shell at our rc which sources the user's real
            # rc THEN appends the guard.
            rcdir = self._rcdir
            if shbase == "zsh" and rcdir:
                env["ZDOTDIR"] = rcdir
                os.execvpe(shell, [shell, "-i"], env)
            elif shbase == "bash" and rcdir:
                os.execvpe(shell, [shell, "-i", "--rcfile",
                                   os.path.join(rcdir, ".bashrc")], env)
            else:
                os.execvpe(shell, [shell, "-i"], env)
            os._exit(1)  # unreachable
        # parent
        self.pid = pid
        self.fd = fd
        # non-blocking reads
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        t = threading.Thread(target=self._reader, daemon=True,
                             name=f"pty-{self.id[:8]}")
        t.start()

    def _rc_dir(self, shell_base: str, root: str) -> str:
        """Create a per-session rc dir that sources the user's real rc then
        appends the cwd-lockdown guard. Returns the dir path."""
        import tempfile
        d = tempfile.mkdtemp(prefix="brain-term-")
        self._rcdir = d  # remembered so close() can clean it up
        rq = _sh_quote(root)
        if shell_base == "zsh":
            guard = (
                f'export __BRAIN_ROOT={rq}\n'
                '# load the user\'s real config first\n'
                '[ -f "$HOME/.zshrc" ] && source "$HOME/.zshrc"\n'
                'chpwd() { case "$PWD/" in "$__BRAIN_ROOT"/*) ;; *) '
                'builtin cd "$__BRAIN_ROOT"; '
                'print -P "%F{yellow}[Verzeichnis ist auf das Projektverzeichnis beschränkt]%f"; esac; }\n'
            )
            with open(os.path.join(d, ".zshrc"), "w") as f:
                f.write(guard)
        else:  # bash
            guard = (
                f'export __BRAIN_ROOT={rq}\n'
                '[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"\n'
                '__brain_guard() { case "$PWD/" in "$__BRAIN_ROOT"/*) ;; *) '
                'builtin cd "$__BRAIN_ROOT"; '
                'echo "[Verzeichnis ist auf das Projektverzeichnis beschränkt]"; esac; }\n'
                'PROMPT_COMMAND="__brain_guard${PROMPT_COMMAND:+; $PROMPT_COMMAND}"\n'
            )
            with open(os.path.join(d, ".bashrc"), "w") as f:
                f.write(guard)
        return d

    def _reader(self):
        while not self._closed:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not r:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            with self._lock:
                self._buf.extend(data)
                if len(self._buf) > _BUFFER_CAP:
                    trimmed = len(self._buf) - _BUFFER_CAP
                    del self._buf[:trimmed]
                    self._abs_base += trimmed
                self.last_active = time.time()
                subs = list(self._subscribers)
            for ev in subs:
                ev.set()
        self.close()

    def write(self, data: bytes):
        if self._closed or self.fd is None:
            return
        try:
            os.write(self.fd, data)
            self.last_active = time.time()
        except OSError:
            self.close()

    def resize(self, rows: int, cols: int):
        if self._closed or self.fd is None:
            return
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    def snapshot(self) -> bytes:
        with self._lock:
            return bytes(self._buf)

    def subscribe(self):
        ev = threading.Event()
        with self._lock:
            self._subscribers.append(ev)
        return ev

    def unsubscribe(self, ev):
        with self._lock:
            try:
                self._subscribers.remove(ev)
            except ValueError:
                pass

    def read_since(self, offset: int):
        """Return (new_bytes, new_offset) of buffer past `offset`. Because the
        buffer is a trimmed ring, if offset is below the trimmed window we
        return the whole current buffer (the client re-syncs)."""
        with self._lock:
            base = self._abs_base
            abs_end = base + len(self._buf)
            if offset < base:
                # client is behind the trim window → resync from current buffer
                return bytes(self._buf), abs_end
            start = max(0, offset - base)
            return bytes(self._buf[start:]), abs_end

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except OSError:
                pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        rcdir = getattr(self, "_rcdir", None)
        if rcdir:
            try:
                import shutil
                shutil.rmtree(rcdir, ignore_errors=True)
            except Exception:
                pass
        with self._lock:
            subs = list(self._subscribers)
        for ev in subs:
            ev.set()

    def info(self) -> dict:
        return {"id": self.id, "agent": self.agent_id, "project": self.project,
                "cwd": self.cwd, "created_at": self.created_at,
                "alive": not self._closed}


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


class TerminalManager:
    def __init__(self):
        self._sessions: dict[str, PtySession] = {}
        self._lock = threading.Lock()
        self._counter = 0
        threading.Thread(target=self._reaper, daemon=True, name="pty-reaper").start()

    def _key(self, agent_id, project):
        return (agent_id or "main", project or "")

    def list(self, agent_id, project) -> list:
        with self._lock:
            return [s.info() for s in self._sessions.values()
                    if (s.agent_id, s.project) == self._key(agent_id, project)
                    and not s._closed]

    def create(self, agent_id, project, cwd) -> PtySession:
        if not PTY_SUPPORTED:
            raise RuntimeError(
                "Interaktives Terminal wird auf diesem Betriebssystem nicht "
                "unterstützt (kein PTY unter Windows)")
        with self._lock:
            live = [s for s in self._sessions.values()
                    if (s.agent_id, s.project) == self._key(agent_id, project)
                    and not s._closed]
            if len(live) >= _MAX_SESSIONS_PER_PROJECT:
                raise RuntimeError("Maximale Anzahl Terminal-Sitzungen erreicht")
            self._counter += 1
            sid = f"term-{int(time.time())}-{self._counter}"
        sess = PtySession(sid, agent_id or "main", project or "", cwd)
        with self._lock:
            self._sessions[sid] = sess
        return sess

    def get(self, sid) -> PtySession | None:
        with self._lock:
            return self._sessions.get(sid)

    def close(self, sid) -> bool:
        with self._lock:
            s = self._sessions.pop(sid, None)
        if s:
            s.close()
            return True
        return False

    def _reaper(self):
        while True:
            time.sleep(120)
            now = time.time()
            with self._lock:
                items = list(self._sessions.items())
            for sid, s in items:
                if s._closed or (now - s.last_active) > _IDLE_KILL_SECONDS:
                    self.close(sid)


terminal_manager = TerminalManager()
