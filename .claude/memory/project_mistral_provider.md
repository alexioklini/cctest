---
name: Mistral Provider Integration
description: Mistral SDK provider type added (type "mistral"), replicates Vibe CLI headers/metadata for Pro subscription key access
type: project
---

Mistral provider type implemented (2026-04-05) — third api_type alongside "openai" and "anthropic".

Uses official `mistralai` Python SDK (`from mistralai.client import Mistral`) instead of raw HTTP, replicating Vibe CLI behavior:
- `user-agent: mistral-client-python/Mistral-Vibe/2.5.0`
- `x-affinity: {session_id}`
- `metadata: {agent_entrypoint: "cli", client_name: "vibe_cli", ...}`

**Why:** Pro subscription API key (`APIKeyScope.vibe`) works with the SDK since requests look like they come from the official Vibe CLI. Testing showed the key works even without Vibe headers, but we replicate them for safety.

**How to apply:** Provider config uses `"type": "mistral"` with `base_url: "https://api.mistral.ai/v1"`. Models: `devstral-small-latest` ($0.10/$0.30), `mistral-vibe-cli-latest` aka Devstral 2 ($0.40/$2.00). Auto-discovered models from Mistral API land under provider "Mistral Pro".

**Key files changed:** `claude_cli.py` (~200 lines: `_handle_mistral_response`, `_get_mistral_vibe_headers/metadata`, `_create_mistral_client`), `server.py` (warmup, multimodal, tool format branches), `web/index.html` (provider type dropdown), `config.json` (provider + model entries).
