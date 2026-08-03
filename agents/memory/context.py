"""Prompt-safe presentation of durable user memory."""

from collections.abc import Sequence

from agents.memory.models import UserMemory

DURABLE_MEMORY_HEADER = "[Durable User Memories — database-backed]"
SHORT_TERM_SUMMARY_HEADER = "[Short-term Conversation Summary]"


def format_durable_memories(memories: Sequence[UserMemory]) -> str:
    """Render one freshly loaded, clearly sourced durable-memory block."""
    lines = [DURABLE_MEMORY_HEADER]
    if not memories:
        return "\n".join((*lines, "(none stored)"))
    lines.extend(
        f"- [{memory.memory_type.value}] {memory.display_name}: {memory.content}"
        for memory in memories
    )
    return "\n".join(lines)


def append_agent_context(
    prompt: str,
    *,
    memory_context: str | None,
    summary: str | None,
) -> str:
    """Append durable and short-term context with unambiguous shared labels."""
    durable_block = memory_context or f"{DURABLE_MEMORY_HEADER}\n(none loaded)"
    sections = [prompt.rstrip(), durable_block]
    if summary:
        sections.append(f"{SHORT_TERM_SUMMARY_HEADER}\n{summary}")
    return "\n\n".join(sections)
