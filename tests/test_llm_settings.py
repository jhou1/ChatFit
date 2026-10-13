import pytest

from agents.llm_settings import resolve_llm_config_from_env


def test_resolves_canonical_openai_compatible_configuration():
    config = resolve_llm_config_from_env(
        {
            "LLM_API_BASE_URL": " https://api.example.com/v1 ",
            "LLM_API_KEY": " canonical-key ",
            "LLM_MODEL": " glm-4.5 ",
            "LLM_TEMPERATURE": "0.2",
            "LLM_MAX_TOKENS": "4096",
        }
    )

    assert config.provider == "openai_compatible"
    assert config.base_url == "https://api.example.com/v1"
    assert config.model_name == "glm-4.5"
    assert config.api_key == "canonical-key"
    assert config.temperature == 0.2
    assert config.max_tokens == 4096
    assert config.kwargs == {}


def test_reads_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("LLM_API_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "canonical-key")
    monkeypatch.setenv("LLM_MODEL", "glm-4.5")

    config = resolve_llm_config_from_env()

    assert config.model_name == "glm-4.5"
    assert config.api_key == "canonical-key"


def test_llm_api_key_takes_precedence_over_glm_api_key():
    config = resolve_llm_config_from_env(
        {
            "LLM_API_BASE_URL": "https://api.example.com/v1",
            "LLM_API_KEY": "canonical-key",
            "GLM_API_KEY": "glm-key",
            "LLM_MODEL": "glm-4.5",
        }
    )

    assert config.api_key == "canonical-key"


def test_accepts_blank_canonical_key_when_glm_key_is_configured():
    config = resolve_llm_config_from_env(
        {
            "LLM_API_BASE_URL": "https://api.example.com/v1",
            "LLM_API_KEY": " ",
            "GLM_API_KEY": "glm-key",
            "LLM_MODEL": "glm-4.5",
        }
    )

    assert config.api_key == "glm-key"


def test_rejects_missing_required_values_without_exposing_secrets():
    with pytest.raises(ValueError) as error:
        resolve_llm_config_from_env({"GLM_API_KEY": "secret-value"})

    message = str(error.value)
    assert "LLM_API_BASE_URL" in message
    assert "LLM_MODEL" in message
    assert "secret-value" not in message


@pytest.mark.parametrize(
    "env",
    (
        {"LLM_API_KEY": "key", "LLM_MODEL": "model"},
        {
            "LLM_API_BASE_URL": "https://api.example.com/v1",
            "LLM_MODEL": "model",
        },
        {
            "LLM_API_BASE_URL": "https://api.example.com/v1",
            "LLM_API_KEY": "key",
            "GLM_API_KEY": "glm-key",
        },
    ),
)
def test_rejects_each_blank_or_missing_required_value(env):
    with pytest.raises(ValueError, match="must be set and non-empty"):
        resolve_llm_config_from_env(env)


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("LLM_TEMPERATURE", "not-a-number"),
        ("LLM_MAX_TOKENS", "not-a-number"),
        ("LLM_MAX_TOKENS", "0"),
    ),
)
def test_rejects_invalid_optional_numeric_values(name, value):
    with pytest.raises(ValueError, match=name):
        resolve_llm_config_from_env(
            {
                "LLM_API_BASE_URL": "https://api.example.com/v1",
                "LLM_API_KEY": "key",
                "LLM_MODEL": "model",
                name: value,
            }
        )


def test_adds_llm_proxy_to_client_args():
    config = resolve_llm_config_from_env(
        {
            "LLM_API_BASE_URL": "https://api.example.com/v1",
            "LLM_API_KEY": "key",
            "LLM_MODEL": "model",
            "LLM_PROXY": "socks5://127.0.0.1:8990",
        }
    )

    assert config.kwargs == {"client_args": {"proxy": "socks5://127.0.0.1:8990"}}
