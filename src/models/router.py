"""ModelRouter (INTERFACES.md s15).

Role-based dispatch (PLANNER / RESEARCHER / CODER / REVIEWER / VERIFIER /
GENERAL_AGENT). Selection MUST NOT grant authority: a stronger model does
not automatically receive broader permissions, higher limits, lower
verification requirements, or policy bypass capability (s15).
"""
from __future__ import annotations

from typing import Dict, Optional

from core import ModelRequest, ModelResponse
from core.enums import ModelRole

from .model_port import ModelPort


class ModelRouter:
    """Dispatches generate() calls to the port registered for the role."""

    def __init__(self, ports: Dict[ModelRole, ModelPort]):
        self.ports = dict(ports)

    def select(self, role: ModelRole, task_context: Optional[dict] = None,
               constraints: Optional[dict] = None) -> ModelRole:
        """Returns the role whose port will serve the request. Constraints
        are advisory only and never change authority (s15)."""
        if role in self.ports:
            return role
        raise ValueError(f"no model port registered for role {role}")

    def generate(self, role: ModelRole, request: ModelRequest) -> ModelResponse:
        return self.ports[self.select(role)].generate(request)
