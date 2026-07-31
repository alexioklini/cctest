---
name: Server restart via launchd
description: Brain server is managed by launchd — always restart with launchctl, never python3 brain.py or curl /v1/restart
type: feedback
originSessionId: f6f8a0a0-8ab1-4c25-ae71-2fd175ad7ffd
---
Always restart the Brain server via launchd: `launchctl kickstart -k gui/$(id -u)/com.brain-agent.server`

**Why:** User has corrected this multiple times. The server runs as a launchd daemon, not a manual process.

**How to apply:** Whenever the Brain server needs a restart (config reload, code changes, etc.), use the launchctl command. Never suggest `python3 brain.py restart` or `curl /v1/restart`.
