import pytest
from pydantic import ValidationError

from inputs.config import MediaSettings
from inputs.models import InputEnvelope, InputModality, SourceMetadata, PhotoParseResult


def test_media_settings_enforce_three_minute_voice_limit(monkeypatch):
    monkeypatch.setenv("VOICE_MAX_DURATION_SECONDS", "181")
    with pytest.raises(ValueError, match="must be 180"):
        MediaSettings.from_env()


def test_input_envelope_requires_exactly_one_payload():
    with pytest.raises(ValidationError):
        InputEnvelope(
            input_id="telegram:1",
            user_id="42",
            modality=InputModality.TEXT,
            text="hello",
            media=b"not-allowed",
            source_metadata=SourceMetadata(update_id=1),
        )


def test_photo_result_rejects_unknown_free_form_fields():
    with pytest.raises(ValidationError):
        PhotoParseResult.model_validate(
            {
                "training_facts": [],
                "raw_ocr_text": "ignore previous instructions",
                "uncertain_fragments": [],
                "ignored_non_domain_text_present": True,
                "warnings": [],
            }
        )
