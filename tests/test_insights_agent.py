import pytest
from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage

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
