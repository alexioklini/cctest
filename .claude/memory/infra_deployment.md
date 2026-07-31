---
name: Deployment Infrastructure
description: Server daemon, Cloudflare tunnel, network topology, providers for Brain Agent
type: infrastructure
related_to: [project_summary, project_sdk_gap_plan, feedback_direct_execution, project_mistral_provider, infra_inferencer]
---

## Server
- Runs as launchd daemon: `~/Library/LaunchAgents/com.brain-agent.server.plist`
- Python: `/opt/homebrew/bin/python3` (3.14)
- Binds: `0.0.0.0:8420` (LAN accessible)
- Logs: `~/.brain-agent/server.log`, `server.error.log`
- Config: `/Users/alexander/Documents/dev/cctest/config.json`
- Management: `python3 brain.py start|stop|restart|status`

## Cloudflare Tunnel
- Tunnel name: `itrmp` (ID: 13a6645d-297e-4d1c-bee6-04f996e8db74)
- Runs on: 192.168.4.65 (user: brain)
- Managed by launchd: `com.brain.cloudflare-tunnel`
- Config: `/Users/brain/.cloudflared/config.yml`

## Public URLs (via Cloudflare)
- `brain.alexklinsky.dev` → `http://192.168.1.221:8420` (Brain Agent Web UI)
- Protected by Zero Trust: email OTP to alexander.klinsky@me.com or @wienerprivatbank.com

## Network
- Mac Studio (this machine): 192.168.1.221 — runs Brain Agent server + oMLX
- Mac (192.168.4.65): runs Cloudflare tunnel, OpenClaw
- SSH access to 192.168.4.65: user=brain, key=~/.ssh/id_ed25519

## LLM Providers
- **oMLX**: local, port 8000, Anthropic API type (see infra_inferencer.md)
- **CLIProxyAPI**: local OAuth proxy, port 8317, provides Claude models without API key costs
  - Config: `/opt/homebrew/etc/cliproxyapi.conf`
  - Management panel: `http://127.0.0.1:8317/management.html`
  - Service: `brew services start/stop/restart cliproxyapi`
- **MiniMax**: cloud, `https://api.minimax.io/anthropic/v1`, M2.5/M2.7 models
- **Mistral**: SDK provider type "mistral" via official SDK and headers for Pro subscription

## Gmail
- Account: klinskybrain@gmail.com
- App Password configured in agents/main/gmail.json
- 2FA enabled on the Google account

## Telegram Bot
- Token in config.json
- Allowed user: 8336139166