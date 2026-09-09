"""v1 policy registries - the authoritative executable policy baseline.

Operation rules are a direct transcript of POLICY_RULES.md s26-s37 and the
permission matrix of s20. The Policy Engine MUST NOT invent missing rules:
an operation without a registry entry, or a classification combination
without a matrix row, evaluates to DENY (POLICY_RULES.md s1/s20).

The requested* fields of a PolicyRequest never influence these values
(s5: informational inputs only; authoritative values come from here).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from core import PolicyDecisionValue, Reversibility, RiskLevel, SideEffect, TargetType, Idempotency

# POLICY.md s6: the only explicitly compatible policy/rule version pair.
COMPATIBLE_VERSIONS = (("0.6", "1.0"),)

# Permission matrix, POLICY_RULES.md s20. Index by PermissionMode:
# PLAN=0, ASK=1, AUTO=2, DANGEROUS=3. Missing combinations => DENY.
MATRIX = {
    (RiskLevel.LOW, SideEffect.READ_ONLY): (
        PolicyDecisionValue.ALLOW, PolicyDecisionValue.ALLOW,
        PolicyDecisionValue.ALLOW, PolicyDecisionValue.ALLOW),
    (RiskLevel.MEDIUM, SideEffect.READ_ONLY): (
        PolicyDecisionValue.ALLOW, PolicyDecisionValue.ALLOW,
        PolicyDecisionValue.ALLOW, PolicyDecisionValue.ALLOW),
    (RiskLevel.LOW, SideEffect.MUTATING, Reversibility.REVERSIBLE): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ALLOW,
        PolicyDecisionValue.ALLOW, PolicyDecisionValue.ALLOW),
    (RiskLevel.LOW, SideEffect.MUTATING, Reversibility.PARTIALLY_REVERSIBLE): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ALLOW,
        PolicyDecisionValue.ALLOW, PolicyDecisionValue.ALLOW),
    (RiskLevel.MEDIUM, SideEffect.MUTATING, Reversibility.REVERSIBLE): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ALLOW,
        PolicyDecisionValue.ALLOW, PolicyDecisionValue.ALLOW),
    (RiskLevel.MEDIUM, SideEffect.MUTATING, Reversibility.PARTIALLY_REVERSIBLE): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ALLOW),
    (RiskLevel.HIGH, SideEffect.READ_ONLY, Reversibility.REVERSIBLE): (
        PolicyDecisionValue.ALLOW, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ALLOW),
    (RiskLevel.HIGH, SideEffect.READ_ONLY, Reversibility.PARTIALLY_REVERSIBLE): (
        PolicyDecisionValue.ALLOW, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ASK),
    (RiskLevel.HIGH, SideEffect.READ_ONLY, Reversibility.IRREVERSIBLE): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ASK),
    (RiskLevel.HIGH, SideEffect.MUTATING, Reversibility.REVERSIBLE): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ALLOW),
    (RiskLevel.HIGH, SideEffect.MUTATING, Reversibility.PARTIALLY_REVERSIBLE): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ASK),
    (RiskLevel.HIGH, SideEffect.MUTATING, Reversibility.IRREVERSIBLE): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ASK),
    (RiskLevel.HIGH, SideEffect.DESTRUCTIVE): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ASK),
    (RiskLevel.HIGH, SideEffect.EXTERNAL_EFFECT): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ASK),
    (RiskLevel.CRITICAL,): (
        PolicyDecisionValue.DENY, PolicyDecisionValue.ASK,
        PolicyDecisionValue.ASK, PolicyDecisionValue.ASK),
}
MODE_INDEX = {
    "PLAN": 0, "ASK": 1, "AUTO": 2, "DANGEROUS": 3,
}


@dataclass(frozen=True)
class OperationRule:
    """Authoritative classification and constraints for one v1 operation.

    requiredScopes: TAC field names the canonical target must match.
    scopeUnsatisfiable: the operation needs target authorization but no v1
        TAC scope category can authorize it (deletion has no delete-scope;
        P-RULE-18 forbids reusing write scope) -> always DENY.
    packageOperationGate: the operation id must also be listed in the task's
        allowedPackageOperations (POLICY_RULES.md s29).
    destinationArg: argument key carrying a filesystem path that must match
        allowedWritePaths (network.download, s32).
    """
    operationId: str
    riskLevel: RiskLevel
    sideEffect: SideEffect
    reversibility: Reversibility
    idempotency: Idempotency
    targetKind: Optional[TargetType] = None
    requiredScopes: Tuple[str, ...] = ()
    requiredArgs: Tuple[str, ...] = ()
    packageOperationGate: bool = False
    mandatoryConfirmation: bool = False
    outOfScope: bool = False
    forcedDecision: Optional[PolicyDecisionValue] = None
    scopeUnsatisfiable: bool = False
    destinationArg: Optional[str] = None


# POLICY_RULES.md s26-s37, transcribed. Operations whose reversibility or
# idempotency is not specified by the registry use UNKNOWN, which the engine
# treats conservatively (s16/s17). Combinations without a matrix row then
# evaluate to DENY (s20) - see ADR-004.
OPERATIONS = {
    # -- filesystem registry (s26) ---------------------------------------
    "fs.read_file": OperationRule(
        "fs.read_file", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.FILESYSTEM, ("allowedReadPaths",), ("path",)),
    "fs.list_directory": OperationRule(
        "fs.list_directory", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.FILESYSTEM, ("allowedReadPaths",), ("path",)),
    "fs.stat": OperationRule(
        "fs.stat", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.FILESYSTEM, ("allowedReadPaths",), ("path",)),
    "fs.search": OperationRule(
        "fs.search", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.FILESYSTEM, ("allowedSearchPaths",), ("path",)),
    "fs.create_file": OperationRule(
        "fs.create_file", RiskLevel.MEDIUM, SideEffect.MUTATING,
        Reversibility.REVERSIBLE, Idempotency.CONDITIONALLY_IDEMPOTENT,
        TargetType.FILESYSTEM, ("allowedWritePaths",), ("path",)),
    "fs.write_file": OperationRule(
        "fs.write_file", RiskLevel.MEDIUM, SideEffect.MUTATING,
        Reversibility.REVERSIBLE, Idempotency.CONDITIONALLY_IDEMPOTENT,
        TargetType.FILESYSTEM, ("allowedWritePaths",), ("path",)),
    "fs.delete_file": OperationRule(
        "fs.delete_file", RiskLevel.HIGH, SideEffect.DESTRUCTIVE,
        Reversibility.IRREVERSIBLE, Idempotency.NON_IDEMPOTENT,
        TargetType.FILESYSTEM, (), ("path",), scopeUnsatisfiable=True),
    "fs.delete_directory": OperationRule(
        "fs.delete_directory", RiskLevel.HIGH, SideEffect.DESTRUCTIVE,
        Reversibility.IRREVERSIBLE, Idempotency.NON_IDEMPOTENT,
        TargetType.FILESYSTEM, (), ("path",), scopeUnsatisfiable=True),
    # -- process registry (s27; reversibility unspecified -> UNKNOWN) -----
    "process.list": OperationRule(
        "process.list", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        TargetType.PROCESS, ("allowedProcesses",), ("process",)),
    "process.inspect": OperationRule(
        "process.inspect", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        TargetType.PROCESS, ("allowedProcesses",), ("process",)),
    "process.start": OperationRule(
        "process.start", RiskLevel.MEDIUM, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        TargetType.PROCESS, ("allowedProcesses",), ("command",)),
    "process.stop": OperationRule(
        "process.stop", RiskLevel.MEDIUM, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        TargetType.PROCESS, ("allowedProcesses",), ("process",)),
    # -- Termux:API registry (s28) ----------------------------------------
    "termux_api.battery_status": OperationRule(
        "termux_api.battery_status", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT),
    "termux_api.wifi_status": OperationRule(
        "termux_api.wifi_status", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT),
    "termux_api.device_info": OperationRule(
        "termux_api.device_info", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT),
    "termux_api.send_notification": OperationRule(
        "termux_api.send_notification", RiskLevel.MEDIUM, SideEffect.EXTERNAL_EFFECT,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        requiredArgs=("title", "text")),
    # -- Android package registry (s29) ------------------------------------
    "package.list": OperationRule(
        "package.list", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.PACKAGE, ("allowedPackages",), ("package",),
        packageOperationGate=True),
    "package.inspect": OperationRule(
        "package.inspect", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.PACKAGE, ("allowedPackages",), ("package",),
        packageOperationGate=True),
    "package.install": OperationRule(
        "package.install", RiskLevel.HIGH, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        TargetType.PACKAGE, ("allowedPackages",), ("package",),
        packageOperationGate=True),
    "package.uninstall": OperationRule(
        "package.uninstall", RiskLevel.HIGH, SideEffect.DESTRUCTIVE,
        Reversibility.IRREVERSIBLE, Idempotency.NON_IDEMPOTENT,
        TargetType.PACKAGE, ("allowedPackages",), ("package",),
        packageOperationGate=True),
    "package.clear_data": OperationRule(
        "package.clear_data", RiskLevel.HIGH, SideEffect.DESTRUCTIVE,
        Reversibility.IRREVERSIBLE, Idempotency.NON_IDEMPOTENT,
        TargetType.PACKAGE, ("allowedPackages",), ("package",),
        packageOperationGate=True, mandatoryConfirmation=True),
    # -- Android settings registry (s30) -----------------------------------
    "settings.read": OperationRule(
        "settings.read", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.ANDROID_SETTING, ("allowedAndroidSettings",), ("setting",)),
    "settings.write": OperationRule(
        "settings.write", RiskLevel.MEDIUM, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        TargetType.ANDROID_SETTING, ("allowedAndroidSettings",), ("setting",)),
    "settings.security_write": OperationRule(
        "settings.security_write", RiskLevel.HIGH, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        TargetType.ANDROID_SETTING, ("allowedAndroidSettings",), ("setting",)),
    "settings.security_control_disable": OperationRule(
        "settings.security_control_disable", RiskLevel.CRITICAL, SideEffect.DESTRUCTIVE,
        Reversibility.IRREVERSIBLE, Idempotency.NON_IDEMPOTENT,
        TargetType.ANDROID_SETTING, ("allowedAndroidSettings",), ("setting",)),
}
OPERATIONS.update({
    # -- accessibility registry (s31; tap/type_text/submit require ASK) ----
    "accessibility.inspect_ui": OperationRule(
        "accessibility.inspect_ui", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.UI, ("allowedUIActions",)),
    "accessibility.tap": OperationRule(
        "accessibility.tap", RiskLevel.MEDIUM, SideEffect.EXTERNAL_EFFECT,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        TargetType.UI, ("allowedUIActions",), ("target",),
        forcedDecision=PolicyDecisionValue.ASK),
    "accessibility.type_text": OperationRule(
        "accessibility.type_text", RiskLevel.MEDIUM, SideEffect.EXTERNAL_EFFECT,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        TargetType.UI, ("allowedUIActions",), ("target",),
        forcedDecision=PolicyDecisionValue.ASK),
    "accessibility.submit": OperationRule(
        "accessibility.submit", RiskLevel.HIGH, SideEffect.EXTERNAL_EFFECT,
        Reversibility.UNKNOWN, Idempotency.NON_IDEMPOTENT,
        TargetType.UI, ("allowedUIActions",), ("target",),
        forcedDecision=PolicyDecisionValue.ASK),
    # -- network registry (s32) --------------------------------------------
    "network.dns_lookup": OperationRule(
        "network.dns_lookup", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.NETWORK_DOMAIN, ("allowedNetworkDomains",), ("hostname",)),
    "network.http_get": OperationRule(
        "network.http_get", RiskLevel.LOW, SideEffect.READ_ONLY,
        Reversibility.REVERSIBLE, Idempotency.IDEMPOTENT,
        TargetType.NETWORK_DOMAIN,
        ("allowedNetworkDomains", "allowedNetworkDestinations"), ("url",)),
    "network.download": OperationRule(
        "network.download", RiskLevel.MEDIUM, SideEffect.MUTATING,
        Reversibility.REVERSIBLE, Idempotency.CONDITIONALLY_IDEMPOTENT,
        TargetType.NETWORK_DOMAIN, ("allowedNetworkDomains",),
        ("url", "destination"), destinationArg="destination"),
    "network.http_mutation": OperationRule(
        "network.http_mutation", RiskLevel.HIGH, SideEffect.EXTERNAL_EFFECT,
        Reversibility.UNKNOWN, Idempotency.NON_IDEMPOTENT,
        TargetType.NETWORK_DOMAIN, ("allowedNetworkDomains",), ("url",)),
    # -- generic command execution (s36-s37) --------------------------------
    "shell.exec": OperationRule(
        "shell.exec", RiskLevel.CRITICAL, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        requiredArgs=("command",)),
    "adb.shell": OperationRule(
        "adb.shell", RiskLevel.CRITICAL, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        requiredArgs=("command",), mandatoryConfirmation=True),
    "root.shell": OperationRule(
        "root.shell", RiskLevel.CRITICAL, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN,
        requiredArgs=("command",), mandatoryConfirmation=True),
    # -- out-of-scope privileged capabilities (s33-s35) ---------------------
    "shizuku.execute_structured": OperationRule(
        "shizuku.execute_structured", RiskLevel.CRITICAL, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN, outOfScope=True),
    "adb.structured_operation": OperationRule(
        "adb.structured_operation", RiskLevel.CRITICAL, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN, outOfScope=True),
    "root.structured_operation": OperationRule(
        "root.structured_operation", RiskLevel.CRITICAL, SideEffect.MUTATING,
        Reversibility.UNKNOWN, Idempotency.UNKNOWN, outOfScope=True),
})

# PROTECTED_PATHS.md s4-s12: semantic protection classes. Name patterns are
# for runtime adapters; the engine matches concrete runtime mappings.
PROTECTED_PATH_CLASSES = {
    "P0": ["POLICY.md", "POLICY_RULES.md", "POLICY_RULES.*", "PROJECT_CONTRACT.md"],
    "P1": ["SECURITY.md", "THREAT_MODEL.md"],
    "P2": ["TASK_SCHEMA.md"],
    "P3": ["VERIFICATION.md"],
    "P4": ["CONTINUITY.md"],
    "P5": [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
           "credentials.*", "secrets.*", "secret.*", "tokens.*"],
    "P6": [],
    "P7": [],
    "P8": ["PROJECT_CONTRACT.md", "ARCHITECTURE.md", "INTERFACES.md",
           "TASK_SCHEMA.md", "POLICY.md", "POLICY_RULES.md", "SECURITY.md",
           "THREAT_MODEL.md", "VERIFICATION.md", "CONTINUITY.md",
           "ROADMAP.md", "DECISIONS.md"],
}
