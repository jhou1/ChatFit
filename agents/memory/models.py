"""Typed records used by the durable user-memory store."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryType(StrEnum):
    """Supported categories of durable user memory."""

    TRAINING_TEMPLATE = "training_template"
    TRAINING_PREFERENCE = "training_preference"
    DIETARY_PREFERENCE = "dietary_preference"
    HEALTH_CONSTRAINT = "health_constraint"
    PROFILE = "profile"
    OTHER = "other"


class UserMemory(BaseModel):
    """An immutable user-memory record returned by the store."""

    model_config = ConfigDict(frozen=True)

    id: str
    owner_key: str
    memory_type: MemoryType
    canonical_key: str
    display_name: str
    content: str
    aliases: tuple[str, ...] = ()
    version: int = Field(ge=1)
    created_at: str
    updated_at: str


class NewUserMemory(BaseModel):
    """The data required to create a memory."""

    model_config = ConfigDict(frozen=True)

    memory_type: MemoryType
    canonical_key: str
    display_name: str
    content: str
    aliases: tuple[str, ...] = ()


class MemoryUpdate(BaseModel):
    """The replacement values for an existing memory."""

    model_config = ConfigDict(frozen=True)

    display_name: str
    content: str
    aliases: tuple[str, ...] = ()
    expected_version: int = Field(ge=1)


class RememberResult(BaseModel):
    """The result of an idempotent remember operation."""

    model_config = ConfigDict(frozen=True)

    status: Literal["created", "unchanged"]
    memory: UserMemory


class MemoryMutationDecision(BaseModel):
    """A structured interpretation of one explicit memory command."""

    model_config = ConfigDict(frozen=True)

    intent: Literal["remember", "update", "forget", "clarify"]
    memory_type: MemoryType | None = None
    canonical_key: str | None = None
    display_name: str | None = None
    content: str | None = None
    aliases: tuple[str, ...] = ()
    target_query: str | None = None
    clarification_question: str | None = None


class PendingMemoryAction(BaseModel):
    """A mutation awaiting an exact target or other missing information."""

    model_config = ConfigDict(frozen=True)

    decision: MemoryMutationDecision
    candidate_ids: tuple[str, ...] = ()
    candidate_versions: tuple[int, ...] = ()
    question: str


class MemoryAgentResult(BaseModel):
    """The user-facing response and optional mutation awaiting clarification."""

    model_config = ConfigDict(frozen=True)

    response: str
    pending: PendingMemoryAction | None = None


class MemoryConflictError(Exception):
    """Raised when a requested memory mutation conflicts with existing data."""


class StaleMemoryError(Exception):
    """Raised when an update is based on an obsolete memory version."""
