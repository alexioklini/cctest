#!/usr/bin/env python3
"""SparkDash — DGX-Spark-style monitoring dashboard for the Mac mini M4 vLLM host.

Self-contained, stdlib only (no pip install). Run ON the Mac mini:

    python3 sparkdash.py            # serves on 0.0.0.0:8013
    python3 sparkdash.py --port N --vllm http://127.0.0.1:8012

Metrics are read locally:
  * GPU utilization + allocated unified memory  -> ioreg IOAccelerator PerformanceStatistics
  * CPU usage + memory + load                   -> top -l 1 / sysctl / vm_stat
  * thermal pressure                            -> pmset -g therm (no sudo)
  * vLLM serving stats                          -> Prometheus /metrics on the vLLM port
"""
import argparse
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
    out = _run(["sysctl", "-n", key]).strip()
    return out


def host_info():
    return {
        "model": _sysctl("hw.model") or "Mac",
        "chip": _sysctl("machdep.cpu.brand_string") or "Apple Silicon",
        "ncpu": int(_sysctl("hw.ncpu") or 0),
        "memsize": int(_sysctl("hw.memsize") or 0),
        "uptime": _run(["uptime"]).strip(),
    }


def gpu_stats():
    """GPU utilization % and allocated unified memory (bytes) from ioreg."""
    out = _run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"])
    util = mem = 0
    m = re.search(r'"Device Utilization %"=(\d+)', out)
    if m:
        util = int(m.group(1))
    m = re.search(r'"In use system memory"=(\d+)', out)
    if m:
        mem = int(m.group(1))
    return {"util": util, "alloc_mem": mem}


def cpu_mem_stats():
    """CPU usage %, used/total memory, load averages from top + sysctl."""
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


def thermal_level():
    """Best-effort thermal status without sudo. Returns a label + 0-3 level."""
    out = _run(["pmset", "-g", "therm"])
    # CPU_Scheduler_Limit / CPU_Speed_Limit < 100 indicates throttling.
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", out)
    if m:
        limit = int(m.group(1))
        if limit >= 100:
            return {"label": "Nominal", "level": 0, "speed_limit": limit}
        if limit >= 75:
            return {"label": "Fair", "level": 1, "speed_limit": limit}
        if limit >= 50:
            return {"label": "Serious", "level": 2, "speed_limit": limit}
        return {"label": "Critical", "level": 3, "speed_limit": limit}
    return {"label": "Nominal", "level": 0, "speed_limit": 100}


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

# previous counter snapshot for rate computation
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
                m = re.search(r'model_name="([^"]+)"', line)
                if m:
                    model = m.group(1)
                num = line.rsplit(" ", 1)[-1]
                try:
                    vals[key] = float(num)
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
    return {
        "ts": time.time(),
        "host": host,
        "gpu": {
            "util": gpu["util"],
            "alloc_mem": gpu["alloc_mem"],
        },
        "cpu": cm["cpu"],
        "load": cm["load"],
        "mem": {"used": cm["mem_used"], "total": host["memsize"]},
        "thermal": thermal_level(),
        "vllm": vllm_stats(vllm_url),
    }


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    vllm_url = "http://127.0.0.1:8012"

    def log_message(self, *a):
        pass  # quiet

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
        elif self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>DGX Spark · System Dashboard</title>
<style>
  :root{
    --bg:#0b0f0c; --panel:#121712; --panel2:#0e130e; --line:#1d271d;
    --green:#76b900; --green-dim:#4d7a00; --text:#e6efe6; --muted:#7e8c7e;
    --red:#e0533d; --amber:#e0a93d;
  }
  *{box-sizing:border-box}
  body{margin:0;background:
       radial-gradient(1200px 600px at 80% -10%, #15391544, transparent 60%),
       var(--bg);
       color:var(--text);font-family:"SF Pro Display",-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased}
  header{display:flex;align-items:center;gap:14px;padding:20px 28px;border-bottom:1px solid var(--line)}
  .logo{display:flex;align-items:center;gap:10px;font-weight:700;letter-spacing:.5px;font-size:18px}
  .logo .mark{width:14px;height:14px;background:var(--green);border-radius:3px;box-shadow:0 0 14px var(--green)}
  .sub{color:var(--muted);font-size:13px}
  .spacer{flex:1}
  .pill{font-size:12px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:5px 12px}
  .pill b{color:var(--green)}
  .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);
       box-shadow:0 0 8px var(--green);margin-right:6px;vertical-align:middle}
  .dot.off{background:var(--red);box-shadow:0 0 8px var(--red)}
  main{padding:24px 28px;max-width:1200px;margin:0 auto}
  .grid{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}
  .tile{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
        border-radius:16px;padding:20px 22px;position:relative;overflow:hidden}
  .tile h3{margin:0 0 4px;font-size:13px;font-weight:600;color:var(--muted);
           text-transform:uppercase;letter-spacing:1.2px}
  .tile .big{font-size:42px;font-weight:700;line-height:1.1;margin-top:6px}
  .tile .big small{font-size:18px;color:var(--muted);font-weight:500}
  .tile .meta{color:var(--muted);font-size:13px;margin-top:6px}
  .bar{height:8px;border-radius:6px;background:#0a0d0a;margin-top:16px;overflow:hidden;border:1px solid var(--line)}
  .bar>span{display:block;height:100%;background:linear-gradient(90deg,var(--green-dim),var(--green));
            border-radius:6px;transition:width .5s ease}
  .bar>span.warn{background:linear-gradient(90deg,#7a5a00,var(--amber))}
  .bar>span.crit{background:linear-gradient(90deg,#7a1d00,var(--red))}
  /* radial gauge */
  .gauge{display:flex;align-items:center;gap:22px}
  .ring{--p:0;--col:var(--green);width:118px;height:118px;border-radius:50%;flex:none;
        background:conic-gradient(var(--col) calc(var(--p)*1%), #18211833 0);
        display:grid;place-items:center;position:relative;transition:--p .6s}
  .ring::before{content:"";position:absolute;inset:11px;border-radius:50%;background:var(--panel)}
  .ring .v{position:relative;font-size:26px;font-weight:700}
  .ring .v small{font-size:13px;color:var(--muted)}
  .span2{grid-column:span 2}
  .vllm{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:6px}
  .stat{background:#0a0d0a;border:1px solid var(--line);border-radius:12px;padding:14px}
  .stat .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.8px}
  .stat .n{font-size:26px;font-weight:700;margin-top:6px}
  .stat .n small{font-size:13px;color:var(--muted);font-weight:500}
  .model{font-size:13px;color:var(--green);margin-bottom:14px;font-family:ui-monospace,Menlo,monospace}
  footer{color:var(--muted);font-size:12px;text-align:center;padding:18px}
  @media(max-width:780px){.grid{grid-template-columns:1fr}.span2{grid-column:span 1}.vllm{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<header>
  <div class="logo"><span class="mark"></span> DGX <span style="color:var(--green)">SPARK</span></div>
  <span class="sub" id="hostsub">System Dashboard</span>
  <div class="spacer"></div>
  <span class="pill" id="modelpill">—</span>
  <span class="pill"><span class="dot" id="livedot"></span><span id="livetxt">live</span></span>
</header>
<main>
  <div class="grid">
    <!-- GPU -->
    <div class="tile">
      <h3>GPU Utilization</h3>
      <div class="gauge">
        <div class="ring" id="gpuring"><div class="v"><span id="gpuval">0</span><small>%</small></div></div>
        <div>
          <div class="meta">Apple GPU · Metal</div>
          <div class="big" style="font-size:26px"><span id="gpumem">0</span><small> GB allocated</small></div>
          <div class="meta">unified memory in use by GPU</div>
        </div>
      </div>
    </div>
    <!-- Unified memory -->
    <div class="tile">
      <h3>Unified Memory</h3>
      <div class="big"><span id="memused">0</span><small> / <span id="memtotal">0</span> GB</small></div>
      <div class="meta" id="mempct">0% used</div>
      <div class="bar"><span id="membar" style="width:0%"></span></div>
    </div>
    <!-- CPU -->
    <div class="tile">
      <h3>CPU Utilization</h3>
      <div class="big"><span id="cpuval">0</span><small> %</small></div>
      <div class="meta" id="loadtxt">load — — —</div>
      <div class="bar"><span id="cpubar" style="width:0%"></span></div>
    </div>
    <!-- Thermal -->
    <div class="tile">
      <h3>Thermal / Power</h3>
      <div class="big" id="thermlabel" style="font-size:34px">Nominal</div>
      <div class="meta" id="thermmeta">CPU speed limit 100%</div>
      <div class="bar"><span id="thermbar" style="width:8%"></span></div>
    </div>
    <!-- vLLM panel -->
    <div class="tile span2">
      <h3>vLLM Inference Engine</h3>
      <div class="model" id="vllmmodel">—</div>
      <div class="vllm">
        <div class="stat"><div class="l">Running</div><div class="n" id="v_run">0</div></div>
        <div class="stat"><div class="l">Waiting</div><div class="n" id="v_wait">0</div></div>
        <div class="stat"><div class="l">Generation</div><div class="n"><span id="v_tps">0</span><small> tok/s</small></div></div>
        <div class="stat"><div class="l">KV Cache</div><div class="n"><span id="v_kv">0</span><small> %</small></div></div>
        <div class="stat"><div class="l">Prompt</div><div class="n"><span id="v_ptps">0</span><small> tok/s</small></div></div>
        <div class="stat"><div class="l">Prefix Hit</div><div class="n"><span id="v_prefix">0</span><small> %</small></div></div>
        <div class="stat"><div class="l">Gen tokens</div><div class="n" id="v_gentot" style="font-size:20px">0</div></div>
        <div class="stat"><div class="l">Prompt tokens</div><div class="n" id="v_ptot" style="font-size:20px">0</div></div>
      </div>
    </div>
  </div>
  <footer id="uptime">—</footer>
</main>
<script>
const GB = 1024**3;
const fmt = n => n>=1e9 ? (n/1e9).toFixed(2)+"B" : n>=1e6 ? (n/1e6).toFixed(1)+"M" : n>=1e3 ? (n/1e3).toFixed(1)+"K" : ""+n;
function cls(p){ return p>=90?"crit":p>=70?"warn":""; }
function ringcol(p){ return p>=90?"var(--red)":p>=70?"var(--amber)":"var(--green)"; }

async function tick(){
  let d;
  try{ d = await (await fetch("/api/metrics",{cache:"no-store"})).json(); }
  catch(e){ document.getElementById("livedot").classList.add("off");
            document.getElementById("livetxt").textContent="offline"; return; }
  document.getElementById("livedot").classList.remove("off");
  document.getElementById("livetxt").textContent="live";

  // header
  document.getElementById("hostsub").textContent = d.host.chip + " · " + d.host.ncpu + " cores";

  // GPU
  const gu = d.gpu.util;
  const ring = document.getElementById("gpuring");
  ring.style.setProperty("--p", gu);
  ring.style.setProperty("--col", ringcol(gu));
  document.getElementById("gpuval").textContent = gu;
  document.getElementById("gpumem").textContent = (d.gpu.alloc_mem/GB).toFixed(1);

  // Unified memory
  const mu = d.mem.used/GB, mt = d.mem.total/GB, mp = mt? mu/mt*100 : 0;
  document.getElementById("memused").textContent = mu.toFixed(1);
  document.getElementById("memtotal").textContent = mt.toFixed(0);
  document.getElementById("mempct").textContent = mp.toFixed(0)+"% used";
  const mb = document.getElementById("membar");
  mb.style.width = mp+"%"; mb.className = cls(mp);

  // CPU
  document.getElementById("cpuval").textContent = d.cpu;
  document.getElementById("loadtxt").textContent = "load " + d.load.map(x=>x.toFixed(2)).join("  ");
  const cb = document.getElementById("cpubar");
  cb.style.width = Math.min(100,d.cpu)+"%"; cb.className = cls(d.cpu);

  // Thermal
  document.getElementById("thermlabel").textContent = d.thermal.label;
  document.getElementById("thermmeta").textContent = "CPU speed limit "+d.thermal.speed_limit+"%";
  const tb = document.getElementById("thermbar");
  const tlvl = [8,40,70,100][d.thermal.level];
  tb.style.width = tlvl+"%"; tb.className = d.thermal.level>=3?"crit":d.thermal.level>=1?"warn":"";

  // vLLM
  const v = d.vllm, pill=document.getElementById("modelpill");
  if(v.up){
    document.getElementById("vllmmodel").textContent = "● "+v.model+"   (max_model_len served)";
    pill.innerHTML = "<b>"+v.model+"</b>";
    document.getElementById("v_run").textContent = v.running;
    document.getElementById("v_wait").textContent = v.waiting;
    document.getElementById("v_tps").textContent = v.gen_tps;
    document.getElementById("v_ptps").textContent = v.prompt_tps;
    document.getElementById("v_kv").textContent = v.kv_cache;
    document.getElementById("v_prefix").textContent = v.prefix_hit_rate;
    document.getElementById("v_gentot").textContent = fmt(v.total_gen_tokens);
    document.getElementById("v_ptot").textContent = fmt(v.total_prompt_tokens);
  } else {
    document.getElementById("vllmmodel").textContent = "● engine offline";
    pill.textContent = "vLLM offline";
  }

  document.getElementById("uptime").textContent = d.host.model + " · " + d.host.uptime;
}
tick(); setInterval(tick, 2000);
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
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"SparkDash on http://{args.host}:{args.port}  (vLLM: {args.vllm})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
