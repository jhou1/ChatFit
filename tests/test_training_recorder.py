import sqlite3
import pytest
from datetime import datetime, timedelta

from agents.training_recorder import make_record_training_graph
from utils.llm_factory import LLMConfig
from utils.db import init_db, add_training_session
from models import RecordTrainingInput, TrainingSession, TrainingSet

from langchain_core.messages import HumanMessage

@pytest.fixture
def temp_db_path(tmp_path):
    db_path = tmp_path / "test_training_agent.db"
    init_db(db_path)
    return db_path

@pytest.fixture
def llm_config():
    return LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        # required config in my test env
        kwargs={"client_args": {"proxy": "socks5://127.0.0.1:8990"}},
        temperature=0
    )

@pytest.mark.e2e
def test_e2e_save_training_session(llm_config, temp_db_path):
    message = HumanMessage(content="I ran 5km in 30 minutes yesterday, rpe was around 5. Felt great!")
    state = {"messages": [message]}
    app = make_record_training_graph(llm_config, temp_db_path)
    response = app.invoke(state)

    # because addition of new practices must be approved
    # we create a new message to approve adding the record
    agent_reply = response["messages"][-1].content[0]["text"]
    assert "running" in agent_reply.lower()
    assert "?" in agent_reply.lower()

    state["messages"].extend([
        response["messages"][-1],
        HumanMessage(content="Yes, please add it as distance practice.")
    ])
    app.invoke(state)

    # agent should have inserted db records
    with sqlite3.connect(temp_db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * from training_sessions")
        rows = cursor.fetchall()

        assert len(rows) == 1

        # verify that data has been correctly inserted
        saved_session = rows[0]
        assert saved_session["rpe"] == 5
        assert saved_session["created_at"] == (datetime.now() - timedelta(days=1)).date().isoformat()

        cursor.execute("SELECT * from training_sets")
        rows = cursor.fetchall()
        saved_sets = rows[0]
        assert len(rows) == 1
        assert saved_sets["distance"] == 5.0
        assert saved_sets["duration"] == 30.0

@pytest.mark.e2e
def test_e2e_save_multiple_training_sessions(llm_config, temp_db_path):
    message = HumanMessage(content="I ran 10km in 30 minutes yesterday, then I snatched a 24kg kettlebell, 10 sets and 10 reps per set. RPE was 10.")
    state = {"messages": [message]}
    app = make_record_training_graph(llm_config, temp_db_path)
    response = app.invoke(state)

    agent_reply = response["messages"][-1].content[0]["text"]
    assert "running" in agent_reply.lower()
    assert "kettlebell" in agent_reply.lower()
    assert "?" in agent_reply.lower()

    state["messages"].extend([
        response["messages"][-1],
        HumanMessage(content="Yes, please add running as distance practice and add kettlebell snatch as weighted practice.")
    ])
    app.invoke(state)


    # agent should have inserted db records
    with sqlite3.connect(temp_db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM training_sessions")
        rows = cursor.fetchall()
        assert len(rows) == 2

        cursor.execute("SELECT * FROM training_sets WHERE weight == 24")
        rows = cursor.fetchall()
        assert len(rows) == 10

        cursor.execute("SELECT * FROM training_sets WHERE distance == 10")
        rows = cursor.fetchall()
        assert len(rows) == 1

        cursor.execute("SELECT name, type FROM practices")
        rows = cursor.fetchall()
        for row in rows:
            assert row["name"].lower() in ["running", "kettlebell snatch"]
            assert row["type"].lower() in ["weighted", "distance"]

@pytest.mark.e2e
def test_retrieve_training_sessions(llm_config, temp_db_path):
    # prepare test data
    for i in range(1, 8):
        test_input = RecordTrainingInput(
            date=datetime.now().date() - timedelta(i),
            sessions=[
                TrainingSession(
                    practice_name=f"Squat",
                    practice_type="bodyweight",
                    note="Testing",
                    sets=[TrainingSet(set_number=1, reps=10*i)]
                )
            ],
            confirm_new_practices=True 
        )

        add_training_session(test_input, temp_db_path)

    message = HumanMessage(content="Tell me about in the past 7 days, what trainings and practice types I had practiced.")
    state = {"messages": [message]}
    app = make_record_training_graph(llm_config, temp_db_path)
    response = app.invoke({"messages": state["messages"]})
    final_text = response["messages"][-1].content[0]["text"]

    assert "squat" in final_text.lower()
    assert "bodyweight" in final_text.lower()
