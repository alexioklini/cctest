# Feature Proposal: Docker Deployment

**Status:** Proposed
**Priority:** Medium
**Effort:** Medium (4-6 days)
**Affects:** New Dockerfile, docker-compose.yml, server.py (minor), brain.py (detect container)

---

## Problem

Brain Agent is currently macOS-specific in several ways:

- **launchd** manages the server and QMD daemons — Linux has no launchd
- **oMLX** requires Apple Silicon (MLX framework) — no Linux equivalent bundled
- **CLIProxyAPI** installed via Homebrew — not available on all platforms
- **brew services** used for process management — not portable
- **brain.py start/stop** generates `.plist` files for launchd

This means Brain Agent cannot run on:

- Linux servers or cloud VMs (AWS, GCP, Azure, Hetzner)
- CI/CD pipelines for automated testing
- Docker-based home server setups (Unraid, Synology, TrueNAS)
- Team deployments where members use different OSes

Users who want to self-host Brain Agent on a Linux VPS have no supported path.

---

## Proposed Solution

A multi-stage Dockerfile and docker-compose.yml that packages the full Brain Agent
stack (server + QMD + optional Telegram) into containers with proper volume mounts
for persistent data.

---

## Architecture

### Container Layout

```text
┌──────────────────────────────────────────────────────────────────────┐
│  docker-compose.yml                                                   │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  brain-server (port 8420)                                       │ │
│  │  ┌──────────────────────────────────────────────────────────┐   │ │
│  │  │  server.py + claude_cli.py                               │   │ │
│  │  │  - HTTP API + SSE streaming                              │   │ │
│  │  │  - All 20+ tools (file ops, shell, web, Gmail, etc.)     │   │ │
│  │  │  - Scheduler (background thread)                         │   │ │
│  │  │  - Telegram bot (in-process thread, if configured)       │   │ │
│  │  └──────────────────────────────────────────────────────────┘   │ │
│  │  Volumes: config.json, agents/, web/                            │ │
│  └──────────────────────┬──────────────────────────────────────────┘ │
│                          │ HTTP :8181                                 │
│  ┌──────────────────────┴──────────────────────────────────────────┐ │
│  │  brain-qmd (port 8181)                                          │ │
│  │  ┌──────────────────────────────────────────────────────────┐   │ │
│  │  │  qmd mcp --http --port 8181                              │   │ │
│  │  │  - BM25 + vector search + LLM reranking                  │   │ │
│  │  │  - embeddinggemma model (auto-downloaded)                 │   │ │
│  │  └──────────────────────────────────────────────────────────┘   │ │
│  │  Volumes: agents/ (shared, read), qmd-cache/                    │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  Network: brain-net (bridge)                                         │
│  brain-server → brain-qmd:8181 (internal)                            │
│  Host → brain-server:8420 (exposed)                                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### Volume Mounts

```text
Host filesystem                    Container mount              Purpose
──────────────────────────────────────────────────────────────────────────
./config.json                      /app/config.json             Provider keys, settings
./agents/                          /app/agents/                 Agent data, memory, DBs
./web/                             /app/web/                    Web UI (for dev)
brain-qmd-cache (named volume)     /root/.cache/qmd/            QMD index + embeddings
```

---

## Dockerfile

```text
┌──────────────────────────────────────────────────────────────────────┐
│  Dockerfile                                                           │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  # Stage 1: Base image with Python                                   │
│  FROM python:3.12-slim AS base                                       │
│                                                                      │
│  RUN apt-get update && apt-get install -y --no-install-recommends \   │
│      git curl sqlite3 && \                                           │
│      rm -rf /var/lib/apt/lists/*                                     │
│                                                                      │
│  WORKDIR /app                                                        │
│                                                                      │
│  # Stage 2: Install dependencies                                     │
│  FROM base AS deps                                                   │
│  COPY requirements.txt .                                             │
│  RUN pip install --no-cache-dir -r requirements.txt                  │
│                                                                      │
│  # Stage 3: Application                                              │
│  FROM deps AS app                                                    │
│  COPY server.py claude_cli.py client.py telegram.py tools.md ./      │
│  COPY web/ ./web/                                                    │
│                                                                      │
│  # Default config location                                           │
│  VOLUME ["/app/config.json", "/app/agents"]                          │
│                                                                      │
│  EXPOSE 8420                                                         │
│                                                                      │
│  # Health check                                                      │
│  HEALTHCHECK --interval=30s --timeout=5s --retries=3 \               │
│      CMD curl -f http://localhost:8420/v1/status || exit 1           │
│                                                                      │
│  # Run server directly (no launchd, no brain.py)                     │
│  CMD ["python3", "server.py"]                                        │
│                                                                      │
│  ──────────────────────────────────────────────────────────           │
│                                                                      │
│  # QMD image (separate Dockerfile.qmd)                               │
│  FROM golang:1.22-alpine AS qmd-build                                │
│  RUN go install github.com/tobi/qmd@latest                           │
│                                                                      │
│  FROM alpine:3.20 AS qmd                                             │
│  COPY --from=qmd-build /go/bin/qmd /usr/local/bin/qmd               │
│  VOLUME ["/data/agents", "/root/.cache/qmd"]                         │
│  EXPOSE 8181                                                         │
│  CMD ["qmd", "mcp", "--http", "--port", "8181"]                      │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## docker-compose.yml

```text
┌──────────────────────────────────────────────────────────────────────┐
│  docker-compose.yml                                                   │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  version: "3.8"                                                      │
│                                                                      │
│  services:                                                           │
│    server:                                                           │
│      build: .                                                        │
│      container_name: brain-server                                    │
│      ports:                                                          │
│        - "8420:8420"                                                  │
│      volumes:                                                        │
│        - ./config.json:/app/config.json:ro                           │
│        - ./agents:/app/agents                                        │
│        - ./web:/app/web:ro                                           │
│        - ./tools.md:/app/tools.md:ro                                 │
│      environment:                                                    │
│        - BRAIN_QMD_HOST=qmd                                          │
│        - BRAIN_QMD_PORT=8181                                         │
│        - BRAIN_DOCKER=1                                              │
│      depends_on:                                                     │
│        qmd:                                                          │
│          condition: service_healthy                                   │
│      restart: unless-stopped                                         │
│      networks:                                                       │
│        - brain-net                                                   │
│                                                                      │
│    qmd:                                                              │
│      build:                                                          │
│        context: .                                                    │
│        dockerfile: Dockerfile.qmd                                    │
│      container_name: brain-qmd                                       │
│      volumes:                                                        │
│        - ./agents:/data/agents:ro                                    │
│        - qmd-cache:/root/.cache/qmd                                  │
│      healthcheck:                                                    │
│        test: ["CMD", "nc", "-z", "localhost", "8181"]                │
│        interval: 10s                                                 │
│        timeout: 5s                                                   │
│        retries: 5                                                    │
│      restart: unless-stopped                                         │
│      networks:                                                       │
│        - brain-net                                                   │
│                                                                      │
│  volumes:                                                            │
│    qmd-cache:                                                        │
│                                                                      │
│  networks:                                                           │
│    brain-net:                                                        │
│      driver: bridge                                                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Terminal Output: docker compose up

```text
$ docker compose up -d
[+] Building 45.2s (14/14) FINISHED
 => [server deps 1/2] COPY requirements.txt .                    0.1s
 => [server deps 2/2] RUN pip install --no-cache-dir ...         8.3s
 => [server app 1/2] COPY server.py claude_cli.py ...            0.2s
 => [qmd qmd-build 1/1] RUN go install github.com/tobi/qmd ...  32.1s
 => [qmd 1/1] COPY --from=qmd-build /go/bin/qmd ...             0.1s
[+] Running 3/3
 ✔ Network brain-net        Created                              0.1s
 ✔ Container brain-qmd      Healthy                              12.3s
 ✔ Container brain-server   Started                              12.5s

$ docker compose ps
NAME             IMAGE          STATUS                   PORTS
brain-server     brain-server   Up 5 seconds (healthy)   0.0.0.0:8420->8420/tcp
brain-qmd        brain-qmd      Up 17 seconds (healthy)  8181/tcp

$ docker compose logs server --tail 5
brain-server  | [INFO] Brain Agent server starting on 0.0.0.0:8420
brain-server  | [INFO] Docker mode: QMD at qmd:8181
brain-server  | [INFO] Loaded 3 agents: main, Researcher, Reporter
brain-server  | [INFO] QMD connected: 3 collections, 47 documents indexed
brain-server  | [INFO] Server ready — open http://localhost:8420

$ curl -s http://localhost:8420/v1/status | python3 -m json.tool
{
    "status": "ok",
    "version": "1.6.0",
    "agents": 3,
    "services": {
        "qmd": "connected",
        "scheduler": "running"
    }
}
```

---

## Workflow: First-Time Setup

### Step 1: Clone Repository

```text
$ git clone https://github.com/user/brain-agent.git
$ cd brain-agent
```

### Step 2: Configure

```text
$ cp config.example.json config.json
$ nano config.json

# Minimal config — just need one provider:
{
  "server": {"host": "0.0.0.0", "port": 8420},
  "providers": {
    "anthropic": {
      "base_url": "https://api.anthropic.com/v1",
      "api_key": "sk-ant-...",
      "type": "anthropic",
      "default_model": "claude-sonnet-4-6"
    }
  },
  "default_provider": "anthropic"
}
```

### Step 3: Start

```text
$ docker compose up -d
```

### Step 4: Use

```text
# Open in browser
open http://localhost:8420

# Or connect from any machine on the network
open http://192.168.1.100:8420
```

### Step 5: Verify Everything Works

```text
$ docker compose exec server python3 -c "
import json, urllib.request
r = urllib.request.urlopen('http://localhost:8420/v1/status')
print(json.loads(r.read()))
"
{'status': 'ok', 'version': '1.6.0', 'agents': 3, ...}

# Check QMD
$ docker compose exec server python3 -c "
import json, urllib.request
r = urllib.request.urlopen('http://qmd:8181/health')
print(json.loads(r.read()))
"
{'status': 'ok', 'collections': 3}
```

---

## Code Changes Required

### server.py: Docker-Aware QMD Connection

```text
Current:  QMD_HOST = "127.0.0.1"
          QMD_PORT = 8181

Docker:   QMD_HOST = os.environ.get("BRAIN_QMD_HOST", "127.0.0.1")
          QMD_PORT = int(os.environ.get("BRAIN_QMD_PORT", "8181"))
```

### server.py: Skip launchd in Docker

```text
if os.environ.get("BRAIN_DOCKER"):
    # Skip launchd plist generation
    # Skip brew service checks
    # Use /app/ as base path instead of script __file__ location
```

### brain.py: Docker Compose Integration

```text
$ python3 brain.py docker up      # docker compose up -d
$ python3 brain.py docker down    # docker compose down
$ python3 brain.py docker logs    # docker compose logs -f
$ python3 brain.py docker status  # docker compose ps + health checks
```

### New File: requirements.txt

```text
# Server dependencies (stdlib-only core, optional extras)
rich>=13.0           # TUI only
prompt_toolkit>=3.0  # TUI only
# Note: server.py and claude_cli.py use stdlib only
```

---

## What About oMLX?

oMLX requires Apple Silicon and the MLX framework. In Docker on Linux, there are
three options:

### Option A: External oMLX (Recommended for Mac hosts)

```text
┌────────────────────┐     ┌────────────────────────────┐
│  Docker containers │     │  macOS host                │
│  ┌──────────────┐  │     │  ┌──────────────────────┐  │
│  │ brain-server ├──┼─────┼──▶ oMLX on port 8000    │  │
│  └──────────────┘  │     │  └──────────────────────┘  │
└────────────────────┘     └────────────────────────────┘

config.json:
  "omlx": {
    "base_url": "http://host.docker.internal:8000/v1",
    "api_key": "",
    "type": "openai"
  }
```

### Option B: GPU Passthrough (Linux with NVIDIA)

```text
docker-compose.override.yml:
  services:
    ollama:
      image: ollama/ollama:latest
      deploy:
        resources:
          reservations:
            devices:
              - driver: nvidia
                count: 1
                capabilities: [gpu]
      ports:
        - "11434:11434"
```

### Option C: No Local Inference

Use cloud providers only (Anthropic, MiniMax). Local inference is optional —
Brain Agent works fine with just cloud API keys.

---

## Persistent Data

All persistent data lives outside the containers via volumes:

| Data                | Location         | Volume Mount       | Survives Rebuild |
|--------------------|------------------|--------------------|------------------|
| Provider config     | config.json      | bind mount         | Yes              |
| Agent data          | agents/          | bind mount         | Yes              |
| Chat history        | agents/main/chats.db | bind mount (via agents/) | Yes     |
| Scheduled tasks     | agents/main/scheduler.db | bind mount (via agents/) | Yes  |
| Memory files        | agents/*/\*.md   | bind mount (via agents/) | Yes     |
| QMD index           | ~/.cache/qmd/    | named volume       | Yes              |
| QMD embeddings      | ~/.cache/qmd/    | named volume       | Yes              |
| Web UI              | web/             | bind mount (dev)   | Yes              |

---

## Environment Variables

| Variable           | Default          | Description                           |
|-------------------|------------------|---------------------------------------|
| `BRAIN_DOCKER`     | (unset)          | Set to `1` to enable Docker mode      |
| `BRAIN_QMD_HOST`   | `127.0.0.1`      | QMD hostname (set to `qmd` in compose)|
| `BRAIN_QMD_PORT`   | `8181`           | QMD port                              |
| `BRAIN_HOST`       | `0.0.0.0`        | Server bind address                   |
| `BRAIN_PORT`       | `8420`           | Server port                           |
| `BRAIN_CONFIG`     | `./config.json`  | Config file path                      |
| `BRAIN_DATA_DIR`   | `./agents`       | Agents data directory                 |

---

## Benefits

- **Platform independence**: Run on any Linux server, cloud VM, or NAS
- **Reproducible setup**: `docker compose up` gets you a working system in minutes
- **Easy updates**: `docker compose pull && docker compose up -d`
- **Isolation**: No Python version conflicts, no system dependency issues
- **Team deployment**: Share docker-compose.yml, each team member adds their own config.json
- **CI/CD testing**: Spin up Brain Agent in GitHub Actions for integration tests
- **Backup**: Everything in `agents/` — just tar and ship

## Trade-offs

- **No launchd auto-start**: Need Docker daemon running, or systemd unit for Docker Compose
  - Mitigation: `restart: unless-stopped` handles container restarts automatically
- **No oMLX**: Apple Silicon MLX models require macOS host (see options above)
- **Docker overhead**: Small memory/CPU overhead from containerization (~50MB baseline)
- **File permissions**: Volume mount UID/GID may need mapping for non-root hosts
- **Networking**: `host.docker.internal` needed to reach host services (oMLX, CLIProxyAPI)

## Effort Estimate

| Component               | Effort  |
|------------------------|---------|
| Dockerfile + multi-stage build | 0.5 day |
| Dockerfile.qmd          | 0.5 day |
| docker-compose.yml       | 0.5 day |
| server.py Docker mode    | 1 day   |
| Environment variable support | 0.5 day |
| brain.py docker commands | 0.5 day |
| config.example.json update | 0.25 day |
| Testing on Linux VM      | 1 day   |
| Documentation            | 0.5 day |
| **Total**               | **5.25 days** |

---

## Future Extensions

- **GitHub Container Registry**: Publish pre-built images so users skip the build step
- **Helm chart**: For Kubernetes deployments with horizontal scaling
- **ARM64 images**: Multi-arch builds for Raspberry Pi / ARM servers
- **Watchtower integration**: Auto-update containers when new images are published
- **Docker secrets**: For API keys instead of bind-mounted config.json
