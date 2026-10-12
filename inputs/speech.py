import base64
import os
from dataclasses import dataclass
from typing import Protocol

from inputs.media_providers import (
    MediaProviderConfig,
    OpenAICompatibleMediaClient,
)

SPEECH_TRANSCRIPTION_PROMPT = (
    "Transcribe this voice message exactly as spoken in its original language. "
    "Do not add any extra commentary or text."
)


class SpeechTranscriber(Protocol):
    async def transcribe(self, audio_bytes: bytes, mime_type: str) -> str:
        """Transcribe audio bytes in their original language."""


@dataclass(frozen=True)
class OpenAICompatibleSpeechTranscriber:
    client: OpenAICompatibleMediaClient

    async def transcribe(self, audio_bytes: bytes, mime_type: str) -> str:
        content = [
            {"type": "text", "text": SPEECH_TRANSCRIPTION_PROMPT},
            {
                "type": "input_audio",
                "input_audio": {
                    "data": base64.b64encode(audio_bytes).decode("ascii"),
                    "format": _audio_format(mime_type),
                },
            },
        ]
        return await self.client.chat(content)


def _audio_format(mime_type: str) -> str:
    subtype = mime_type.split(";", maxsplit=1)[0].split("/")[-1].lower()
    aliases = {
        "mpeg": "mp3",
        "x-wav": "wav",
        "wave": "wav",
    }
    return aliases.get(subtype, subtype)


def build_speech_transcriber_from_env() -> SpeechTranscriber | None:
    config = MediaProviderConfig.from_env("VOICE_")
    if config is None:
        return None
    timeout = float(os.getenv("MEDIA_PROVIDER_TIMEOUT_SECONDS", "45.0"))
    return OpenAICompatibleSpeechTranscriber(
        client=OpenAICompatibleMediaClient(config, timeout=timeout)
    )
