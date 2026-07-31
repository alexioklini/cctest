---
name: PI SDK Sidecar for Code Mode
description: PI coding agent SDK replaces dead Anthropic SDK sidecar for code mode — Node.js on port 8422, works with OpenAI/Mistral providers
type: project
originSessionId: c6a4ddd7-2690-4bdb-b84e-0eb1b171bfb1
---
PI SDK sidecar (`pi_sidecar/pi_sidecar.ts`) replaces the Anthropic Agent SDK sidecar for code mode.

**Why:** Anthropic models/SDK dropped — `sdk_sidecar.py` (port 8421) only works with `api_type=anthropic`. Code mode was dead since `_use_sdk = session.api_type == "anthropic"` was always false.

**How to apply:**
- Code mode sessions (`session.status == "code"`) route to PI sidecar on port 8422 via `_use_pi_sdk` flag in server.py
- PI SDK uses `openai-completions` API type for both oMLX and Mistral
- Key fix: `baseUrl` must keep `/v1` suffix (PI SDK's OpenAI client appends `/chat/completions`)
- Non-OpenAI providers need `compat: { supportsStore: false, supportsDeveloperRole: false }` — Mistral rejects `store: false` with 422
- Brain Agent custom tools proxied via HTTP to `/v1/tools/call`; PI's native read/write/edit/bash run in Node.js
- Anthropic SDK sidecar (`sdk_sidecar.py`, port 8421) stays intact alongside
- Auto-started by server.py with watchdog thread, same pattern as SDK sidecar
