#!/usr/bin/env python3
"""SparkDash — DGX-Spark-style monitor + control plane for vLLM-metal on Mac.

Self-contained, stdlib only (no pip install). Run ON the Mac mini:

    python3 sparkdash.py            # serves on 0.0.0.0:8013

System metrics are read locally (ioreg / host_processor_info / top / pmset).
vLLM-metal instances are supervised as child subprocesses, defined in a registry
(~/sparkdash-instances.json) and managed from the Settings UI. Mutating
endpoints require a shared token (config ~/sparkdash.json or SPARKDASH_TOKEN).
"""
import argparse
import ctypes
import ctypes.util
import json
import os
import re
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import URLError

# ---------------------------------------------------------------------------
# Config + username/password auth (PBKDF2, stdlib only)
# ---------------------------------------------------------------------------
import hashlib
import hmac
import secrets
import http.cookies

_CONFIG_FILE = os.path.expanduser("~/sparkdash.json")
_INSTANCES_FILE = os.path.expanduser("~/sparkdash-instances.json")
_SESSIONS_FILE = os.path.expanduser("~/sparkdash-sessions.json")
_VENV_DEFAULT = os.path.expanduser("~/.venv-vllm-metal/bin/vllm")
_PBKDF2_ITERS = 240_000
_SESSION_TTL = 30 * 24 * 3600  # 30 days
_config_lock = threading.Lock()


def _load_config():
    try:
        with open(_CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_config(cfg):
    with _config_lock:
        tmp = _CONFIG_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, _CONFIG_FILE)
        os.chmod(_CONFIG_FILE, 0o600)


def auth_configured():
    return bool(_load_config().get("auth", {}).get("hash"))


def _hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                               _PBKDF2_ITERS).hex()


def set_credentials(username, password):
    """First-run setup: store username + salted PBKDF2 hash. No plaintext."""
    salt = secrets.token_hex(16)
    cfg = _load_config()
    cfg["auth"] = {
        "username": username,
        "salt": salt,
        "hash": _hash_password(password, salt),
        "iterations": _PBKDF2_ITERS,
    }
    cfg.pop("token", None)  # retire the old shared-token scheme
    _save_config(cfg)


def verify_credentials(username, password):
    auth = _load_config().get("auth", {})
    if not auth.get("hash"):
        return False
    if username != auth.get("username"):
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(),
                               bytes.fromhex(auth["salt"]),
                               auth.get("iterations", _PBKDF2_ITERS)).hex()
    return hmac.compare_digest(calc, auth["hash"])


# --- sessions (in-memory + persisted so logins survive restart) ------------
_sessions = {}          # token -> expiry epoch
_sessions_lock = threading.Lock()


def _load_sessions():
    try:
        with open(_SESSIONS_FILE) as f:
            data = json.load(f)
        now = time.time()
        return {t: e for t, e in data.items() if e > now}
    except (OSError, ValueError):
        return {}


def _persist_sessions():
    try:
        tmp = _SESSIONS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_sessions, f)
        os.replace(tmp, _SESSIONS_FILE)
        os.chmod(_SESSIONS_FILE, 0o600)
    except OSError:
        pass


def new_session():
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = time.time() + _SESSION_TTL
        _persist_sessions()
    return token


def valid_session(token):
    if not token:
        return False
    with _sessions_lock:
        exp = _sessions.get(token)
        if exp and exp > time.time():
            return True
        if exp:  # expired
            _sessions.pop(token, None)
            _persist_sessions()
    return False


def drop_session(token):
    with _sessions_lock:
        if _sessions.pop(token, None) is not None:
            _persist_sessions()


# ---------------------------------------------------------------------------
# Instance registry (persistent JSON) — one entry per vLLM-metal instance.
# host is always 'local' today; the field exists so remote hosts can be added.
# ---------------------------------------------------------------------------
_registry_lock = threading.Lock()


def load_instances():
    try:
        with open(_INSTANCES_FILE) as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_instances(instances):
    with _registry_lock:
        tmp = _INSTANCES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(instances, f, indent=2)
        os.replace(tmp, _INSTANCES_FILE)


def get_instance(name):
    for i in load_instances():
        if i.get("name") == name:
            return i
    return None


def upsert_instance(inst):
    instances = load_instances()
    for idx, i in enumerate(instances):
        if i.get("name") == inst["name"]:
            instances[idx] = inst
            break
    else:
        instances.append(inst)
    save_instances(instances)


def delete_instance(name):
    save_instances([i for i in load_instances() if i.get("name") != name])


def instance_log_path(name):
    return os.path.expanduser(f"~/Library/Logs/vllm-{name}.log")


# flags SparkDash owns directly — never take them from the generic args map
_RESERVED_FLAGS = {"--host", "--port", "--served-model-name"}


def build_vllm_args(inst):
    """Translate a registry entry into the `vllm serve …` argv.

    Identity (model/port/served-name/host) is fixed by SparkDash. Everything
    else comes from the generic `args` map {flag: value}, where:
      * value True            -> bare flag (--enforce-eager)
      * value False/None/""   -> omitted (or --no-<flag> if it's a known bool)
      * value scalar/str      -> --flag value
    Legacy curated fields (max_num_seqs, etc.) are migrated into `args`.
    """
    venv = inst.get("venv") or _VENV_DEFAULT
    argv = [venv, "serve", inst["model"],
            "--host", "0.0.0.0",
            "--port", str(inst["port"]),
            "--served-model-name", inst.get("served_name") or inst["model"]]

    args = dict(inst.get("args") or {})
    # migrate legacy curated fields into the generic map (back-compat)
    if inst.get("max_num_seqs") and "--max-num-seqs" not in args:
        args["--max-num-seqs"] = inst["max_num_seqs"]
    if inst.get("max_model_len") and "--max-model-len" not in args:
        args["--max-model-len"] = inst["max_model_len"]
    if inst.get("enable_tool_choice", True) and "--enable-auto-tool-choice" not in args:
        args["--enable-auto-tool-choice"] = True
        args.setdefault("--tool-call-parser", inst.get("tool_parser") or "hermes")

    for flag, val in args.items():
        if not flag.startswith("--") or flag in _RESERVED_FLAGS:
            continue
        if val is True:
            argv.append(flag)
        elif val is False or val is None or val == "":
            continue
        else:
            argv += [flag, str(val)]

    # raw extra_args string still appended verbatim for anything not modeled
    for extra in (inst.get("extra_args") or "").split():
        argv.append(extra)
    return argv


# ---------------------------------------------------------------------------
# vLLM flag schema — parsed from `vllm serve --help=all` (this exact build).
# Drives the auto-generated Advanced settings form. Cached on disk.
# ---------------------------------------------------------------------------
_FLAGS_CACHE_FILE = os.path.expanduser("~/sparkdash-vllm-flags.json")
_flags_cache = {"t": 0.0, "data": None}
_FLAGS_TTL = 3600.0


def _parse_vllm_flags(text):
    """Parse `vllm serve --help=all` output into grouped flag schemas."""
    lines = text.splitlines()
    groups = []                      # [{group, desc, flags:[...]}]
    cur = {"group": "General", "desc": "", "flags": []}
    groups.append(cur)
    i = 0
    # a group header: column-0 word(s) ending with ':' that's not a flag
    header_re = re.compile(r"^([A-Za-z][A-Za-z0-9 _/()-]*):\s*$")
    flag_re = re.compile(r"^  (--[a-zA-Z0-9-]+|-[a-zA-Z])(.*)$")
    while i < len(lines):
        ln = lines[i]
        hm = header_re.match(ln)
        if hm and not ln.startswith("  "):
            name = hm.group(1).strip()
            if name.lower() not in ("usage", "positional arguments", "options"):
                cur = {"group": name, "desc": "", "flags": []}
                groups.append(cur)
            i += 1
            continue
        fm = flag_re.match(ln)
        if fm:
            head = ln.strip()
            # collect wrapped description (deeper-indented following lines)
            desc_lines = []
            # the flag line itself may carry an inline metavar/choices only
            j = i + 1
            while j < len(lines) and re.match(r"^ {6,}\S", lines[j]) and not flag_re.match(lines[j]):
                desc_lines.append(lines[j].strip())
                j += 1
            desc = " ".join(desc_lines)
            flag = _flag_from_head(head, desc)
            if flag:
                cur["flags"].append(flag)
            i = j
            continue
        i += 1
    return [g for g in groups if g["flags"]]


def _flag_from_head(head, desc):
    """Build a flag schema from its header line + description."""
    # primary flag name
    pm = re.match(r"(--[a-zA-Z0-9-]+|-[a-zA-Z])", head)
    if not pm:
        return None
    name = pm.group(1)
    if name in ("-h", "--help"):
        return None
    # default
    default = None
    dm = re.search(r"\(default:\s*(.*?)\)\s*$", desc)
    if dm:
        default = dm.group(1)
        desc = desc[:dm.start()].strip()
    # choices {a,b,c}
    choices = None
    cm = re.search(r"\{([^}]+)\}", head)
    if cm:
        choices = [c.strip() for c in cm.group(1).split(",")]
    # boolean if a --no- variant is present
    is_bool = ("--no-" in head) or (
        not cm and not re.search(r"[A-Z_]{2,}", head.replace(name.upper(), "")))
    # detect a value metavar (UPPER_CASE token) -> takes a value
    has_value = bool(re.search(r"\b[A-Z][A-Z0-9_]+\b", head)) or bool(choices)
    typ = "choice" if choices else ("bool" if (is_bool and not has_value) else "value")
    return {"name": name, "type": typ, "choices": choices,
            "default": default, "desc": desc.strip()}


def get_vllm_flags(venv=None, force=False):
    """Return parsed flag groups (cached on disk + in-memory)."""
    now = time.time()
    if not force and _flags_cache["data"] and now - _flags_cache["t"] < _FLAGS_TTL:
        return _flags_cache["data"]
    if not force:
        try:
            with open(_FLAGS_CACHE_FILE) as f:
                data = json.load(f)
            _flags_cache.update(t=now, data=data)
            return data
        except (OSError, ValueError):
            pass
    vllm = venv or _VENV_DEFAULT
    out = _run([vllm, "serve", "--help=all"], timeout=40)
    groups = _parse_vllm_flags(out)
    data = {"groups": groups,
            "count": sum(len(g["flags"]) for g in groups)}
    try:
        with open(_FLAGS_CACHE_FILE, "w") as f:
            json.dump(data, f)
    except OSError:
        pass
    _flags_cache.update(t=now, data=data)
    return data


# ---------------------------------------------------------------------------
# Hugging Face Hub model search (proxy) + vllm-metal version/update
# ---------------------------------------------------------------------------
def hf_search(query, limit=25, mlx_only=True):
    """Search the HF Hub for text-generation models. No auth needed."""
    from urllib.parse import quote
    url = ("https://huggingface.co/api/models?"
           f"search={quote(query)}&limit={int(limit)}"
           "&filter=text-generation&sort=downloads&direction=-1")
    try:
        raw = urlopen(url, timeout=10).read().decode()
        data = json.loads(raw)
    except Exception as e:
        return {"error": str(e), "models": []}
    out = []
    for m in data:
        mid = m.get("id") or m.get("modelId") or ""
        tags = m.get("tags", []) or []
        low = mid.lower()
        is_mlx = "mlx" in low or "mlx" in tags
        if mlx_only and not is_mlx:
            continue
        quant = next((q for q in ("4bit", "8bit", "3bit", "6bit", "bf16", "fp16", "gguf", "awq", "gptq")
                      if q in low), "")
        out.append({
            "id": mid,
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "quant": quant,
            "mlx": is_mlx,
            "pipeline": m.get("pipeline_tag", ""),
        })
    return {"models": out, "query": query, "mlx_only": mlx_only}


def vllm_version(venv=None):
    """Installed vllm + vllm-metal versions via pip show (fast, offline)."""
    vbin = venv or _VENV_DEFAULT
    pip = os.path.join(os.path.dirname(vbin), "pip")
    info = {}
    for pkg in ("vllm", "vllm-metal"):
        out = _run([pip, "show", pkg], timeout=10)
        m = re.search(r"^Version:\s*(.+)$", out, re.M)
        info[pkg] = m.group(1).strip() if m else None
    return info


def vllm_latest(pkg="vllm-metal"):
    """Latest version on PyPI (network)."""
    try:
        raw = urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=10).read().decode()
        return json.loads(raw).get("info", {}).get("version")
    except Exception:
        return None


_update_state = {"running": False, "log": "", "rc": None}
_update_lock = threading.Lock()


def run_vllm_update(pkg="vllm-metal", venv=None):
    """pip install -U the package in the venv; stream output into _update_state."""
    with _update_lock:
        if _update_state["running"]:
            return False
        _update_state.update(running=True, log="", rc=None)
    vbin = venv or _VENV_DEFAULT
    pip = os.path.join(os.path.dirname(vbin), "pip")

    def _worker():
        try:
            p = subprocess.Popen([pip, "install", "-U", pkg],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True)
            for line in p.stdout:
                with _update_lock:
                    _update_state["log"] += line
            p.wait()
            with _update_lock:
                _update_state["rc"] = p.returncode
        except Exception as e:
            with _update_lock:
                _update_state["log"] += f"\nERROR: {e}\n"
                _update_state["rc"] = 1
        finally:
            with _update_lock:
                _update_state["running"] = False

    threading.Thread(target=_worker, daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# Instance supervisor — spawns/kills vllm as child subprocesses.
# ---------------------------------------------------------------------------
def _port_listener_pids(port):
    """PIDs LISTENing on a TCP port (the authoritative liveness signal —
    survives SparkDash restarts, unlike an in-memory Popen handle)."""
    out = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
    pids = []
    for ln in out.split():
        try:
            pids.append(int(ln))
        except ValueError:
            pass
    return pids


class InstanceSupervisor:
    def __init__(self):
        self._procs = {}        # name -> Popen
        self._lock = threading.Lock()
        self._stopping = set()  # names intentionally stopped (skip autorestart)

    def is_running(self, name):
        # authoritative: is something LISTENing on the instance's port?
        inst = get_instance(name)
        if inst and _port_listener_pids(inst["port"]):
            return True
        with self._lock:
            p = self._procs.get(name)
            return p is not None and p.poll() is None

    def pid(self, name):
        with self._lock:
            p = self._procs.get(name)
        if p and p.poll() is None:
            return p.pid
        # adopted/orphan: report the actual listener pid on the port
        inst = get_instance(name)
        pids = _port_listener_pids(inst["port"]) if inst else []
        return pids[0] if pids else None

    def port_busy(self, name):
        """True if ANY process is already serving this instance's port —
        whether we spawned it or it's an orphan from a prior run."""
        inst = get_instance(name)
        return bool(inst and _port_listener_pids(inst["port"]))

    def start(self, name):
        inst = get_instance(name)
        if not inst:
            return False, "no such instance"
        if self.is_running(name):
            return True, "already running"
        # GUARD: never spawn a duplicate onto a port that's already serving.
        # This is what prevented the multi-zombie leak — after a SparkDash
        # restart our Popen handle is empty, but the orphaned vllm still listens.
        if self.port_busy(name):
            with self._lock:
                self._stopping.discard(name)
            return True, f"already serving on :{inst['port']} (adopted)"
        args = build_vllm_args(inst)
        env = dict(os.environ)
        venv_bin = os.path.dirname(inst.get("venv") or _VENV_DEFAULT)
        env["PATH"] = venv_bin + ":" + env.get("PATH", "")
        try:
            logf = open(instance_log_path(name), "a")
            logf.write(f"\n=== SparkDash start {name}: {' '.join(args)} ===\n")
            logf.flush()
            p = subprocess.Popen(args, stdout=logf, stderr=subprocess.STDOUT,
                                 env=env, start_new_session=True)
        except OSError as e:
            return False, f"spawn failed: {e}"
        with self._lock:
            self._procs[name] = p
            self._stopping.discard(name)
        return True, f"started pid {p.pid}"

    def stop(self, name):
        inst = get_instance(name)
        with self._lock:
            p = self._procs.get(name)
            self._stopping.add(name)
        killed = []
        # 1) our own child (process group)
        if p is not None and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                killed.append(p.pid)
            except (ProcessLookupError, PermissionError):
                try:
                    p.terminate(); killed.append(p.pid)
                except Exception:
                    pass
        # 2) ALSO kill any orphan/adopted listeners on this port (+ their groups)
        if inst:
            for pid in _port_listener_pids(inst["port"]):
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                killed.append(pid)
        return True, (f"stopping pids {sorted(set(killed))}" if killed
                      else "not running")

    def restore_on_boot(self):
        for inst in load_instances():
            if inst.get("autostart"):
                self.start(inst["name"])

    def _watchdog(self):
        """Restart autostart instances that died unexpectedly."""
        while True:
            time.sleep(5)
            for inst in load_instances():
                name = inst["name"]
                if not inst.get("autostart"):
                    continue
                with self._lock:
                    stopping = name in self._stopping
                    p = self._procs.get(name)
                dead = p is not None and p.poll() is not None
                never = p is None
                if not stopping and (dead or never):
                    self.start(name)


supervisor = InstanceSupervisor()

# ---------------------------------------------------------------------------
# Metric collectors
# ---------------------------------------------------------------------------

def _run(cmd, timeout=4):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return ""


def _sysctl(key):
    return _run(["sysctl", "-n", key]).strip()


def host_info():
    return {
        "model": _sysctl("hw.model") or "Mac",
        "chip": _sysctl("machdep.cpu.brand_string") or "Apple Silicon",
        "ncpu": int(_sysctl("hw.ncpu") or 0),
        "pcore": int(_sysctl("hw.perflevel0.logicalcpu") or 0),
        "ecore": int(_sysctl("hw.perflevel1.logicalcpu") or 0),
        "memsize": int(_sysctl("hw.memsize") or 0),
        "uptime": _run(["uptime"]).strip(),
    }


def gpu_stats():
    out = _run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"])
    util = mem = 0
    m = re.search(r'"Device Utilization %"=(\d+)', out)
    if m:
        util = int(m.group(1))
    m = re.search(r'"In use system memory"=(\d+)', out)
    if m:
        mem = int(m.group(1))
    return {"util": util, "alloc_mem": mem}


# --- per-core CPU via host_processor_info (no sudo) -------------------------

_PROCESSOR_CPU_LOAD_INFO = 2
_CPU_STATE_MAX = 4  # user, system, idle, nice
_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_libc.mach_host_self.restype = ctypes.c_uint
_prev_core_ticks = None  # list of (user+sys+nice, total) per core


def _read_core_ticks():
    host = _libc.mach_host_self()
    ncpu = ctypes.c_uint(0)
    info = ctypes.POINTER(ctypes.c_int)()
    cnt = ctypes.c_uint(0)
    r = _libc.host_processor_info(host, _PROCESSOR_CPU_LOAD_INFO,
                                  ctypes.byref(ncpu), ctypes.byref(info),
                                  ctypes.byref(cnt))
    if r != 0:
        return []
    n = ncpu.value
    out = []
    for c in range(n):
        base = c * _CPU_STATE_MAX
        user, system, idle, nice = (info[base + i] for i in range(4))
        busy = user + system + nice
        out.append((busy, busy + idle))
    return out


def per_core_cpu():
    """Returns list of per-core busy % since last call (delta-based)."""
    global _prev_core_ticks
    cur = _read_core_ticks()
    pct = []
    if _prev_core_ticks and len(_prev_core_ticks) == len(cur):
        for (pb, pt), (cb, ct) in zip(_prev_core_ticks, cur):
            dtot = ct - pt
            pct.append(round((cb - pb) / dtot * 100, 1) if dtot > 0 else 0.0)
    else:
        pct = [0.0] * len(cur)
    _prev_core_ticks = cur
    return pct


def memory_breakdown():
    """Activity-Monitor-style memory accounting from vm_stat (page-level).

    'Memory Used' = app (anonymous, non-purgeable) + wired + compressed.
    Crucially this COUNTS the compressor, so a loaded-but-paged-out model still
    shows up — top's PhysMem 'used' only counts resident pages and made the
    System Memory gauge read as if vLLM wasn't loaded.
    """
    out = _run(["vm_stat"])
    pg = 16384
    m = re.search(r"page size of (\d+)", out)
    if m:
        pg = int(m.group(1))

    def pages(label):
        mm = re.search(rf"{re.escape(label)}:\s+(\d+)", out)
        return int(mm.group(1)) * pg if mm else 0

    wired = pages("Pages wired down")
    compressed = pages("Pages occupied by compressor")
    anon = pages("Anonymous pages")
    purgeable = pages("Pages purgeable")
    app = max(0, anon - purgeable)          # app (anonymous) memory, ex-purgeable
    used = app + wired + compressed         # == Activity Monitor "Memory Used"
    total = int(_sysctl("hw.memsize") or 0)
    cached = max(0, total - used - pages("Pages free"))  # file cache + reclaimable
    return {"used": used, "app": app, "wired": wired,
            "compressed": compressed, "cached": cached, "total": total}


def cpu_mem_stats():
    out = _run(["top", "-l", "1", "-n", "0"])
    cpu = 0.0
    load = [0.0, 0.0, 0.0]
    for line in out.splitlines():
        m = re.search(r"CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys,\s*([\d.]+)% idle", line)
        if m:
            cpu = round(100.0 - float(m.group(3)), 1)
        m = re.search(r"Load Avg:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", line)
        if m:
            load = [float(m.group(i)) for i in (1, 2, 3)]
    mb = memory_breakdown()
    return {"cpu": cpu, "mem_used": mb["used"], "mem_detail": mb, "load": load}


def swap_stats():
    out = _run(["sysctl", "-n", "vm.swapusage"])
    used = total = 0
    m = re.search(r"total\s*=\s*([\d.]+)M.*used\s*=\s*([\d.]+)M", out)
    if m:
        total = int(float(m.group(1)) * 1024**2)
        used = int(float(m.group(2)) * 1024**2)
    return {"used": used, "total": total}


def thermal_level():
    out = _run(["pmset", "-g", "therm"])
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", out)
    if m:
        limit = int(m.group(1))
        label = ("Nominal" if limit >= 100 else "Fair" if limit >= 75
                 else "Serious" if limit >= 50 else "Critical")
        level = (0 if limit >= 100 else 1 if limit >= 75 else 2 if limit >= 50 else 3)
        return {"label": label, "level": level, "speed_limit": limit}
    return {"label": "Nominal", "level": 0, "speed_limit": 100}


def top_processes(n=15):
    """Top processes by CPU. Returns pid, cpu%, mem%, rss bytes, command leaf."""
    out = _run(["ps", "-axo", "pid,pcpu,pmem,rss,comm", "-r"], timeout=5)
    rows = []
    for line in out.splitlines()[1:]:  # skip header
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid, pcpu, pmem, rss, comm = parts
        try:
            rows.append({
                "pid": int(pid),
                "cpu": float(pcpu.replace(",", ".")),
                "mem": float(pmem.replace(",", ".")),
                "rss": int(rss) * 1024,
                "name": comm.split("/")[-1],
            })
        except ValueError:
            continue
        if len(rows) >= n:
            break
    return rows


def file_systems():
    """Mounted real volumes (skips synthetic system mounts)."""
    out = _run(["df", "-k"], timeout=5)
    fs = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        dev, blocks, used, avail, cap, _iused, _ifree, _ip, mount = parts[:9]
        if not dev.startswith("/dev/"):
            continue
        # keep root + Data, drop the read-only system snapshots/helpers
        if mount.startswith("/System/Volumes/") and mount != "/System/Volumes/Data":
            continue
        try:
            fs.append({
                "mount": mount,
                "device": dev,
                "total": int(blocks) * 1024,
                "used": int(used) * 1024,
                "avail": int(avail) * 1024,
                "pct": int(cap.rstrip("%")),
            })
        except ValueError:
            continue
    return fs


def memory_pressure():
    """Honest memory health: system-wide free %, mapped to a 0-3 level.

    macOS reports 'free %' rather than a pressure colour for normal users;
    high free % == green. Levels mirror Activity Monitor's green/yellow/red.
    """
    out = _run(["memory_pressure"], timeout=4)
    free = None
    m = re.search(r"free percentage:\s*(\d+)%", out)
    if m:
        free = int(m.group(1))
    if free is None:
        return {"free_pct": None, "label": "—", "level": 0}
    if free >= 40:
        label, level = "Normal", 0
    elif free >= 20:
        label, level = "Warning", 1
    elif free >= 10:
        label, level = "Elevated", 2
    else:
        label, level = "Critical", 3
    return {"free_pct": free, "label": label, "level": level}


# --- vLLM / Metal memory breakdown (cached; footprint is slow) --------------

_VLLM_MEM_CACHE = {"t": 0.0, "data": None}
_VLLM_MEM_TTL = 15.0  # seconds


def _disk_weights_bytes(model_id):
    """Best-effort on-disk size of the model's weights (== loaded into GPU).

    vLLM may report only the served-name (e.g. 'Qwen2.5-7B-Instruct-4bit'),
    so match the HF hub cache dir by *suffix* rather than the full repo id.
    """
    if not model_id:
        return 0
    leaf = model_id.split("/")[-1]  # served-name or repo leaf
    out = _run(["bash", "-lc",
                'du -sk "$HOME"/.cache/huggingface/hub/models--*--'
                + leaf + ' 2>/dev/null | sort -rn | head -1'], timeout=6)
    m = re.search(r"^(\d+)", out)
    return int(m.group(1)) * 1024 if m else 0


_UNIT = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "B": 1}


def _footprint_pid(pid):
    """(gpu_graphics_bytes, phys_footprint_bytes) for one pid via `footprint`."""
    fp = _run(["footprint", "-p", str(pid)], timeout=8)
    gpu = rss = 0
    m = re.search(r"([\d.]+)\s*([KMG]?B)\s+.*IOAccelerator \(graphics\)", fp)
    if m:
        gpu = int(float(m.group(1)) * _UNIT[m.group(2)])
    m = re.search(r"phys_footprint:\s*([\d.]+)\s*([KMG]?B)", fp)
    if m:
        rss = int(float(m.group(1)) * _UNIT[m.group(2)])
    return gpu, rss


def _child_pids(parent):
    """Direct child PIDs of a parent (one level)."""
    out = _run(["pgrep", "-P", str(parent)])
    return [int(x) for x in out.split() if x.isdigit()]


def _vllm_pids_for_port(port):
    """The vllm process serving `port` (the LISTEN pid) plus its EngineCore
    children — the processes that actually hold this instance's GPU memory.
    Walks the real process tree, so it never double-counts other instances."""
    listeners = _port_listener_pids(port)
    pids = set(listeners)
    for lp in listeners:
        for ch in _child_pids(lp):
            pids.add(ch)
            # EngineCore can be a grandchild
            for gch in _child_pids(ch):
                pids.add(gch)
    return pids


_vllm_mem_cache = {}  # port -> {t, data}


def vllm_memory_for(port, model_id):
    """Per-instance GPU/unified-memory footprint, split weights vs KV/other.
    Only counts the process tree of the vllm LISTENing on `port`, so multiple
    instances are attributed independently. Cached per port (footprint is slow)."""
    now = time.time()
    c = _vllm_mem_cache.get(port)
    if c and now - c["t"] < _VLLM_MEM_TTL:
        return c["data"]

    pids = _vllm_pids_for_port(port)
    gpu_bytes = rss_bytes = 0
    for p in pids:
        g, r = _footprint_pid(p)
        gpu_bytes += g
        rss_bytes += r

    weights = _disk_weights_bytes(model_id) if gpu_bytes else 0
    kv_other = max(0, gpu_bytes - weights)
    data = {
        "running": bool(pids),
        "gpu_total": gpu_bytes,
        "weights": weights,
        "kv_other": kv_other,
        "phys_footprint": rss_bytes,
    }
    _vllm_mem_cache[port] = {"t": now, "data": data}
    return data


# --- vLLM Prometheus scrape -------------------------------------------------

_VLLM_GAUGES = {
    "vllm:num_requests_running": "running",
    "vllm:num_requests_waiting": "waiting",
    "vllm:kv_cache_usage_perc": "kv_cache",
    "vllm:prompt_tokens_total": "prompt_tokens",
    "vllm:generation_tokens_total": "generation_tokens",
    "vllm:prefix_cache_queries_total": "prefix_queries",
    "vllm:prefix_cache_hits_total": "prefix_hits",
}
_prev_by_url = {}  # base_url -> {t, prompt_tokens, generation_tokens}


def vllm_stats(base_url):
    try:
        raw = urlopen(base_url.rstrip("/") + "/metrics", timeout=3).read().decode()
    except (URLError, OSError):
        return {"up": False}
    vals = {}
    model = None
    for line in raw.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        for metric, key in _VLLM_GAUGES.items():
            if line.startswith(metric + "{") or line.startswith(metric + " "):
                mm = re.search(r'model_name="([^"]+)"', line)
                if mm:
                    model = mm.group(1)
                try:
                    vals[key] = float(line.rsplit(" ", 1)[-1])
                except ValueError:
                    pass
                break
    now = time.time()
    prev = _prev_by_url.setdefault(base_url, {"t": None, "prompt_tokens": 0.0, "generation_tokens": 0.0})
    gen_tps = prompt_tps = 0.0
    if prev["t"] is not None:
        dt = now - prev["t"]
        if dt > 0:
            gen_tps = max(0.0, (vals.get("generation_tokens", 0) - prev["generation_tokens"]) / dt)
            prompt_tps = max(0.0, (vals.get("prompt_tokens", 0) - prev["prompt_tokens"]) / dt)
    prev.update(t=now,
                prompt_tokens=vals.get("prompt_tokens", 0.0),
                generation_tokens=vals.get("generation_tokens", 0.0))
    pq = vals.get("prefix_queries", 0.0)
    ph = vals.get("prefix_hits", 0.0)
    return {
        "up": True,
        "model": model or "—",
        "running": int(vals.get("running", 0)),
        "waiting": int(vals.get("waiting", 0)),
        "kv_cache": round(vals.get("kv_cache", 0.0) * 100, 1),
        "gen_tps": round(gen_tps, 1),
        "prompt_tps": round(prompt_tps, 1),
        "prefix_hit_rate": round(ph / pq * 100, 1) if pq else 0.0,
        "total_gen_tokens": int(vals.get("generation_tokens", 0)),
        "total_prompt_tokens": int(vals.get("prompt_tokens", 0)),
    }


def instances_status():
    """Status of every registered instance: registry fields + supervisor state
    + live scrape (if running). Sorted by name for stable display."""
    out = []
    for inst in sorted(load_instances(), key=lambda i: i.get("name", "")):
        name = inst["name"]
        base = f"http://127.0.0.1:{inst['port']}"
        running = supervisor.is_running(name)
        stats = vllm_stats(base) if (running or True) else {"up": False}
        out.append({
            "name": name,
            "host": inst.get("host", "local"),
            "port": inst["port"],
            "model": inst.get("model"),
            "served_name": inst.get("served_name") or inst.get("model"),
            "autostart": bool(inst.get("autostart")),
            "args": inst.get("args", {}),
            "extra_args": inst.get("extra_args", ""),
            "venv": inst.get("venv", ""),
            "supervised": running,
            "pid": supervisor.pid(name),
            "up": stats.get("up", False),
            "stats": stats if stats.get("up") else None,
            "memory": vllm_memory_for(inst["port"],
                                      stats.get("model") if stats.get("up") else inst.get("model"))
                       if (running or stats.get("up")) else None,
        })
    return out


def collect():
    gpu = gpu_stats()
    cm = cpu_mem_stats()
    host = host_info()
    insts = instances_status()
    return {
        "ts": time.time(),
        "host": host,
        "gpu": {"util": gpu["util"], "alloc_mem": gpu["alloc_mem"]},
        "cpu": cm["cpu"],
        "cores": per_core_cpu(),
        "load": cm["load"],
        "mem": {"used": cm["mem_used"], "total": host["memsize"],
                "detail": cm.get("mem_detail", {})},
        "swap": swap_stats(),
        "thermal": thermal_level(),
        "pressure": memory_pressure(),
        "processes": top_processes(15),
        "filesystems": file_systems(),
        "instances": insts,
    }


# ---------------------------------------------------------------------------
# Inference activity log — tail + parse vLLM's own engine-stats lines
# ---------------------------------------------------------------------------
# vLLM logs every ~10s (loggers.py):
#   Engine 000: Avg prompt throughput: 2.7 tokens/s, Avg generation throughput:
#   9.5 tokens/s, Running: 1 reqs, Waiting: 0 reqs, GPU KV cache usage: 0.1%,
#   Prefix cache hit rate: 94.1%
from collections import deque

_ACTIVITY_FILE = os.path.expanduser("~/sparkdash-activity.jsonl")
_activity = deque(maxlen=500)          # in-memory recent events for the UI
_activity_lock = threading.Lock()

_STATS_RE = re.compile(
    r"(\d\d-\d\d \d\d:\d\d:\d\d).*?Engine \d+:.*?"
    r"Avg prompt throughput:\s*([\d.]+) tokens/s.*?"
    r"Avg generation throughput:\s*([\d.]+) tokens/s.*?"
    r"Running:\s*(\d+) reqs.*?Waiting:\s*(\d+) reqs.*?"
    r"GPU KV cache usage:\s*([\d.]+)%.*?"
    r"Prefix cache hit rate:\s*([\d.]+)%")

# vLLM access line: `127.0.0.1:55827 - "POST /v1/chat/completions HTTP/1.1" 200 OK`
_ACCESS_RE = re.compile(
    r'(\d+\.\d+\.\d+\.\d+):\d+ - "POST (/v1/(?:chat/completions|completions))')

# pending client requests seen since the last stats line was recorded
_pending_clients = []


def _ip_label(ip):
    """Just the client IP."""
    return ip


def _parse_stats_line(line):
    m = _STATS_RE.search(line)
    if not m:
        return None
    ts, ptps, gtps, run, wait, kv, prefix = m.groups()
    return {
        "ts": ts,
        "prompt_tps": float(ptps),
        "gen_tps": float(gtps),
        "running": int(run),
        "waiting": int(wait),
        "kv_cache": float(kv),
        "prefix_hit": float(prefix),
    }


# per-instance tail state: name -> {"pos": int, "awaiting": dict|None}
_tail_state = {}


def _process_log_chunk(name, chunk):
    """Parse new log text for one instance; append call rows tagged w/ instance."""
    state = _tail_state.setdefault(name, {"pos": 0, "awaiting": None})
    for line in chunk.splitlines():
        am = _ACCESS_RE.search(line)
        if am:
            ip, ep = am.group(1), am.group(2)
            wall = _run(["date", "+%m-%d %H:%M:%S"]).strip()
            ev = {
                "ts": wall, "instance": name,
                "client": _ip_label(ip), "ip": ip, "endpoint": ep,
                "gen_tps": 0.0, "prompt_tps": 0.0,
                "kv_cache": 0.0, "prefix_hit": 0.0,
                "gpu_mem_gb": round(gpu_stats()["alloc_mem"] / 1024**3, 2),
            }
            with _activity_lock:
                _activity.append(ev)
            try:
                with open(_ACTIVITY_FILE, "a") as wf:
                    wf.write(json.dumps(ev) + "\n")
            except OSError:
                pass
            state["awaiting"] = ev
            continue
        st = _parse_stats_line(line)
        if st and state["awaiting"] is not None:
            aw = state["awaiting"]
            with _activity_lock:
                aw["gen_tps"] = max(aw["gen_tps"], st["gen_tps"])
                aw["prompt_tps"] = max(aw["prompt_tps"], st["prompt_tps"])
                aw["kv_cache"] = max(aw["kv_cache"], st["kv_cache"])
                aw["prefix_hit"] = st["prefix_hit"]
            if not (st["running"] or st["gen_tps"] > 0):
                state["awaiting"] = None


def _activity_tailer():
    """Background thread: follow EVERY registered instance's log. One row per
    actual LLM call, tagged with the instance name, enriched with throughput."""
    # seed from existing activity file so the panel isn't empty after restart
    try:
        with open(_ACTIVITY_FILE) as f:
            for ln in f.readlines()[-200:]:
                try:
                    _activity.append(json.loads(ln))
                except ValueError:
                    pass
    except OSError:
        pass

    # start each known instance's tail at end-of-file (skip historical lines)
    for inst in load_instances():
        lp = instance_log_path(inst["name"])
        try:
            _tail_state[inst["name"]] = {"pos": os.path.getsize(lp), "awaiting": None}
        except OSError:
            _tail_state[inst["name"]] = {"pos": 0, "awaiting": None}

    while True:
        try:
            for inst in load_instances():
                name = inst["name"]
                lp = instance_log_path(name)
                state = _tail_state.setdefault(name, {"pos": 0, "awaiting": None})
                try:
                    size = os.path.getsize(lp)
                except OSError:
                    continue
                if size < state["pos"]:
                    state["pos"] = 0
                if size > state["pos"]:
                    with open(lp, "r", errors="replace") as f:
                        f.seek(state["pos"])
                        chunk = f.read()
                        state["pos"] = f.tell()
                    _process_log_chunk(name, chunk)
        except Exception:
            pass
        time.sleep(2)


def recent_activity(n=80):
    with _activity_lock:
        return list(_activity)[-n:]


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    _extra_set_cookie = None

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # same-origin only; credentials ride a cookie so no wildcard CORS
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        if self._extra_set_cookie:
            self.send_header("Set-Cookie", self._extra_set_cookie)
            self._extra_set_cookie = None
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None

    def _session_token(self):
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        try:
            c = http.cookies.SimpleCookie(raw)
            return c["sparkdash_session"].value if "sparkdash_session" in c else None
        except Exception:
            return None

    def _authed(self):
        """Authed if no credentials are configured yet (so setup is reachable),
        or a valid session cookie is present."""
        if not auth_configured():
            return True
        return valid_session(self._session_token())

    def _require_auth(self):
        if not self._authed():
            self._send(401, json.dumps({"error": "login required"}))
            return False
        return True

    def _set_session_cookie(self, token):
        c = http.cookies.SimpleCookie()
        c["sparkdash_session"] = token
        c["sparkdash_session"]["max-age"] = _SESSION_TTL
        c["sparkdash_session"]["path"] = "/"
        c["sparkdash_session"]["httponly"] = True
        c["sparkdash_session"]["samesite"] = "Lax"
        self._extra_set_cookie = c["sparkdash_session"].OutputString()

    # --- validation shared by create/edit ---
    def _validate_instance(self, body, existing_name=None):
        name = (body.get("name") or "").strip()
        if not _NAME_RE.match(name):
            return None, "name must be 1-40 chars [a-zA-Z0-9_-]"
        if not body.get("model"):
            return None, "model is required"
        try:
            port = int(body.get("port"))
        except (TypeError, ValueError):
            return None, "port must be an integer"
        if not (1024 <= port <= 65535):
            return None, "port out of range"
        # port/name collision with a DIFFERENT instance
        for i in load_instances():
            if i["name"] != existing_name:
                if i["name"] == name:
                    return None, f"instance '{name}' already exists"
                if int(i["port"]) == port:
                    return None, f"port {port} already used by '{i['name']}'"
        # generic flag map {flag: value}; drop reserved + empty entries
        raw_args = body.get("args") or {}
        args = {}
        if isinstance(raw_args, dict):
            for k, v in raw_args.items():
                if not isinstance(k, str) or not k.startswith("--"):
                    continue
                if k in _RESERVED_FLAGS:
                    continue
                if v is False or v is None or v == "":
                    continue
                args[k] = v
        inst = {
            "name": name,
            "host": "local",
            "port": port,
            "model": body["model"].strip(),
            "served_name": (body.get("served_name") or body["model"]).strip(),
            "args": args,
            "extra_args": (body.get("extra_args") or "").strip(),
            "venv": (body.get("venv") or "").strip(),
            "autostart": bool(body.get("autostart", False)),
        }
        return inst, None

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path.startswith("/api/metrics"):
            self._send(200, json.dumps(collect()))
        elif self.path.startswith("/api/activity"):
            self._send(200, json.dumps(recent_activity(80)))
        elif self.path.startswith("/api/instances"):
            self._send(200, json.dumps(instances_status()))
        elif self.path.startswith("/api/auth-status"):
            self._send(200, json.dumps({"configured": auth_configured(),
                                        "authed": self._authed()}))
        elif self.path.startswith("/api/vllm-flags"):
            force = "force=1" in self.path
            self._send(200, json.dumps(get_vllm_flags(force=force)))
        elif self.path.startswith("/api/vllm-version"):
            installed = vllm_version()
            self._send(200, json.dumps({"installed": installed,
                                        "latest": vllm_latest("vllm-metal")}))
        elif self.path.startswith("/api/vllm-update-status"):
            with _update_lock:
                self._send(200, json.dumps(dict(_update_state)))
        elif self.path.startswith("/api/hf/search"):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            query = (q.get("q", [""])[0]).strip()
            mlx = q.get("mlx", ["1"])[0] != "0"
            if not query:
                self._send(400, json.dumps({"error": "q required"}))
            else:
                self._send(200, json.dumps(hf_search(query, mlx_only=mlx)))
        elif self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        # --- auth routes (no session required) ---
        if self.path.rstrip("/") == "/api/setup":
            if auth_configured():
                return self._send(403, json.dumps({"error": "already configured"}))
            body = self._json_body() or {}
            u = (body.get("username") or "").strip()
            p = body.get("password") or ""
            if len(u) < 3 or len(p) < 6:
                return self._send(400, json.dumps({"error": "username ≥3 and password ≥6 chars"}))
            set_credentials(u, p)
            tok = new_session()
            self._set_session_cookie(tok)
            return self._send(200, json.dumps({"ok": True}))
        if self.path.rstrip("/") == "/api/login":
            body = self._json_body() or {}
            if verify_credentials((body.get("username") or "").strip(),
                                  body.get("password") or ""):
                tok = new_session()
                self._set_session_cookie(tok)
                return self._send(200, json.dumps({"ok": True}))
            return self._send(401, json.dumps({"error": "invalid credentials"}))
        if self.path.rstrip("/") == "/api/logout":
            drop_session(self._session_token())
            return self._send(200, json.dumps({"ok": True}))

        # --- everything below requires a valid session ---
        if not self._require_auth():
            return
        # /api/vllm-update  (pip install -U vllm-metal)
        if self.path.rstrip("/") == "/api/vllm-update":
            ok = run_vllm_update("vllm-metal")
            return self._send(200 if ok else 409,
                              json.dumps({"ok": ok,
                                          "msg": "update started" if ok else "already running"}))
        # /api/instances/<name>/start | /stop
        m = re.match(r"^/api/instances/([^/]+)/(start|stop)$", self.path)
        if m:
            name, action = m.group(1), m.group(2)
            if not get_instance(name):
                return self._send(404, json.dumps({"error": "no such instance"}))
            ok, msg = (supervisor.start if action == "start" else supervisor.stop)(name)
            return self._send(200 if ok else 500, json.dumps({"ok": ok, "msg": msg}))
        # /api/instances  (create)
        if self.path.rstrip("/") == "/api/instances":
            body = self._json_body()
            if body is None:
                return self._send(400, json.dumps({"error": "bad json"}))
            inst, err = self._validate_instance(body)
            if err:
                return self._send(400, json.dumps({"error": err}))
            upsert_instance(inst)
            return self._send(201, json.dumps({"ok": True, "instance": inst}))
        self._send(404, json.dumps({"error": "not found"}))

    def do_PUT(self):
        if not self._require_auth():
            return
        m = re.match(r"^/api/instances/([^/]+)$", self.path)
        if not m:
            return self._send(404, json.dumps({"error": "not found"}))
        name = m.group(1)
        if not get_instance(name):
            return self._send(404, json.dumps({"error": "no such instance"}))
        body = self._json_body()
        if body is None:
            return self._send(400, json.dumps({"error": "bad json"}))
        body.setdefault("name", name)
        inst, err = self._validate_instance(body, existing_name=name)
        if err:
            return self._send(400, json.dumps({"error": err}))
        # if name changed, drop the old entry
        if inst["name"] != name:
            delete_instance(name)
        upsert_instance(inst)
        return self._send(200, json.dumps({"ok": True, "instance": inst,
                                           "note": "restart the instance to apply changes"}))

    def do_DELETE(self):
        if not self._require_auth():
            return
        m = re.match(r"^/api/instances/([^/]+)$", self.path)
        if not m:
            return self._send(404, json.dumps({"error": "not found"}))
        name = m.group(1)
        if not get_instance(name):
            return self._send(404, json.dumps({"error": "no such instance"}))
        supervisor.stop(name)
        delete_instance(name)
        return self._send(200, json.dumps({"ok": True}))


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Your DGX Dashboard</title>
<style>
  :root{
    --bg:#0a0a0a; --panel:#161616; --panel2:#1c1c1c; --line:#2a2a2a;
    --green:#76b900; --green-dim:#4d7a00; --text:#e8e8e8; --muted:#8a8a8a;
    --red:#e0533d; --amber:#e0a93d; --blue:#3a6ea5;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);
       font-family:"SF Pro Display",-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased}
  header{padding:18px 28px 0}
  .welcome{color:var(--muted);font-size:15px}
  .title{font-size:30px;font-weight:700;margin:2px 0 0}
  nav{display:flex;gap:26px;margin-top:14px;border-bottom:1px solid var(--line);padding-bottom:0}
  nav a{color:var(--muted);text-decoration:none;font-size:15px;padding-bottom:12px;position:relative}
  nav a.active{color:var(--text)}
  nav a.active::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:2px;background:var(--green)}
  .livepill{margin-left:auto;font-size:12px;color:var(--muted)}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);
       box-shadow:0 0 8px var(--green);margin-right:6px}
  .dot.off{background:var(--red);box-shadow:0 0 8px var(--red)}
  main{display:grid;grid-template-columns:430px 1fr;gap:18px;padding:22px 28px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px}
  /* LEFT: gauge + chart pairs */
  .leftcol{display:flex;flex-direction:column;gap:18px}
  .gpair{display:grid;grid-template-columns:1fr 1fr;gap:0;padding:0;overflow:hidden}
  .gbox{padding:18px;display:flex;flex-direction:column;align-items:center;justify-content:center}
  .gbox h3{margin:0 0 10px;font-size:15px;font-weight:600}
  .cbox{padding:14px 16px;border-left:1px solid var(--line);display:flex;flex-direction:column}
  .cbox .axis{color:var(--muted);font-size:12px}
  .cbox canvas{width:100%;flex:1;min-height:120px}
  .gval{font-size:30px;font-weight:700;margin-top:-46px;text-align:center}
  .gsub{color:var(--muted);font-size:13px;text-align:center;margin-top:36px}
  /* RIGHT: GNOME system monitor style */
  .resources{padding:0}
  .restab{display:flex;align-items:center;gap:8px;padding:14px 18px;border-bottom:1px solid var(--line)}
  .restab .chip{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:14px;padding:7px 14px;border-radius:8px}
  .restab .chip{cursor:pointer;user-select:none}
  .restab .chip:hover{color:var(--text)}
  .restab .chip.active{background:var(--panel2);color:var(--text)}
  table.act td.lft,table.act th.lft{text-align:left}
  .fsitem{padding:14px 0;border-bottom:1px solid #1a1a1a}
  .fsitem:last-child{border-bottom:none}
  .fsitem .top{display:flex;justify-content:space-between;font-size:14px;margin-bottom:8px}
  .fsitem .mount{font-weight:600}
  .fsitem .dev{color:var(--muted);font-size:12px;font-family:ui-monospace,Menlo,monospace}
  .fsitem .nums{color:var(--muted);font-size:13px}
  .fsbar{height:10px;border-radius:5px;background:#0c0c0c;border:1px solid var(--line);overflow:hidden}
  .fsbar>span{display:block;height:100%;background:linear-gradient(90deg,var(--green-dim),var(--green))}
  .fsbar>span.warn{background:linear-gradient(90deg,#7a5a00,var(--amber))}
  .fsbar>span.crit{background:linear-gradient(90deg,#7a1d00,var(--red))}
  .section{padding:14px 18px;border-bottom:1px solid var(--line)}
  .section h4{margin:0 0 10px;font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px}
  .section h4::before{content:"▾";color:var(--muted);font-size:12px}
  .graphwrap{position:relative}
  .graphwrap canvas{width:100%;height:120px;display:block}
  .ticks{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;margin-top:4px}
  .corelegend{display:grid;grid-template-columns:repeat(4,1fr);gap:4px 18px;margin-top:12px}
  .core{display:flex;align-items:center;gap:8px;font-size:13px}
  .core .sw{width:26px;height:13px;border-radius:3px;flex:none}
  .core .nm{color:var(--text)}
  .core .pc{color:var(--muted);margin-left:auto;font-variant-numeric:tabular-nums}
  .memrow{display:flex;gap:60px;margin-top:12px}
  .memrow .item{font-size:14px}
  .memrow .item .h{font-weight:600;display:flex;align-items:center;gap:8px}
  .memrow .item .h .pip{width:10px;height:10px;border-radius:50%}
  .memrow .item .d{color:var(--muted);font-size:13px;margin-top:3px}
  .membar-track{display:flex;height:22px;border-radius:6px;overflow:hidden;border:1px solid var(--line);background:#0c0c0c}
  .membar-track .seg{height:100%;transition:width .5s ease}
  .membar-track #seg_w{background:linear-gradient(90deg,var(--green-dim),var(--green))}
  .membar-track #seg_kv{background:linear-gradient(90deg,#7a5a00,var(--amber))}
  /* vLLM strip */
  #instpanels{grid-column:1 / -1}
  .vllm{padding:18px 22px;margin-bottom:18px}
  .vllm h4{margin:0 0 4px;font-size:15px;font-weight:600}
  .model{font-size:13px;color:var(--green);margin-bottom:14px;font-family:ui-monospace,Menlo,monospace}
  .ihdr{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
  .ihdr .idot{width:9px;height:9px;border-radius:50%}
  .ihdr .iname{font-weight:700;font-size:15px}
  .ihdr .imodel{color:var(--green);font-size:12px;font-family:ui-monospace,Menlo,monospace}
  .ihdr .istate{color:var(--muted);font-size:12px;margin-left:auto}
  .memhdr{color:var(--muted);font-size:13px;margin:16px 0 8px}
  .memhdr b{color:var(--text)}
  .memlegend{display:flex;gap:24px;margin-top:10px;font-size:13px;color:var(--muted)}
  .memlegend .pip{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:7px}
  .vgrid{display:grid;grid-template-columns:repeat(8,1fr);gap:12px}
  .stat{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px}
  .stat .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.6px}
  .stat .n{font-size:22px;font-weight:700;margin-top:5px}
  .stat .n small{font-size:12px;color:var(--muted);font-weight:500}
  /* activity log */
  .activity{grid-column:1 / -1;padding:18px 22px}
  .activity h4{margin:0 0 12px;font-size:15px;font-weight:600;display:flex;align-items:center;gap:10px}
  .activity .hint{color:var(--muted);font-size:12px;font-weight:400}
  .actlog{max-height:280px;overflow-y:auto;border:1px solid var(--line);border-radius:10px;background:#0c0c0c}
  table.act{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
  table.act th{position:sticky;top:0;background:#141414;color:var(--muted);font-weight:600;
               text-align:right;padding:9px 12px;font-size:11px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid var(--line)}
  table.act th:first-child,table.act td:first-child{text-align:left}
  table.act td{padding:7px 12px;text-align:right;border-bottom:1px solid #1a1a1a}
  table.act tr.idle td{color:var(--muted)}
  table.act tr.busy td:nth-child(3){color:var(--green);font-weight:600}
  .badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600}
  .badge.run{background:#1f3a00;color:var(--green)}
  .badge.idle{background:#222;color:var(--muted)}
  .cl{display:inline-block;padding:2px 7px;border-radius:5px;font-size:11px}
  .cl.local{background:#16263a;color:#7fb3e0}
  .cl.remote{background:#3a2a16;color:var(--amber)}
  .cl.none{color:#555}
  footer{color:var(--muted);font-size:12px;text-align:center;padding:0 0 22px}
  /* settings page */
  #page-settings main{max-width:1000px;margin:0 auto;padding:22px 28px}
  .btn{background:#222;color:var(--text);border:1px solid var(--line);border-radius:8px;
       padding:7px 14px;font-size:13px;cursor:pointer}
  .btn:hover{background:#2c2c2c}
  .btn.primary{background:var(--green-dim);border-color:var(--green);color:#fff}
  .btn.primary:hover{background:var(--green)}
  .btn.danger{color:var(--red);border-color:#5a2018}
  .btn.danger:hover{background:#2a1410}
  .btn.go{color:var(--green);border-color:#2f5500}
  .btn.go:hover{background:#16260a}
  input,select{background:#0c0c0c;border:1px solid var(--line);border-radius:7px;color:var(--text);
       padding:7px 10px;font-size:13px;outline:none}
  input:focus{border-color:var(--green-dim)}
  .form{display:grid;grid-template-columns:1fr 1fr;gap:12px 16px}
  .form label{display:flex;flex-direction:column;gap:5px;font-size:13px;color:var(--muted)}
  .form label.wide{grid-column:1 / -1}
  .form label.chk{flex-direction:row;align-items:center;gap:8px}
  .req{color:var(--red)}
  .inst{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px;background:#0e0e0e}
  .inst .top{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
  .inst .nm{font-weight:700;font-size:15px}
  .inst .st{font-size:12px;padding:3px 9px;border-radius:6px;font-weight:600}
  .inst .st.run{background:#1f3a00;color:var(--green)}
  .inst .st.stop{background:#2a2a2a;color:var(--muted)}
  .inst .st.ext{background:#3a2a16;color:var(--amber)}
  .inst .meta{color:var(--muted);font-size:12px;font-family:ui-monospace,Menlo,monospace;margin-top:8px;word-break:break-all}
  .inst .acts{margin-left:auto;display:flex;gap:8px}
  .inst .live{font-size:12px;color:var(--muted);margin-top:8px}
  .inst .live b{color:var(--green)}
  /* config tabs */
  .cfgtabs{display:flex;flex-wrap:wrap;gap:6px;margin:18px 0 0;border-bottom:1px solid var(--line);padding-bottom:0}
  .cfgtab{padding:8px 13px;font-size:13px;color:var(--muted);cursor:pointer;border-radius:8px 8px 0 0;position:relative;white-space:nowrap}
  .cfgtab:hover{color:var(--text)}
  .cfgtab.active{color:var(--text);background:#0e0e0e}
  .cfgtab.active::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:2px;background:var(--green)}
  .cfgtab .badge{font-size:10px;color:var(--muted);margin-left:5px}
  .cfgpane{display:none;padding:18px 2px}
  .cfgpane.active{display:block}
  .cfgpane.curated{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .ff{display:flex;flex-direction:column;gap:5px;margin-bottom:14px}
  .ff .fl{font-size:13px;color:var(--text);display:flex;align-items:center;gap:8px}
  .ff .fl code{color:var(--green);font-size:11px;font-family:ui-monospace,Menlo,monospace;font-weight:400}
  .ff .fd{font-size:12px;color:var(--muted);line-height:1.45}
  .ff.wide{grid-column:1 / -1}
  .cfgsearch{width:100%;margin-bottom:14px}
  .cfggrid{display:grid;grid-template-columns:1fr 1fr;gap:14px 22px}
  .toggle{display:inline-flex;align-items:center;gap:8px;cursor:pointer}
  /* modal */
  .modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:flex-start;justify-content:center;z-index:50;padding:60px 20px}
  .modalbox{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px 24px;width:100%;max-width:720px}
  .hfrow{display:flex;align-items:center;gap:12px;padding:11px 12px;border:1px solid var(--line);border-radius:9px;margin-bottom:8px}
  .hfrow .hid{font-family:ui-monospace,Menlo,monospace;font-size:13px;flex:1;word-break:break-all}
  .hfrow .tag{font-size:11px;padding:2px 7px;border-radius:5px;background:#1f3a00;color:var(--green)}
  .hfrow .dl{font-size:12px;color:var(--muted);white-space:nowrap}
  @media(max-width:1100px){main{grid-template-columns:1fr}.vgrid{grid-template-columns:repeat(4,1fr)}}
</style>
</head>
<body>
<header>
  <div class="welcome">👋 Welcome</div>
  <div class="title">Your DGX Dashboard</div>
  <nav>
    <a class="active" id="nav-home" onclick="showPage('home')">Home</a><a id="nav-settings" onclick="showPage('settings')">Settings</a><a>Docs ↗</a><a>Forums ↗</a><a>Resources ↗</a>
    <span class="livepill"><span class="dot" id="livedot"></span><span id="livetxt">live</span></span>
  </nav>
</header>
<div id="page-home">
<main>
  <!-- LEFT COLUMN: gauge + sparkline pairs -->
  <div class="leftcol">
    <div class="card gpair">
      <div class="gbox">
        <h3>System Memory</h3>
        <canvas id="memgauge" width="170" height="110"></canvas>
        <div class="gval"><span id="memval">0</span><small style="font-size:16px"> GB</small></div>
        <div class="gsub"><span id="memavail">0</span> GB available</div>
        <div class="gsub" id="membreak" style="margin-top:4px;font-size:11px"></div>
      </div>
      <div class="cbox">
        <div class="axis" id="memaxis">0 GB</div>
        <canvas id="memchart"></canvas>
      </div>
    </div>
    <div class="card gpair">
      <div class="gbox">
        <h3>GPU Utilization</h3>
        <canvas id="gpugauge" width="170" height="110"></canvas>
        <div class="gval"><span id="gpuval">0</span><small style="font-size:16px"> %</small></div>
        <div class="gsub"><span id="gpumem">0</span> GB allocated</div>
      </div>
      <div class="cbox">
        <div class="axis">100%</div>
        <canvas id="gpuchart"></canvas>
      </div>
    </div>
  </div>

  <!-- RIGHT COLUMN: Resources panel -->
  <div class="card resources">
    <div class="restab">
      <span class="chip" data-view="processes" onclick="switchView('processes')">☰ Processes</span>
      <span class="chip active" data-view="resources" onclick="switchView('resources')">◷ Resources</span>
      <span class="chip" data-view="filesystems" onclick="switchView('filesystems')">▣ File Systems</span>
    </div>

    <!-- RESOURCES view -->
    <div id="view-resources">
      <div class="section">
        <h4>CPU</h4>
        <div class="graphwrap"><canvas id="cpugraph"></canvas></div>
        <div class="ticks"><span>1 min</span><span>50 secs</span><span>40 secs</span><span>30 secs</span><span>20 secs</span><span>10 secs</span></div>
        <div class="corelegend" id="corelegend"></div>
      </div>
      <div class="section">
        <h4>Memory and Swap</h4>
        <div class="graphwrap"><canvas id="memswapgraph"></canvas></div>
        <div class="ticks"><span>1 min</span><span>50 secs</span><span>40 secs</span><span>30 secs</span><span>20 secs</span><span>10 secs</span></div>
        <div class="memrow">
          <div class="item"><div class="h"><span class="pip" style="background:var(--green)"></span>Memory</div>
            <div class="d" id="memline">—</div></div>
          <div class="item"><div class="h"><span class="pip" style="background:var(--blue)"></span>Swap</div>
            <div class="d" id="swapline">—</div></div>
          <div class="item"><div class="h"><span class="pip" id="prespip" style="background:var(--green)"></span>Pressure</div>
            <div class="d" id="presline">—</div></div>
        </div>
      </div>
    </div>

    <!-- PROCESSES view -->
    <div id="view-processes" style="display:none">
      <div class="section" style="border-bottom:none">
        <h4>Top Processes <span class="hint" style="font-weight:400;color:var(--muted);font-size:12px">(by CPU)</span></h4>
        <div class="actlog" style="max-height:430px">
          <table class="act">
            <thead><tr><th>PID</th><th>Process</th><th>CPU %</th><th>Mem %</th><th>RSS</th></tr></thead>
            <tbody id="procbody"><tr class="idle"><td colspan="5">loading…</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- FILE SYSTEMS view -->
    <div id="view-filesystems" style="display:none">
      <div class="section" style="border-bottom:none">
        <h4>File Systems</h4>
        <div id="fsbody"><div class="d" style="color:var(--muted)">loading…</div></div>
      </div>
    </div>
  </div>

  <!-- vLLM instances: one full-width panel per instance -->
  <div id="instpanels"></div>

  <!-- Inference activity log -->
  <div class="card activity">
    <h4>Inference Activity <span class="hint">(one row per LLM call · newest first · logged to ~/sparkdash-activity.jsonl)</span></h4>
    <div class="actlog">
      <table class="act">
        <thead><tr>
          <th>Time</th><th>Instance</th><th>Client</th><th>Endpoint</th><th>Gen t/s</th><th>Prompt t/s</th>
          <th>KV %</th><th>Prefix %</th><th>GPU mem</th>
        </tr></thead>
        <tbody id="actbody"><tr class="idle"><td colspan="9">no LLM calls recorded yet</td></tr></tbody>
      </table>
    </div>
  </div>
</main>
<footer id="uptime">—</footer>
</div><!-- /page-home -->

<!-- ===================== SETTINGS PAGE ===================== -->
<div id="page-settings" style="display:none">
  <main style="display:block">

    <!-- LOGIN / SETUP gate (shown when not authed) -->
    <div id="authgate" class="card" style="display:none;max-width:380px;margin:40px auto;padding:26px 28px">
      <h4 id="authtitle" style="margin:0 0 4px;font-size:18px">Sign in</h4>
      <div id="authsub" class="hint" style="color:var(--muted);font-size:13px;margin-bottom:18px">Enter your SparkDash credentials.</div>
      <div class="form" style="grid-template-columns:1fr">
        <label>Username<input id="a_user" autocomplete="username"/></label>
        <label>Password<input id="a_pass" type="password" autocomplete="current-password"/></label>
        <label id="a_pass2row" class="wide" style="display:none">Confirm password<input id="a_pass2" type="password"/></label>
      </div>
      <div style="margin-top:18px">
        <button class="btn primary" id="authbtn" onclick="doAuth()">Sign in</button>
        <span id="authmsg" style="margin-left:12px;font-size:13px"></span>
      </div>
    </div>

    <!-- MANAGER (shown when authed) -->
    <div id="manager" style="display:none">
    <div class="card" style="padding:20px 24px;margin-bottom:18px">
      <div style="display:flex;align-items:center">
        <div>
          <h4 style="margin:0 0 4px;font-size:16px">vLLM-metal Instances</h4>
          <div class="hint" style="color:var(--muted);font-size:13px">Start/stop, edit, and add served models supervised by SparkDash on this host.</div>
        </div>
        <button class="btn" style="margin-left:auto" onclick="doLogout()">Sign out</button>
      </div>
      <div id="instlist" style="margin-top:16px">loading…</div>
    </div>

    <!-- runtime / updater card -->
    <div class="card" style="padding:18px 24px;margin-bottom:18px">
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <div><h4 style="margin:0;font-size:15px">vLLM-metal runtime</h4>
          <div class="hint" id="verline" style="color:var(--muted);font-size:13px;margin-top:4px">checking…</div></div>
        <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
          <button class="btn" onclick="checkVersion()">Check for updates</button>
          <button class="btn go" id="updbtn" onclick="doUpdate()">Update vllm-metal</button>
        </div>
      </div>
      <pre id="updlog" style="display:none;margin-top:12px;max-height:220px;overflow:auto;background:#0c0c0c;border:1px solid var(--line);border-radius:8px;padding:12px;font-size:12px;white-space:pre-wrap"></pre>
    </div>

    <!-- instance config builder -->
    <div class="card" style="padding:20px 24px">
      <h4 style="margin:0 0 14px;font-size:16px" id="formtitle">Add Instance</h4>

      <!-- identity (always visible) -->
      <div class="form">
        <label>Name <span class="req">*</span><input id="f_name" placeholder="qwen7b"/></label>
        <label>Port <span class="req">*</span><input id="f_port" type="number" placeholder="8012"/></label>
        <label class="wide">Model (HF repo or local path) <span class="req">*</span>
          <span style="display:flex;gap:8px"><input id="f_model" style="flex:1" placeholder="mlx-community/Qwen2.5-7B-Instruct-4bit"/>
          <button class="btn" type="button" onclick="openHF()">🔍 Browse HF</button></span></label>
        <label class="wide">Served name<input id="f_served" placeholder="(defaults to model)"/></label>
        <label class="chk"><input id="f_autostart" type="checkbox"/> Autostart (boot + restart on crash)</label>
        <label>venv vllm path<input id="f_venv" placeholder="(default ~/.venv-vllm-metal/bin/vllm)"/></label>
      </div>

      <!-- config tabs -->
      <div class="cfgtabs" id="cfgtabs"></div>
      <div id="cfgpanes"></div>

      <div style="margin-top:18px">
        <button class="btn primary" onclick="submitInstance()" id="submitbtn">Add Instance</button>
        <button class="btn" onclick="resetForm()" id="cancelbtn" style="display:none">Cancel</button>
        <button class="btn" type="button" onclick="toggleArgsPreview()">Preview command</button>
        <span id="formmsg" style="margin-left:12px;font-size:13px"></span>
        <pre id="argspreview" style="display:none;margin-top:12px;background:#0c0c0c;border:1px solid var(--line);border-radius:8px;padding:12px;font-size:12px;white-space:pre-wrap;color:var(--green)"></pre>
      </div>
    </div>
    </div><!-- /manager -->

  <!-- HF model browser modal -->
  <div id="hfmodal" class="modal" style="display:none">
    <div class="modalbox">
      <div style="display:flex;align-items:center;gap:10px">
        <h4 style="margin:0;font-size:16px">Hugging Face model browser</h4>
        <button class="btn" style="margin-left:auto" onclick="closeHF()">✕</button>
      </div>
      <div style="display:flex;gap:8px;margin:14px 0">
        <input id="hfq" style="flex:1" placeholder="search models, e.g. Qwen2.5 7B" onkeydown="if(event.key==='Enter')runHF()"/>
        <label class="chk" style="white-space:nowrap;color:var(--muted);font-size:13px"><input id="hfmlx" type="checkbox" checked/> MLX only</label>
        <button class="btn primary" onclick="runHF()">Search</button>
      </div>
      <div id="hfresults" style="max-height:420px;overflow:auto">Search the Hub for a model to serve.</div>
    </div>
  </div>
  </main>
</div>

<script>
const GB = 1024**3;
const N = 60;                       // history points (~2min at 2s)
const fmt = n => n>=1e9?(n/1e9).toFixed(2)+"B":n>=1e6?(n/1e6).toFixed(1)+"M":n>=1e3?(n/1e3).toFixed(1)+"K":""+n;
const CORE_COLORS = ["#e0533d","#e08a3d","#e0c23d","#a9e03d","#5fe03d","#3de08a","#3de0c2","#3da9e0","#3d6ee0","#8a3de0","#c23de0","#e03da9"];

// rolling history buffers
const H = { mem:[], gpu:[], mempct:[], swappct:[], cores:[] };
function push(arr,v){ arr.push(v); if(arr.length>N) arr.shift(); }

function dpr(c){ const r=window.devicePixelRatio||1; const w=c.clientWidth,h=c.clientHeight;
  if(c.width!==w*r||c.height!==h*r){c.width=w*r;c.height=h*r;} const x=c.getContext("2d"); x.setTransform(r,0,0,r,0,0); return x; }

// half-donut gauge
function gauge(id,pct,color){
  const c=document.getElementById(id),x=c.getContext("2d");
  const w=c.width,h=c.height,cx=w/2,cy=h-6,rad=Math.min(w/2,h)-12,lw=16;
  x.clearRect(0,0,w,h);
  x.lineCap="round"; x.lineWidth=lw;
  x.beginPath(); x.arc(cx,cy,rad,Math.PI,2*Math.PI); x.strokeStyle="#333"; x.stroke();
  const end=Math.PI+(pct/100)*Math.PI;
  x.beginPath(); x.arc(cx,cy,rad,Math.PI,end); x.strokeStyle=color; x.stroke();
}
function gcol(p){ return p>=90?"#e0533d":p>=70?"#e0a93d":"#76b900"; }

// area sparkline (single series, blue like DGX)
function spark(id,data,max){
  const c=document.getElementById(id),x=dpr(c),w=c.clientWidth,h=c.clientHeight;
  x.clearRect(0,0,w,h);
  if(data.length<2) return;
  const step=w/(N-1), y=v=>h-4-(Math.min(v,max)/max)*(h-8);
  x.beginPath();
  data.forEach((v,i)=>{ const px=i*step,py=y(v); i?x.lineTo(px,py):x.moveTo(px,py); });
  x.lineTo((data.length-1)*step,h); x.lineTo(0,h); x.closePath();
  x.fillStyle="rgba(58,110,165,.35)"; x.fill();
  x.beginPath();
  data.forEach((v,i)=>{ const px=i*step,py=y(v); i?x.lineTo(px,py):x.moveTo(px,py); });
  x.strokeStyle="#5b9bd5"; x.lineWidth=1.5; x.stroke();
}

// multi-line graph (per-core) with gridlines + right axis
function multiline(id,series,colors,max){
  const c=document.getElementById(id),x=dpr(c),w=c.clientWidth,h=c.clientHeight;
  x.clearRect(0,0,w,h);
  // grid
  x.strokeStyle="#262626"; x.lineWidth=1; x.fillStyle="#8a8a8a"; x.font="10px sans-serif";
  [0,.5,1].forEach(f=>{ const py=4+f*(h-8); x.beginPath(); x.moveTo(0,py); x.lineTo(w-32,py); x.stroke();
    x.fillText(Math.round((1-f)*max)+(max===100?" %":""), w-28, py+3); });
  [0,.2,.4,.6,.8].forEach(f=>{ const px=f*(w-32); x.beginPath(); x.moveTo(px,4); x.lineTo(px,h-4); x.stroke(); });
  const step=(w-32)/(N-1), y=v=>4+(1-Math.min(v,max)/max)*(h-8);
  series.forEach((data,si)=>{ if(data.length<2) return;
    x.beginPath(); data.forEach((v,i)=>{ const px=i*step,py=y(v); i?x.lineTo(px,py):x.moveTo(px,py); });
    x.strokeStyle=colors[si%colors.length]; x.lineWidth=1.2; x.stroke();
  });
}

// One full-width panel per instance (serving stats + own memory breakdown).
function renderInstancePanels(insts){
  const wrap=document.getElementById("instpanels");
  if(!wrap) return;
  if(!insts.length){
    wrap.innerHTML='<div class="card vllm" style="color:var(--muted)">No vLLM-metal instances configured — add one in Settings.</div>';
    return;
  }
  wrap.innerHTML = insts.map(i=>{
    const s=i.stats, m=i.memory;
    const dot = i.up ? "var(--green)" : (i.supervised ? "var(--amber)" : "var(--muted)");
    const state = i.up ? "serving" : (i.supervised ? "loading…" : "stopped");
    let stats;
    if(i.up && s){
      stats=`<div class="vgrid">
        <div class="stat"><div class="l">Running</div><div class="n">${s.running}</div></div>
        <div class="stat"><div class="l">Waiting</div><div class="n">${s.waiting}</div></div>
        <div class="stat"><div class="l">Generation</div><div class="n">${s.gen_tps}<small> t/s</small></div></div>
        <div class="stat"><div class="l">Prompt</div><div class="n">${s.prompt_tps}<small> t/s</small></div></div>
        <div class="stat"><div class="l">KV Cache</div><div class="n">${s.kv_cache}<small> %</small></div></div>
        <div class="stat"><div class="l">Prefix Hit</div><div class="n">${s.prefix_hit_rate}<small> %</small></div></div>
        <div class="stat"><div class="l">Gen tok</div><div class="n" style="font-size:18px">${fmt(s.total_gen_tokens)}</div></div>
        <div class="stat"><div class="l">Prompt tok</div><div class="n" style="font-size:18px">${fmt(s.total_prompt_tokens)}</div></div>
      </div>`;
    } else {
      stats=`<div style="color:var(--muted);font-size:13px;padding:6px 0">${i.supervised?"engine starting — model loading into memory…":"not running — start it in Settings"}</div>`;
    }
    let mem="";
    if(m && m.gpu_total>0){
      const wG=m.weights/GB, kvG=m.kv_other/GB, totG=m.gpu_total/GB;
      const kvUse = s ? s.kv_cache : 0;   // live KV pool usage %
      mem=`<div class="memhdr">Unified / Metal memory allocated — <b>${totG.toFixed(2)} GB</b> <span style="color:#8a8a8a">(incl. paged-out)</span></div>
        <div class="membar-track"><div class="seg" style="background:linear-gradient(90deg,var(--green-dim),var(--green));width:${wG/totG*100}%"></div><div class="seg" style="background:linear-gradient(90deg,#7a5a00,var(--amber));width:${kvG/totG*100}%"></div></div>
        <div class="memlegend"><span><span class="pip" style="background:var(--green)"></span>weights ${wG.toFixed(2)} GB</span>
        <span><span class="pip" style="background:var(--amber)"></span>KV cache pool ${kvG.toFixed(2)} GB <span style="color:#8a8a8a">(reserved · ${kvUse}% in use)</span></span></div>`;
    }
    return `<div class="card vllm">
      <div class="ihdr"><span class="idot" style="background:${dot}"></span>
        <span class="iname">${i.name}</span>
        <span class="imodel">${s?s.model:(i.model||"")}</span>
        <span class="istate">:${i.port} · ${state}${i.pid?" · pid "+i.pid:""}</span></div>
      ${stats}${mem}</div>`;
  }).join("");
}

async function tick(){
  let d;
  try{ d=await (await fetch("/api/metrics",{cache:"no-store"})).json(); }
  catch(e){ document.getElementById("livedot").classList.add("off");
            document.getElementById("livetxt").textContent="offline"; return; }
  document.getElementById("livedot").classList.remove("off");
  document.getElementById("livetxt").textContent="live";

  // --- System Memory gauge + chart ---
  const mu=d.mem.used/GB, mt=d.mem.total/GB, mp=mt?mu/mt*100:0;
  gauge("memgauge",mp,gcol(mp));
  document.getElementById("memval").textContent=mu.toFixed(2);
  document.getElementById("memavail").textContent=(mt-mu).toFixed(0);
  document.getElementById("memaxis").textContent=mt.toFixed(2)+" GB";
  push(H.mem,mu); spark("memchart",H.mem,mt);
  const det=d.mem.detail||{};
  if(det.total){
    document.getElementById("membreak").textContent=
      `app ${(det.app/GB).toFixed(1)} · wired ${(det.wired/GB).toFixed(1)} · compressed ${(det.compressed/GB).toFixed(1)} GB`;
  }

  // --- GPU gauge + chart ---
  const gu=d.gpu.util;
  gauge("gpugauge",gu,gcol(gu));
  document.getElementById("gpuval").textContent=gu;
  document.getElementById("gpumem").textContent=(d.gpu.alloc_mem/GB).toFixed(1);
  push(H.gpu,gu); spark("gpuchart",H.gpu,100);

  // --- per-core CPU graph + legend ---
  const cores=d.cores||[];
  if(H.cores.length!==cores.length) H.cores=cores.map(()=>[]);
  cores.forEach((v,i)=>push(H.cores[i],v));
  multiline("cpugraph",H.cores,CORE_COLORS,100);
  const leg=document.getElementById("corelegend");
  if(leg.children.length!==cores.length){
    leg.innerHTML=cores.map((_,i)=>{
      const kind = i<d.host.pcore ? "P" : "E";
      return `<div class="core"><span class="sw" style="background:${CORE_COLORS[i%CORE_COLORS.length]}"></span>`
        +`<span class="nm">CPU${i+1} <span style="color:#8a8a8a">${kind}</span></span><span class="pc" id="cpc${i}">0%</span></div>`;
    }).join("");
  }
  cores.forEach((v,i)=>{ const el=document.getElementById("cpc"+i); if(el) el.textContent=v.toFixed(0)+"%"; });

  // --- Memory and Swap graph ---
  const sp=d.swap, su=sp.used/GB, st=sp.total/GB, spct=st?su/st*100:0;
  push(H.mempct,mp); push(H.swappct,spct);
  multiline("memswapgraph",[H.mempct,H.swappct],["#76b900","#5b9bd5"],100);
  document.getElementById("memline").textContent=
    `${mu.toFixed(1)} GB (${mp.toFixed(1)}%) of ${mt.toFixed(1)} GB`;
  document.getElementById("swapline").textContent=
    `${su.toFixed(2)} GB (${spct.toFixed(1)}%) of ${st.toFixed(1)} GB`;

  // --- memory pressure (honest health signal) ---
  const pr=d.pressure||{};
  const prcol=["#76b900","#e0a93d","#e08a3d","#e0533d"][pr.level||0];
  document.getElementById("prespip").style.background=prcol;
  document.getElementById("presline").textContent=
    pr.free_pct!=null ? `${pr.label} · ${pr.free_pct}% free` : "—";

  // --- vLLM instances: one panel each ---
  renderInstancePanels(d.instances||[]);

  // --- Processes table ---
  const procs=d.processes||[];
  const pb=document.getElementById("procbody");
  if(pb){ pb.innerHTML = procs.length ? procs.map(p=>
    `<tr class="busy"><td>${p.pid}</td><td class="lft">${p.name}</td>`
    +`<td>${p.cpu.toFixed(1)}</td><td>${p.mem.toFixed(1)}</td>`
    +`<td>${(p.rss/1024/1024).toFixed(0)} MB</td></tr>`).join("")
    : '<tr class="idle"><td colspan="5">no data</td></tr>'; }

  // --- File systems ---
  const fss=d.filesystems||[];
  const fb=document.getElementById("fsbody");
  if(fb){ fb.innerHTML = fss.length ? fss.map(f=>{
    const cls=f.pct>=90?"crit":f.pct>=75?"warn":"";
    return `<div class="fsitem"><div class="top"><div><span class="mount">${f.mount}</span> `
      +`<span class="dev">${f.device}</span></div>`
      +`<div class="nums">${(f.used/GB).toFixed(1)} / ${(f.total/GB).toFixed(0)} GB · ${f.pct}%</div></div>`
      +`<div class="fsbar"><span class="${cls}" style="width:${f.pct}%"></span></div></div>`;
  }).join("") : '<div class="d" style="color:var(--muted)">no data</div>'; }

  document.getElementById("uptime").textContent=d.host.model+" · "+d.host.chip+" · "+d.host.uptime;
}

function switchView(name){
  ["resources","processes","filesystems"].forEach(v=>{
    document.getElementById("view-"+v).style.display = v===name ? "" : "none";
  });
  document.querySelectorAll(".restab .chip").forEach(c=>{
    c.classList.toggle("active", c.dataset.view===name);
  });
}

async function tickActivity(){
  let rows;
  try{ rows=await (await fetch("/api/activity",{cache:"no-store"})).json(); }
  catch(e){ return; }
  const body=document.getElementById("actbody");
  if(!rows.length){ body.innerHTML='<tr class="idle"><td colspan="9">no LLM calls recorded yet</td></tr>'; return; }
  body.innerHTML = rows.slice().reverse().map(r=>{
    const isLocal = r.ip==="127.0.0.1";
    const cl = `<span class="cl ${isLocal?"local":"remote"}">${r.client}</span>`;
    const ep = (r.endpoint||"").replace("/v1/","");
    const inst = r.instance||"—";
    return `<tr class="busy"><td>${r.ts}</td><td style="text-align:left;color:var(--green)">${inst}</td>`
      +`<td style="text-align:left">${cl}</td>`
      +`<td style="text-align:left;color:#8a8a8a">${ep}</td>`
      +`<td>${r.gen_tps.toFixed(1)}</td><td>${r.prompt_tps.toFixed(1)}</td>`
      +`<td>${r.kv_cache.toFixed(1)}</td><td>${r.prefix_hit.toFixed(1)}</td>`
      +`<td>${r.gpu_mem_gb!=null?r.gpu_mem_gb.toFixed(1)+" GB":"—"}</td></tr>`;
  }).join("");
}

// ===================== SETTINGS / INSTANCE MANAGER =====================
let editing = null;        // instance name being edited, or null
let needsSetup = false;    // true when no credentials configured yet

function showPage(p){
  document.getElementById("page-home").style.display = p==="home"?"":"none";
  document.getElementById("page-settings").style.display = p==="settings"?"":"none";
  document.getElementById("nav-home").classList.toggle("active", p==="home");
  document.getElementById("nav-settings").classList.toggle("active", p==="settings");
  if(p==="settings") refreshAuthUI();
}

const authHeaders = () => ({"Content-Type":"application/json"});  // cookie carries auth

async function refreshAuthUI(){
  let s;
  try{ s = await (await fetch("/api/auth-status",{cache:"no-store"})).json(); }
  catch(e){ return; }
  needsSetup = !s.configured;
  const gate=document.getElementById("authgate"), mgr=document.getElementById("manager");
  // Show the manager only when credentials EXIST and the session is valid.
  // First-run (not configured) always shows the setup gate, even though the
  // server treats requests as open until an account is created.
  if(s.configured && s.authed){
    gate.style.display="none"; mgr.style.display=""; loadInstances();
    resetForm(); checkVersion();
  } else {
    mgr.style.display="none"; gate.style.display="";
    document.getElementById("authtitle").textContent = needsSetup ? "Create admin account" : "Sign in";
    document.getElementById("authsub").textContent = needsSetup
      ? "First-run setup — choose a username and password."
      : "Enter your SparkDash credentials.";
    document.getElementById("a_pass2row").style.display = needsSetup ? "" : "none";
    document.getElementById("authbtn").textContent = needsSetup ? "Create account" : "Sign in";
    document.getElementById("authmsg").textContent="";
  }
}

async function doAuth(){
  const u=document.getElementById("a_user").value.trim();
  const p=document.getElementById("a_pass").value;
  const msg=document.getElementById("authmsg"); msg.style.color="var(--red)";
  if(needsSetup){
    const p2=document.getElementById("a_pass2").value;
    if(p!==p2){ msg.textContent="passwords don't match"; return; }
    if(u.length<3||p.length<6){ msg.textContent="username ≥3, password ≥6"; return; }
    const r=await fetch("/api/setup",{method:"POST",headers:authHeaders(),body:JSON.stringify({username:u,password:p})});
    const j=await r.json().catch(()=>({}));
    if(r.ok){ refreshAuthUI(); } else { msg.textContent=j.error||"setup failed"; }
  } else {
    const r=await fetch("/api/login",{method:"POST",headers:authHeaders(),body:JSON.stringify({username:u,password:p})});
    const j=await r.json().catch(()=>({}));
    if(r.ok){ document.getElementById("a_pass").value=""; refreshAuthUI(); }
    else { msg.textContent=j.error||"login failed"; }
  }
}
async function doLogout(){
  await fetch("/api/logout",{method:"POST",headers:authHeaders()});
  refreshAuthUI();
}

async function loadInstances(){
  let list;
  try{ list = await (await fetch("/api/instances",{cache:"no-store"})).json(); }
  catch(e){ document.getElementById("instlist").textContent="error loading"; return; }
  const el = document.getElementById("instlist");
  if(!list.length){ el.innerHTML='<div style="color:var(--muted);font-size:13px">No instances yet — add one below.</div>'; return; }
  el.innerHTML = list.map(i=>{
    let st,stcls;
    if(i.supervised){ st="running"+(i.pid?" · pid "+i.pid:""); stcls="run"; }
    else if(i.up){ st="running (external)"; stcls="ext"; }
    else { st="stopped"; stcls="stop"; }
    const live = i.up && i.stats
      ? `<div class="live">model <b>${i.stats.model}</b> · ${i.stats.running} running · ${i.stats.gen_tps} t/s · KV ${i.stats.kv_cache}%</div>` : "";
    const startStop = (i.supervised)
      ? `<button class="btn danger" onclick="ctl('${i.name}','stop')">Stop</button>`
      : `<button class="btn go" onclick="ctl('${i.name}','start')">Start</button>`;
    return `<div class="inst"><div class="top">
        <span class="nm">${i.name}</span>
        <span class="st ${stcls}">${st}</span>
        <span style="color:var(--muted);font-size:12px">:${i.port}${i.autostart?" · autostart":""}</span>
        <div class="acts">${startStop}
          <button class="btn" onclick='editInstance(${JSON.stringify(i)})'>Edit</button>
          <button class="btn danger" onclick="removeInstance('${i.name}')">Remove</button>
        </div>
      </div>
      <div class="meta">${i.model}${i.served_name&&i.served_name!==i.model?" → "+i.served_name:""}${i.max_num_seqs?" · max-num-seqs "+i.max_num_seqs:""}${i.tool_parser?" · "+i.tool_parser:""}</div>
      ${live}</div>`;
  }).join("");
}

async function ctl(name, action){
  const r = await fetch(`/api/instances/${name}/${action}`,{method:"POST",headers:authHeaders()});
  const j = await r.json().catch(()=>({}));
  if(r.status===401){ refreshAuthUI(); return; }
  if(!r.ok){ alert("Failed: "+(j.error||j.msg||r.status)); }
  setTimeout(loadInstances, 600);
}

function fval(id){ const e=document.getElementById(id); return e?e.value.trim():""; }

// ---- config form: schema-driven tabs ----
let FLAGS=null;            // {groups:[{group,flags:[...]}], count}
let FLAGMAP={};            // flag name -> schema
// curated "Common" tab: the flags you actually tune, with rich help
const COMMON=[
  {f:"--gpu-memory-utilization", label:"GPU memory utilization", help:"Fraction of unified memory vLLM may use (KV cache pool size). Lower it to fit multiple models. e.g. 0.92"},
  {f:"--max-model-len", label:"Max context length", help:"Maximum tokens (prompt+output) per request. Larger = more KV memory reserved."},
  {f:"--max-num-seqs", label:"Max concurrent sequences", help:"Max requests batched together. Higher = more throughput + more memory."},
  {f:"--kv-cache-dtype", label:"KV cache dtype (incl. TurboQuant)", help:"Quantize the KV cache to save memory. turboquant_* and fp8_* shrink KV at small quality cost."},
  {f:"--quantization", label:"Weight quantization", help:"Weight quant method (awq, gptq, fp8, …). Usually auto-detected from the model."},
  {f:"--dtype", label:"Compute dtype", help:"Model compute precision (auto/float16/bfloat16)."},
  {f:"--tensor-parallel-size", label:"Tensor parallel size", help:"Split each layer across N devices. >1 needs multiple GPUs."},
  {f:"--pipeline-parallel-size", label:"Pipeline parallel size", help:"Split layers into N stages across devices."},
  {f:"--enforce-eager", label:"Enforce eager (no compile)", help:"Disable graph compilation. Slower but lower memory + faster startup."},
  {f:"--enable-auto-tool-choice", label:"Enable auto tool choice", help:"Let the model emit tool calls (function calling)."},
  {f:"--tool-call-parser", label:"Tool-call parser", help:"Parser format for tool calls (e.g. hermes). Required with auto tool choice."},
  {f:"--enable-prefix-caching", label:"Prefix caching", help:"Reuse KV for shared prompt prefixes — big speedup for repeated system prompts."},
];

function widget(schema, val){
  // returns an input element id'd by data-flag, prefilled from val
  const f=schema.name;
  const cur = (val!==undefined && val!==null) ? val : "";
  if(schema.type==="bool"){
    const on = cur===true||cur==="true"||cur===1;
    return `<label class="toggle"><input type="checkbox" data-flag="${f}" ${on?"checked":""}/> enabled</label>`;
  }
  if(schema.type==="choice"){
    const opts=["<option value=\"\">(default)</option>"].concat(
      (schema.choices||[]).map(c=>`<option value="${c}" ${String(cur)===c?"selected":""}>${c}</option>`));
    return `<select data-flag="${f}">${opts.join("")}</select>`;
  }
  return `<input data-flag="${f}" value="${cur===""?"":String(cur).replace(/"/g,'&quot;')}" placeholder="${schema.default&&schema.default!=='None'?schema.default:''}"/>`;
}

function fieldHTML(schema, val, label, help){
  const def = (schema.default && schema.default!=="None") ? ` · default ${schema.default}` : "";
  return `<div class="ff${schema.type==='value'&&!schema.choices?'':' '}"><div class="fl">${label||schema.name} <code>${schema.name}</code></div>`
    +`${widget(schema,val)}`
    +`<div class="fd">${(help||schema.desc||"").replace(/</g,"&lt;")}${def}</div></div>`;
}

function renderConfigTabs(args){
  args=args||{};
  const tabsEl=document.getElementById("cfgtabs"), panes=document.getElementById("cfgpanes");
  if(!FLAGS){ tabsEl.innerHTML='<span class="hint" style="color:var(--muted);font-size:13px">loading vLLM flags…</span>'; return; }
  // Common tab + one tab per group
  const tabs=[{id:"common",label:"Common"}].concat(FLAGS.groups.map((g,idx)=>({id:"g"+idx,label:g.group,count:g.flags.length})));
  tabsEl.innerHTML=tabs.map((t,i)=>`<div class="cfgtab ${i===0?'active':''}" data-tab="${t.id}" onclick="cfgTab('${t.id}')">${t.label}${t.count?`<span class="badge">${t.count}</span>`:''}</div>`).join("");
  // panes
  let html=`<div class="cfgpane curated active" id="pane-common">`;
  COMMON.forEach(c=>{ const s=FLAGMAP[c.f]; if(s) html+=fieldHTML(s,args[c.f],c.label,c.help); });
  html+=`</div>`;
  FLAGS.groups.forEach((g,idx)=>{
    html+=`<div class="cfgpane" id="pane-g${idx}">`
      +`<input class="cfgsearch" placeholder="filter ${g.group} flags…" oninput="filterPane(this,'g${idx}')"/>`
      +`<div class="cfggrid">`
      +g.flags.map(s=>`<div class="ffwrap" data-name="${s.name}">${fieldHTML(s,args[s.name])}</div>`).join("")
      +`</div></div>`;
  });
  panes.innerHTML=html;
}
function cfgTab(id){
  document.querySelectorAll(".cfgtab").forEach(t=>t.classList.toggle("active",t.dataset.tab===id));
  document.querySelectorAll(".cfgpane").forEach(p=>p.classList.toggle("active",p.id==="pane-"+id));
}
function filterPane(inp,gid){
  const q=inp.value.toLowerCase();
  document.querySelectorAll(`#pane-${gid} .ffwrap`).forEach(w=>{
    w.style.display = w.dataset.name.toLowerCase().includes(q) ? "" : "none";
  });
}
function collectArgs(){
  const args={};
  document.querySelectorAll("#cfgpanes [data-flag]").forEach(el=>{
    const f=el.dataset.flag;
    if(el.type==="checkbox"){ if(el.checked) args[f]=true; }
    else { const v=el.value.trim(); if(v!=="") args[f]=v; }
  });
  return args;
}
async function ensureFlags(){
  if(FLAGS) return;
  try{ FLAGS=await (await fetch("/api/vllm-flags",{cache:"no-store"})).json(); }
  catch(e){ FLAGS={groups:[],count:0}; }
  FLAGMAP={}; FLAGS.groups.forEach(g=>g.flags.forEach(s=>FLAGMAP[s.name]=s));
}

function formBody(){
  return {
    name: fval("f_name"), port: parseInt(fval("f_port"))||0,
    model: fval("f_model"), served_name: fval("f_served"),
    autostart: document.getElementById("f_autostart").checked,
    venv: fval("f_venv"),
    args: collectArgs(),
  };
}
async function resetForm(){
  editing=null;
  ["f_name","f_port","f_model","f_served","f_venv"].forEach(i=>document.getElementById(i).value="");
  document.getElementById("f_autostart").checked=false;
  document.getElementById("f_name").disabled=false;
  document.getElementById("formtitle").textContent="Add Instance";
  document.getElementById("submitbtn").textContent="Add Instance";
  document.getElementById("cancelbtn").style.display="none";
  document.getElementById("formmsg").textContent="";
  await ensureFlags(); renderConfigTabs({});
}
async function editInstance(i){
  editing=i.name;
  document.getElementById("f_name").value=i.name;
  document.getElementById("f_name").disabled=true;
  document.getElementById("f_port").value=i.port;
  document.getElementById("f_model").value=i.model||"";
  document.getElementById("f_served").value=(i.served_name&&i.served_name!==i.model)?i.served_name:"";
  document.getElementById("f_autostart").checked=!!i.autostart;
  document.getElementById("f_venv").value=i.venv||"";
  document.getElementById("formtitle").textContent="Edit "+i.name;
  document.getElementById("submitbtn").textContent="Save Changes";
  document.getElementById("cancelbtn").style.display="";
  await ensureFlags(); renderConfigTabs(i.args||{});
  window.scrollTo(0,document.body.scrollHeight);
}
async function submitInstance(){
  const body=formBody();
  const url = editing ? `/api/instances/${editing}` : "/api/instances";
  const method = editing ? "PUT" : "POST";
  const r = await fetch(url,{method,headers:authHeaders(),body:JSON.stringify(body)});
  const j = await r.json().catch(()=>({}));
  const msg=document.getElementById("formmsg");
  if(r.status===401){ refreshAuthUI(); return; }
  if(r.ok){ msg.style.color="var(--green)"; msg.textContent=editing?"saved — restart instance to apply":"created"; resetForm(); loadInstances(); }
  else { msg.style.color="var(--red)"; msg.textContent=j.error||("error "+r.status); }
}
async function removeInstance(name){
  if(!confirm("Remove instance '"+name+"'? It will be stopped and deleted from the registry."))return;
  const r=await fetch(`/api/instances/${name}`,{method:"DELETE",headers:authHeaders()});
  if(!r.ok){ const j=await r.json().catch(()=>({})); alert("Failed: "+(j.error||r.status)); }
  loadInstances();
}
function toggleArgsPreview(){
  const el=document.getElementById("argspreview");
  if(el.style.display==="none"){
    const a=collectArgs(); const parts=[];
    for(const k in a){ parts.push(a[k]===true?k:`${k} ${a[k]}`); }
    el.textContent="vllm serve "+(fval("f_model")||"<model>")+" \\\n  --port "+(fval("f_port")||"<port>")
      +(parts.length?" \\\n  "+parts.join(" \\\n  "):"");
    el.style.display="";
  } else el.style.display="none";
}

// ---- HF model browser ----
function openHF(){ document.getElementById("hfmodal").style.display="flex"; document.getElementById("hfq").focus(); }
function closeHF(){ document.getElementById("hfmodal").style.display="none"; }
async function runHF(){
  const q=document.getElementById("hfq").value.trim();
  const mlx=document.getElementById("hfmlx").checked?"1":"0";
  const box=document.getElementById("hfresults");
  if(!q){ box.textContent="Enter a search term."; return; }
  box.textContent="searching…";
  try{
    const d=await (await fetch(`/api/hf/search?q=${encodeURIComponent(q)}&mlx=${mlx}`)).json();
    const ms=d.models||[];
    if(!ms.length){ box.innerHTML='<div style="color:var(--muted)">No models found.'+(mlx==="1"?' Try unchecking "MLX only".':'')+'</div>'; return; }
    box.innerHTML=ms.map(m=>`<div class="hfrow">
      <span class="hid">${m.id}</span>
      ${m.quant?`<span class="tag">${m.quant}</span>`:""}
      <span class="dl">▼ ${m.downloads.toLocaleString()}</span>
      <button class="btn go" onclick="pickHF('${m.id}')">Use</button></div>`).join("");
  }catch(e){ box.textContent="search failed"; }
}
function pickHF(id){
  document.getElementById("f_model").value=id;
  closeHF();
  // suggest a name + served-name from the leaf
  const leaf=id.split("/").pop();
  if(!fval("f_name")) document.getElementById("f_name").value=leaf.toLowerCase().replace(/[^a-z0-9_-]/g,"-").slice(0,40);
}

// ---- vllm-metal version / update ----
async function checkVersion(){
  const el=document.getElementById("verline"); el.textContent="checking…";
  try{
    const d=await (await fetch("/api/vllm-version",{cache:"no-store"})).json();
    const inst=d.installed||{};
    el.innerHTML=`installed vllm-metal <b style="color:var(--green)">${inst["vllm-metal"]||"?"}</b> · vllm ${inst["vllm"]||"?"} · PyPI latest ${d.latest||"?"}`;
  }catch(e){ el.textContent="version check failed"; }
}
async function doUpdate(){
  if(!confirm("Update vllm-metal via pip in the venv? Restart instances afterward to use the new build."))return;
  const log=document.getElementById("updlog"); log.style.display=""; log.textContent="starting update…\n";
  const r=await fetch("/api/vllm-update",{method:"POST",headers:authHeaders()});
  if(r.status===401){ refreshAuthUI(); return; }
  const poll=setInterval(async()=>{
    const s=await (await fetch("/api/vllm-update-status",{cache:"no-store"})).json();
    log.textContent=s.log||""; log.scrollTop=log.scrollHeight;
    if(!s.running && s.rc!==null){ clearInterval(poll); log.textContent+=`\n[exit ${s.rc}]`; checkVersion(); }
  },1500);
}

// honor deep-links: #settings page, or #processes/#filesystems sub-tab
const _hv=(location.hash||"").replace("#","");
if(_hv==="settings") showPage("settings");
else if(["processes","filesystems","resources"].includes(_hv)) switchView(_hv);

tick(); tickActivity();
setInterval(tick,2000); setInterval(tickActivity,3000);
window.addEventListener("resize",()=>tick());
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8013)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    _sessions.update(_load_sessions())
    # supervise instances: restore autostart ones, then watchdog + activity tail
    supervisor.restore_on_boot()
    threading.Thread(target=supervisor._watchdog, daemon=True).start()
    threading.Thread(target=_activity_tailer, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    auth = "configured" if auth_configured() else "NOT SET (first-run setup needed)"
    print(f"SparkDash on http://{args.host}:{args.port}  · auth: {auth}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
