"""TermuxAndroidAdapter (Phase 14 v1 subset): Android capability tools
backed by the device's pm/settings binaries.

Exposure rule (ADR-012): only registry-complete rows are exposed -
package.list, package.inspect (LOW READ_ONLY, complete) and settings.read
(LOW READ_ONLY, complete). Settings writes, package mutation, launch,
force-stop, and accessibility operations have no v1 path (incomplete
matrix rows per ADR-004, or no registry rows at all) and are not
registered. Everything executes through the ExecutionPipeline; the
adapter has no authority of its own. Commands run as argument lists
(no shell), so no command injection surface exists.
"""
from __future__ import annotations

import subprocess
from typing import Callable, Dict, Optional

from core import Error, Tool, ToolResult, utcnow_iso
from core.enums import Reversibility, RiskLevel, SideEffect, SideEffectState


def _default_run(argv, timeout: int = 15):
    completed = subprocess.run([str(a) for a in argv], capture_output=True,
                               timeout=timeout, check=False)
    if completed.returncode != 0:
        raise OSError(f"{argv[0]} exited {completed.returncode}: "
                      f"{completed.stderr.decode(errors='replace').strip()}")
    return completed.stdout.decode("utf-8", errors="replace")


class TermuxAndroidAdapter:
    """Android package inspection and settings read via pm/settings."""

    def __init__(self, run_command: Optional[Callable] = None):
        self.run_command = run_command or _default_run

    def tools(self) -> Dict[str, Tool]:
        return {
            "package.list": self._tool("package.list", self._package_list),
            "package.inspect": self._tool("package.inspect", self._package_inspect),
            "settings.read": self._tool("settings.read", self._settings_read),
        }

    def _tool(self, operation_id, operation) -> Tool:
        return Tool(
            id=operation_id, name=operation_id,
            description=f"Android {operation_id} via Termux binaries",
            inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
            sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
            execute=operation)

    # -- operations -------------------------------------------------------------

    def _package_list(self, args, context):
        package = args.get("package")
        if not isinstance(package, str) or not package:
            return self._failure("package.list requires a package argument")
        try:
            output = self.run_command(["pm", "list", "packages", package])
            return ToolResult(success=True,
                              sideEffectState=SideEffectState.KNOWN_COMPLETED,
                              timestamp=utcnow_iso(),
                              output={"packages": [
                                  line.split("package:", 1)[1]
                                  for line in output.splitlines()
                                  if line.startswith("package:")]})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._failure(f"pm failed: {exc}")

    def _package_inspect(self, args, context):
        package = args.get("package")
        if not isinstance(package, str) or not package:
            return self._failure("package.inspect requires a package argument")
        try:
            output = self.run_command(["pm", "path", package])
            return ToolResult(success=True,
                              sideEffectState=SideEffectState.KNOWN_COMPLETED,
                              timestamp=utcnow_iso(),
                              output={"paths": [
                                  line.split("package:", 1)[1]
                                  for line in output.splitlines()
                                  if line.startswith("package:")]})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._failure(f"pm failed: {exc}")

    def _settings_read(self, args, context):
        namespace = args.get("namespace")
        setting = args.get("setting")
        if namespace not in ("system", "secure", "global") or \
                not isinstance(setting, str) or not setting:
            return self._failure("settings.read requires namespace "
                                 "(system|secure|global) and setting")
        try:
            output = self.run_command(["settings", "get", namespace, setting])
            return ToolResult(success=True,
                              sideEffectState=SideEffectState.KNOWN_COMPLETED,
                              timestamp=utcnow_iso(),
                              output={"value": output.strip()})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._failure(f"settings failed: {exc}")

    @staticmethod
    def _failure(message: str) -> ToolResult:
        return ToolResult(success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                          timestamp=utcnow_iso(),
                          error=Error(code="ANDROID_OPERATION_FAILED",
                                      message=message, retryable=False))
