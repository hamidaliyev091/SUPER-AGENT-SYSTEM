"""Deterministic target canonicalization and scope matching.

Implements TASK_SCHEMA.md s17: canonicalization MUST be deterministic and
identical for authorization and matching; ambiguous or unresolvable inputs
fail closed (canonicalize returns None -> the Policy Engine denies).

Scope matching uses deterministic glob semantics: "*" matches any run of
non-separator characters, "**" matches any run including separators,
"?" matches a single non-separator character, everything else is literal.
The same matching applies to filesystem paths, domains, package ids, etc.
"""
from __future__ import annotations

import os
import posixpath
import re
from typing import Optional

from core import TargetType

_PACKAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._]*")
_SETTING_RE = re.compile(r"[A-Za-z0-9_.]+")


def _translate_pattern(pattern: str) -> str:
    out = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                out.append(".*")
                index += 2
            else:
                out.append("[^/]*")
                index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return "".join(out)


def match_scope(canonical_value: str, patterns) -> bool:
    """True when the canonical value matches at least one scope pattern."""
    for pattern in patterns:
        if re.fullmatch(_translate_pattern(pattern), canonical_value):
            return True
    return False


class Canonicalizer:
    """Canonicalizes targets per kind. Returns None when ambiguous."""

    def __init__(self, base_dir: str = "/", resolve_symlinks: bool = True):
        self.base_dir = posixpath.normpath(base_dir) if base_dir else "/"
        self.resolve_symlinks = resolve_symlinks

    def canonicalize(self, target_type: TargetType, value: str) -> Optional[str]:
        if not isinstance(value, str) or not value or "\x00" in value:
            return None
        if target_type is TargetType.FILESYSTEM:
            return self._canonicalize_path(value)
        if target_type is TargetType.PACKAGE:
            return value if _PACKAGE_RE.fullmatch(value) else None
        if target_type is TargetType.NETWORK_DOMAIN:
            domain = value.strip().lower().rstrip(".")
            if not domain or re.search(r"[\s/\\\x00-\x1f]", domain):
                return None
            try:
                return domain.encode("idna").decode("ascii")
            except UnicodeError:
                return None
        if target_type is TargetType.NETWORK_DESTINATION:
            destination = value.strip().lower()
            if not destination:
                return None
            if ":" in destination:
                host, _, port = destination.rpartition(":")
                if not host or not port.isdigit() or not 0 <= int(port) <= 65535:
                    return None
            return destination
        if target_type is TargetType.ANDROID_SETTING:
            setting = value.strip().lower()
            return setting if _SETTING_RE.fullmatch(setting) else None
        if target_type is TargetType.UI:
            return value.strip()
        if target_type is TargetType.PROCESS:
            return value.strip()
        if target_type is TargetType.RESOURCE:
            return value.strip().upper()
        return None

    def _canonicalize_path(self, value: str) -> Optional[str]:
        path = value
        if not path.startswith("/"):
            path = posixpath.join(self.base_dir, path)
        path = posixpath.normpath(path)
        if self.resolve_symlinks:
            # Resolves symlinks and platform case/normalization rules where
            # the platform supports it (TASK_SCHEMA.md s17).
            path = os.path.realpath(path)
        if not path or not path.startswith("/") or "\x00" in path:
            return None
        return path
