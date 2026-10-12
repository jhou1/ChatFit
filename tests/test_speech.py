import base64
from collections.abc import Sequence
from typing import Any

import pytest

from inputs.speech import (
    OpenAICompatibleSpeechTranscriber,
    build_speech_transcriber_from_env,
)


class RecordingMediaClient:
    def __init__(self, text: str = "hello") -> None:
        self.text = text
        self.content: list[dict[str, Any]] | None = None

    async def chat(self, content: Sequence[dict[str, Any]]) -> str:
        self.content = list(content)
        return self.text


def test_speech_factory_disables_provider_when_configuration_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("VOICE_API_BASE_URL", "VOICE_API_KEY", "VOICE_API_MODEL"):
        monkeypatch.delenv(name, raising=False)

    assert build_speech_transcriber_from_env() is None


def test_speech_factory_disables_partial_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_API_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("VOICE_API_MODEL", "voice-model")
    monkeypatch.delenv("VOICE_API_KEY", raising=False)

    assert build_speech_transcriber_from_env() is None


def test_speech_factory_creates_generic_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_API_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("VOICE_API_KEY", "voice-key")
    monkeypatch.setenv("VOICE_API_MODEL", "voice-model")

    transcriber = build_speech_transcriber_from_env()

    assert isinstance(transcriber, OpenAICompatibleSpeechTranscriber)


@pytest.mark.asyncio
async def test_speech_transcriber_sends_base64_ogg_audio() -> None:
    client = RecordingMediaClient()
    transcriber = OpenAICompatibleSpeechTranscriber(client)

    result = await transcriber.transcribe(b"audio-bytes", "audio/ogg")

    assert client.content is not None
    assert result == "hello"
    assert client.content[0]["type"] == "text"
    assert client.content[0]["text"] == (
        "Transcribe this voice message exactly as spoken in its original "
        "language. Do not add any extra commentary or text."
    )
    assert client.content[1] == {
        "type": "input_audio",
        "input_audio": {
            "data": base64.b64encode(b"audio-bytes").decode("ascii"),
            "format": "ogg",
        },
    }


@pytest.mark.parametrize(
    ("mime_type", "audio_format"),
    (
        ("audio/mpeg", "mp3"),
        ("audio/mp4", "mp4"),
        ("audio/wav", "wav"),
        ("audio/x-wav", "wav"),
    ),
)
@pytest.mark.asyncio
async def test_speech_transcriber_maps_common_mime_types(
    mime_type: str, audio_format: str
) -> None:
    client = RecordingMediaClient()
    transcriber = OpenAICompatibleSpeechTranscriber(client)

    await transcriber.transcribe(b"audio", mime_type)

    assert client.content is not None
    assert client.content[1]["input_audio"]["format"] == audio_format
