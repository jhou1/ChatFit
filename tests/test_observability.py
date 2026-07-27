import json

import pytest

from agents.observability import (
    InMemorySink,
    Observation,
    content_attributes,
    hash_user_identifier,
    mark_current_span_status,
    mark_trace_status,
    observation_sink,
    observe_span,
    start_trace,
)


def test_user_identifier_is_keyed_and_stable(monkeypatch):
    monkeypatch.setenv("OBSERVABILITY_HASH_KEY", "stable-test-key")

    first = hash_user_identifier("telegram-user-123")
    second = hash_user_identifier("telegram-user-123")

    assert first == second
    assert first != "telegram-user-123"
    assert "telegram-user-123" not in first


def test_trace_records_parent_child_path_without_sensitive_content(monkeypatch):
    monkeypatch.setenv("OBSERVABILITY_HASH_KEY", "stable-test-key")
    sink = InMemorySink()
    sensitive_message = "I weigh 80kg and my token is secret"

    with observation_sink(sink):
        with start_trace(
            "chat.request",
            trace_id="trace-1",
            request_id="request-1",
            session_id="session-1",
            user_key=hash_user_identifier("raw-user-id"),
            attributes=content_attributes(sensitive_message, "request.message"),
        ):
            with observe_span("assistant_selector"):
                pass

    starts = [
        observation
        for observation in sink.observations
        if observation.signal == "span.start"
    ]
    assert [observation.name for observation in starts] == [
        "chat.request",
        "assistant_selector",
    ]
    assert starts[1].parent_span_id == starts[0].span_id
    serialized = json.dumps([observation.__dict__ for observation in sink.observations])
    assert sensitive_message not in serialized
    assert "raw-user-id" not in serialized


def test_observation_sink_failure_is_fail_open():
    class BrokenSink:
        def emit(self, observation: Observation) -> None:
            raise RuntimeError("backend unavailable")

    with observation_sink(BrokenSink()):
        with start_trace(
            "chat.request",
            request_id="request-1",
            session_id="session-1",
            user_key="user-key",
        ):
            with observe_span("tool.example"):
                pass


def test_failed_span_has_sanitized_error_classification():
    sink = InMemorySink()

    with pytest.raises(ValueError, match="private detail"):
        with observation_sink(sink):
            with start_trace(
                "chat.request",
                request_id="request-1",
                session_id="session-1",
                user_key="user-key",
            ):
                with observe_span("tool.example"):
                    raise ValueError("private detail")

    serialized = json.dumps([observation.__dict__ for observation in sink.observations])
    tool_end = next(
        observation
        for observation in sink.observations
        if observation.signal == "span.end" and observation.name == "tool.example"
    )
    assert tool_end.status == "error"
    assert tool_end.attributes["error.type"] == "ValueError"
    assert "private detail" not in serialized


def test_interrupted_status_can_be_set_for_current_and_root_spans():
    sink = InMemorySink()

    with observation_sink(sink):
        with start_trace(
            "chat.request",
            request_id="request-1",
            session_id="session-1",
            user_key="user-key",
        ):
            with observe_span("graph.run"):
                mark_current_span_status("interrupted")
                mark_trace_status("interrupted")

    ended = {
        observation.name: observation.status
        for observation in sink.observations
        if observation.signal == "span.end"
    }
    assert ended == {
        "graph.run": "interrupted",
        "chat.request": "interrupted",
    }
