---
name: "austrian-document-validator repo uses API key not OAuth"
description: "Clarification that this repo uses standard API key authentication, not OAuth tokens"
type: reference
agent: Minimax
---

The austrian-document-validator GitHub repo (alexioklini/austrian-document-validator) uses Anthropic SDK with a standard API key (sk-ant-... format) from ANTHROPIC_API_KEY environment variable, not an OAuth token. The SDK accepts both API keys and OAuth tokens in the api_key parameter.
