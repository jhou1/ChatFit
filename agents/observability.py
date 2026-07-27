"""Backend-neutral, privacy-aware observability primitives for Agent execution."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping, Protocol

logger = logging.getLogger("chatfit.observability")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(_handler)
logger.propagate = False

_PROCESS_HASH_KEY = secrets.token_bytes(32)
_current_trace: ContextVar[TraceContext | None] = ContextVar(
    "chatfit_current_trace", default=None
)
_current_span_id: ContextVar[str | None] = ContextVar(
    "chatfit_current_span_id", default=None
)
_root_span_id: ContextVar[str | None] = ContextVar("chatfit_root_span_id", default=None)
_span_statuses: ContextVar[dict[str, str] | None] = ContextVar(
    "chatfit_span_statuses", default=None
)
_sink_override: ContextVar[ObservationSink | None] = ContextVar(
    "chatfit_observation_sink", default=None
)


@dataclass(frozen=True)
class TraceContext:
    """Correlation identifiers shared by every observation in one request."""

    trace_id: str
    request_id: str
    session_id: str
    user_key: str
    run_id: str | None = None
    case_id: str | None = None


@dataclass(frozen=True)
class Observation:
    """Stable event envelope that can be exported to any tracing backend."""

    signal: str
    name: str
    timestamp: str
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    request_id: str | None
    session_id: str | None
    user_key: str | None
    run_id: str | None
    case_id: str | None
    status: str | None
    duration_ms: float | None
    attributes: dict[str, Any]


class ObservationSink(Protocol):
    """Destination for structured observations."""

    def emit(self, observation: Observation) -> None:
        """Emit an observation without changing application behavior."""


class LoggingSink:
    """Write one JSON observation per log record."""

    def emit(self, observation: Observation) -> None:
        logger.info(
            "agent_observation %s", json.dumps(asdict(observation), sort_keys=True)
        )


class InMemorySink:
    """Test sink that retains observations in emission order."""

    def __init__(self) -> None:
        self.observations: list[Observation] = []

    def emit(self, observation: Observation) -> None:
        self.observations.append(observation)


_default_sink: ObservationSink = LoggingSink()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _hash_key() -> bytes:
    configured = os.environ.get("OBSERVABILITY_HASH_KEY")
    return configured.encode("utf-8") if configured else _PROCESS_HASH_KEY


def private_fingerprint(value: str) -> str:
    """Return a keyed digest suitable for correlation without exposing content."""

    return hmac.new(_hash_key(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_user_identifier(user_id: str) -> str:
    """Create the privacy-safe user correlation key used in traces."""

    return private_fingerprint(f"user:{user_id}")


def content_attributes(value: str, prefix: str = "content") -> dict[str, Any]:
    """Describe sensitive content without recording the content itself."""

    return {
        f"{prefix}.chars": len(value),
        f"{prefix}.fingerprint": private_fingerprint(value),
    }


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    return type(value).__name__


def _safe_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    if not attributes:
        return {}
    return {str(key): _safe_value(value) for key, value in attributes.items()}


def _emit(
    signal: str,
    name: str,
    *,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    status: str | None = None,
    duration_ms: float | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> None:
    """Emit fail-open: telemetry failures must never affect the chat request."""

    context = _current_trace.get()
    observation = Observation(
        signal=signal,
        name=name,
        timestamp=_now(),
        trace_id=context.trace_id if context else None,
        span_id=span_id,
        parent_span_id=parent_span_id,
        request_id=context.request_id if context else None,
        session_id=context.session_id if context else None,
        user_key=context.user_key if context else None,
        run_id=context.run_id if context else None,
        case_id=context.case_id if context else None,
        status=status,
        duration_ms=duration_ms,
        attributes=_safe_attributes(attributes),
    )
    sink = _sink_override.get() or _default_sink
    try:
        sink.emit(observation)
    except Exception:
        logger.warning("Observation sink failed; continuing without telemetry")


def current_trace_context() -> TraceContext | None:
    return _current_trace.get()


def emit_event(name: str, attributes: Mapping[str, Any] | None = None) -> None:
    """Emit an instantaneous event under the active span."""

    _emit(
        "event",
        name,
        span_id=_current_span_id.get(),
        attributes=attributes,
    )


def mark_current_span_status(status: str) -> None:
    """Override the final status of the active span."""

    span_id = _current_span_id.get()
    statuses = _span_statuses.get()
    if span_id is not None and statuses is not None:
        statuses[span_id] = status


def mark_trace_status(status: str) -> None:
    """Override the final status of the root request span."""

    span_id = _root_span_id.get()
    statuses = _span_statuses.get()
    if span_id is not None and statuses is not None:
        statuses[span_id] = status


def error_attributes(error: BaseException) -> dict[str, Any]:
    """Return stable, non-sensitive error classification."""

    if isinstance(error, TimeoutError):
        code = "timeout"
        retryable = True
    elif isinstance(error, ConnectionError):
        code = "connection"
        retryable = True
    elif isinstance(error, OSError):
        code = "io"
        retryable = True
    else:
        code = "application"
        retryable = False
    return {
        "error.type": type(error).__name__,
        "error.code": code,
        "error.retryable": retryable,
    }


@contextmanager
def observation_sink(sink: ObservationSink) -> Iterator[None]:
    """Temporarily route observations to a sink, primarily for tests."""

    token = _sink_override.set(sink)
    try:
        yield
    finally:
        _sink_override.reset(token)


@contextmanager
def start_trace(
    name: str,
    *,
    request_id: str,
    session_id: str,
    user_key: str,
    trace_id: str | None = None,
    run_id: str | None = None,
    case_id: str | None = None,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[TraceContext]:
    """Start a root request trace and make it available through ContextVar."""

    context = TraceContext(
        trace_id=trace_id or _new_id(),
        request_id=request_id,
        session_id=session_id,
        user_key=user_key,
        run_id=run_id,
        case_id=case_id,
    )
    span_id = _new_id()
    statuses: dict[str, str] = {}
    trace_token = _current_trace.set(context)
    span_token = _current_span_id.set(span_id)
    root_token = _root_span_id.set(span_id)
    statuses_token = _span_statuses.set(statuses)
    started = time.monotonic()
    status = "ok"
    end_attributes: dict[str, Any] = {}
    _emit(
        "span.start",
        name,
        span_id=span_id,
        attributes=attributes,
    )
    try:
        yield context
    except BaseException as error:
        status = "error"
        end_attributes.update(error_attributes(error))
        raise
    finally:
        final_status = statuses.get(span_id, status)
        _emit(
            "span.end",
            name,
            span_id=span_id,
            status=final_status,
            duration_ms=(time.monotonic() - started) * 1000,
            attributes=end_attributes,
        )
        _span_statuses.reset(statuses_token)
        _root_span_id.reset(root_token)
        _current_span_id.reset(span_token)
        _current_trace.reset(trace_token)


@contextmanager
def observe_span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[str]:
    """Create a child span while preserving async task-local parentage."""

    span_id = _new_id()
    parent_span_id = _current_span_id.get()
    statuses = _span_statuses.get()
    statuses_token = None
    if statuses is None:
        statuses = {}
        statuses_token = _span_statuses.set(statuses)
    span_token = _current_span_id.set(span_id)
    started = time.monotonic()
    status = "ok"
    end_attributes: dict[str, Any] = {}
    _emit(
        "span.start",
        name,
        span_id=span_id,
        parent_span_id=parent_span_id,
        attributes=attributes,
    )
    try:
        yield span_id
    except BaseException as error:
        status = "error"
        end_attributes.update(error_attributes(error))
        raise
    finally:
        final_status = statuses.get(span_id, status)
        _emit(
            "span.end",
            name,
            span_id=span_id,
            parent_span_id=parent_span_id,
            status=final_status,
            duration_ms=(time.monotonic() - started) * 1000,
            attributes=end_attributes,
        )
        _current_span_id.reset(span_token)
        if statuses_token is not None:
            _span_statuses.reset(statuses_token)
