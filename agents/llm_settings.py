"""Environment-backed configuration for ChatFit's chat models."""

from collections.abc import Mapping
import os

from agents.llm_factory import LLMConfig


def _required_value(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _required_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw_value = env.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return float(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error


def _required_positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw_value = env.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def resolve_llm_config_from_env(
    env: Mapping[str, str] | None = None,
) -> LLMConfig:
    """Resolve the platform-agnostic chat model configuration.

    ChatFit targets any OpenAI-compatible endpoint. Keeping this resolution in
    one place ensures the API, CLI, and evaluation tooling cannot drift between
    different provider defaults.
    """

    values = os.environ if env is None else env
    base_url = _required_value(values, "LLM_API_BASE_URL")
    api_key = _required_value(values, "LLM_API_KEY")
    if api_key is None:
        api_key = _required_value(values, "GLM_API_KEY")
    model_name = _required_value(values, "LLM_MODEL")

    if base_url is None or api_key is None or model_name is None:
        missing = []
        if base_url is None:
            missing.append("LLM_API_BASE_URL")
        if api_key is None:
            missing.append("LLM_API_KEY (or GLM_API_KEY)")
        if model_name is None:
            missing.append("LLM_MODEL")
        names = ", ".join(missing)
        raise ValueError(f"{names} must be set and non-empty")

    kwargs: dict[str, str] = {}
    proxy = values.get("LLM_PROXY", "").strip()
    if proxy:
        kwargs["openai_proxy"] = proxy

    return LLMConfig(
        provider="openai_compatible",
        base_url=base_url,
        model_name=model_name,
        api_key=api_key,
        temperature=_required_float(values, "LLM_TEMPERATURE", 0.5),
        max_tokens=_required_positive_int(values, "LLM_MAX_TOKENS", 8192),
        kwargs=kwargs,
    )
