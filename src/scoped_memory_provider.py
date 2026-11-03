"""Continuity-safe episodic memory contracts, layered on the native provider.

A fork addition. It lives beside ``src/memory_provider.py`` rather than inside
it so an upstream merge has one line to reconcile in that file instead of three
blocks. Upstream owns ``MemoryProvider``, ``MemoryRecord`` and
``MemorySearchHit``, and nothing here changes them: a scoped provider is a
separate, opt-in contract, and the legacy native memory deliberately cannot
masquerade as one.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from src.memory_provider import MemoryRecord, MemorySearchHit

@dataclass(frozen=True)
class ScopedMemoryScope:
    """Non-optional tenancy and provenance contract for episodic memory.

    This contract is intentionally separate from the legacy ``MemoryProvider``
    methods.  Existing native memory has only owner/session metadata and must
    not masquerade as a scoped continuity provider until it passes these
    constraints.  Grant IDs are deliberately opaque audit references: callers
    decide whether a grant is valid before building this scope; providers never
    infer authority from arbitrary model text.
    """

    owner_id: str
    home_kind: str
    project_id: Optional[str]
    session_id: Optional[str]
    sensitivity: str = "normal"
    expires_at: Optional[datetime] = None
    provenance_kind: str = ""
    provenance_id: str = ""
    grant_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.owner_id:
            raise ValueError("scoped memory requires an owner")
        if self.home_kind not in {"personal", "project"}:
            raise ValueError("scoped memory home kind is invalid")
        if self.home_kind == "project" and not self.project_id:
            raise ValueError("project scoped memory requires a project")
        if self.home_kind == "personal" and self.project_id is not None:
            raise ValueError("personal scoped memory cannot carry a project")
        if self.sensitivity not in {"normal", "sensitive"}:
            raise ValueError("scoped memory sensitivity is invalid")
        if any(not grant_id for grant_id in self.grant_ids):
            raise ValueError("scoped memory grant IDs must be non-empty")


@dataclass(frozen=True)
class ScopedMemoryQuery:
    """A provider request with its scope fixed before recall starts."""

    text: str
    scope: ScopedMemoryScope
    top_k: int = 5

    def __post_init__(self) -> None:
        if not str(self.text or "").strip():
            raise ValueError("scoped memory query is empty")
        if self.top_k < 1 or self.top_k > 20:
            raise ValueError("scoped memory query limit is invalid")

class ScopedMemoryProvider(ABC):
    """Provider-neutral contract for continuity-safe episodic retrieval.

    Implementations are opt-in.  They must verify the exact scope *before*
    ranking, store provenance with each record, honour expiry, and return no
    sensitive record unless the calling policy built a matching sensitive scope.
    The compiler remains the sole prompt-admission point.
    """

    scoped_provider_id = "unknown-scoped"

    @abstractmethod
    async def remember_scoped(
        self,
        text: str,
        *,
        scope: ScopedMemoryScope,
        category: str = "fact",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        """Store a source-validated episodic record within exactly one scope."""

    @abstractmethod
    async def recall_scoped(self, query: ScopedMemoryQuery) -> List[MemorySearchHit]:
        """Return only records matching the complete query scope."""

    @abstractmethod
    async def list_scoped(self, *, scope: ScopedMemoryScope, limit: int = 100) -> List[MemoryRecord]:
        """List non-expired records from exactly one owner/home/project scope."""

    @abstractmethod
    async def delete_scoped(self, memory_id: str, *, scope: ScopedMemoryScope) -> bool:
        """Delete one record only when its complete stored scope matches."""


class ScopedMemoryProviderRegistry:
    """Select a continuity-safe provider for the synchronous prompt compiler.

    ``ContextCompiler`` is intentionally synchronous because the native chat
    path calls it while its async loop is already active.  A scoped provider may opt into that bounded path by
    exposing ``recall_scoped_sync``.  Providers that only expose asynchronous
    I/O are never run with a nested event loop; they require a future async
    compiler integration instead of becoming an accidental prompt authority.
    """

    def __init__(self, providers: Optional[Iterable[ScopedMemoryProvider]] = None):
        self._providers: Dict[str, ScopedMemoryProvider] = {}
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: ScopedMemoryProvider) -> None:
        provider_id = str(getattr(provider, "scoped_provider_id", "") or "")
        if not provider_id or provider_id == "unknown-scoped":
            raise ValueError("scoped memory provider requires a stable provider id")
        if provider_id in self._providers:
            raise ValueError(f"Scoped memory provider already registered: {provider_id}")
        self._providers[provider_id] = provider

    def active(self) -> List[ScopedMemoryProvider]:
        return [provider for provider in self._providers.values() if getattr(provider, "enabled", True)]

    def recall_scoped_sync(self, query: ScopedMemoryQuery) -> List[MemorySearchHit]:
        """Return one exact-scope provider's bounded hits.

        The first enabled synchronous provider is the explicit source of this
        recall.  Combining rankings from multiple backends would quietly
        change context authority and duplicate memories, so that is deferred
        until a separately reviewed provider-selection design exists.
        """
        for provider in self.active():
            recall = getattr(provider, "recall_scoped_sync", None)
            if not callable(recall):
                continue
            raw_hits = recall(query)
            accepted: List[MemorySearchHit] = []
            seen: set[str] = set()
            for hit in raw_hits or []:
                if not isinstance(hit, MemorySearchHit):
                    continue
                memory = hit.memory
                stored_scope = memory.scope
                if not self._matches_query_scope(stored_scope, query.scope):
                    continue
                if not memory.id or memory.id in seen:
                    continue
                seen.add(memory.id)
                accepted.append(hit)
                if len(accepted) >= query.top_k:
                    break
            return accepted
        raise RuntimeError("no synchronous scoped memory provider is available")

    @staticmethod
    def _matches_query_scope(stored: Optional[ScopedMemoryScope], requested: ScopedMemoryScope) -> bool:
        if stored is None:
            return False
        return (
            stored.owner_id == requested.owner_id
            and stored.home_kind == requested.home_kind
            and stored.project_id == requested.project_id
            and stored.sensitivity == requested.sensitivity
            and bool(stored.provenance_kind)
            and bool(stored.provenance_id)
        )
