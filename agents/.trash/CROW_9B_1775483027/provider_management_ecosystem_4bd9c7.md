---
name: provider_management_ecosystem
description: LLM provider integration ecosystem within CROW_9B architecture
type: project
agent: CROW_9B
related:
  - file: project_summary_arch_links_d8ce8a.md
    type: same_topic
  - file: tech_infrastructure_dependencies_cf424e.md
    type: same_topic
  - file: sdk_constraints_rule_chain_ebee58.md
    type: same_topic
---

# CROW_9B Provider Ecosystem

The CROW_9B platform integrates multiple LLM providers with distinct characteristics:

## Local Provider
- **oMLX**: Local inference server on port 8000
- Model: Crow-4B
- API Type: anthropic (critical distinction from OpenAI-compatible providers)
- Usage: Immediate, offline-capable inference

## Remote Providers
- **Mistral**: SDK provider type "mistral"
- Requires: Pro subscription API key
- Usage: Accesses Mistral's API with Pro tier features
- Quota: Separate from CLIProxyAPI but shares 5-hour Claude quota structure

## Infrastructure Awareness
- Providers are orchestrated by **infrastructure_deployment** configuration
- Management UI documented in `feedback_provider_model_sync` (known to be flaky, requires multiple attempts)

## Relationship Complexity
- Local vs remote provider decisions compete for architecture mindshare
- REST sidecar hooks must adapt to both provider types (anthropic API type vs mistral SDK)
- Provider/model synchronization UI from 2026-04 struggles with dual-provider architecture

# Frontmatter Relationships
- `project_mistral_provider` → **remote_provider_config**
- `infra_inferencer` → **local_provider_core**
- `feedback_omlx_anthropic` → **local_provider_api_type**
- `feedback_provider_model_sync` → **validation_ui_challenge**
- `project_token_fixes` → **cross_provider_validation_fix**
