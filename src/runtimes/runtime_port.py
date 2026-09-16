"""RuntimePort (INTERFACES.md s16, ARCHITECTURE.md s5.10).

The Runtime Adapter isolates the Core from agent runtimes (Pi, OpenCode,
future runtimes). It translates runtime-agnostic commands into
runtime-specific operations and exposes session start/send/stop/resume.
It has NO authority: every tool execution flows through the Core policy
pipeline, and the Core never imports runtime-specific code.

Concrete PiRuntimeAdapter / OpenCodeRuntimeAdapter implementations belong
to the runtime owner and plug in here; the Core-side contract is complete.
"""
from __future__ import annotations

from typing import List

from core import RuntimeEvent, RuntimeSession


class RuntimePort:
    """Provider/runtime-independent session interface (INTERFACES s16)."""

    def start(self, task_context: dict) -> RuntimeSession:
        raise NotImplementedError

    def send(self, session_id: str, input) -> List[RuntimeEvent]:
        raise NotImplementedError

    def stop(self, session_id: str) -> dict:
        raise NotImplementedError

    def resume(self, session_id: str) -> RuntimeSession:
        raise NotImplementedError
