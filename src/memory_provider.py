"""Memory provider interfaces for native and external memory systems."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


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


@dataclass
class MemoryRecord:
    """Provider-neutral memory entry."""

    id: str
    text: str
    timestamp: int = 0
    category: str = "fact"
    source: str = "unknown"
    owner: Optional[str] = None
    session_id: Optional[str] = None
    scope: Optional[ScopedMemoryScope] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemorySearchHit:
    """A memory returned by provider recall."""

    memory: MemoryRecord
    provider_id: str
    score: Optional[float] = None


class MemoryProvider(ABC):
    """Base contract for Odysseus memory providers.

    The native memory provider should always be available. External providers
    can add recall/write behavior and their own tools without replacing the
    built-in local memory baseline.
    """

    provider_id = "unknown"
    display_name = "Unknown"
    enabled = True

    async def initialize(self) -> None:
        """Prepare provider resources before use."""

    async def shutdown(self) -> None:
        """Release provider resources."""

    @abstractmethod
    async def remember(
        self,
        text: str,
        *,
        owner: Optional[str] = None,
        session_id: Optional[str] = None,
        category: str = "fact",
        source: str = "user",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        """Store a memory and return the stored record."""

    @abstractmethod
    async def recall(
        self,
        query: str,
        *,
        owner: Optional[str] = None,
        top_k: int = 5,
    ) -> List[MemorySearchHit]:
        """Return provider memories relevant to the query."""

    @abstractmethod
    async def list_memories(
        self,
        *,
        owner: Optional[str] = None,
        limit: int = 100,
    ) -> List[MemoryRecord]:
        """List memories visible to the owner."""

    @abstractmethod
    async def delete(self, memory_id: str, *, owner: Optional[str] = None) -> bool:
        """Delete a memory by ID when allowed by the provider."""

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return provider-defined tool schemas when this provider is enabled."""
        return []

    async def handle_tool_call(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Handle a provider-defined tool call."""
        raise KeyError(f"Provider {self.provider_id} does not expose tool {name}")


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


class NativeMemoryProvider(MemoryProvider):
    """Provider adapter for Odysseus' built-in memory manager and vector store."""

    provider_id = "native"
    display_name = "Odysseus native memory"

    _CORE_FIELDS = {
        "id",
        "text",
        "timestamp",
        "source",
        "category",
        "uses",
        "owner",
        "session_id",
        "metadata",
    }

    def __init__(self, memory_manager, memory_vector=None):
        self.memory_manager = memory_manager
        self.memory_vector = memory_vector

    def _to_record(self, entry: Dict[str, Any]) -> MemoryRecord:
        metadata = {
            key: value
            for key, value in entry.items()
            if key not in self._CORE_FIELDS
        }
        stored_metadata = entry.get("metadata")
        if isinstance(stored_metadata, dict):
            metadata.update(stored_metadata)

        return MemoryRecord(
            id=entry.get("id", ""),
            text=entry.get("text", ""),
            timestamp=entry.get("timestamp", 0),
            category=entry.get("category", "fact"),
            source=entry.get("source", "unknown"),
            owner=entry.get("owner"),
            session_id=entry.get("session_id"),
            metadata=metadata,
        )

    async def remember(
        self,
        text: str,
        *,
        owner: Optional[str] = None,
        session_id: Optional[str] = None,
        category: str = "fact",
        source: str = "user",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        entry = self.memory_manager.add_entry(
            text,
            source=source,
            category=category,
            owner=owner,
        )
        if session_id:
            entry["session_id"] = session_id
        if metadata:
            entry["metadata"] = dict(metadata)

        # Strict load: read-modify-write. `load_all` degrades an unreadable
        # store to [], which would save this single entry over everything
        # already stored (issue #5673). The provider API has no error channel,
        # so MemoryStoreUnreadable propagates to the caller.
        memories = self.memory_manager.load_all_for_update()
        memories.append(entry)
        self.memory_manager.save(memories)

        if self._vector_available():
            self.memory_vector.add(entry["id"], entry["text"])

        return self._to_record(entry)

    async def recall(
        self,
        query: str,
        *,
        owner: Optional[str] = None,
        top_k: int = 5,
    ) -> List[MemorySearchHit]:
        memories = self.memory_manager.load(owner=owner)
        by_id = {m.get("id"): m for m in memories}

        if self._vector_available():
            hits: List[MemorySearchHit] = []
            for result in self.memory_vector.search(query, k=top_k):
                if not isinstance(result, dict):
                    continue
                memory_id = result.get("memory_id")
                entry = by_id.get(memory_id) if memory_id else result
                if not entry:
                    continue
                if owner is not None and entry.get("owner") != owner:
                    continue
                hits.append(
                    MemorySearchHit(
                        memory=self._to_record(entry),
                        provider_id=self.provider_id,
                        score=result.get("score"),
                    )
                )
            if hits:
                return hits

        fallback = self.memory_manager.get_relevant_memories(
            query,
            memories,
            max_items=top_k,
        )
        return [
            MemorySearchHit(
                memory=self._to_record(entry),
                provider_id=self.provider_id,
                score=None,
            )
            for entry in fallback
        ]

    async def list_memories(
        self,
        *,
        owner: Optional[str] = None,
        limit: int = 100,
    ) -> List[MemoryRecord]:
        return [
            self._to_record(entry)
            for entry in self.memory_manager.load(owner=owner)[:limit]
        ]

    async def delete(self, memory_id: str, *, owner: Optional[str] = None) -> bool:
        # Strict load for the same reason: `remaining` is derived from this
        # list and saved back, so it must never be built from a store we
        # failed to read.
        memories = self.memory_manager.load_all_for_update()
        remaining = []
        deleted_id = None

        for entry in memories:
            if entry.get("id") != memory_id:
                remaining.append(entry)
                continue
            if owner is not None and entry.get("owner") != owner:
                remaining.append(entry)
                continue
            deleted_id = entry.get("id")

        if deleted_id is None:
            return False

        self.memory_manager.save(remaining)
        if self._vector_available():
            self.memory_vector.remove(deleted_id)
        return True

    def _vector_available(self) -> bool:
        return bool(self.memory_vector and getattr(self.memory_vector, "healthy", True))


class MemoryProviderRegistry:
    """Container for native and optional external memory providers."""

    def __init__(self, providers: Optional[Iterable[MemoryProvider]] = None):
        self._providers: Dict[str, MemoryProvider] = {}
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: MemoryProvider) -> None:
        if provider.provider_id in self._providers:
            raise ValueError(f"Memory provider already registered: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> MemoryProvider:
        return self._providers[provider_id]

    def all(self) -> List[MemoryProvider]:
        return list(self._providers.values())

    def active(self) -> List[MemoryProvider]:
        return [provider for provider in self._providers.values() if provider.enabled]

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        schemas: List[Dict[str, Any]] = []
        seen: Dict[str, str] = {}

        for provider in self.active():
            for schema in provider.get_tool_schemas():
                name = self._tool_name(schema)
                if name in seen:
                    raise ValueError(
                        f"Memory tool name conflict: {name} from "
                        f"{provider.provider_id} already exposed by {seen[name]}"
                    )
                seen[name] = provider.provider_id
                schemas.append(schema)

        return schemas

    async def handle_tool_call(self, name: str, arguments: Dict[str, Any]) -> Any:
        provider_by_tool: Dict[str, MemoryProvider] = {}
        for provider in self.active():
            for schema in provider.get_tool_schemas():
                tool_name = self._tool_name(schema)
                if tool_name in provider_by_tool:
                    raise ValueError(
                        f"Memory tool name conflict: {tool_name} from "
                        f"{provider.provider_id} already exposed by "
                        f"{provider_by_tool[tool_name].provider_id}"
                    )
                provider_by_tool[tool_name] = provider

        provider = provider_by_tool.get(name)
        if provider:
            return await provider.handle_tool_call(name, arguments)
        raise KeyError(f"No active memory provider exposes tool {name}")

    @staticmethod
    def _tool_name(schema: Dict[str, Any]) -> str:
        if not isinstance(schema, dict):
            raise ValueError("Memory provider tool schema must be a dict")
        name = schema.get("name")
        if isinstance(name, str) and name:
            return name
        function = schema.get("function")
        if isinstance(function, dict):
            function_name = function.get("name")
            if isinstance(function_name, str) and function_name:
                return function_name
        raise ValueError("Memory provider tool schema is missing a tool name")
