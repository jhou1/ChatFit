import os

from typing import Literal, Optional
from pydantic import BaseModel

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic


class LLMConfig(BaseModel):
    provider: Literal["openai", "google", "anthropic", "local"]
    base_url: Optional[str] = None
    model_name: str
    api_key: Optional[str] = None
    temperature: float = 0.5
    max_tokens: int = 2048
    kwargs: dict = {}


provider_classes = {
    "openai": ChatOpenAI,
    "google": ChatGoogleGenerativeAI,
    "anthropic": ChatAnthropic,
    "local": ChatOpenAI,
}


def create_chat_model(config: LLMConfig):
    provider = config.provider.lower()
    provider_class = provider_classes.get(provider)

    if not provider_class:
        raise ValueError(f"Unsupported provider: {provider}")

    provider_api_key_envvar = f"{provider.upper()}_API_KEY"

    kwargs = {
        "model": config.model_name,
        "api_key": config.api_key or os.getenv(provider_api_key_envvar),
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        **config.kwargs,
    }

    if config.base_url:
        kwargs["base_url"] = config.base_url

    return provider_class(**kwargs)
