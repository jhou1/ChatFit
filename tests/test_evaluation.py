import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from evaluation.graders import Trajectory, grade_turn
from evaluation.models import EvaluationTurn, load_evaluation_cases
from evaluation.report import (
    CaseResult,
    ExperimentMetadata,
    ExperimentReport,
    ReleaseThresholds,
)
from scripts.llm_judge import evaluate_trace, parse_judge_response


def test_repository_evaluation_dataset_is_valid_and_unique():
    cases = load_evaluation_cases("evaluation/chatfit_golden_test_set.jsonl")

    assert len(cases) >= 10
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.turns for case in cases)


def test_evaluation_dataset_rejects_duplicate_ids(tmp_path: Path):
    dataset = tmp_path / "duplicate.jsonl"
    dataset.write_text(
        json.dumps({"case_id": "duplicate", "turns": [{"user_input": "hello"}]})
        + "\n"
        + json.dumps({"case_id": "duplicate", "turns": [{"user_input": "goodbye"}]})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_evaluation_cases(dataset)


def test_evaluation_dataset_rejects_empty_case_list(tmp_path: Path):
    dataset = tmp_path / "empty.jsonl"
    dataset.write_text("\n", encoding="utf-8")

    with pytest.raises(ValueError, match="at least one case"):
        load_evaluation_cases(dataset)


@pytest.mark.parametrize(
    "invalid_turn",
    [
        {"user_input": "hello", "expected_trajectory_eval": [{"eval_type": ""}]},
        {"user_input": "   "},
        {
            "user_input": "hello",
            "expected_response_eval": {"rubrics": [{"dimension_name": ""}]},
        },
    ],
)
def test_evaluation_schema_rejects_unknown_fields_and_empty_contracts(invalid_turn):
    with pytest.raises(ValidationError):
        EvaluationTurn.model_validate(invalid_turn)


def test_deterministic_grader_rejects_unexpected_routes():
    expected = EvaluationTurn.model_validate(
        {
            "user_input": "log my run",
            "expected_trajectory_eval": [
                {"eval_type": "routing", "expected_agent": "training_agent"}
            ],
        }
    )
    trajectory = Trajectory(
        routes=["meal_agent"],
        tool_calls=[
            {
                "name": "log_training_session",
                "args": {"distance": 5, "duration": 30},
            }
        ],
        response="Training saved.",
    )

    grade = grade_turn(expected, trajectory)

    assert not grade.passed
    assert "missing_route" in {failure.code for failure in grade.failures}


def test_deterministic_grader_rejects_missing_tools():
    expected = EvaluationTurn.model_validate(
        {
            "user_input": "log my run",
            "expected_trajectory_eval": [
                {"eval_type": "tool_call", "expected_tool": "log_training_session"}
            ],
        }
    )
    trajectory = Trajectory(
        tool_calls=[
            {"name": "log_meal", "args": {}},
        ]
    )

    grade = grade_turn(expected, trajectory)

    assert not grade.passed
    assert "missing_tool" in {failure.code for failure in grade.failures}


def test_deterministic_grader_returns_actionable_failures():
    expected = EvaluationTurn.model_validate(
        {
            "user_input": "hello",
            "expected_trajectory_eval": [
                {"eval_type": "routing", "expected_agent": "chatter"},
                {"eval_type": "tool_avoidance", "avoid_tool": "log_meal"},
            ],
        }
    )

    grade = grade_turn(
        expected,
        Trajectory(
            routes=["meal_agent"],
            tool_calls=[{"name": "log_meal", "args": {}}],
        ),
    )

    assert not grade.passed
    codes = {failure.code for failure in grade.failures}
    assert "missing_route" in codes
    assert "avoid_tool" in codes


def test_experiment_report_enforces_release_gate_and_renders_markdown():
    report = ExperimentReport(
        metadata=ExperimentMetadata(
            run_id="run-1",
            commit_sha="abc123",
            dataset="golden",
            dataset_version="1",
            model="fake-model",
            prompt_version="1",
            grader_version="1",
        ),
        cases=[
            CaseResult(
                case_id="safe",
                passed=True,
                tags=["high_risk"],
                llm_score=5,
            ),
            CaseResult(
                case_id="regression",
                passed=False,
                failure_codes=["tool_args"],
                llm_score=3,
            ),
        ],
    )

    gate = report.release_gate()
    markdown = report.to_markdown()

    assert not gate.passed
    assert any("任务完成率" in failure for failure in gate.failures)
    assert "❌ 拦截" in markdown
    assert "tool_args" in markdown


def test_release_gate_requires_tone_scores_by_default():
    report = ExperimentReport(
        metadata=ExperimentMetadata(
            run_id="run-1",
            commit_sha="abc123",
            dataset="golden",
            dataset_version="1",
            model="fake-model",
            prompt_version="1",
            grader_version="1",
        ),
        cases=[CaseResult(case_id="case-1", passed=True)],
    )

    gate = report.release_gate()

    assert not gate.passed
    assert "总体综合得分缺失" in gate.failures


def test_release_gate_rejects_partial_tone_coverage():
    report = ExperimentReport(
        metadata=ExperimentMetadata(
            run_id="run-coverage",
            commit_sha="abc123",
            dataset="golden",
            dataset_version="1",
            model="fake-model",
            prompt_version="1",
            grader_version="1",
        ),
        cases=[
            CaseResult(case_id="scored", passed=True, llm_score=5),
            CaseResult(case_id="missing-score", passed=True),
        ],
    )

    gate = report.release_gate()

    assert not gate.passed
    assert any("大模型打分覆盖率" in failure for failure in gate.failures)


def test_release_gate_can_explicitly_disable_tone_gates():
    report = ExperimentReport(
        metadata=ExperimentMetadata(
            run_id="run-no-judge",
            commit_sha="abc123",
            dataset="smoke",
            dataset_version="1",
            model="fake-model",
            prompt_version="1",
            grader_version="1",
        ),
        cases=[CaseResult(case_id="mechanical", passed=True)],
    )

    gate = report.release_gate(
        ReleaseThresholds(
            require_llm_scores=False,
            minimum_high_risk_completion_rate=0,
        )
    )

    assert gate.passed


def test_release_gate_fails_closed_when_high_risk_slice_is_missing():
    report = ExperimentReport(
        metadata=ExperimentMetadata(
            run_id="run-no-high-risk",
            commit_sha="abc123",
            dataset="golden",
            dataset_version="1",
            model="fake-model",
            prompt_version="1",
            grader_version="1",
        ),
        cases=[CaseResult(case_id="ordinary", passed=True, llm_score=5)],
    )

    gate = report.release_gate()

    assert not gate.passed
    assert "高风险用例缺失" in gate.failures


def test_parse_judge_response_validates_contract():
    valid_json = """
    {
      "evaluations": [
        {
          "dimension": "Semantic Accuracy & Clarity",
          "evidence": "Good clarity.",
          "score": 4,
          "weight": 0.6
        }
      ],
      "overall_weighted_score": 4.0
    }
    """
    result = parse_judge_response(valid_json)

    assert result.overall_weighted_score == 4.0
    assert result.evaluations[0].score == 4

    with pytest.raises(ValueError, match="valid JSON"):
        parse_judge_response("SCORE: 6\nREASON: invalid")


@pytest.mark.asyncio
async def test_llm_judge_scores_real_supplied_input_and_output():
    class FakeJudge:
        def __init__(self) -> None:
            self.messages = None

        async def ainvoke(self, messages):
            self.messages = messages
            return AIMessage(
                content='{"evaluations": [{"dimension": "Tone", "evidence": "Supportive", "score": 5, "weight": 1.0}], "overall_weighted_score": 5.0}'
            )

    fake_judge = FakeJudge()
    fake_langfuse = SimpleNamespace(create_score=lambda **kwargs: None)
    recorded = {}

    def record_score(**kwargs):
        recorded.update(kwargs)

    fake_langfuse.create_score = record_score
    result = await evaluate_trace(
        "trace-123",
        "I completed my workout",
        "Great work—your session was saved.",
        [
            {
                "dimension_name": "Tone",
                "criteria_description": "d",
                "evidence_requirement": "e",
                "weight": 1.0,
            }
        ],
        judge_llm=fake_judge,
        langfuse_client=fake_langfuse,
    )

    assert result.overall_weighted_score == 5.0
    assert "I completed my workout" in fake_judge.messages[1].content
    assert "your session was saved" in fake_judge.messages[1].content
