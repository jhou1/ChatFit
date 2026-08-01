import json
from datetime import date, datetime, timedelta

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import agents.roles.insights as insights_module
from agents.roles.insights import make_insights_agent_graph
from agents.llm_factory import LLMConfig
from agents.sqlite_handler import init_db, add_training_session, add_meal_log
from agents.models import TrainingSession, TrainingSet, TrainingInputRecorder, MealInfo
from agents.utils import extract_text


@pytest.fixture
def temp_db_path(tmp_path):
    db_path = tmp_path / "test_insights_agent.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def llm_config():
    return LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        kwargs={"client_args": {"proxy": "socks5://127.0.0.1:8990"}},
        temperature=0,
    )


def seed_data(db_path):
    # Seed training data: 3 heavy days, 1 light day to test waveness
    today = datetime.now().date()
    for i in range(4):
        rpe = 9 if i < 3 else 4  # High RPE for 3 days, then low
        test_input = TrainingInputRecorder(
            date=today - timedelta(days=i),
            sessions=[
                TrainingSession(
                    practice_name="Squat",
                    practice_type="weighted",
                    rpe=rpe,
                    note="Hard" if rpe == 9 else "Light",
                    sets=[TrainingSet(set_number=1, weight=100, reps=10)],
                )
            ],
            confirm_new_practices=True,
        )
        add_training_session(test_input, db_path)

    # Seed meals
    for i in range(4):
        meal = MealInfo(
            date=today - timedelta(days=i),
            meal_type="dinner",
            items="Chicken and Rice",
            note="Good meal",
        )
        add_meal_log(meal, str(db_path))


def seed_data_for_date(db_path, target_date: date, label: str) -> None:
    """Seed one complete training and meal record for a calendar date."""
    add_training_session(
        TrainingInputRecorder(
            date=target_date,
            sessions=[
                TrainingSession(
                    practice_name=f"Squat {label}",
                    practice_type="weighted",
                    rpe=7,
                    note=label,
                    sets=[TrainingSet(set_number=1, weight=100, reps=5)],
                )
            ],
            confirm_new_practices=True,
        ),
        str(db_path),
    )
    add_meal_log(
        MealInfo(
            date=target_date,
            meal_type="dinner",
            items=label,
            note=label,
        ),
        str(db_path),
    )


@pytest.mark.asyncio
async def test_weekly_insights_uses_both_fixed_window_tools(
    monkeypatch, llm_config, temp_db_path
):
    """Breaks if a scheduled review queries dates outside its supplied week."""
    seed_data_for_date(temp_db_path, date(2026, 7, 25), "outside")
    seed_data_for_date(temp_db_path, date(2026, 7, 26), "sunday")
    seed_data_for_date(temp_db_path, date(2026, 8, 1), "saturday")

    turn = 0
    captured_system_prompt = ""

    async def fake_execute(_llm, messages):
        nonlocal turn, captured_system_prompt
        turn += 1
        if turn == 1:
            captured_system_prompt = messages[0].content
            return {
                "messages": AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "retrieve_recent_training", "args": {}, "id": "t"},
                        {"name": "retrieve_recent_meals", "args": {}, "id": "m"},
                    ],
                )
            }
        tool_messages = {
            message.name: message.content
            for message in messages
            if isinstance(message, ToolMessage)
        }
        training = json.loads(tool_messages["retrieve_recent_training"])
        meals = json.loads(tool_messages["retrieve_recent_meals"])
        assert {row["training_date"] for row in training} == {
            "2026-07-26",
            "2026-08-01",
        }
        assert {row["date"] for row in meals} == {
            "2026-07-26",
            "2026-08-01",
        }
        return {"messages": AIMessage(content="本周训练和饮食保持稳定。")}

    monkeypatch.setattr(insights_module, "_execute_llm_query_safely", fake_execute)

    summary = await insights_module.generate_weekly_insights(
        llm_config,
        str(temp_db_path),
        date(2026, 7, 26),
        date(2026, 8, 1),
    )

    assert summary == "本周训练和饮食保持稳定。"
    assert "2026-07-26" in captured_system_prompt
    assert "2026-08-01" in captured_system_prompt


@pytest.mark.asyncio
async def test_weekly_insights_rejects_final_answer_without_tool_results(
    monkeypatch, llm_config, temp_db_path
):
    """Breaks if a scheduled summary can bypass both required weekly reads."""

    async def fake_execute(_llm, _messages):
        return {"messages": AIMessage(content="未查询数据也生成的周总结。")}

    monkeypatch.setattr(insights_module, "_execute_llm_query_safely", fake_execute)

    with pytest.raises(RuntimeError, match="weekly insights generation failed"):
        await insights_module.generate_weekly_insights(
            llm_config,
            str(temp_db_path),
            date(2026, 7, 26),
            date(2026, 8, 1),
        )


@pytest.mark.asyncio
async def test_weekly_insights_rejects_final_answer_with_only_one_tool_result(
    monkeypatch, llm_config, temp_db_path
):
    """Breaks if one weekly data domain is enough to accept a summary."""
    turn = 0

    async def fake_execute(_llm, _messages):
        nonlocal turn
        turn += 1
        if turn == 1:
            return {
                "messages": AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "retrieve_recent_training", "args": {}, "id": "t"}
                    ],
                )
            }
        return {"messages": AIMessage(content="只查询训练后生成的周总结。")}

    monkeypatch.setattr(insights_module, "_execute_llm_query_safely", fake_execute)

    with pytest.raises(RuntimeError, match="weekly insights generation failed"):
        await insights_module.generate_weekly_insights(
            llm_config,
            str(temp_db_path),
            date(2026, 7, 26),
            date(2026, 8, 1),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final_content", ["", "[Error] LLM request failed after retries."]
)
async def test_weekly_insights_rejects_blank_or_error_final_content(
    monkeypatch, llm_config, temp_db_path, final_content
):
    """Breaks if retryable failures are presented as a weekly summary."""

    async def fake_execute(_llm, _messages):
        return {"messages": AIMessage(content=final_content)}

    monkeypatch.setattr(insights_module, "_execute_llm_query_safely", fake_execute)

    with pytest.raises(RuntimeError):
        await insights_module.generate_weekly_insights(
            llm_config,
            str(temp_db_path),
            date(2026, 7, 26),
            date(2026, 8, 1),
        )


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_insights_agent_analysis(llm_config, temp_db_path):
    seed_data(temp_db_path)

    app = make_insights_agent_graph(llm_config, str(temp_db_path))

    message = HumanMessage(
        content="Can you analyze my training and recovery for the last week? Am I doing wavy progressive overload properly? Am I eating enough?"
    )
    state = {"messages": [message]}

    response = await app.ainvoke(state)

    final_text = extract_text(response["messages"][-1]).lower()

    # Check if the agent mentions waveness or rpe, and squats/meals
    assert "volume" in final_text or "weight" in final_text
    assert "rpe" in final_text
    assert "rice" in final_text or "meal" in final_text or "chicken" in final_text
    # Should mention lack of recovery or something about 3 hard days
    assert len(final_text) > 100  # Should give a comprehensive analysis
