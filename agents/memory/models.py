"""Typed records used by the durable user-memory store."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    intent: Literal["remember", "update", "forget", "clarify"] | None = None
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

    owner_key: str
    operation: Literal["remember", "update", "forget"]
    decision: MemoryMutationDecision
    candidate_ids: tuple[str, ...] = ()
    candidate_versions: tuple[int, ...] = ()
    requires_confirmation: bool = False
    question: str

    @model_validator(mode="after")
    def candidate_versions_align(self) -> Self:
        """Require one captured version for every captured candidate ID."""
        if len(self.candidate_ids) != len(self.candidate_versions):
            raise ValueError("candidate IDs and versions must have equal lengths")
        return self


class MemoryAgentResult(BaseModel):
    """The user-facing response and optional mutation awaiting clarification."""

    model_config = ConfigDict(frozen=True)

    response: str
    pending: PendingMemoryAction | None = None
    mutation_committed: bool = False
    mutation_result: (
        Literal[
            "committed",
            "unchanged",
            "conflict",
            "clarify",
            "failed",
            "forgotten",
        ]
        | None
    ) = None
    memory_id: str | None = None


class MemoryConflictError(Exception):
    """Raised when a requested memory mutation conflicts with existing data."""


class StaleMemoryError(Exception):
    """Raised when an update is based on an obsolete memory version."""
