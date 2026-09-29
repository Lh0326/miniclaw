from miniclaw.context.builder import DEFAULT_CONTEXT_PRIORITIES
from miniclaw.context.types import ContextSource
from miniclaw.memory.store import SqliteMemoryStore


class MemoryContextSource:
    def __init__(self, store: SqliteMemoryStore, *, limit: int = 5) -> None:
        self.store = store
        self.limit = limit

    async def build(self, query: str) -> ContextSource:
        records = await self.store.search(query, limit=self.limit)
        text = "\n".join(
            f"[{record.memory_id}] source={record.source_ref}\n{record.text}"
            for record in records
        )
        return ContextSource(
            "memory",
            text,
            DEFAULT_CONTEXT_PRIORITIES["memory"],
        )


class MemoryContextProvider:
    """Adapt MemoryContextSource to the AgentLoop context provider protocol."""

    def __init__(self, store: SqliteMemoryStore, *, limit: int = 5) -> None:
        self.source = MemoryContextSource(store, limit=limit)

    async def build(self, prompt: str) -> tuple[ContextSource, ...]:
        return (await self.source.build(prompt),)
