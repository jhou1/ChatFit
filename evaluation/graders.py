"""Deterministic trajectory graders used before any LLM-based judging."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from evaluation.models import EvaluationTurn


@dataclass(frozen=True)
class Trajectory:
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    response: str = ""


@dataclass(frozen=True)
class GradeFailure:
    code: str
    message: str


@dataclass(frozen=True)
class TurnGrade:
    passed: bool
    failures: list[GradeFailure]
    route_precision: float | None
    route_recall: float | None


def _route_metrics(
    expected: Iterable[str] | None, actual: Iterable[str]
) -> tuple[float | None, float | None]:
    if expected is None:
        return None, None
    expected_set = set(expected)
    actual_set = set(actual)
    true_positives = len(expected_set & actual_set)
    precision = (
        true_positives / len(actual_set) if actual_set else float(not expected_set)
    )
    recall = (
        true_positives / len(expected_set) if expected_set else float(not actual_set)
    )
    return precision, recall


def grade_turn(expected: EvaluationTurn, actual: Trajectory) -> TurnGrade:
    """Grade observable mechanics without making probabilistic judgments."""

    failures: list[GradeFailure] = []
    actual_tool_names = [str(call.get("name", "")) for call in actual.tool_calls]

    if (
        expected.expected_tools_count is not None
        and len(actual.tool_calls) != expected.expected_tools_count
    ):
        failures.append(
            GradeFailure(
                code="tool_count",
                message=(
                    f"expected {expected.expected_tools_count} tool calls, "
                    f"got {len(actual.tool_calls)}"
                ),
            )
        )

    if expected.expected_tools is not None:
        expected_tool_counts = Counter(tool.name for tool in expected.expected_tools)
        actual_tool_counts = Counter(actual_tool_names)
        unexpected_tool_counts = actual_tool_counts - expected_tool_counts
        
        # Filter out read tools from strict checking, as they can be called dynamically
        read_tools = {"normalize_practice_name", "retrieve_recent_training", "retrieve_recent_meals"}
        for t in read_tools:
            unexpected_tool_counts.pop(t, None)
            
        if unexpected_tool_counts:
            failures.append(
                GradeFailure(
                    code="unexpected_tool",
                    message=(
                        "unexpected tool calls: "
                        f"{dict(sorted(unexpected_tool_counts.items()))}"
                    ),
                )
            )
        for expected_tool in expected.expected_tools:
            matching_calls = [
                call
                for call in actual.tool_calls
                if call.get("name") == expected_tool.name
            ]
            if not matching_calls:
                failures.append(
                    GradeFailure(
                        code="missing_tool",
                        message=f"expected tool {expected_tool.name} was not called",
                    )
                )
                continue
            serialized_args = " ".join(
                str(call.get("args", "")).lower() for call in matching_calls
            )
            for required_value in expected_tool.args_contain:
                if required_value.lower() not in serialized_args:
                    failures.append(
                        GradeFailure(
                            code="tool_args",
                            message=(
                                f"tool {expected_tool.name} arguments did not "
                                f"contain {required_value!r}"
                            ),
                        )
                    )

    route_precision, route_recall = _route_metrics(
        expected.expected_routes, actual.routes
    )
    if expected.expected_routes is not None:
        missing_routes = set(expected.expected_routes) - set(actual.routes)
        unexpected_routes = set(actual.routes) - set(expected.expected_routes)
        if missing_routes:
            failures.append(
                GradeFailure(
                    code="missing_route",
                    message=f"missing expected routes: {sorted(missing_routes)}",
                )
            )
        if unexpected_routes:
            failures.append(
                GradeFailure(
                    code="unexpected_route",
                    message=f"unexpected routes: {sorted(unexpected_routes)}",
                )
            )

    response_lower = actual.response.lower()
    for required_text in expected.expected_response_contains:
        if required_text.lower() not in response_lower:
            failures.append(
                GradeFailure(
                    code="response_content",
                    message=f"response did not contain {required_text!r}",
                )
            )

    return TurnGrade(
        passed=not failures,
        failures=failures,
        route_precision=route_precision,
        route_recall=route_recall,
    )
