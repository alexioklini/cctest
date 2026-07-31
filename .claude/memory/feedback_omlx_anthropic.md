---
name: oMLX supports Anthropic protocol
description: oMLX local inference server uses anthropic API type, not openai — do not assume it's OpenAI-compatible
type: feedback
---

oMLX on port 8000 uses `"type": "anthropic"` in the provider config. This is correct — oMLX supports the Anthropic API protocol natively. Do not suggest changing it to openai.

**Why:** User corrected me when I assumed oMLX was OpenAI-compatible based on the name.
**How to apply:** When troubleshooting provider issues, trust the existing config type for oMLX.
