---
name: CLIProxyAPI Architecture
description: User learned CLIProxyAPI's core architecture and how it differs from CLI subprocess approaches
type: technical
agent: Minimax
related:
  - file: chats-indexed/chat-58eedd4d0248-000.md
    type: same_topic
  - file: chats-indexed/chat-58eedd4d0248-001.md
    type: extends
last_recalled: 2026-04-05
  - file: chats-indexed/chat-58eedd4d0248-002.md
    type: references
  - file: anthropic_oauth_direct_sdk_6ec3bf.md
    type: references
---

CLIProxyAPI is a native Go binary that makes direct API calls to Anthropic using OAuth tokens, not a subprocess wrapper around Claude CLI. It translates OpenAI format requests to Anthropic API calls and returns responses in OpenAI format.
