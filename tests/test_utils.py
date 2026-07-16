from datetime import datetime

from agents.models import MealInfo
from agents.sqlite_handler import init_db, add_meal_log
from agents.llm_factory import LLMConfig, create_chat_model


def test_add_meal_log(tmp_path):
    db_path = tmp_path / "meal_record_test.db"
    init_db(db_path)
    meal_record = MealInfo(
        date=datetime.now().date().isoformat(),
        note="breakfast: milk and egg, dinner: fish and rice",
    )
    row_id = add_meal_log(meal_record, db_path)
    assert isinstance(row_id, int)


def test_create_google_chat_client():
    llm_config = LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        api_key="api",
        temperature=0.5,
        max_tokens=2048,
    )
    create_chat_model(llm_config)


def test_create_google_llm_config_with_proxy():
    llm_config = LLMConfig(
        provider="google",
        model_name="gemini-3.5-flash",
        api_key="api",
        temperature=0.5,
        max_tokens=2048,
        kwargs={"client_args": {"proxy": "socks5://127.0.0.1:8990"}},
    )
    create_chat_model(llm_config)


def test_create_openai_chat_client():
    llm_config = LLMConfig(
        provider="openai", api_key="api", model_name="gpt-5.5", temperature=0.1
    )
    create_chat_model(llm_config)


def test_create_anthropic_chat_client():
    llm_config = LLMConfig(
        provider="anthropic",
        api_key="api",
        model_name="opus-4.6",
        temperature=0.2,
        max_tokens=1020,
    )
    create_chat_model(llm_config)


def test_create_local_chat_client():
    llm_config = LLMConfig(
        provider="local",
        model_name="llama3",
        base_url="http://localhost:11434/v1",
        api_key="not-needed",
    )
    model = create_chat_model(llm_config)
    assert getattr(model, "openai_api_base", None) == "http://localhost:11434/v1"


def test_create_unsupported_chat_client():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LLMConfig(provider="unsupported_provider", model_name="fake-model")
