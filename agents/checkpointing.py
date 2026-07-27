"""Observed LangGraph SQLite checkpointer."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.observability import emit_event, observe_span


class ObservedAsyncSqliteSaver(AsyncSqliteSaver):
    """Add fail-open telemetry around checkpoint persistence operations."""

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        with observe_span("checkpoint.load"):
            checkpoint = await super().aget_tuple(config)
            emit_event(
                "checkpoint.loaded", {"checkpoint.found": checkpoint is not None}
            )
            return checkpoint

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        with observe_span("checkpoint.list", {"checkpoint.limit": limit}):
            count = 0
            async for checkpoint in super().alist(
                config, filter=filter, before=before, limit=limit
            ):
                count += 1
                yield checkpoint
            emit_event("checkpoint.listed", {"checkpoint.count": count})

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        with observe_span("checkpoint.save"):
            saved_config = await super().aput(
                config, checkpoint, metadata, new_versions
            )
            emit_event("checkpoint.saved")
            return saved_config

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with observe_span(
            "checkpoint.save_writes",
            {"checkpoint.write_count": len(writes)},
        ):
            await super().aput_writes(config, writes, task_id, task_path)
            emit_event("checkpoint.writes_saved")

    async def adelete_thread(self, thread_id: str) -> None:
        with observe_span("checkpoint.delete_thread"):
            await super().adelete_thread(thread_id)
            emit_event("checkpoint.thread_deleted")
