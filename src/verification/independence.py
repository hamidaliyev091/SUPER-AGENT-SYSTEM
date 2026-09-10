"""Independence assessment (VERIFICATION.md s8-s11).

Evidence independence is decided by collector identity, never by claims:

- collector == verification engine (policy-governed observation of actual
  state) -> LEVEL_1_INDEPENDENT_RUNTIME;
- collector in the registered separate-verifier set -> LEVEL_2;
- anything else (the acting model / acting operation's own records) ->
  LEVEL_0_SELF.

HIGH, CRITICAL, and irreversible operations require independent evidence;
when the best available evidence is below the required level the criterion
must evaluate INCONCLUSIVE, never PASS (s11).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from core import Evidence
from core.enums import IndependenceLevel

_LEVEL_RANK = {
    IndependenceLevel.LEVEL_0_SELF: 0,
    IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME: 1,
    IndependenceLevel.LEVEL_2_SEPARATE_VERIFIER: 2,
}


@dataclass
class IndependenceResult:
    """Best level achieved by a set of evidence vs. the required level."""
    level: Optional[IndependenceLevel]
    requiredLevel: Optional[IndependenceLevel]
    sufficient: bool
    reason: str = ""
    evidenceIds: list = field(default_factory=list)


def _level_of(evidence: Evidence, verifier_identity: str,
              separate_verifiers: frozenset) -> IndependenceLevel:
    if evidence.collectorIdentity in separate_verifiers:
        return IndependenceLevel.LEVEL_2_SEPARATE_VERIFIER
    if evidence.collectorIdentity == verifier_identity:
        return IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME
    return IndependenceLevel.LEVEL_0_SELF


def assess_independence(evidence: List[Evidence], verifier_identity: str,
                        separate_verifiers: frozenset,
                        required_level: Optional[IndependenceLevel]) -> IndependenceResult:
    if not evidence:
        return IndependenceResult(
            level=None, requiredLevel=required_level, sufficient=False,
            reason="no evidence available")
    levels = [_level_of(item, verifier_identity, separate_verifiers)
              for item in evidence]
    best = max(levels, key=lambda level: _LEVEL_RANK[level])
    sufficient = (required_level is None
                  or _LEVEL_RANK[best] >= _LEVEL_RANK[required_level])
    reason = "" if sufficient else (
        f"best evidence level {best.value} below required {required_level.value}")
    return IndependenceResult(
        level=best, requiredLevel=required_level, sufficient=sufficient,
        reason=reason, evidenceIds=[item.evidenceId for item in evidence])
