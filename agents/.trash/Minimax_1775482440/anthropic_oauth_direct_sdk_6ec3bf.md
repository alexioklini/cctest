---
name: anthropic_oauth_direct_sdk
description: Anthropic OAuth works directly with official SDK
type: reference
agent: Minimax
last_recalled: 2026-04-05
related:
  - file: cliproxyapi_architecture_10a44d.md
    type: references
---

Anthropic OAuth tokens (sk-ant-oat01-...) work DIRECTLY with the official Anthropic SDK via:
  client = anthropic.Anthropic(api_key="sk-ant-oat01-...")
  client.messages.create(..., extra_headers={"anthropic-beta": "oauth-2025-04-20"})

This bypasses CLIProxyAPI. Valid models for OAuth: claude-haiku-4-5-20251001, claude-sonnet-4-5-20250929, claude-sonnet-4-6, claude-opus-4-6, etc.

Tested and confirmed working on 2026-03-26 with user alexander's Max subscription token.

Current brain-agent setup uses CLIProxyAPI (:8317) as proxy. Could be replaced with direct SDK calls if desired in future.
