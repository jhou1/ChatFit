"""Deterministic trajectory graders used before any LLM-based judging."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    route_precision: float | None = None
    route_recall: float | None = None


def grade_turn(expected: EvaluationTurn, actual: Trajectory) -> TurnGrade:
    """Grade observable mechanics without making probabilistic judgments."""

    failures: list[GradeFailure] = []
    actual_tool_names = [str(call.get("name", "")) for call in actual.tool_calls]

    for assertion in expected.expected_trajectory_eval:
        if assertion.eval_type == "routing":
            if (
                assertion.expected_agent
                and assertion.expected_agent not in actual.routes
            ):
                # We could have a loose match since agents might have different node names.
                # E.g. "TrainingAgent" in "training_node"
                # Let's do a loose check.
                match_found = any(
                    assertion.expected_agent.lower() in route.lower()
                    or route.lower() in assertion.expected_agent.lower()
                    for route in actual.routes
                )
                if not match_found and assertion.expected_agent not in actual.routes:
                    failures.append(
                        GradeFailure(
                            code="missing_route",
                            message=f"expected route to {assertion.expected_agent}, but actual routes were {actual.routes}",
                        )
                    )

        elif assertion.eval_type == "tool_call":
            if assertion.expected_tool:
                matching_calls = [
                    call
                    for call in actual.tool_calls
                    if call.get("name") == assertion.expected_tool
                ]
                if not matching_calls:
                    failures.append(
                        GradeFailure(
                            code="missing_tool",
                            message=f"expected tool {assertion.expected_tool} was not called. Called: {actual_tool_names}",
                        )
                    )
                    continue

                # Check args contain (legacy support)
                if assertion.expected_args_contain:
                    serialized_args = " ".join(
                        str(call.get("args", "")).lower() for call in matching_calls
                    )
                    for required_value in assertion.expected_args_contain:
                        if required_value.lower() not in serialized_args:
                            failures.append(
                                GradeFailure(
                                    code="tool_args",
                                    message=(
                                        f"tool {assertion.expected_tool} arguments did not "
                                        f"contain {required_value!r}"
                                    ),
                                )
                            )

                # Check exact params include
                if assertion.expected_params_include:
                    # We expect AT LEAST ONE call to have these params
                    param_matched = False
                    for call in matching_calls:
                        args = call.get("args", {})
                        if not isinstance(args, dict):
                            continue

                        match = True
                        for k, v in assertion.expected_params_include.items():
                            if k not in args:
                                match = False
                                break
                            # Simple equality or containment
                            if isinstance(v, list) and isinstance(args[k], list):
                                if not set(v).issubset(set(args[k])):
                                    match = False
                                    break
                            elif args[k] != v:
                                match = False
                                break

                        if match:
                            param_matched = True
                            break

                    if not param_matched:
                        failures.append(
                            GradeFailure(
                                code="tool_params",
                                message=(
                                    f"tool {assertion.expected_tool} was called but no call matched "
                                    f"expected params: {assertion.expected_params_include}"
                                ),
                            )
                        )

        elif assertion.eval_type == "tool_avoidance":
            if assertion.avoid_tool and assertion.avoid_tool in actual_tool_names:
                failures.append(
                    GradeFailure(
                        code="avoid_tool",
                        message=f"tool {assertion.avoid_tool} was called when it should have been avoided.",
                    )
                )

        elif assertion.eval_type == "clarification_trigger":
            # For clarification, we usually expect NO side-effect tools to be called
            # and the system asks for clarification.
            # We assert that no domain tools (like log_training, log_meal) were called.
            # We will just verify tool_calls is empty for now (except maybe some safe read tools).
            if actual.tool_calls:
                failures.append(
                    GradeFailure(
                        code="clarification_failed",
                        message=f"expected clarification, but tools were called: {actual_tool_names}",
                    )
                )

        elif assertion.eval_type == "db_state":
            # DB state is evaluated separately in the test runner
            pass

    return TurnGrade(passed=not failures, failures=failures)
