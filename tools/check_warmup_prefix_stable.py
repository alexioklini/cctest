#!/usr/bin/env python3
"""Warmup KV-prefix byte-stability check — the C1 refactor gate.

The whole point of warmup is that the warm-pool prime and the first live turn
build a BYTE-IDENTICAL prefix (system prompt + active tool set), so the oMLX KV
cache hits. CLAUDE.md invariant #3 / REFACTOR_HANDOVER invariant #3: the C1
extraction of `_build_system_prompt` + model-selection MUST NOT change that
prefix by a single byte.

This script calls `brain.build_first_turn_prefix(...)` — the SINGLE source of
truth both warmup and the chat worker use — with a fixed, representative,
no-project main-agent context, and prints the SHA-256 of:
  * the system prompt string
  * the sorted active-tool-name set

Usage:
  # capture the pre-extraction baseline
  python3 tools/check_warmup_prefix_stable.py            # prints hashes
  python3 tools/check_warmup_prefix_stable.py --save     # writes /tmp/warmup_prefix_baseline.json
  # after the C1 extraction, compare:
  python3 tools/check_warmup_prefix_stable.py --check     # exits non-zero on drift

The system prompt rounds its timestamp to the hour, so the hash is stable
within an hour. If a run straddles an hour boundary, re-run --save then --check
back-to-back (seconds apart) — they will agree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from engine.context import ExecutionContext, init_thread_context  # noqa: E402

_BASELINE_PATH = "/tmp/warmup_prefix_baseline.json"
_MODEL = "gemma-4-26B"  # representative; profile-overlay path exercised
_AGENT = "main"


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def compute_prefix_hashes() -> dict:
    """Build the first-turn prefix exactly as warmup does and hash it.

    Bare main agent, no project, no MCP, empty discovered-tools — the same
    baseline `run_model_warmup` primes for the warm pool.
    """
    agent_config = brain.AgentConfig(_AGENT)
    ctx = ExecutionContext(
        mode="chat",
        agent_id=_AGENT,
        memory_store=brain.MemoryStore(_AGENT, base_dir=agent_config.memory_dir),
        mcp_manager=None,
    )
    init_thread_context(ctx, agent_config=agent_config)
    try:
        system_prompt, _active_tools, active_tool_names = brain.build_first_turn_prefix(
            _MODEL, _AGENT, mcp_manager=None, discovered_tools=set(),
            is_openai_shape=True,
        )
    finally:
        # Don't let _current_model leak to any subsequent caller.
        try:
            brain.get_request_context()._current_model = None
        except Exception:
            pass
        brain.clear_thread_context()

    tool_names_sorted = ",".join(sorted(n for n in active_tool_names if n))
    return {
        "model": _MODEL,
        "agent": _AGENT,
        "system_prompt_sha256": _sha(system_prompt),
        "system_prompt_len": len(system_prompt),
        "tool_names_sha256": _sha(tool_names_sorted),
        "tool_count": len([n for n in active_tool_names if n]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="write baseline to " + _BASELINE_PATH)
    ap.add_argument("--check", action="store_true", help="compare against saved baseline; non-zero exit on drift")
    args = ap.parse_args()

    cur = compute_prefix_hashes()
    print(json.dumps(cur, indent=2))

    if args.save:
        with open(_BASELINE_PATH, "w") as f:
            json.dump(cur, f, indent=2)
        print(f"\n[saved baseline -> {_BASELINE_PATH}]")
        return 0

    if args.check:
        if not os.path.exists(_BASELINE_PATH):
            print(f"\nERROR: no baseline at {_BASELINE_PATH}; run --save first", file=sys.stderr)
            return 2
        with open(_BASELINE_PATH) as f:
            base = json.load(f)
        sp_ok = base["system_prompt_sha256"] == cur["system_prompt_sha256"]
        tn_ok = base["tool_names_sha256"] == cur["tool_names_sha256"]
        if sp_ok and tn_ok:
            print("\n✓ WARMUP PREFIX BYTE-IDENTICAL (system prompt + tool set unchanged)")
            return 0
        print("\n✗ WARMUP PREFIX DRIFT:", file=sys.stderr)
        if not sp_ok:
            print(f"  system prompt: {base['system_prompt_sha256']} (len {base['system_prompt_len']})"
                  f" -> {cur['system_prompt_sha256']} (len {cur['system_prompt_len']})", file=sys.stderr)
        if not tn_ok:
            print(f"  tool set: {base['tool_names_sha256']} ({base['tool_count']} tools)"
                  f" -> {cur['tool_names_sha256']} ({cur['tool_count']} tools)", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
