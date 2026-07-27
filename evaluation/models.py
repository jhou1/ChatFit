"""Versioned YAML contracts for ChatFit Agent evaluation cases."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class StrictEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExpectedTool(StrictEvaluationModel):
    name: NonEmptyString
    args_contain: list[NonEmptyString] = Field(default_factory=list)


class ExpectedDatabaseState(StrictEvaluationModel):
    query: NonEmptyString
    expected_value: Any


class EvaluationTurn(StrictEvaluationModel):
    user: NonEmptyString
    expected_tools: list[ExpectedTool] | None = None
    expected_tools_count: int | None = Field(default=None, ge=0)
    expected_routes: list[NonEmptyString] | None = None
    expected_response_contains: list[NonEmptyString] = Field(default_factory=list)
    expected_db_state: list[ExpectedDatabaseState] = Field(default_factory=list)


class EvaluationCase(StrictEvaluationModel):
    id: NonEmptyString
    version: int = Field(default=1, ge=1)
    tags: list[NonEmptyString] = Field(default_factory=list)
    input_locale: NonEmptyString | None = None
    seed_db_fixture: NonEmptyString | None = None
    turns: list[EvaluationTurn]

    @model_validator(mode="after")
    def require_turns(self) -> "EvaluationCase":
        if not self.turns:
            raise ValueError("evaluation case must contain at least one turn")
        return self


def load_evaluation_cases(path: str | Path) -> list[EvaluationCase]:
    """Load and validate a version-controlled YAML evaluation dataset."""

    dataset_path = Path(path)
    with dataset_path.open("r", encoding="utf-8") as dataset_file:
        raw_cases = yaml.safe_load(dataset_file)
    if not isinstance(raw_cases, list):
        raise ValueError("evaluation dataset must be a YAML list")
    if not raw_cases:
        raise ValueError("evaluation dataset must contain at least one case")

    cases = [EvaluationCase.model_validate(case) for case in raw_cases]
    case_ids = [case.id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("evaluation case ids must be unique")
    return cases
