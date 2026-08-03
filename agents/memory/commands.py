"""Deterministic grammar for explicit durable-memory commands."""

import re
from dataclasses import dataclass
from typing import Literal

MemoryOperation = Literal["remember", "update", "forget"]

_UPDATE_PATTERNS = (
    re.compile(
        r"^(?:把)?(?P<target>.+?)(?:更新成|改成)(?P<content>.*)$",
        re.DOTALL,
    ),
    re.compile(
        r"^修改(?P<target>.+?)(?:为|成)(?P<content>.*)$",
        re.DOTALL,
    ),
)
_TARGET_EDGE_CHARACTERS = " \t\r\n，。！？、,:：;；.?!「」『』“”‘’"
_REFERENTIAL_REMEMBER_PAYLOADS = frozenset(
    ("我的名字", "我的姓名", "我的昵称", "这个内容", "这条内容")
)


@dataclass(frozen=True)
class MemoryCommand:
    """A parser-authorized memory mutation and its exact user-supplied fields."""

    operation: MemoryOperation
    payload: str | None = None
    target_queries: tuple[str, ...] = ()


def _clean_target_query(value: str) -> str:
    return value.strip(_TARGET_EDGE_CHARACTERS)


def _target_variants(operation: MemoryOperation, target: str) -> tuple[str, ...]:
    cleaned = _clean_target_query(target)
    if not cleaned:
        return ()
    variants = [cleaned]
    if operation == "update" and cleaned.endswith("模板"):
        variants.append(_clean_target_query(cleaned[: -len("模板")]))
    if operation == "forget" and cleaned.startswith("我"):
        variants.append(_clean_target_query(cleaned[len("我") :]))
    return tuple(dict.fromkeys(query for query in variants if query))


def _remember_payload(value: str) -> str | None:
    if value.strip() in _REFERENTIAL_REMEMBER_PAYLOADS:
        return None
    return value or None


def parse_memory_command(user_message: str) -> MemoryCommand | None:
    """Parse only supported explicit commands; preserve payload bytes verbatim."""
    command_text = user_message.lstrip()

    if command_text.startswith("记住"):
        return MemoryCommand(
            operation="remember",
            payload=_remember_payload(command_text[len("记住") :]),
        )

    for suffix in ("，记下来", ",记下来"):
        if command_text.endswith(suffix):
            return MemoryCommand(
                operation="remember",
                payload=_remember_payload(command_text[: -len(suffix)]),
            )

    for pattern in _UPDATE_PATTERNS:
        match = pattern.fullmatch(command_text)
        if match is not None:
            return MemoryCommand(
                operation="update",
                payload=match.group("content"),
                target_queries=_target_variants("update", match.group("target")),
            )

    for prefix in (
        "更新这个记忆",
        "更新这条记忆",
        "修改这个记忆",
        "修改这条记忆",
    ):
        if command_text.startswith(prefix):
            return MemoryCommand(operation="update")

    if command_text.startswith("忘掉"):
        target = command_text[len("忘掉") :]
        return MemoryCommand(
            operation="forget",
            target_queries=_target_variants("forget", target),
        )

    for prefix in ("删除这个记忆", "删除这条记忆"):
        if command_text.startswith(prefix):
            target = command_text[len(prefix) :]
            return MemoryCommand(
                operation="forget",
                target_queries=_target_variants("forget", target),
            )

    return None
