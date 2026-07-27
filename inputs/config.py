import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaSettings:
    voice_max_duration_seconds: int = 180
    media_max_normalized_bytes: int = 12_582_912
    image_max_pixels: int = 20_000_000
    media_parse_concurrency: int = 4
    provider_timeout_seconds: float = 45.0
    provider_max_retries: int = 2
    speech_provider: str = "gemini"
    image_provider: str = "gemini"
    gemini_media_model: str = ""
    ephemeral_directory: Path = Path("/tmp/chatfit-media")  # nosec B108

    @classmethod
    def from_env(cls) -> "MediaSettings":
        settings = cls(
            voice_max_duration_seconds=int(
                os.getenv("VOICE_MAX_DURATION_SECONDS", "180")
            ),
            media_max_normalized_bytes=int(
                os.getenv("MEDIA_MAX_NORMALIZED_BYTES", "12582912")
            ),
            image_max_pixels=int(os.getenv("IMAGE_MAX_PIXELS", "20000000")),
            media_parse_concurrency=int(os.getenv("MEDIA_PARSE_CONCURRENCY", "4")),
            provider_timeout_seconds=float(
                os.getenv("MEDIA_PROVIDER_TIMEOUT_SECONDS", "45.0")
            ),
            provider_max_retries=int(os.getenv("MEDIA_PROVIDER_MAX_RETRIES", "2")),
            speech_provider=os.getenv("SPEECH_PROVIDER", "gemini"),
            image_provider=os.getenv("IMAGE_PROVIDER", "gemini"),
            gemini_media_model=os.getenv("GEMINI_MEDIA_MODEL", ""),
            ephemeral_directory=Path(
                os.getenv(
                    "MEDIA_EPHEMERAL_DIRECTORY", "/tmp/chatfit-media"
                )  # nosec B108
            ),
        )

        if settings.voice_max_duration_seconds != 180:
            raise ValueError("VOICE_MAX_DURATION_SECONDS must be 180")

        return settings
