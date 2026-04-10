---
name: "\"reliability_issue_main_2026-04-04\""
description: Known reliability issue with relationship discovery scheduled task for main agent
type: reference
agent: main
---

The relationship discovery task for main agent attempted on 2026-04-04 completed with 0 tools executed despite logging success. This follows a pattern of `ValueError: unknown url type: '/messages'` errors on 2026-04-03. Root cause: scheduled task runner's HTTP client initialized with relative path instead of absolute URL for the messages endpoint.
