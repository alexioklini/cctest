#!/usr/bin/env python3
"""SparkDash — DGX-Spark-style monitoring dashboard for the Mac mini M4 vLLM host.

Self-contained, stdlib only (no pip install). Run ON the Mac mini:

    python3 sparkdash.py            # serves on 0.0.0.0:8013
    python3 sparkdash.py --port N --vllm http://127.0.0.1:8012

Metrics are read locally:
  * GPU utilization + allocated unified memory  -> ioreg IOAccelerator PerformanceStatistics
  * Per-core + aggregate CPU                     -> host_processor_info (ctypes, no sudo)
  * Memory + swap                                -> top / sysctl vm.swapusage
  * thermal pressure                             -> pmset -g therm (no sudo)
  * vLLM serving stats                           -> Prometheus /metrics on the vLLM port
"""
import argparse
import ctypes
import ctypes.util
import json
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import URLError

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
_prev = {"t": None, "prompt_tokens": 0.0, "generation_tokens": 0.0}


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
    gen_tps = prompt_tps = 0.0
    if _prev["t"] is not None:
        dt = now - _prev["t"]
        if dt > 0:
            gen_tps = max(0.0, (vals.get("generation_tokens", 0) - _prev["generation_tokens"]) / dt)
            prompt_tps = max(0.0, (vals.get("prompt_tokens", 0) - _prev["prompt_tokens"]) / dt)
    _prev.update(t=now,
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


def collect(vllm_url):
    gpu = gpu_stats()
    cm = cpu_mem_stats()
    host = host_info()
    vstats = vllm_stats(vllm_url)
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
        "vllm": vstats,
        "vllm_mem": vllm_memory(vstats.get("model") if vstats.get("up") else None),
    }


# ---------------------------------------------------------------------------
# Inference activity log — tail + parse vLLM's own engine-stats lines
# ---------------------------------------------------------------------------
# vLLM logs every ~10s (loggers.py):
#   Engine 000: Avg prompt throughput: 2.7 tokens/s, Avg generation throughput:
#   9.5 tokens/s, Running: 1 reqs, Waiting: 0 reqs, GPU KV cache usage: 0.1%,
#   Prefix cache hit rate: 94.1%
import os
import threading
from collections import deque

_VLLM_LOG = os.path.expanduser("~/Library/Logs/vllm-metal.log")
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


def _activity_tailer():
    """Background thread: follow the vLLM log, record non-idle engine-stats
    events to memory + a rolling JSONL file (with current GPU memory)."""
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

    pos = 0
    try:
        pos = os.path.getsize(_VLLM_LOG)  # start at end, only new lines
    except OSError:
        pos = 0
    while True:
        try:
            size = os.path.getsize(_VLLM_LOG)
            if size < pos:           # log rotated/truncated
                pos = 0
            if size > pos:
                with open(_VLLM_LOG, "r", errors="replace") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                for line in chunk.splitlines():
                    ev = _parse_stats_line(line)
                    if not ev:
                        continue
                    # only record when something happened (skip idle zero rows,
                    # but always keep the first row after activity to mark the end)
                    busy = ev["running"] or ev["waiting"] or ev["gen_tps"] > 0 or ev["prompt_tps"] > 0
                    if not busy:
                        if _activity and _activity[-1].get("busy"):
                            ev["busy"] = False
                        else:
                            continue
                    else:
                        ev["busy"] = True
                    ev["gpu_mem_gb"] = round(gpu_stats()["alloc_mem"] / 1024**3, 2)
                    with _activity_lock:
                        _activity.append(ev)
                    try:
                        with open(_ACTIVITY_FILE, "a") as wf:
                            wf.write(json.dumps(ev) + "\n")
                    except OSError:
                        pass
        except Exception:
            pass
        time.sleep(2)


def recent_activity(n=60):
    with _activity_lock:
        return list(_activity)[-n:]


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    vllm_url = "http://127.0.0.1:8012"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/metrics"):
            self._send(200, json.dumps(collect(self.vllm_url)), "application/json")
        elif self.path.startswith("/api/activity"):
            self._send(200, json.dumps(recent_activity(80)), "application/json")
        elif self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")


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
  .restab .chip.active{background:var(--panel2);color:var(--text)}
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
  footer{color:var(--muted);font-size:12px;text-align:center;padding:0 0 22px}
  @media(max-width:1100px){main{grid-template-columns:1fr}.vgrid{grid-template-columns:repeat(4,1fr)}}
</style>
</head>
<body>
<header>
  <div class="welcome">👋 Welcome</div>
  <div class="title">Your DGX Dashboard</div>
  <nav>
    <a class="active">Home</a><a>Settings</a><a>Docs ↗</a><a>Forums ↗</a><a>Resources ↗</a>
    <span class="livepill"><span class="dot" id="livedot"></span><span id="livetxt">live</span></span>
  </nav>
</header>
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
      <span class="chip">☰ Processes</span>
      <span class="chip active">◷ Resources</span>
      <span class="chip">▣ File Systems</span>
    </div>
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
    <h4>Inference Activity <span class="hint">(vLLM engine stats · newest first · logged to ~/sparkdash-activity.jsonl)</span></h4>
    <div class="actlog">
      <table class="act">
        <thead><tr>
          <th>Time</th><th>State</th><th>Gen t/s</th><th>Prompt t/s</th>
          <th>Running</th><th>Waiting</th><th>KV %</th><th>Prefix %</th><th>GPU mem</th>
        </tr></thead>
        <tbody id="actbody"><tr class="idle"><td colspan="9">waiting for activity…</td></tr></tbody>
      </table>
    </div>
  </div>
</main>
<footer id="uptime">—</footer>

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

  document.getElementById("uptime").textContent=d.host.model+" · "+d.host.chip+" · "+d.host.uptime;
}

async function tickActivity(){
  let rows;
  try{ rows=await (await fetch("/api/activity",{cache:"no-store"})).json(); }
  catch(e){ return; }
  const body=document.getElementById("actbody");
  if(!rows.length){ body.innerHTML='<tr class="idle"><td colspan="9">no activity recorded yet</td></tr>'; return; }
  body.innerHTML = rows.slice().reverse().map(r=>{
    const cls = r.busy ? "busy" : "idle";
    const badge = r.busy ? '<span class="badge run">running</span>' : '<span class="badge idle">idle</span>';
    return `<tr class="${cls}"><td>${r.ts}</td><td>${badge}</td>`
      +`<td>${r.gen_tps.toFixed(1)}</td><td>${r.prompt_tps.toFixed(1)}</td>`
      +`<td>${r.running}</td><td>${r.waiting}</td>`
      +`<td>${r.kv_cache.toFixed(1)}</td><td>${r.prefix_hit.toFixed(1)}</td>`
      +`<td>${r.gpu_mem_gb!=null?r.gpu_mem_gb.toFixed(1)+" GB":"—"}</td></tr>`;
  }).join("");
}

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
    ap.add_argument("--vllm", default="http://127.0.0.1:8012")
    args = ap.parse_args()
    Handler.vllm_url = args.vllm
    threading.Thread(target=_activity_tailer, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SparkDash on http://{args.host}:{args.port}  (vLLM: {args.vllm})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
