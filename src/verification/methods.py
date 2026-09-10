"""Verification method registry (Phase 5).

A criterion's verificationMethod names a registered VerificationMethodSpec.
The spec declares:

- the tool whose invocation collects the evidence (always through the
  ExecutionPipeline, so verification cannot bypass Policy - VERIFICATION.md
  s12.1);
- the arguments for that invocation;
- an assessor that deterministically evaluates the collected observation
  against the criterion.

The assessor is plain code, never a model: "tool returned success" and
"command exited successfully" are observations, not verification
(ROADMAP.md Phase 5). A spec with toolId=None describes a method that has
only the acting side's own records as evidence (Level 0 self-evidence).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple, Union

from core import SuccessCriterion, Task, ToolResult
from core.enums import IndependenceLevel, VerificationStatus

# assessor(tool_result, task, criterion) -> (status, reason)
Assessor = Callable[[Optional[ToolResult], Task, SuccessCriterion],
                    Tuple[VerificationStatus, str]]


@dataclass
class VerificationMethodSpec:
    """Registered evaluation method for one verificationMethod name."""
    method: str
    toolId: Optional[str]
    arguments: Union[Dict, Callable[[Task, SuccessCriterion], Dict], None] = None
    assessor: Optional[Assessor] = None
    includeActionEvidence: bool = False
    requiredIndependenceLevel: Optional[IndependenceLevel] = None

    def build_arguments(self, task: Task, criterion: SuccessCriterion) -> dict:
        if self.arguments is None:
            return {}
        if callable(self.arguments):
            return self.arguments(task, criterion)
        return dict(self.arguments)


# -- assessor helpers ---------------------------------------------------------

def _observe(tool_result: Optional[ToolResult]) -> dict:
    if tool_result is None or not isinstance(tool_result.output, dict):
        return {}
    return tool_result.output


def output_contains(key: str, expected) -> Assessor:
    """PASS iff output[key] == expected. Missing output -> INCONCLUSIVE."""
    def assessor(tool_result, task, criterion):
        output = _observe(tool_result)
        if key not in output:
            return VerificationStatus.INCONCLUSIVE, f"observation lacks {key!r}"
        if output[key] == expected:
            return VerificationStatus.PASS, f"{key!r} equals expected value"
        return VerificationStatus.FAIL, (
            f"{key!r} is {output[key]!r}, expected {expected!r}")
    return assessor


def output_satisfies(predicate: Callable[[dict], bool],
                     description: str = "criterion predicate") -> Assessor:
    """PASS iff predicate(output) is true; predicate failure -> FAIL.
    Missing output -> INCONCLUSIVE."""
    def assessor(tool_result, task, criterion):
        if tool_result is None:
            return VerificationStatus.INCONCLUSIVE, "no observation available"
        if predicate(tool_result.output):
            return VerificationStatus.PASS, description
        return VerificationStatus.FAIL, description + " not satisfied"
    return assessor
