"""Configuration and HTTP support for optional media-provider APIs."""

from collections.abc import Sequence
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class MediaChatClient(Protocol):
    async def chat(self, content: Sequence[dict[str, Any]]) -> str:
        """Send multimodal content and return provider text."""


@dataclass(frozen=True)
class MediaProviderConfig:
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls, prefix: str) -> "MediaProviderConfig | None":
        base_url = os.getenv(f"{prefix}API_BASE_URL", "").strip()
        api_key = os.getenv(f"{prefix}API_KEY", "").strip()
        model = os.getenv(f"{prefix}API_MODEL", "").strip()
        values = (base_url, api_key, model)

        if not any(values):
            return None
        if not all(values):
            label = prefix.rstrip("_").upper()
            logger.warning(
                "%sAPI_BASE_URL, %sAPI_KEY, and %sAPI_MODEL must all be set; "
                "%s input is disabled",
                prefix,
                prefix,
                prefix,
                label,
            )
            return None
        return cls(base_url=base_url, api_key=api_key, model=model)


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _response_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content)


class OpenAICompatibleMediaClient:
    def __init__(
        self,
        config: MediaProviderConfig,
        *,
        timeout: float = 45.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._timeout = timeout
        self._transport = transport

    async def chat(self, content: Sequence[dict[str, Any]]) -> str:
        payload = {
            "model": self._config.model,
            "messages": [{"role": "user", "content": list(content)}],
            "temperature": 0,
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            response = await client.post(
                chat_completions_url(self._config.base_url),
                json=payload,
                headers={"Authorization": f"Bearer {self._config.api_key}"},
            )
            response.raise_for_status()

        data = response.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            raise ValueError("Media provider returned no text")
        message = choices[0].get("message", {})
        text = _response_text(message.get("content")).strip()
        if not text:
            raise ValueError("Media provider returned no text")
        return text
