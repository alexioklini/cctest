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
# Config + shared token
# ---------------------------------------------------------------------------
_CONFIG_FILE = os.path.expanduser("~/sparkdash.json")
_INSTANCES_FILE = os.path.expanduser("~/sparkdash-instances.json")
_VENV_DEFAULT = os.path.expanduser("~/.venv-vllm-metal/bin/vllm")


def _load_config():
    try:
        with open(_CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def auth_token():
    """Shared token required for mutating endpoints. env wins over config."""
    return os.environ.get("SPARKDASH_TOKEN") or _load_config().get("token") or ""


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


def build_vllm_args(inst):
    """Translate a registry entry into the `vllm serve …` argv."""
    venv = inst.get("venv") or _VENV_DEFAULT
    args = [venv, "serve", inst["model"],
            "--host", "0.0.0.0",
            "--port", str(inst["port"]),
            "--served-model-name", inst.get("served_name") or inst["model"]]
    if inst.get("max_num_seqs"):
        args += ["--max-num-seqs", str(inst["max_num_seqs"])]
    if inst.get("max_model_len"):
        args += ["--max-model-len", str(inst["max_model_len"])]
    if inst.get("enable_tool_choice", True):
        args += ["--enable-auto-tool-choice",
                 "--tool-call-parser", inst.get("tool_parser") or "hermes"]
    for extra in (inst.get("extra_args") or "").split():
        args.append(extra)
    return args


# ---------------------------------------------------------------------------
# Instance supervisor — spawns/kills vllm as child subprocesses.
# ---------------------------------------------------------------------------
class InstanceSupervisor:
    def __init__(self):
        self._procs = {}        # name -> Popen
        self._lock = threading.Lock()
        self._stopping = set()  # names intentionally stopped (skip autorestart)

    def is_running(self, name):
        with self._lock:
            p = self._procs.get(name)
            return p is not None and p.poll() is None

    def pid(self, name):
        with self._lock:
            p = self._procs.get(name)
            return p.pid if p and p.poll() is None else None

    def start(self, name):
        inst = get_instance(name)
        if not inst:
            return False, "no such instance"
        if self.is_running(name):
            return True, "already running"
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
        with self._lock:
            p = self._procs.get(name)
            self._stopping.add(name)
        if p is None or p.poll() is not None:
            # maybe an externally-running process on this port; nothing we own
            return True, "not running (or not supervised)"
        try:
            # SIGTERM the whole process group (vllm spawns workers)
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                p.terminate()
            except Exception:
                pass
        return True, "stopping"

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


def cpu_mem_stats():
    out = _run(["top", "-l", "1", "-n", "0"])
    cpu = 0.0
    mem_used = 0
    load = [0.0, 0.0, 0.0]
    for line in out.splitlines():
        m = re.search(r"CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys,\s*([\d.]+)% idle", line)
        if m:
            cpu = round(100.0 - float(m.group(3)), 1)
        m = re.search(r"PhysMem:\s*([\d.]+)([MGT]) used", line)
        if m:
            val, unit = float(m.group(1)), m.group(2)
            mem_used = int(val * {"M": 1024**2, "G": 1024**3, "T": 1024**4}[unit])
        m = re.search(r"Load Avg:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", line)
        if m:
            load = [float(m.group(i)) for i in (1, 2, 3)]
    return {"cpu": cpu, "mem_used": mem_used, "load": load}


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


def vllm_memory(model_id):
    """GPU/unified-memory footprint of the vLLM engine, split weights vs KV/other.

    The authoritative total is the EngineCore process's 'IOAccelerator
    (graphics)' region from `footprint`; weights are the on-disk model size;
    the remainder is KV cache + activations + Metal runtime.
    """
    now = time.time()
    c = _VLLM_MEM_CACHE
    if c["data"] is not None and now - c["t"] < _VLLM_MEM_TTL:
        return c["data"]

    # find the EngineCore pid (holds the GPU allocations)
    pids = _run(["pgrep", "-f", "VLLM::EngineCore"]).split()
    serve_pids = _run(["pgrep", "-f", "vllm serve"]).split()
    gpu_bytes = 0
    rss_bytes = 0
    for p in pids:
        fp = _run(["footprint", "-p", p], timeout=8)
        m = re.search(r"([\d.]+)\s*([KMG]B)\s+.*IOAccelerator \(graphics\)", fp)
        if m:
            gpu_bytes += int(float(m.group(1)) * {"KB": 1024, "MB": 1024**2, "GB": 1024**3}[m.group(2)])
        m = re.search(r"phys_footprint:\s*([\d.]+)\s*([KMG]B)", fp)
        if m:
            rss_bytes += int(float(m.group(1)) * {"KB": 1024, "MB": 1024**2, "GB": 1024**3}[m.group(2)])

    weights = _disk_weights_bytes(model_id) if gpu_bytes else 0
    kv_other = max(0, gpu_bytes - weights)
    data = {
        "running": bool(pids or serve_pids),
        "gpu_total": gpu_bytes,        # weights + KV + activations (unified mem)
        "weights": weights,            # model weights (== on-disk 4-bit size)
        "kv_other": kv_other,          # KV cache + activations + Metal runtime
        "phys_footprint": rss_bytes,   # total process footprint incl. GPU
    }
    c.update(t=now, data=data)
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
            "max_num_seqs": inst.get("max_num_seqs"),
            "max_model_len": inst.get("max_model_len"),
            "tool_parser": inst.get("tool_parser"),
            "extra_args": inst.get("extra_args", ""),
            "venv": inst.get("venv", ""),
            "supervised": running,
            "pid": supervisor.pid(name),
            "up": stats.get("up", False),
            "stats": stats if stats.get("up") else None,
        })
    return out


def collect():
    gpu = gpu_stats()
    cm = cpu_mem_stats()
    host = host_info()
    insts = instances_status()
    # primary = first instance that's actually serving (for the headline panel)
    primary = next((i for i in insts if i["up"]), None)
    vstats = primary["stats"] if primary else {"up": False}
    return {
        "ts": time.time(),
        "host": host,
        "gpu": {"util": gpu["util"], "alloc_mem": gpu["alloc_mem"]},
        "cpu": cm["cpu"],
        "cores": per_core_cpu(),
        "load": cm["load"],
        "mem": {"used": cm["mem_used"], "total": host["memsize"]},
        "swap": swap_stats(),
        "thermal": thermal_level(),
        "pressure": memory_pressure(),
        "processes": top_processes(15),
        "filesystems": file_systems(),
        "vllm": vstats,
        "vllm_mem": vllm_memory(vstats.get("model") if vstats.get("up") else None),
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

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Sparkdash-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return None

    def _authed(self):
        """True if no token is configured (open) or the header matches."""
        tok = auth_token()
        if not tok:
            return True
        return self.headers.get("X-Sparkdash-Token", "") == tok

    def _require_auth(self):
        if not self._authed():
            self._send(401, json.dumps({"error": "invalid or missing token"}))
            return False
        return True

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
        inst = {
            "name": name,
            "host": "local",
            "port": port,
            "model": body["model"].strip(),
            "served_name": (body.get("served_name") or body["model"]).strip(),
            "max_num_seqs": body.get("max_num_seqs") or None,
            "max_model_len": body.get("max_model_len") or None,
            "enable_tool_choice": bool(body.get("enable_tool_choice", True)),
            "tool_parser": (body.get("tool_parser") or "hermes").strip(),
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
        elif self.path.startswith("/api/auth-required"):
            self._send(200, json.dumps({"required": bool(auth_token())}))
        elif self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not self._require_auth():
            return
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
  .vllm{grid-column:1 / -1;padding:18px 22px}
  .vllm h4{margin:0 0 4px;font-size:15px;font-weight:600}
  .model{font-size:13px;color:var(--green);margin-bottom:14px;font-family:ui-monospace,Menlo,monospace}
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
      <div class="section" style="border-bottom:none">
        <h4>vLLM Memory (Unified / Metal)</h4>
        <div class="membar-track"><div class="seg" id="seg_w" style="width:0%"></div><div class="seg" id="seg_kv" style="width:0%"></div></div>
        <div class="memrow" style="margin-top:14px">
          <div class="item"><div class="h"><span class="pip" style="background:var(--green)"></span>Model weights</div>
            <div class="d" id="ml_w">—</div></div>
          <div class="item"><div class="h"><span class="pip" style="background:var(--amber)"></span>KV cache + activations</div>
            <div class="d" id="ml_kv">—</div></div>
          <div class="item"><div class="h"><span class="pip" style="background:var(--muted)"></span>GPU total</div>
            <div class="d" id="ml_tot">—</div></div>
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

  <!-- vLLM full-width strip -->
  <div class="card vllm">
    <h4>vLLM Inference Engine</h4>
    <div class="model" id="vllmmodel">—</div>
    <div class="vgrid">
      <div class="stat"><div class="l">Running</div><div class="n" id="v_run">0</div></div>
      <div class="stat"><div class="l">Waiting</div><div class="n" id="v_wait">0</div></div>
      <div class="stat"><div class="l">Generation</div><div class="n"><span id="v_tps">0</span><small> t/s</small></div></div>
      <div class="stat"><div class="l">Prompt</div><div class="n"><span id="v_ptps">0</span><small> t/s</small></div></div>
      <div class="stat"><div class="l">KV Cache</div><div class="n"><span id="v_kv">0</span><small> %</small></div></div>
      <div class="stat"><div class="l">Prefix Hit</div><div class="n"><span id="v_prefix">0</span><small> %</small></div></div>
      <div class="stat"><div class="l">Gen tok</div><div class="n" id="v_gentot" style="font-size:18px">0</div></div>
      <div class="stat"><div class="l">Prompt tok</div><div class="n" id="v_ptot" style="font-size:18px">0</div></div>
    </div>
  </div>

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
    <div class="card" style="padding:20px 24px;margin-bottom:18px">
      <h4 style="margin:0 0 4px;font-size:16px">vLLM-metal Instances</h4>
      <div class="hint" style="color:var(--muted);font-size:13px">Start/stop, edit, and add served models supervised by SparkDash on this host.</div>
      <div id="authrow" style="margin-top:14px;display:none">
        <span style="font-size:13px;color:var(--muted)">Control token</span>
        <input id="tokfield" type="password" placeholder="X-Sparkdash-Token" style="margin-left:8px"/>
        <button class="btn" onclick="saveToken()">Save</button>
        <span id="tokstate" style="font-size:12px;margin-left:8px;color:var(--muted)"></span>
      </div>
      <div id="instlist" style="margin-top:16px">loading…</div>
    </div>

    <div class="card" style="padding:20px 24px">
      <h4 style="margin:0 0 14px;font-size:16px" id="formtitle">Add Instance</h4>
      <div class="form">
        <label>Name <span class="req">*</span><input id="f_name" placeholder="qwen7b"/></label>
        <label>Port <span class="req">*</span><input id="f_port" type="number" placeholder="8012"/></label>
        <label class="wide">Model (HF repo or path) <span class="req">*</span><input id="f_model" placeholder="mlx-community/Qwen2.5-7B-Instruct-4bit"/></label>
        <label class="wide">Served name<input id="f_served" placeholder="(defaults to model)"/></label>
        <label>Max num seqs<input id="f_seqs" type="number" placeholder="8"/></label>
        <label>Max model len<input id="f_mlen" type="number" placeholder="(model default)"/></label>
        <label>Tool-call parser<input id="f_parser" placeholder="hermes"/></label>
        <label class="chk"><input id="f_tools" type="checkbox" checked/> Enable auto tool choice</label>
        <label class="chk"><input id="f_autostart" type="checkbox"/> Autostart (boot + restart on crash)</label>
        <label class="wide">Extra args<input id="f_extra" placeholder="--gpu-memory-utilization 0.9"/></label>
        <label class="wide">venv vllm path<input id="f_venv" placeholder="(default ~/.venv-vllm-metal/bin/vllm)"/></label>
      </div>
      <div style="margin-top:16px">
        <button class="btn primary" onclick="submitInstance()" id="submitbtn">Add Instance</button>
        <button class="btn" onclick="resetForm()" id="cancelbtn" style="display:none">Cancel</button>
        <span id="formmsg" style="margin-left:12px;font-size:13px"></span>
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
  document.getElementById("memavail").textContent=mt.toFixed(0);
  document.getElementById("memaxis").textContent=mt.toFixed(2)+" GB";
  push(H.mem,mu); spark("memchart",H.mem,mt);

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

  // --- vLLM memory breakdown (weights vs KV/other) ---
  const vm=d.vllm_mem||{};
  if(vm.running && vm.gpu_total>0){
    const wG=vm.weights/GB, kvG=vm.kv_other/GB, totG=vm.gpu_total/GB;
    document.getElementById("seg_w").style.width=(wG/totG*100)+"%";
    document.getElementById("seg_kv").style.width=(kvG/totG*100)+"%";
    document.getElementById("ml_w").textContent=wG.toFixed(2)+" GB";
    document.getElementById("ml_kv").textContent=kvG.toFixed(2)+" GB";
    document.getElementById("ml_tot").textContent=totG.toFixed(2)+" GB unified";
  } else {
    document.getElementById("seg_w").style.width="0%";
    document.getElementById("seg_kv").style.width="0%";
    document.getElementById("ml_w").textContent="—";
    document.getElementById("ml_kv").textContent="—";
    document.getElementById("ml_tot").textContent="engine not running";
  }

  // --- vLLM ---
  const v=d.vllm;
  if(v.up){
    document.getElementById("vllmmodel").textContent="● "+v.model;
    document.getElementById("v_run").textContent=v.running;
    document.getElementById("v_wait").textContent=v.waiting;
    document.getElementById("v_tps").textContent=v.gen_tps;
    document.getElementById("v_ptps").textContent=v.prompt_tps;
    document.getElementById("v_kv").textContent=v.kv_cache;
    document.getElementById("v_prefix").textContent=v.prefix_hit_rate;
    document.getElementById("v_gentot").textContent=fmt(v.total_gen_tokens);
    document.getElementById("v_ptot").textContent=fmt(v.total_prompt_tokens);
  } else { document.getElementById("vllmmodel").textContent="● engine offline"; }

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
let TOKEN = localStorage.getItem("sparkdash_token") || "";
let editing = null;   // instance name being edited, or null

function showPage(p){
  document.getElementById("page-home").style.display = p==="home"?"":"none";
  document.getElementById("page-settings").style.display = p==="settings"?"":"none";
  document.getElementById("nav-home").classList.toggle("active", p==="home");
  document.getElementById("nav-settings").classList.toggle("active", p==="settings");
  if(p==="settings") loadInstances();
}

function authHeaders(){
  const h = {"Content-Type":"application/json"};
  if(TOKEN) h["X-Sparkdash-Token"]=TOKEN;
  return h;
}
function saveToken(){
  TOKEN = document.getElementById("tokfield").value.trim();
  localStorage.setItem("sparkdash_token", TOKEN);
  document.getElementById("tokstate").textContent = TOKEN ? "saved" : "cleared";
}

async function checkAuth(){
  try{
    const r = await (await fetch("/api/auth-required")).json();
    if(r.required){
      document.getElementById("authrow").style.display="";
      document.getElementById("tokfield").value=TOKEN;
    }
  }catch(e){}
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
  if(!r.ok){ alert("Failed: "+(j.error||j.msg||r.status)); }
  setTimeout(loadInstances, 600);
}

function fval(id){ return document.getElementById(id).value.trim(); }
function formBody(){
  return {
    name: fval("f_name"), port: parseInt(fval("f_port"))||0,
    model: fval("f_model"), served_name: fval("f_served"),
    max_num_seqs: parseInt(fval("f_seqs"))||null,
    max_model_len: parseInt(fval("f_mlen"))||null,
    tool_parser: fval("f_parser")||"hermes",
    enable_tool_choice: document.getElementById("f_tools").checked,
    autostart: document.getElementById("f_autostart").checked,
    extra_args: fval("f_extra"), venv: fval("f_venv"),
  };
}
function resetForm(){
  editing=null;
  ["f_name","f_port","f_model","f_served","f_seqs","f_mlen","f_extra","f_venv"].forEach(i=>document.getElementById(i).value="");
  document.getElementById("f_parser").value="hermes";
  document.getElementById("f_tools").checked=true;
  document.getElementById("f_autostart").checked=false;
  document.getElementById("f_name").disabled=false;
  document.getElementById("formtitle").textContent="Add Instance";
  document.getElementById("submitbtn").textContent="Add Instance";
  document.getElementById("cancelbtn").style.display="none";
  document.getElementById("formmsg").textContent="";
}
function editInstance(i){
  editing=i.name;
  document.getElementById("f_name").value=i.name;
  document.getElementById("f_name").disabled=true;
  document.getElementById("f_port").value=i.port;
  document.getElementById("f_model").value=i.model||"";
  document.getElementById("f_served").value=(i.served_name&&i.served_name!==i.model)?i.served_name:"";
  document.getElementById("f_seqs").value=i.max_num_seqs||"";
  document.getElementById("f_mlen").value=i.max_model_len||"";
  document.getElementById("f_parser").value=i.tool_parser||"hermes";
  document.getElementById("f_tools").checked=i.enable_tool_choice!==false;
  document.getElementById("f_autostart").checked=!!i.autostart;
  document.getElementById("f_extra").value=i.extra_args||"";
  document.getElementById("f_venv").value=i.venv||"";
  document.getElementById("formtitle").textContent="Edit "+i.name;
  document.getElementById("submitbtn").textContent="Save Changes";
  document.getElementById("cancelbtn").style.display="";
  window.scrollTo(0,document.body.scrollHeight);
}
async function submitInstance(){
  const body=formBody();
  const url = editing ? `/api/instances/${editing}` : "/api/instances";
  const method = editing ? "PUT" : "POST";
  const r = await fetch(url,{method,headers:authHeaders(),body:JSON.stringify(body)});
  const j = await r.json().catch(()=>({}));
  const msg=document.getElementById("formmsg");
  if(r.ok){ msg.style.color="var(--green)"; msg.textContent=editing?"saved — restart to apply":"created"; resetForm(); loadInstances(); }
  else { msg.style.color="var(--red)"; msg.textContent=j.error||("error "+r.status); }
}
async function removeInstance(name){
  if(!confirm("Remove instance '"+name+"'? It will be stopped and deleted from the registry."))return;
  const r=await fetch(`/api/instances/${name}`,{method:"DELETE",headers:authHeaders()});
  if(!r.ok){ const j=await r.json().catch(()=>({})); alert("Failed: "+(j.error||r.status)); }
  loadInstances();
}
checkAuth();

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
    # supervise instances: restore autostart ones, then watchdog + activity tail
    supervisor.restore_on_boot()
    threading.Thread(target=supervisor._watchdog, daemon=True).start()
    threading.Thread(target=_activity_tailer, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    tok = "token set" if auth_token() else "NO TOKEN (control endpoints OPEN)"
    print(f"SparkDash on http://{args.host}:{args.port}  · auth: {tok}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
