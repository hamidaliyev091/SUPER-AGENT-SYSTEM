"""Production verification methods for device state (Phase 14 WS8).

Until now every real assessor lived in test code: `VerificationEngine`
defaulted to `methods={}`, so a task on the device could only ever be
INCONCLUSIVE. This module registers the methods a device task actually
needs, so VERIFICATION.md s5 ("associated with a verification method,
associated with evidence requirements") holds on the real path.

Each method is (evidence-collection tool, arguments, code assessor):

    file-content-equals       fs.read_file        output.content == expected
    ui-foreground-package-is  android.observe_ui  output.foregroundPackage == expected
    ui-node-text-present      android.observe_ui  some node text contains expected
    foreground-package-changed android.observe_ui foregroundPackage != previous
    screenshot-captured       android.screenshot  a real image artifact was stored

Expected values come from the criterion's `evidenceRequirements`, written as
`key=value` entries (the field is declared by the frozen contract and was
unused until now, so no schema change is needed). A criterion that does not
carry the requirement its method needs is INCONCLUSIVE - the assessors never
guess, and a missing value can never become a PASS.

Assessors are plain code. They never call a model, and they read only what
the evidence-collection action observed through the pipeline (so evidence
cannot bypass Policy, VERIFICATION.md s12.1).
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Optional, Tuple

from core import SuccessCriterion, Task, ToolResult
from core.enums import VerificationStatus

from .methods import VerificationMethodSpec

#: Assessment outcome of one collected observation.
Assessment = Tuple[VerificationStatus, str]

_PNG_SHA256 = re.compile(r"[0-9a-f]{64}")


def parse_requirements(criterion: SuccessCriterion) -> Dict[str, str]:
    """The criterion's `key=value` evidence requirements as a dict.

    A malformed entry is kept out of the result rather than guessed at; a
    method that needs it will report INCONCLUSIVE.
    """
    requirements = {}
    for entry in criterion.evidenceRequirements or ():
        if not isinstance(entry, str) or "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        key = key.strip()
        if key:
            requirements[key] = value.strip()
    return requirements


def _inconclusive(why: str) -> Assessment:
    return VerificationStatus.INCONCLUSIVE, why


def _missing(key: str, method: str) -> Assessment:
    return _inconclusive(
        f"criterion declares no {key!r} evidence requirement for {method}")


def _observed(tool_result: Optional[ToolResult]) -> Optional[dict]:
    if tool_result is None or not isinstance(tool_result.output, dict):
        return None
    return tool_result.output


# -- assessors ----------------------------------------------------------------

def assess_file_content(tool_result: Optional[ToolResult], task: Task,
                        criterion: SuccessCriterion) -> Assessment:
    """The file the evidence action read contains exactly the expected text."""
    requirements = parse_requirements(criterion)
    if "expected" not in requirements:
        return _missing("expected", "file-content-equals")
    output = _observed(tool_result)
    if output is None or "content" not in output:
        return _inconclusive("the read produced no content to compare")
    actual = output["content"]
    if not isinstance(actual, str):
        return _inconclusive("the observed content is not text")
    expected = requirements["expected"]
    if actual == expected:
        return VerificationStatus.PASS, "the file content is exactly as required"
    return VerificationStatus.FAIL, (
        f"file content differs from the expected value "
        f"({len(actual)} chars observed, {len(expected)} expected)")


def assess_foreground_package(tool_result: Optional[ToolResult], task: Task,
                              criterion: SuccessCriterion) -> Assessment:
    """The device reports exactly the expected foreground package."""
    requirements = parse_requirements(criterion)
    if "package" not in requirements:
        return _missing("package", "ui-foreground-package-is")
    output = _observed(tool_result)
    if output is None or "foregroundPackage" not in output:
        return _inconclusive("the observation reports no foreground package")
    actual = output["foregroundPackage"]
    expected = requirements["package"]
    if actual == expected:
        return VerificationStatus.PASS, f"the foreground app is {expected}"
    return VerificationStatus.FAIL, (
        f"the foreground app is {actual!r}, expected {expected!r}")


def assess_node_text_present(tool_result: Optional[ToolResult], task: Task,
                             criterion: SuccessCriterion) -> Assessment:
    """Some node on screen carries the expected text."""
    requirements = parse_requirements(criterion)
    if "text" not in requirements:
        return _missing("text", "ui-node-text-present")
    output = _observed(tool_result)
    if output is None or not isinstance(output.get("nodes"), list):
        return _inconclusive("the observation carries no UI nodes")
    expected = requirements["text"]
    for node in output["nodes"]:
        if isinstance(node, dict) and expected in str(node.get("text", "")):
            return VerificationStatus.PASS, f"the screen shows {expected!r}"
    return VerificationStatus.FAIL, (
        f"no visible node contains {expected!r} "
        f"({len(output['nodes'])} nodes observed)")


def assess_foreground_package_changed(tool_result: Optional[ToolResult], task: Task,
                                      criterion: SuccessCriterion) -> Assessment:
    """The foreground app is not the one that was in front before the action.

    The device reports both, from the same observation, so no earlier state
    has to be trusted or remembered.
    """
    output = _observed(tool_result)
    if output is None or "foregroundPackage" not in output:
        return _inconclusive("the observation reports no foreground package")
    current = output["foregroundPackage"]
    previous = output.get("previousForegroundPackage")
    if not current:
        return VerificationStatus.FAIL, "the device reports no foreground app"
    if current == previous:
        return VerificationStatus.FAIL, (
            f"the foreground app is still {current!r}")
    return VerificationStatus.PASS, f"the foreground app changed to {current}"


def assess_screenshot_captured(tool_result: Optional[ToolResult], task: Task,
                               criterion: SuccessCriterion) -> Assessment:
    """A real image artifact was captured and stored content-addressed.

    The digest proves the stored bytes are the bytes the device produced; the
    adapter checked it before accepting the capture, and the artifact exists.
    """
    output = _observed(tool_result)
    artifact = output.get("artifact") if output else None
    if not isinstance(artifact, dict):
        return VerificationStatus.FAIL, "the capture produced no artifact"
    digest = artifact.get("sha256")
    if not isinstance(digest, str) or not _PNG_SHA256.fullmatch(digest):
        return VerificationStatus.FAIL, "the artifact carries no content digest"
    size = artifact.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return VerificationStatus.FAIL, "the artifact is empty"
    if artifact.get("kind") != "image":
        return VerificationStatus.FAIL, "the artifact is not an image"
    return VerificationStatus.PASS, f"a {size}-byte screenshot is stored"


# -- registry -----------------------------------------------------------------

#: Evidence requirements a method cannot assess without, declared so an
#: operator's incomplete criterion is refused when it is written instead of
#: leaving the task INCONCLUSIVE for its whole life with no hint of why.
REQUIRED_REQUIREMENTS = {
    "file-content-equals": ("path", "expected"),
    "ui-foreground-package-is": ("package",),
    "ui-node-text-present": ("text",),
    "foreground-package-changed": (),
    "screenshot-captured": (),
}


def default_methods() -> Dict[str, VerificationMethodSpec]:
    """Every production verification method, keyed by verificationMethod."""
    return {
        "file-content-equals": VerificationMethodSpec(
            method="file-content-equals", toolId="fs.read_file",
            arguments=lambda task, criterion: {
                "path": parse_requirements(criterion).get("path") or ""},
            assessor=assess_file_content),
        "ui-foreground-package-is": VerificationMethodSpec(
            method="ui-foreground-package-is", toolId="android.observe_ui",
            assessor=assess_foreground_package),
        "ui-node-text-present": VerificationMethodSpec(
            method="ui-node-text-present", toolId="android.observe_ui",
            assessor=assess_node_text_present),
        "foreground-package-changed": VerificationMethodSpec(
            method="foreground-package-changed", toolId="android.observe_ui",
            assessor=assess_foreground_package_changed),
        "screenshot-captured": VerificationMethodSpec(
            method="screenshot-captured", toolId="android.screenshot",
            assessor=assess_screenshot_captured),
    }


def criterion_for(method: str, description: str, **requirements) -> SuccessCriterion:
    """Build a criterion whose expected values are key=value requirements."""
    return SuccessCriterion(
        id=f"c-{method}", description=description, verificationMethod=method,
        evidenceRequirements=[f"{key}={value}" for key, value in requirements.items()])


def as_assessor(method: str) -> Callable:
    """The registered assessor for a method name (used by tests)."""
    return default_methods()[method].assessor
