---
name: infrastructure_provider_relationships
description: Relationships between local infrastructure and provider configurations
type: project
agent: main
related:
  - file: multi_agent_research_team_relationships_53ba83.md
    type: same_topic
  - file: crow_9b_relationships_part_1_b35ee3.md
    type: same_topic
last_recalled: 2026-04-08
---

# Local Infrastructure & Provider Stack — Core Relationships

## Local Inference Server (oMLX)
- **server**: omlx (port 8000) runs Crow-4B model locally
- **running_on**: M2 Max silicon (21+ day uptime; load averages 4.30/3.90/3.75)
- **uses_api_type**: anthropic (not openai) — critical for request serialization
- **persists_ssd_cache**: Fast SSD cache path already configured in plist
- **deployment_topology**: localhost + Cloudflare tunnel for remote access

## Anthropic SDK Sidecar Pattern
- **has_issue**: REST sidecar required due to SDK hooks causing SSE buffering
- **endpoints**: main agent REST sidecar routes messages via `/messages/proxy/{convId}` for playback/debugging
- **url_construction_bug**: ValueError `unknown url type: '/messages'` in sidecar init due to missing protocol in URL instantiation in ClaudeCodeSidecar.py (BASE_PORT value ignored for path-only URLs)
- **fix_required**: prepend `http://127.0.0.1:${BASE_PORT}` to `/messages` base
- **affects**: scheduled tasks like _relationship_discovery_main

## Provider Types in Use
- **omlx_local**: anthropic API type; local inference; minimal latency
- **mistral_sdk**: "mistral" provider type; replicates Vibe CLI for Pro subscription access
- **minimax**: custom provider; used by Minimax agent
- **cliproxyapi**: shares main agent’s Claude quota; high-risk for runaway loops

## Memory & Artifacts Configuration
- **memory_index**: structured markdown files in [memory/](memory/) directory
- **artifact_panel**: dynamic HTML artifacts displayed inline in Claude.ai; design parity against claude.ai verified in Chrome
- **backlog_quality**: tool results not shown in chat UI blocked by SDK hooks; provider/model UI flaky

## Known Operational Limits
- **cliproxy_quota_limit**: 5 hours shared among CLIProxyAPI users — all CROW_9B tool invocations via sidecar consume this quota
- **rest_sidecar_requirement**: to decouple streaming from protocol and stabilize tool invocation visibility

## Recommendations (from project files)
- **migrate_to_sidecar**: adopt server-side hooks + REST sidecar pattern to stabilize streaming and tool visibility
- **quota_monitoring**: add telemetry/counters for CLIProxyAPI usage to avoid silent quota exhaustion
- **provider_circuit_breakers**: implement fallback to local oMLX Crow-4B when Pro-subscription providers are rate-limited/exhausted
