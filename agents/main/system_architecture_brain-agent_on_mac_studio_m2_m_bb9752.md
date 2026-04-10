---
name: "system architecture: brain-agent on mac studio m2 max, macbook air m1 as remote node"
description: "Documents the two-machine setup where Brain Agent runs on Mac Studio M2 Max (local) and MacBook Air M1 acts as a remote node; includes OS version, connectivity, and latency details."
type: chat_transcript
agent: main
related:
  - file: reporter_agent_role_dependencies_ba9393.md
    type: same_topic
  - file: memory_summary.md
    type: same_topic
  - file: infra_minimax_provider_relationships_a5e519.md
    type: same_topic
  - file: tool-expansion-analysis-2026-03_-_reporter_agent_s_2cf7e8.md
    type: same_topic
  - file: tool-expansion-analysis_-_memory-architecture_665052.md
    type: same_topic
  - file: reporter_agent_-_web_ui_tool_toggle_presentation_79f2ec.md
    type: same_topic
  - file: researcher_agent_role_9f388c.md
    type: same_topic
  - file: shell-env-fix-2025_-_claude-code-version-path_35b821.md
    type: same_topic
  - file: relationship_summary_researcher_agent_acea8d.md
    type: same_topic
  - file: memory_summary_aab3f9.md
    type: same_topic
---

## System Architecture

The user has a two-machine setup for running Brain Agent:

| Machine | Role | OS | Status | Details |
|---|---|---|---|---|
| **Mac Studio M2 Max** | Local — runs Brain Agent | macOS 26.2 (Tahoe) | Primary | Development machine |
| **MacBook Air M1** | Remote node (`MBAirM1`) | macOS 26.3 (Tahoe), Build 25D125 | Online & Reachable ✅ | ~30ms latency via VPN |

**Key points:**
- Brain Agent executes on the local Mac Studio M2 Max
- MacBook Air M1 is registered as a remote node that Brain Agent can execute commands on
- MacBook Air M1 is always online and reachable
- The MacBook Air M1 runs macOS 26.3 (Tahoe), Build 25D125 [user corrected initial report of 26.2]
- Network connectivity: ~30ms latency to remote node via VPN
