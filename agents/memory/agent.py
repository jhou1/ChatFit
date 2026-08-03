"""Interpret and execute explicit user-memory mutations."""

import json
import re
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from agents.llm_factory import LLMConfig, create_chat_model
from agents.memory.models import (
    MemoryAgentResult,
    MemoryConflictError,
    MemoryMutationDecision,
    MemoryUpdate,
    NewUserMemory,
    PendingMemoryAction,
    StaleMemoryError,
    UserMemory,
)
from agents.memory.store import UserMemoryStore, normalize_memory_key, owner_key_for


class MemoryInterpreter(Protocol):
    """Classify one user message using the user's current memory names."""

    async def interpret(
        self,
        *,
        user_message: str,
        memories: Sequence[UserMemory],
        pending: PendingMemoryAction | None,
    ) -> MemoryMutationDecision: ...


_MEMORY_INTERPRETER_PROMPT = (
    "You classify explicit user-memory mutation commands. Return one "
    "intent: remember, update, forget, or clarify. Preserve the user's "
    "explicit memory content verbatim; never paraphrase it. Use only the "
    "supplied current memory names and aliases to identify targets. If "
    "the command is incomplete or could match zero or multiple memories, "
    "choose clarify rather than guessing."
)

_EXPLICIT_UPDATE_PATTERN = re.compile(
    r"^(?:把)?(?P<target>.+?)(?:更新成|改成)(?P<content>.*)$",
    re.DOTALL,
)
_TARGET_EDGE_CHARACTERS = " \t\r\n，。！？、,:：;；.?!「」『』“”‘’"
_CONFIRMATION_REPLIES = frozenset(
    ("确认", "确认执行", "确认更新", "确定", "是的", "继续")
)
_BARE_CONFIRMATION_REPLIES = _CONFIRMATION_REPLIES | frozenset(
    ("再次确认", "是", "对", "yes")
)


class LLMMemoryInterpreter:
    """Interpret memory commands with validated structured model output."""

    def __init__(self, llm_config: LLMConfig) -> None:
        chat_model = create_chat_model(llm_config)
        self._runnable = chat_model.with_structured_output(MemoryMutationDecision)

    async def interpret(
        self,
        *,
        user_message: str,
        memories: Sequence[UserMemory],
        pending: PendingMemoryAction | None,
    ) -> MemoryMutationDecision:
        memory_context = [
            {
                "id": memory.id,
                "memory_type": memory.memory_type.value,
                "canonical_key": memory.canonical_key,
                "display_name": memory.display_name,
                "aliases": list(memory.aliases),
                "version": memory.version,
            }
            for memory in memories
        ]
        pending_context = (
            pending.model_dump(mode="json") if pending is not None else None
        )
        human_prompt = (
            f"User message:\n{user_message}\n\nCurrent memories:\n"
            f"{json.dumps(memory_context, ensure_ascii=False, separators=(',', ':'))}"
            "\n\nPending action:\n"
            f"{json.dumps(pending_context, ensure_ascii=False, separators=(',', ':'))}"
        )
        raw_decision: Any = await self._runnable.ainvoke(
            [
                SystemMessage(content=_MEMORY_INTERPRETER_PROMPT),
                HumanMessage(content=human_prompt),
            ]
        )
        return MemoryMutationDecision.model_validate(raw_decision)


def extract_explicit_memory_payload(user_message: str) -> str | None:
    """Return content explicitly delimited by a supported remember command."""
    if user_message.startswith("记住"):
        payload = user_message[len("记住") :]
        return payload or None
    suffix = "，记下来"
    if user_message.endswith(suffix):
        payload = user_message[: -len(suffix)]
        return payload or None
    return None


def extract_explicit_update_payload(user_message: str) -> str | None:
    """Return the exact replacement from a supported explicit update command."""
    match = _EXPLICIT_UPDATE_PATTERN.fullmatch(user_message)
    return match.group("content") if match is not None else None


def _explicit_operation(
    user_message: str,
) -> Literal["remember", "update", "forget"] | None:
    if user_message.startswith("记住") or user_message.endswith("，记下来"):
        return "remember"
    if _EXPLICIT_UPDATE_PATTERN.fullmatch(user_message) is not None:
        return "update"
    if user_message.startswith(("更新这个记忆", "更新这条记忆")):
        return "update"
    if user_message.startswith("忘掉") or user_message.startswith(
        ("删除这个记忆", "删除这条记忆")
    ):
        return "forget"
    return None


def _clean_target_query(value: str) -> str:
    return value.strip(_TARGET_EDGE_CHARACTERS)


def _direct_target_queries(
    operation: Literal["update", "forget"], user_message: str
) -> tuple[str, ...]:
    target: str | None = None
    if operation == "update":
        match = _EXPLICIT_UPDATE_PATTERN.fullmatch(user_message)
        if match is not None:
            target = _clean_target_query(match.group("target"))
    elif user_message.startswith("忘掉"):
        target = _clean_target_query(user_message[len("忘掉") :])
    else:
        for prefix in ("删除这个记忆", "删除这条记忆"):
            if user_message.startswith(prefix):
                target = _clean_target_query(user_message[len(prefix) :])
                break

    if not target:
        return ()
    variants = [target]
    if operation == "update" and target.endswith("模板"):
        variants.append(_clean_target_query(target[: -len("模板")]))
    if operation == "forget" and target.startswith("我"):
        variants.append(_clean_target_query(target[len("我") :]))
    return tuple(dict.fromkeys(query for query in variants if query))


def _reply_target_queries(user_message: str) -> tuple[str, ...]:
    target = _clean_target_query(user_message)
    return (target,) if target else ()


def _is_confirmation_reply(user_message: str) -> bool:
    return _clean_target_query(user_message) in _CONFIRMATION_REPLIES


def _is_bare_confirmation_reply(user_message: str) -> bool:
    return _clean_target_query(user_message).casefold() in _BARE_CONFIRMATION_REPLIES


class MemoryAgent:
    """Apply interpreted memory mutations through the transactional store."""

    def __init__(
        self, *, store: UserMemoryStore, interpreter: MemoryInterpreter
    ) -> None:
        self._store = store
        self._interpreter = interpreter

    async def handle(
        self,
        *,
        user_id: str,
        user_message: str,
        pending: PendingMemoryAction | None,
    ) -> MemoryAgentResult:
        owner_key = owner_key_for(user_id)
        operation = _explicit_operation(user_message)
        if pending is not None:
            if pending.owner_key != owner_key:
                return MemoryAgentResult(response="这条待确认记忆不属于当前用户。")
            operation = pending.operation
        elif operation is None:
            return MemoryAgentResult(response="没有检测到明确的记忆操作。")
        try:
            memories = self._store.list_memories(owner_key)
        except Exception:
            return self._repository_failure()
        decision = await self._interpreter.interpret(
            user_message=user_message,
            memories=memories,
            pending=pending,
        )
        if pending is None and decision.intent not in (operation, "clarify"):
            return MemoryAgentResult(response="无法确认这条记忆操作，请重新明确说明。")
        if pending is not None:
            decision = self._merge_pending_decision(pending.decision, decision)
            pending_content = pending.decision.content
            if pending_content is None or not pending_content.strip():
                if (
                    pending.operation in ("remember", "update")
                    and not pending.requires_confirmation
                    and _is_bare_confirmation_reply(user_message)
                ):
                    return MemoryAgentResult(
                        response=pending.question,
                        pending=pending,
                    )
                continuation_content: str | None = None
                if pending.operation == "remember":
                    continuation_content = (
                        extract_explicit_memory_payload(user_message) or user_message
                    )
                elif (
                    pending.operation == "update"
                    and len(pending.candidate_ids) == 1
                    and not pending.requires_confirmation
                ):
                    extracted = extract_explicit_update_payload(user_message)
                    continuation_content = (
                        extracted if extracted is not None else user_message
                    )
                if continuation_content is not None:
                    decision = decision.model_copy(
                        update={"content": continuation_content}
                    )
        elif operation == "update":
            decision = decision.model_copy(
                update={"content": extract_explicit_update_payload(user_message)}
            )
        elif operation == "remember":
            decision = decision.model_copy(
                update={"content": extract_explicit_memory_payload(user_message)}
            )

        if pending is None and decision.intent == "clarify" and operation == "remember":
            question = decision.clarification_question or "请告诉我你想修改哪一条记忆。"
            return self._pending_result(owner_key, operation, decision, (), question)

        if operation in ("update", "forget"):
            return self._handle_targeted_mutation(
                owner_key=owner_key,
                operation=operation,
                decision=decision,
                memories=memories,
                pending=pending,
                user_message=user_message,
            )

        explicit_payload = extract_explicit_memory_payload(user_message)
        has_explicit_marker = user_message.startswith("记住") or user_message.endswith(
            "，记下来"
        )
        content = explicit_payload if has_explicit_marker else decision.content
        if (
            decision.memory_type is None
            or not decision.canonical_key
            or not decision.display_name
            or content is None
            or not content.strip()
        ):
            question = decision.clarification_question or "请告诉我需要记住的完整内容。"
            return self._pending_result(
                owner_key,
                operation,
                decision,
                (),
                question,
            )

        try:
            result = self._store.remember(
                owner_key,
                NewUserMemory(
                    memory_type=decision.memory_type,
                    canonical_key=decision.canonical_key,
                    display_name=decision.display_name,
                    content=content,
                    aliases=decision.aliases,
                ),
            )
        except MemoryConflictError:
            return self._clarify_remember_conflict(
                owner_key=owner_key,
                decision=decision,
                content=content,
            )
        except Exception:
            return MemoryAgentResult(response="记忆保存失败，请稍后重试。")

        if result.status == "unchanged":
            return MemoryAgentResult(
                response=f"已记住，无需重复记录「{result.memory.display_name}」。"
            )
        return MemoryAgentResult(response=f"已记住「{result.memory.display_name}」。")

    def _clarify_remember_conflict(
        self,
        *,
        owner_key: str,
        decision: MemoryMutationDecision,
        content: str,
    ) -> MemoryAgentResult:
        queries = (decision.canonical_key or "", *decision.aliases)
        candidates_by_id: dict[str, UserMemory] = {}
        try:
            for query in queries:
                if query:
                    for memory in self._store.resolve(owner_key, query):
                        candidates_by_id[memory.id] = memory
        except Exception:
            return self._repository_failure()

        update_decision = MemoryMutationDecision(
            intent="update",
            memory_type=decision.memory_type,
            canonical_key=decision.canonical_key,
            display_name=decision.display_name,
            content=content,
            aliases=decision.aliases,
            target_query=decision.canonical_key,
            clarification_question=(
                f"「{decision.display_name}」已有不同内容，要更新它吗？"
            ),
        )
        return self._pending_result(
            owner_key,
            "update",
            update_decision,
            list(candidates_by_id.values()),
            update_decision.clarification_question or "要更新这条记忆吗？",
            requires_confirmation=True,
        )

    @staticmethod
    def _merge_pending_decision(
        original: MemoryMutationDecision,
        clarification: MemoryMutationDecision,
    ) -> MemoryMutationDecision:
        updates = {}
        for field in (
            "memory_type",
            "canonical_key",
            "display_name",
            "clarification_question",
        ):
            value = getattr(clarification, field)
            if getattr(original, field) is None and value is not None:
                updates[field] = value
        if not original.aliases and clarification.aliases:
            updates["aliases"] = clarification.aliases
        return original.model_copy(update=updates)

    def _handle_targeted_mutation(
        self,
        *,
        owner_key: str,
        operation: Literal["update", "forget"],
        decision: MemoryMutationDecision,
        memories: Sequence[UserMemory],
        pending: PendingMemoryAction | None,
        user_message: str,
    ) -> MemoryAgentResult:
        try:
            if pending is None:
                candidates = self._resolve_exact_queries(
                    owner_key,
                    memories,
                    _direct_target_queries(operation, user_message),
                )
            elif not pending.candidate_ids:
                recovered = self._resolve_exact_queries(
                    owner_key, memories, _reply_target_queries(user_message)
                )
                if len(recovered) == 1:
                    target = recovered[0]
                    question = f"找到「{target.display_name}」，请再次确认要执行。"
                    return self._pending_result(
                        owner_key,
                        operation,
                        decision,
                        recovered,
                        question,
                        requires_confirmation=True,
                    )
                return MemoryAgentResult(response=pending.question, pending=pending)
            else:
                captured = [
                    memory for memory in memories if memory.id in pending.candidate_ids
                ]
                original_content = pending.decision.content
                supplies_missing_update_content = (
                    operation == "update"
                    and len(pending.candidate_ids) == 1
                    and (original_content is None or not original_content.strip())
                )
                if pending.requires_confirmation:
                    if not _is_confirmation_reply(user_message):
                        return MemoryAgentResult(
                            response=pending.question, pending=pending
                        )
                    candidates = captured
                elif supplies_missing_update_content:
                    candidates = captured
                else:
                    candidates = self._resolve_exact_queries(
                        owner_key,
                        memories,
                        _reply_target_queries(user_message),
                        allowed_ids=set(pending.candidate_ids),
                    )
        except Exception:
            return self._repository_failure()

        if pending is not None:
            if len(candidates) != 1:
                return MemoryAgentResult(response=pending.question, pending=pending)

        if len(candidates) != 1:
            fallback_candidates = list(candidates)
            if not fallback_candidates and decision.memory_type is not None:
                fallback_candidates = [
                    memory
                    for memory in memories
                    if memory.memory_type == decision.memory_type
                ]
            return self._clarify_target(
                owner_key, operation, decision, fallback_candidates
            )

        target = candidates[0]
        expected_version = target.version
        if pending is not None and target.id in pending.candidate_ids:
            versions = dict(
                zip(
                    pending.candidate_ids,
                    pending.candidate_versions,
                    strict=True,
                )
            )
            expected_version = versions[target.id]
            if target.version != expected_version:
                return self._stale_memory_result()

        if operation == "update":
            if decision.content is None or not decision.content.strip():
                question = (
                    "请告诉我新的完整内容。"
                    if pending is not None and pending.requires_confirmation
                    else decision.clarification_question or "请告诉我新的完整内容。"
                )
                return self._pending_result(
                    owner_key, operation, decision, [target], question
                )
            aliases = tuple(dict.fromkeys((*target.aliases, *decision.aliases)))
            try:
                updated = self._store.update(
                    owner_key,
                    target.id,
                    MemoryUpdate(
                        display_name=decision.display_name or target.display_name,
                        content=decision.content,
                        aliases=aliases,
                        expected_version=expected_version,
                    ),
                )
            except StaleMemoryError:
                return self._stale_memory_result()
            except Exception:
                return self._repository_failure()
            return MemoryAgentResult(response=f"已更新「{updated.display_name}」。")

        try:
            forgotten = self._store.forget(
                owner_key,
                target.id,
                expected_version=expected_version,
            )
        except StaleMemoryError:
            return self._stale_memory_result()
        except Exception:
            return self._repository_failure()
        if not forgotten:
            return self._repository_failure()
        return MemoryAgentResult(response=f"已忘掉「{target.display_name}」。")

    def _resolve_exact_queries(
        self,
        owner_key: str,
        memories: Sequence[UserMemory],
        queries: Sequence[str],
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[UserMemory]:
        matches_by_id: dict[str, UserMemory] = {}
        for query in queries:
            for memory in self._store.resolve(owner_key, query):
                if allowed_ids is None or memory.id in allowed_ids:
                    matches_by_id[memory.id] = memory
            normalized_query = normalize_memory_key(query)
            for memory in memories:
                if allowed_ids is not None and memory.id not in allowed_ids:
                    continue
                if normalize_memory_key(memory.display_name) == normalized_query:
                    matches_by_id[memory.id] = memory
        return [memory for memory in memories if memory.id in matches_by_id]

    def _clarify_target(
        self,
        owner_key: str,
        operation: Literal["update", "forget"],
        decision: MemoryMutationDecision,
        candidates: Sequence[UserMemory],
    ) -> MemoryAgentResult:
        question = decision.clarification_question
        if question is None and candidates:
            names = "、".join(f"「{memory.display_name}」" for memory in candidates)
            question = f"请确认你指的是哪一条：{names}？"
        if question is None:
            question = "没有找到对应记忆，请告诉我它的准确名称。"
        return self._pending_result(
            owner_key, operation, decision, candidates, question
        )

    @staticmethod
    def _pending_result(
        owner_key: str,
        operation: Literal["remember", "update", "forget"],
        decision: MemoryMutationDecision,
        candidates: Sequence[UserMemory],
        question: str,
        *,
        requires_confirmation: bool = False,
    ) -> MemoryAgentResult:
        return MemoryAgentResult(
            response=question,
            pending=PendingMemoryAction(
                owner_key=owner_key,
                operation=operation,
                decision=decision,
                candidate_ids=tuple(memory.id for memory in candidates),
                candidate_versions=tuple(memory.version for memory in candidates),
                requires_confirmation=requires_confirmation,
                question=question,
            ),
        )

    @staticmethod
    def _repository_failure() -> MemoryAgentResult:
        return MemoryAgentResult(response="记忆操作失败，请稍后重试。")

    @staticmethod
    def _stale_memory_result() -> MemoryAgentResult:
        return MemoryAgentResult(response="检测到更新，请先查看新内容后再确认。")
