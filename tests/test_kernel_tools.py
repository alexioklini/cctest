"""Kernel-Tools (Quant-Workbench Phase A) — Wiring + Cancel-Eskalation.

Pinnt die Registrierungs-Invarianten der drei Kernel-Tools und den
Eskalations-Dispatch in kill_tool_process (Kernel-Handle: Interrupt vor Kill).
Die Kernel-LEBENSZYKLUS-Kriterien (Zustand über Turns, Idle-Reaper, Zombies)
sind Live-Kriterien im Plan-Log — hier nur, was ohne Kernel prüfbar ist.

Run: python3 -m unittest tests.test_kernel_tools -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from engine.context import request_context  # noqa: E402
from engine.tool_exec import (  # noqa: E402
    register_tool_process,
    unregister_tool_process,
    kill_tool_process,
)

KERNEL_TOOLS = ("kernel_exec", "kernel_status", "kernel_restart")


class TestKernelToolWiring(unittest.TestCase):
    def test_four_site_registration(self):
        from engine.tools import kernel_tools
        from engine.tool_schemas import TOOL_DEFINITIONS
        schema_names = {t["name"] for t in TOOL_DEFINITIONS}
        for name in KERNEL_TOOLS:
            self.assertIn(name, schema_names)
            self.assertIn(name, brain.TOOL_DISPATCH)
            self.assertIn(name, brain.TOOL_GROUPS["code_exec"])
            # Dispatch-Identity: direkte Fn-Ref, kein Lambda-Forwarder.
            self.assertIs(brain.TOOL_DISPATCH[name],
                          getattr(kernel_tools, f"tool_{name}"))

    def test_not_in_restricted_purpose_sets(self):
        # Interactive-only: der Boot-Seed macht ein Tool nur dann in einem
        # restringierten Purpose aktiv, wenn es in dessen Basis-Set steht —
        # Kernel-Tools gehören in KEINES (Scheduler/Workflow/Helpdesk/minimal).
        for name in KERNEL_TOOLS:
            self.assertNotIn(name, brain._WORKFLOW_STEP_TOOLS)
            self.assertNotIn(name, brain.GDPR_ARGS_DEANON_TOOLS)

    def test_refuses_outside_session(self):
        # Ohne Session-Kontext (und in sched-/bg-Kontexten) fail-loud.
        with request_context():
            res = json.loads(brain.TOOL_DISPATCH["kernel_exec"]({"code": "1+1"}))
            self.assertIn("error", res)
        with request_context(current_session_id="sched-42"):
            res = json.loads(brain.TOOL_DISPATCH["kernel_exec"]({"code": "1+1"}))
            self.assertIn("error", res)


class _FakeKernelHandle:
    """Spiegelt SessionKernel.cancel_escalate: 1. Cancel = Interrupt,
    2. Cancel = Kill."""

    def __init__(self):
        self.interrupts = 0
        self.kills = 0
        self._count = 0

    def cancel_escalate(self):
        self._count += 1
        if self._count <= 1:
            self.interrupts += 1
        else:
            self.kills += 1
        return True


class TestCancelEscalation(unittest.TestCase):
    def test_kill_tool_process_dispatches_on_handle(self):
        handle = _FakeKernelHandle()
        with request_context(tool_use_id="u1"):
            # current_turn_id ist ein dynamisches Feld (wie in sidecar_proxy
            # _apply_bg_context) — per Attribut setzen, nicht als Override.
            from engine.context import get_request_context
            get_request_context().current_turn_id = "t1"
            key = register_tool_process(handle)
        try:
            self.assertTrue(kill_tool_process("t1", "u1"))
            self.assertEqual((handle.interrupts, handle.kills), (1, 0))
            self.assertTrue(kill_tool_process("t1", "u1"))
            self.assertEqual((handle.interrupts, handle.kills), (1, 1))
        finally:
            unregister_tool_process(key)

    def test_session_kernel_escalation_states(self):
        # Echte SessionKernel-Eskalation gegen einen Stub-KernelManager —
        # ohne echten Kernel-Prozess (kein pid → _hard_kill wird geprobt).
        from engine.kernels import SessionKernel

        class _KM:
            def __init__(self):
                self.interrupted = 0

            def interrupt_kernel(self):
                self.interrupted += 1

            class provisioner:  # kein Prozess → pid None
                process = None

        km = _KM()
        k = SessionKernel("sid", "python", km, kc=None)
        self.assertTrue(k.cancel_escalate())      # 1. Cancel → Interrupt
        self.assertEqual(km.interrupted, 1)
        self.assertTrue(k.cancel_escalate())      # 2. Cancel → Hard-Kill-Pfad
        self.assertEqual(km.interrupted, 1)


if __name__ == "__main__":
    unittest.main()
