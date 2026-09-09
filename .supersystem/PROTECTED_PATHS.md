PROTECTED_PATHS.md

Version: 1.0
Status: FROZEN
Registry Version: 1.0
Authority: Referenced by POLICY_RULES.md

1. Purpose

This document defines the protected filesystem target registry for v1.

Protected targets are resources that MUST NOT be freely modified by filesystem tools because modification could affect:

policy authority;
security controls;
task authority;
verification authority;
credentials;
audit integrity;
runtime integrity;
system security.

This registry is authoritative for protected-target classification.

2. Core Rule

A filesystem target MUST be evaluated against this registry before mutation.

If a target cannot be classified:

DENY

Unknown security-sensitive paths MUST be treated conservatively.

3. Protection Classes

P0 — POLICY AUTHORITY
P1 — SECURITY AUTHORITY
P2 — TASK AUTHORITY
P3 — VERIFICATION AUTHORITY
P4 — AUDIT / CONTINUITY AUTHORITY
P5 — CREDENTIAL / SECRET STORAGE
P6 — RUNTIME AUTHORITY
P7 — ANDROID SECURITY / SYSTEM
P8 — PROJECT GOVERNANCE

4. P0 — Policy Authority

Protected:

POLICY.md
POLICY_RULES.md
POLICY_RULES.*
PROJECT_CONTRACT.md
policy configuration
policy rule registry
authorization configuration
permission configuration

Runtime-specific canonical paths MUST be registered by the runtime adapter.

Any attempt to modify active policy authority is:

CRITICAL

Policy maintenance is an administrative operation. It MUST be performed only through an explicit, separately authorized workflow that is outside the scope of ordinary autonomous task execution. This workflow MUST:

be initiated by a user or system authority;
be recorded as an administrative action;
require explicit human or system authorization independent of the acting model;
not be available to the acting model, subagents, or runtime extensions.

The acting model and its subagents MUST NOT be able to modify policy authority through filesystem tools.

5. P1 — Security Authority

Protected:

SECURITY.md
THREAT_MODEL.md
security configuration
security control configuration
security boundary configuration
authentication configuration
authorization configuration
access-control configuration

Security authority MUST NOT be treated as ordinary project data.

6. P2 — Task Authority

Protected:

TASK_SCHEMA.md
task state database
task authorization state
task resource-limit state
task lifecycle state
task identity registry

The authoritative machine-readable task store MUST be protected.

Human-readable derived task views may be writable only through controlled workflows.

7. P3 — Verification Authority

Protected:

VERIFICATION.md
completion configuration
completion contracts
verification configuration
verification result store
completion decision store
evidence integrity metadata

Unauthorized modification can invalidate completion authority.

8. P4 — Audit and Continuity Authority

Protected:

CONTINUITY.md
audit log
audit integrity metadata
action journal
checkpoint store
recovery state
integrity chain metadata

Security-sensitive audit and continuity state MUST be integrity-protected according to SECURITY.md.

9. P5 — Credentials and Secrets

Protected categories include:

API credentials
authentication tokens
private keys
SSH private keys
OAuth credentials
session credentials
password stores
credential databases
secret stores
wallet/key material

Plaintext secret files MUST NOT be treated as ordinary writable project files.

If a secret target is unknown:

DENY

10. P6 — Runtime Authority

Protected:

agent runtime authority
extension configuration affecting security
tool registry
tool permission registry
model authorization configuration
runtime startup authorization
plugin security configuration

Runtime code/configuration capable of bypassing Policy MUST be protected.

11. P7 — Android Security and System

The following Android resource classes are protected:

system security configuration
device security configuration
credential/security services
package-manager security state
SELinux/security policy state
system authorization state
root/system configuration
device-encryption configuration
lock-screen/security configuration
security-sensitive Android settings

The exact filesystem path mapping is platform-specific.

Runtime adapters MUST provide a versioned canonical mapping where required.

The Policy Engine MUST NOT infer Android protected paths from arbitrary strings.

12. P8 — Project Governance

Protected project governance files include:

PROJECT_CONTRACT.md
ARCHITECTURE.md
INTERFACES.md
TASK_SCHEMA.md
POLICY.md
POLICY_RULES.md
SECURITY.md
THREAT_MODEL.md
VERIFICATION.md
CONTINUITY.md
ROADMAP.md
DECISIONS.md

Changing governance documents is not inherently forbidden, but they are protected from ordinary autonomous mutation.

Changes MUST follow project change-control requirements and are not within the authority of the acting model or its subagents.

13. Runtime Path Mapping

This registry defines semantic protection classes.

Runtime adapters MUST map actual filesystem paths to these classes.

A runtime adapter MUST NOT:

remove protection;
downgrade a protected target;
reinterpret an unknown security target as safe;
dynamically grant write access.

If runtime mapping is missing or ambiguous:

DENY

14. Runtime Mapping Prerequisite

Before filesystem mutation is enabled for a runtime adapter, that adapter MUST provide a concrete, versioned mapping from the semantic protected-path classes in this document to actual runtime filesystem paths.

The mapping MUST be:

versioned;
deterministic;
validated;
unambiguous;
available to Policy before authorization.

Missing, stale, ambiguous, invalid, or conflicting mappings MUST fail closed and prevent the affected filesystem mutation.

The semantic registry remains authoritative at the policy level while runtime adapters provide the concrete platform mapping.

15. Project-Specific Protected Roots

Each project MAY register additional protected roots.

Example:

projectProtectedPaths:
  /project/.supersystem/**
  /project/.git/config
  /project/.env

Project-specific additions MUST be versioned and cannot weaken this registry.

16. Secret Files

The following filename patterns SHOULD be treated as security-sensitive when their content may contain credentials:

.env
.env.*
*.pem
*.key
*.p12
*.pfx
credentials.*
secrets.*
secret.*
tokens.*

However, filename matching alone is not sufficient to grant or deny access.

The runtime MUST combine:

canonical path;
registered scope;
file classification;
operation;
task authorization.

Unknown secret-like targets SHOULD be conservatively denied for mutation.

17. Deletion Protection

Protected targets MUST NOT be deleted through ordinary filesystem deletion.

Deletion of a protected target requires a dedicated authorized workflow.

Unknown protection classification:

DENY

18. Read vs Write

Protection does not automatically mean that reading is forbidden.

The Policy Engine independently evaluates:

read
write
delete
search

A protected target may be:

readable but not writable
or
not readable and not writable

depending on its security classification and task scope.

19. Registry Versioning

Every protected-path registry has a version.

A task using filesystem mutation MUST record the applicable registry version.

If the registry version cannot be validated:

DENY

20. Integrity

The registry itself is security-relevant.

Its integrity MUST be protected according to SECURITY.md.

A corrupted registry MUST NOT be replaced automatically using model-generated content.

Recovery requires trusted state restoration or explicit administrative intervention.

21. v1 Minimum Registry Requirement

Before filesystem mutation becomes executable in v1, the implementation MUST provide:

this semantic registry;
runtime-specific canonical path mappings;
project-specific protected-root configuration;
deterministic matching;
integrity validation;
adversarial tests.

22. Adversarial Tests

At minimum:

PP-01 Policy file mutation → DENY
PP-02 Security file mutation → DENY
PP-03 Task authority mutation → DENY
PP-04 Verification authority mutation → DENY
PP-05 Audit state mutation → DENY
PP-06 Credential file mutation → DENY
PP-07 Unknown security-sensitive path → DENY
PP-08 Ambiguous canonical path → DENY
PP-09 Symlink/path traversal ambiguity → DENY
PP-10 Registry integrity failure → DENY/BLOCKED
PP-11 Subagent attempting protected-path write → DENY
PP-12 Model attempting registry modification → DENY
PP-13 Registry version mismatch → DENY
PP-14 Protected target deletion → DENY
PP-15 Allow + deny target overlap → DENY
PP-16 Missing runtime mapping → DENY
PP-17 Ambiguous runtime mapping → DENY
PP-18 Stale runtime mapping → DENY

23. Relationship to Policy

POLICY_RULES.md remains the authoritative source for:

operation risk;
permission mode;
ALLOW/ASK/DENY;
target authorization semantics.

This registry supplies protected-target classification.

The two must be evaluated together.

24. Cross-Document Review Rule

If any of the following documents materially changes:

TASK_SCHEMA.md
PROTECTED_PATHS.md
POLICY_RULES.md

the affected document MUST be reviewed together with the other two for cross-document consistency before it can be frozen.

A document MUST NOT be independently frozen if its changes can alter authorization semantics in the other documents.

25. Status

Version: 1.0

Status: FROZEN

This registry establishes the minimum semantic protected-target baseline and the mandatory runtime mapping prerequisite before filesystem mutation is enabled.
