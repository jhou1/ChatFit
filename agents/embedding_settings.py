"""Environment-backed configuration for OpenAI-compatible embeddings."""

from collections.abc import Mapping
import os
from dataclasses import dataclass
from pydantic import SecretStr

from langchain_openai import OpenAIEmbeddings


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EmbeddingConfig":
        values = os.environ if env is None else env
        base_url = values.get("EMBEDDING_API_BASE_URL", "").strip()
        api_key = values.get("EMBEDDING_API_KEY", "").strip()
        model = values.get("EMBEDDING_API_MODEL", "").strip()
        missing = []
        if not base_url:
            missing.append("EMBEDDING_API_BASE_URL")
        if not api_key:
            missing.append("EMBEDDING_API_KEY")
        if not model:
            missing.append("EMBEDDING_API_MODEL")
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"{names} must be set and non-empty")

        if base_url.endswith("/embeddings"):
            base_url = base_url.removesuffix("/embeddings").rstrip("/")
        return cls(base_url=base_url, api_key=api_key, model=model)


def build_embedding_model_from_env(
    env: Mapping[str, str] | None = None,
) -> OpenAIEmbeddings:
    """Build the provider-agnostic embedding model used by recipe RAG."""

    config = EmbeddingConfig.from_env(env)
    return OpenAIEmbeddings(
        base_url=config.base_url,
        api_key=SecretStr(config.api_key),
        model=config.model,
        check_embedding_ctx_length=False,
    )
