---
name: "Reporter Agent Summary <-> memory-architecture"
description: "Agent-roster member behavior defined in terms of architectural memory constraints"
type: reference
agent: main
last_recalled: 2026-04-05
---

Reporter agent operates within the hub-and-spoke memory architecture where the main agent's private memory is shared memory accessible via memory_shared. Reporter accesses platform context and formatting preferences through this shared architecture while having minimal independent memory storage.
