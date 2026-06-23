import os

from typing import Literal, Optional
from pydantic import BaseModel

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic

class LLMConfig(BaseModel):
    provider: Literal["openai", "google", "anthropic",  "local"]
    base_url: Optional[str] = None
    model_name: str
    api_key: Optional[str] = None
    temperature: float = 0.5
    max_tokens: int = 2048
    kwargs: dict = {}

def create_chat_model(config: LLMConfig):
    provider = config.provider.lower()

    if provider == "openai":
        return ChatOpenAI(
            model=config.model_name,
            api_key=os.getenv("OPENAI_API_KEY") or config.api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            **config.kwargs
        )
    elif provider == "google":
        return ChatGoogleGenerativeAI(
            model=config.model_name,
            api_key=os.getenv("GOOGLE_API_KEY") or config.api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            **config.kwargs
        )
    elif provider == "anthropic":
        return ChatAnthropic(
            model=config.model_name,
            api_key=os.getenv("ANTHROPIC_API_KEY") or config.api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            **config.kwargs
        )
    elif provider == "local":
        # for Ollama, llama.cpp, LM Studio, MLX, use OpenAI compatiable api
        if config.base_url is None:
            raise ValueError("To use local LLM, provide a base url.")

        return ChatOpenAI(
            model=config.model_name,
            base_url=config.base_url,
            api_key=config.api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            **config.kwargs
        )
