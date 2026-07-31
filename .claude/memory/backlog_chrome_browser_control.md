---
name: Browser Automation via Electron (Puppeteer + Chrome Control)
description: Backlog — Puppeteer next step after web_fetch/exa via Electron; then full Chrome control via Extension
type: project
originSessionId: f3d716e7-c1a7-4310-8546-7bfc3ccfc785
---
Progressive browser automation roadmap via `client_proxy_tools` + Electron.

**Why:** Puppeteer (server-side headless) is already wired as MCP server. Next step: run Puppeteer locally in Electron so it controls the user's real browser context. All building blocks exist.

**How to apply:** Implement in order — Puppeteer first (simpler), Chrome Extension after if full DOM control needed.

---

## Progression

### Already done
- `web_fetch` / `exa_search` → run locally via Electron `client_proxy_tools` ✓
- Puppeteer MCP server → runs headless on Brain server, screenshots land as artifacts ✓
- Blob→artifact pipeline covers all MIME types ✓

### Next: Puppeteer via Electron (local, per-user)
- Electron spawns local Puppeteer process (has real browser context, cookies, logins)
- Brain emits `proxy_tool` SSE → Electron runs Puppeteer → result + blobs back via `POST /v1/chat/proxy-tool-result`
- No separate MCP server needed — fits existing proxy_tool flow
- Screenshots automatically land as artifacts via blob extraction

### After: Chrome Extension control
- Electron discovers Claude-in-Chrome Extension port
- Same proxy_tool flow, but forwards to Extension instead of Puppeteer
- Full DOM interaction, real user session

## What's new for Puppeteer step
- Electron-side: spawn/manage local Puppeteer, handle `proxy_tool` for `puppeteer__*` tools
- Brain-side: add `puppeteer__*` to `client_proxy_tools` list
- Graceful fallback to server-side Puppeteer MCP when Electron not connected

## Implementation shortcut (ready to build)
Brain-Server muss nichts Neues — er schickt bereits `proxy_tool` SSE für alle Tools in `client_proxy_tools`.
Nur zwei Schritte nötig:
1. `puppeteer__screenshot`, `puppeteer__navigate` etc. in `config.json → client_proxy_tools` eintragen
2. Electron: `proxy_tool` Events für `puppeteer__*` abfangen → lokalen Puppeteer (Node-Modul, bereits in Electron verfügbar) aufrufen → Ergebnis als Blob zurück via `POST /v1/chat/proxy-tool-result`

Kein neuer MCP-Server, kein neues Brain-API — rein Electron-seitige Erweiterung analog `web_fetch`.
