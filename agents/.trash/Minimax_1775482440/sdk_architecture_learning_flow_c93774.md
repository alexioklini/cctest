---
name: sdk_architecture_learning_flow
description: SDK integration learning progression relevant to MiniMax provider architecture
type: reference
agent: Minimax
---

SDK integration architecture learning progression observed:

1. **Initial Understanding**: `anthropic_oauth_direct_sdk` - OAuth tokens work directly with official Anthropic SDK
2. **Architectural Deep Dive**: `CLIProxyAPI Architecture` - Native Go binary making direct API calls (not subprocess wrapper)
3. **Practical Application**: `austrian-document-validator repo uses API key not OAuth` - Real-world SDK usage with standard API keys

## Key Insights
- Both API keys (sk-ant-...) and OAuth tokens work with Anthropic SDK
- CLIProxyAPI provides OpenAI format translation for Anthropic API calls
- SDK accepts both authentication methods in api_key parameter
- This knowledge informs MiniMax provider architecture decisions

## Relationships
- **type**: learning_progression
- **extends**: cli_proxy_api_architecture, anthropic_sdk_direct_usage
- **related**: infra_minimax_provider (provider can use either auth method), CLIProxyAPI Architecture
- **depends_on**: anthropic_oauth_direct_sdk, austrian-document-validator_usage_pattern
