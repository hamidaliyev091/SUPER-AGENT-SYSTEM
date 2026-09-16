"""TermuxEnvironmentAdapter (Phase 12): termux_api.* tools backed by
Termux:API binaries.

Only the registry rows with complete classifications are exposed:
battery_status, wifi_status, device_info (LOW, READ_ONLY, REVERSIBLE,
IDEMPOTENT). When a binary is absent the tool reports failure - never a
fake success. Everything still executes through the ExecutionPipeline;
the adapter creates no alternate authorization path.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable, Dict, Optional

from core import Error, Tool, ToolResult, utcnow_iso
from core.enums import Reversibility, RiskLevel, SideEffect, SideEffectState

_BINARIES = {
    "termux_api.battery_status": "termux-battery-status",
    "termux_api.wifi_status": "termux-wifi-connectioninfo",
    "termux_api.device_info": "termux-device-info",
}


def _default_run(binary: str, timeout: int = 10):
    """Run a Termux:API binary and parse its JSON output."""
    completed = subprocess.run([binary], capture_output=True, timeout=timeout,
                               check=False)
    if completed.returncode != 0:
        raise OSError(f"{binary} exited {completed.returncode}")
    return json.loads(completed.stdout.decode("utf-8", errors="replace"))


class TermuxEnvironmentAdapter:
    """Structured read-only device state via Termux:API binaries."""

    def __init__(self, run_command: Optional[Callable] = None):
        self.run_command = run_command or _default_run

    def tools(self) -> Dict[str, Tool]:
        return {
            operation_id: Tool(
                id=operation_id, name=operation_id, description=f"Termux:API {operation_id}",
                inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
                sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
                execute=self._execute(operation_id))
            for operation_id in _BINARIES
        }

    def _execute(self, operation_id):
        binary = _BINARIES[operation_id]

        def run(args, context):
            try:
                data = self.run_command(binary)
            except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
                return ToolResult(
                    success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                    timestamp=utcnow_iso(),
                    error=Error(code="TERMUX_API_UNAVAILABLE",
                                message=f"{binary} failed: {exc}", retryable=False))
            return ToolResult(success=True,
                              sideEffectState=SideEffectState.KNOWN_COMPLETED,
                              timestamp=utcnow_iso(), output=data)

        return run
