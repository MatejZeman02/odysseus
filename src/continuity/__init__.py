"""Durable, non-destructive conversation continuity primitives."""

from .contracts import (
    ContextBundle,
    DeviceProfileV1,
    ProjectBriefV1,
    PersonalBriefV1,
    ResolvedScope,
    SemanticCheckpointProposalV1,
    ThreadCheckpointV1,
)
from .store import ContinuityStore, ScopeConflictError

__all__ = [
    "ContextBundle",
    "ContinuityStore",
    "DeviceProfileV1",
    "ProjectBriefV1",
    "PersonalBriefV1",
    "ResolvedScope",
    "SemanticCheckpointProposalV1",
    "ScopeConflictError",
    "ThreadCheckpointV1",
]
