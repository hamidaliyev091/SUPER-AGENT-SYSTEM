"""TermuxFilesystemAdapter (Phase 12, ARCHITECTURE s5.11).

Real filesystem tools (fs.*) backed by pathlib, executing on the device
filesystem. No alternate authorization path exists: these are ordinary
Tools executed exclusively through the ExecutionPipeline, so Policy, the
TargetAuthorizationContext, and the protected-path registry apply exactly
as for any other tool.

Mutation tools are registered only when a concrete, versioned
protected-path mapping is supplied (PROTECTED_PATHS.md s14, ARCHITECTURE
s5.11); Policy denies filesystem mutation without one regardless
(P-RULE-51). fs.delete_file / fs.delete_directory are intentionally not
provided: deletion has no v1 authorizing scope (ADR-004) and would always
DENY.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from core import Error, Tool, ToolResult, utcnow_iso
from core.enums import (
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectState,
)


class TermuxFilesystemAdapter:
    """Filesystem tools over the real device filesystem."""

    def __init__(self, protected_mapping=None):
        self.protected_mapping = protected_mapping

    def tools(self) -> Dict[str, Tool]:
        tools = {
            "fs.read_file": self._tool("fs.read_file", "read a file", self._read,
                                       RiskLevel.LOW, SideEffect.READ_ONLY,
                                       Reversibility.REVERSIBLE),
            "fs.list_directory": self._tool("fs.list_directory", "list a directory",
                                            self._list_directory, RiskLevel.LOW,
                                            SideEffect.READ_ONLY, Reversibility.REVERSIBLE),
            "fs.stat": self._tool("fs.stat", "stat a path", self._stat,
                                  RiskLevel.LOW, SideEffect.READ_ONLY,
                                  Reversibility.REVERSIBLE),
        }
        if self.protected_mapping is not None:
            tools["fs.write_file"] = self._tool(
                "fs.write_file", "write a file", self._write,
                RiskLevel.MEDIUM, SideEffect.MUTATING, Reversibility.REVERSIBLE)
        return tools

    @staticmethod
    def _tool(operation_id, description, execute, risk, side_effect, reversibility) -> Tool:
        return Tool(
            id=operation_id, name=operation_id, description=description,
            inputSchema={}, outputSchema={}, riskLevel=risk,
            sideEffect=side_effect, reversibility=reversibility,
            execute=execute)

    # -- real filesystem operations -------------------------------------------

    @staticmethod
    def _read(args, context):
        return TermuxFilesystemAdapter._guard(
            args, lambda path: {"content": Path(path).read_text(encoding="utf-8")})

    @staticmethod
    def _list_directory(args, context):
        def run(path):
            entries = sorted(p.name for p in Path(path).iterdir())
            return {"entries": entries}
        return TermuxFilesystemAdapter._guard(args, run)

    @staticmethod
    def _stat(args, context):
        def run(path):
            stat = os.stat(path)
            return {"size": stat.st_size, "mode": oct(stat.st_mode),
                    "isDirectory": os.path.isdir(path)}
        return TermuxFilesystemAdapter._guard(args, run)

    @staticmethod
    def _write(args, context):
        def run(path):
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(args.get("content", "")), encoding="utf-8")
            return {"path": path, "bytes": target.stat().st_size}
        return TermuxFilesystemAdapter._guard(args, run)

    @staticmethod
    def _guard(args, operation):
        """Failures are ToolResults, never exceptions escaping into the
        pipeline; a failed filesystem action reports KNOWN_FAILED."""
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return ToolResult(success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                              timestamp=utcnow_iso(),
                              error=Error(code="INVALID_ARGUMENTS",
                                          message="fs operation requires a path argument",
                                          retryable=False))
        try:
            return ToolResult(success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                              timestamp=utcnow_iso(), output=operation(path))
        except OSError as exc:
            return ToolResult(success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                              timestamp=utcnow_iso(),
                              error=Error(code="FILESYSTEM_ERROR", message=str(exc),
                                          retryable=False))
