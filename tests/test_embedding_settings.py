import pytest

from agents.embedding_settings import EmbeddingConfig, build_embedding_model_from_env


def test_embedding_config_reads_complete_group(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_API_BASE_URL", " https://api.example.com/v1 ")
    monkeypatch.setenv("EMBEDDING_API_KEY", " embedding-key ")
    monkeypatch.setenv("EMBEDDING_API_MODEL", " embedding-model ")

    config = EmbeddingConfig.from_env()

    assert config == EmbeddingConfig(
        base_url="https://api.example.com/v1",
        api_key="embedding-key",
        model="embedding-model",
    )


def test_embedding_config_normalizes_complete_embeddings_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "EMBEDDING_API_BASE_URL", "https://api.example.com/v1/embeddings"
    )
    monkeypatch.setenv("EMBEDDING_API_KEY", "key")
    monkeypatch.setenv("EMBEDDING_API_MODEL", "model")

    config = EmbeddingConfig.from_env()

    assert config.base_url == "https://api.example.com/v1"


def test_embedding_config_rejects_missing_values_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMBEDDING_API_BASE_URL", raising=False)
    monkeypatch.setenv("EMBEDDING_API_KEY", "secret-value")
    monkeypatch.delenv("EMBEDDING_API_MODEL", raising=False)

    with pytest.raises(ValueError) as error:
        EmbeddingConfig.from_env()

    message = str(error.value)
    assert "EMBEDDING_API_BASE_URL" in message
    assert "EMBEDDING_API_MODEL" in message
    assert "must be set and non-empty" in message
    assert "secret-value" not in message


@pytest.mark.parametrize(
    "env",
    (
        {"EMBEDDING_API_KEY": "key", "EMBEDDING_API_MODEL": "model"},
        {
            "EMBEDDING_API_BASE_URL": "https://api.example.com/v1",
            "EMBEDDING_API_MODEL": "model",
        },
        {
            "EMBEDDING_API_BASE_URL": "https://api.example.com/v1",
            "EMBEDDING_API_KEY": "key",
        },
    ),
)
def test_embedding_config_rejects_each_missing_value(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    for name in ("EMBEDDING_API_BASE_URL", "EMBEDDING_API_KEY", "EMBEDDING_API_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="must be set and non-empty"):
        EmbeddingConfig.from_env()


def test_builds_openai_compatible_embedding_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class RecordingEmbeddings:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "agents.embedding_settings.OpenAIEmbeddings", RecordingEmbeddings
    )
    monkeypatch.setenv("EMBEDDING_API_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-key")
    monkeypatch.setenv("EMBEDDING_API_MODEL", "embedding-model")

    model = build_embedding_model_from_env()

    assert isinstance(model, RecordingEmbeddings)
    assert captured["base_url"] == "https://api.example.com/v1"
    assert captured["api_key"].get_secret_value() == "embedding-key"
    assert captured["model"] == "embedding-model"
    assert captured["check_embedding_ctx_length"] is False
