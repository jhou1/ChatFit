import json
from contextlib import closing
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

import evaluation.runner as evaluation_runner
from evaluation.graders import Trajectory, grade_turn
from evaluation.models import (
    EvaluationTurn,
    ExpectedTrajectoryAssertion,
    load_evaluation_cases,
)
from evaluation.runner import query_expected_scalar
from evaluation.report import (
    CaseResult,
    ExperimentMetadata,
    ExperimentReport,
    ReleaseThresholds,
)
from scripts.llm_judge import evaluate_trace, parse_judge_response
from scripts.generate_golden_test_set import create_dataset


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


def test_db_state_assertion_accepts_memory_and_defaults_to_business():
    memory_assertion = ExpectedTrajectoryAssertion.model_validate(
        {
            "eval_type": "db_state",
            "database": "memory",
            "query": (
                "SELECT COUNT(*) FROM user_memories " "WHERE canonical_key='乳糖不耐受'"
            ),
            "expected_value": 1,
        }
    )
    legacy_assertion = ExpectedTrajectoryAssertion.model_validate(
        {
            "eval_type": "db_state",
            "query": "SELECT COUNT(*) FROM training_sessions",
            "expected_value": 1,
        }
    )

    assert memory_assertion.database == "memory"
    assert legacy_assertion.database == "business"


def test_db_state_assertion_rejects_unknown_database():
    with pytest.raises(ValidationError):
        ExpectedTrajectoryAssertion.model_validate(
            {
                "eval_type": "db_state",
                "database": "checkpoint",
                "query": "SELECT 1",
                "expected_value": 1,
            }
        )


def test_query_expected_scalar_selects_real_business_or_memory_database(tmp_path):
    business_path = tmp_path / "business.db"
    memory_path = tmp_path / "user-memory.db"
    for path, stored_value in ((business_path, 7), (memory_path, 11)):
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE marker (value INTEGER NOT NULL)")
            connection.execute("INSERT INTO marker (value) VALUES (?)", (stored_value,))

    business_assertion = ExpectedTrajectoryAssertion.model_validate(
        {"eval_type": "db_state", "query": "SELECT value FROM marker"}
    )
    memory_assertion = ExpectedTrajectoryAssertion.model_validate(
        {
            "eval_type": "db_state",
            "database": "memory",
            "query": "SELECT value FROM marker",
        }
    )

    assert (
        query_expected_scalar(
            business_assertion,
            business_db_path=business_path,
            memory_db_path=memory_path,
        )
        == 7
    )
    assert (
        query_expected_scalar(
            memory_assertion,
            business_db_path=business_path,
            memory_db_path=memory_path,
        )
        == 11
    )


def test_query_expected_scalar_closes_connection_on_success_and_query_error(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "business.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE marker (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO marker (value) VALUES (7)")

    real_connect = sqlite3.connect
    opened_connections = []

    def tracking_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(evaluation_runner.sqlite3, "connect", tracking_connect)
    valid = ExpectedTrajectoryAssertion.model_validate(
        {"eval_type": "db_state", "query": "SELECT value FROM marker"}
    )
    invalid = ExpectedTrajectoryAssertion.model_validate(
        {"eval_type": "db_state", "query": "SELECT missing FROM marker"}
    )

    assert (
        query_expected_scalar(
            valid,
            business_db_path=db_path,
            memory_db_path=tmp_path / "unused.db",
        )
        == 7
    )
    with pytest.raises(sqlite3.OperationalError, match="no such column"):
        query_expected_scalar(
            invalid,
            business_db_path=db_path,
            memory_db_path=tmp_path / "unused.db",
        )

    assert len(opened_connections) == 2
    for connection in opened_connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_generated_memory_cases_use_memory_agent_and_memory_database(tmp_path):
    generated_path = tmp_path / "generated.jsonl"
    create_dataset(generated_path)
    cases = {case.case_id: case for case in load_evaluation_cases(generated_path)}

    expected_mutation_turns = {
        "IR_04": [(0, 1)],
        "ME_02": [(0, 1)],
        "ME_03": [(0, 1)],
        "ME_06": [(0, 1), (1, 0)],
    }
    for case_id, turns in expected_mutation_turns.items():
        for turn_index, expected_count in turns:
            turn = cases[case_id].turns[turn_index]
            assert turn.expected_trajectory == [
                "assistant_selector -> memory_agent",
                "memory",
            ]
            route_assertion, db_assertion = turn.expected_trajectory_eval
            assert route_assertion.expected_agent == "memory_agent"
            assert db_assertion.eval_type == "db_state"
            assert db_assertion.database == "memory"
            assert db_assertion.expected_value == expected_count

    ir_turn = cases["IR_04"].turns[0]
    assert all("Context Governance" not in step for step in ir_turn.expected_trajectory)
    assert "用户画像" not in ir_turn.expected_result


def test_generated_memory_queries_reject_unrelated_rows(tmp_path):
    generated_path = tmp_path / "generated.jsonl"
    create_dataset(generated_path)
    cases = {case.case_id: case for case in load_evaluation_cases(generated_path)}
    expected_rows = {
        "IR_04": {
            "owner_key": "4af4b0ee33dae83971949801ad8d179075ab05493f5c0579fcc45d3eebb3048d",
            "memory_type": "dietary_preference",
            "canonical_key": "不吃海鲜",
            "content": "我以后不再吃海鲜了。",
        },
        "ME_02": {
            "owner_key": "20c3d1ca7e5381f4a9449436800a7bf48834f87faf18fe91dfa759850f4efc45",
            "memory_type": "training_preference",
            "canonical_key": "周三休息日",
            "content": "我以后周三都不练了，变成休息日",
        },
        "ME_03": {
            "owner_key": "b891e1a326797bd181feb6be5416957b09065c928b915af6860df6052e2c71b4",
            "memory_type": "health_constraint",
            "canonical_key": "乳糖不耐受",
            "content": "我乳糖不耐受",
        },
        "ME_06": {
            "owner_key": "883a1f10c12f6dea69e6163a8d5e5628e862edd6cc6a5d60682a7ec0abdb6bbf",
            "memory_type": "dietary_preference",
            "canonical_key": "不吃香菜",
            "content": "我不吃香菜",
        },
    }

    for case_id, expected_row in expected_rows.items():
        memory_path = tmp_path / f"{case_id}.memory.db"
        business_path = tmp_path / f"{case_id}.business.db"
        with closing(sqlite3.connect(memory_path)) as connection, connection:
            connection.execute("""
                CREATE TABLE user_memories (
                    owner_key TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    content TEXT NOT NULL
                )
                """)
            connection.execute(
                "INSERT INTO user_memories VALUES (?, ?, ?, ?)",
                (
                    "unrelated-owner",
                    expected_row["memory_type"],
                    expected_row["canonical_key"],
                    expected_row["content"],
                ),
            )
        with closing(sqlite3.connect(business_path)):
            pass

        remember_assertion = next(
            assertion
            for assertion in cases[case_id].turns[0].expected_trajectory_eval
            if assertion.eval_type == "db_state"
        )
        assert remember_assertion.expected_value == 1
        assert (
            query_expected_scalar(
                remember_assertion,
                business_db_path=business_path,
                memory_db_path=memory_path,
            )
            == 0
        )

        with closing(sqlite3.connect(memory_path)) as connection, connection:
            connection.execute(
                "INSERT INTO user_memories VALUES (?, ?, ?, ?)",
                tuple(expected_row.values()),
            )
        assert (
            query_expected_scalar(
                remember_assertion,
                business_db_path=business_path,
                memory_db_path=memory_path,
            )
            == 1
        )

        if case_id == "ME_06":
            with closing(sqlite3.connect(memory_path)) as connection, connection:
                connection.execute(
                    "DELETE FROM user_memories WHERE owner_key = ?",
                    (expected_row["owner_key"],),
                )
            forget_assertion = next(
                assertion
                for assertion in cases[case_id].turns[1].expected_trajectory_eval
                if assertion.eval_type == "db_state"
            )
            assert (
                query_expected_scalar(
                    forget_assertion,
                    business_db_path=business_path,
                    memory_db_path=memory_path,
                )
                == 0
            )


def test_golden_dataset_generation_is_byte_deterministic_and_checked_in(tmp_path):
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"

    create_dataset(first_path)
    create_dataset(second_path)

    expected_bytes = Path("evaluation/chatfit_golden_test_set.jsonl").read_bytes()
    assert first_path.read_bytes() == second_path.read_bytes() == expected_bytes


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


@pytest.mark.asyncio
async def test_llm_judge_logs_score_export_failure_without_request_content(
    caplog: pytest.LogCaptureFixture,
):
    """Breaks if a Langfuse score failure is silent or logs private request data."""

    class FakeJudge:
        async def ainvoke(self, messages):
            return AIMessage(
                content='{"evaluations": [{"dimension": "Tone", "evidence": "Supportive", "score": 5, "weight": 1.0}], "overall_weighted_score": 5.0}'
            )

    private_trace = "private-trace-id"
    private_input = "private user input"
    private_output = "private agent output"

    def fail_score_export(**kwargs):
        raise RuntimeError(
            f"private exception: {private_trace} | {private_input} | {private_output}"
        )

    with caplog.at_level("WARNING", logger="scripts.llm_judge"):
        result = await evaluate_trace(
            private_trace,
            private_input,
            private_output,
            [
                {
                    "dimension_name": "Tone",
                    "criteria_description": "d",
                    "evidence_requirement": "e",
                    "weight": 1.0,
                }
            ],
            judge_llm=FakeJudge(),
            langfuse_client=SimpleNamespace(create_score=fail_score_export),
        )

    assert result.overall_weighted_score == 5.0
    assert [record.getMessage() for record in caplog.records] == [
        "Failed to export LLM judge scores to Langfuse"
    ]
    assert caplog.records[-1].exc_info is None
    assert private_trace not in caplog.text
    assert private_input not in caplog.text
    assert private_output not in caplog.text
