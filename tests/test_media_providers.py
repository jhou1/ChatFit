import json
import logging

import httpx
import pytest

from inputs.media_providers import (
    MediaProviderConfig,
    OpenAICompatibleMediaClient,
    chat_completions_url,
)


def test_media_provider_config_is_absent_when_no_values_are_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("VOICE_API_BASE_URL", "VOICE_API_KEY", "VOICE_API_MODEL"):
        monkeypatch.delenv(name, raising=False)

    assert MediaProviderConfig.from_env("VOICE_") is None


def test_media_provider_config_reads_complete_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_API_BASE_URL", " https://api.example.com/v1 ")
    monkeypatch.setenv("VOICE_API_KEY", " voice-key ")
    monkeypatch.setenv("VOICE_API_MODEL", " voice-model ")

    config = MediaProviderConfig.from_env("VOICE_")

    assert config == MediaProviderConfig(
        base_url="https://api.example.com/v1",
        api_key="voice-key",
        model="voice-model",
    )


def test_partial_media_provider_config_disables_provider_without_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OCR_API_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OCR_API_KEY", "secret-value")
    monkeypatch.delenv("OCR_API_MODEL", raising=False)

    with caplog.at_level(logging.WARNING, logger="inputs.media_providers"):
        config = MediaProviderConfig.from_env("OCR_")

    assert config is None
    assert "OCR_API_BASE_URL, OCR_API_KEY, and OCR_API_MODEL" in caplog.text
    assert "OCR input is disabled" in caplog.text
    assert "secret-value" not in caplog.text


@pytest.mark.parametrize(
    ("base_url", "expected"),
    (
        ("https://api.example.com/v1", "https://api.example.com/v1/chat/completions"),
        (
            "https://api.example.com/v1/",
            "https://api.example.com/v1/chat/completions",
        ),
        (
            "https://api.example.com/v1/chat/completions",
            "https://api.example.com/v1/chat/completions",
        ),
    ),
)
def test_chat_completions_url_accepts_root_or_complete_endpoint(
    base_url: str, expected: str
) -> None:
    assert chat_completions_url(base_url) == expected


@pytest.mark.asyncio
async def test_media_client_posts_openai_compatible_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "provider text"}}
                ]
            },
        )

    config = MediaProviderConfig(
        base_url="https://api.example.com/v1",
        api_key="provider-key",
        model="provider-model",
    )
    client = OpenAICompatibleMediaClient(
        config, timeout=12.5, transport=httpx.MockTransport(handler)
    )

    result = await client.chat([{"type": "text", "text": "prompt"}])

    assert result == "provider text"
    assert len(requests) == 1
    assert requests[0].url == "https://api.example.com/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer provider-key"
    assert json.loads(requests[0].content) == {
        "model": "provider-model",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "prompt"}]}],
        "temperature": 0,
    }


@pytest.mark.asyncio
async def test_media_client_joins_multipart_text_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "hello "},
                                {"type": "text", "text": "world"},
                            ],
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleMediaClient(
        MediaProviderConfig(
            base_url="https://api.example.com/v1/chat/completions",
            api_key="key",
            model="model",
        ),
        transport=httpx.MockTransport(handler),
    )

    assert await client.chat([{"type": "text", "text": "prompt"}]) == "hello world"


@pytest.mark.asyncio
async def test_media_client_raises_for_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={"error": "provider unavailable"})

    client = OpenAICompatibleMediaClient(
        MediaProviderConfig(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.chat([{"type": "text", "text": "prompt"}])


@pytest.mark.asyncio
async def test_media_client_retries_transient_http_error() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, json={"error": "provider unavailable"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "recovered"}}]},
        )

    client = OpenAICompatibleMediaClient(
        MediaProviderConfig(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        ),
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )

    assert await client.chat([{"type": "text", "text": "prompt"}]) == "recovered"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_media_client_exhausts_configured_retries() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, json={"error": "rate limited"})

    client = OpenAICompatibleMediaClient(
        MediaProviderConfig(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        ),
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.chat([{"type": "text", "text": "prompt"}])

    assert len(requests) == 3


@pytest.mark.asyncio
async def test_media_client_does_not_retry_client_error() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400, json={"error": "invalid request"})

    client = OpenAICompatibleMediaClient(
        MediaProviderConfig(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        ),
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.chat([{"type": "text", "text": "prompt"}])

    assert len(requests) == 1


@pytest.mark.asyncio
async def test_media_client_rejects_empty_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"choices": []})

    client = OpenAICompatibleMediaClient(
        MediaProviderConfig(
            base_url="https://api.example.com/v1",
            api_key="key",
            model="model",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ValueError, match="Media provider returned no text"):
        await client.chat([{"type": "text", "text": "prompt"}])
