import aiosqlite
import pytest
from langgraph.checkpoint.base import empty_checkpoint

from agents.checkpointing import ObservedAsyncSqliteSaver
from agents.observability import (
    InMemorySink,
    observation_sink,
    start_trace,
)


@pytest.mark.asyncio
async def test_sqlite_checkpointer_emits_save_and_load_spans(tmp_path):
    sink = InMemorySink()
    db_path = tmp_path / "checkpointer.db"
    config = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}
    metadata = {"source": "input", "step": 0, "parents": {}}

    async with aiosqlite.connect(db_path) as connection:
        saver = ObservedAsyncSqliteSaver(connection)
        await saver.setup()

        with observation_sink(sink):
            with start_trace(
                "chat.request",
                request_id="request-1",
                session_id="thread-1",
                user_key="user-key",
            ):
                saved_config = await saver.aput(
                    config,
                    empty_checkpoint(),
                    metadata,
                    {},
                )
                loaded = await saver.aget_tuple(saved_config)
                await saver.aput_writes(
                    saved_config,
                    [("messages", "value")],
                    task_id="task-1",
                )

    assert loaded is not None
    completed_spans = {
        observation.name: observation.status
        for observation in sink.observations
        if observation.signal == "span.end"
    }
    assert completed_spans["checkpoint.save"] == "ok"
    assert completed_spans["checkpoint.load"] == "ok"
    assert completed_spans["checkpoint.save_writes"] == "ok"
