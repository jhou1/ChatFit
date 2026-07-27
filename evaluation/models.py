"""JSONL contracts for ChatFit Agent evaluation cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class StrictEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExpectedTrajectoryAssertion(StrictEvaluationModel):
    eval_type: NonEmptyString
    expected_agent: NonEmptyString | None = None
    expected_tool: NonEmptyString | None = None
    expected_params_include: dict[str, Any] | None = None
    expected_args_contain: list[str] | None = None
    avoid_tool: NonEmptyString | None = None
    expected_behavior: NonEmptyString | None = None
    query: NonEmptyString | None = None
    expected_value: Any | None = None


class RubricDimension(StrictEvaluationModel):
    dimension_name: NonEmptyString
    criteria_description: NonEmptyString
    evidence_requirement: NonEmptyString
    weight: float = Field(ge=0.0, le=1.0)


class ExpectedResponseEval(StrictEvaluationModel):
    rubrics: list[RubricDimension] = Field(default_factory=list)


class EvaluationTurn(StrictEvaluationModel):
    turn_id: int | None = None
    user_input: NonEmptyString
    expected_trajectory_eval: list[ExpectedTrajectoryAssertion] = Field(default_factory=list)
    expected_response_eval: ExpectedResponseEval | None = None
    expected_trajectory: list[str] | None = None
    expected_result: str | None = None


class EvaluationCase(StrictEvaluationModel):
    case_id: NonEmptyString
    capability_tags: list[NonEmptyString] = Field(default_factory=list)
    description: NonEmptyString | None = None
    turns: list[EvaluationTurn]

    @model_validator(mode="after")
    def require_turns(self) -> "EvaluationCase":
        if not self.turns:
            raise ValueError("evaluation case must contain at least one turn")
        return self


def load_evaluation_cases(path: str | Path) -> list[EvaluationCase]:
    """Load and validate a version-controlled JSONL evaluation dataset."""

    dataset_path = Path(path)
    raw_cases = []
    with dataset_path.open("r", encoding="utf-8") as dataset_file:
        for line in dataset_file:
            line = line.strip()
            if not line:
                continue
            raw_cases.append(json.loads(line))
            
    if not raw_cases:
        raise ValueError("evaluation dataset must contain at least one case")

    cases = [EvaluationCase.model_validate(case) for case in raw_cases]
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("evaluation case ids must be unique")
    return cases
