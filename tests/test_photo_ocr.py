import pytest

from inputs.photo_ocr import (
    GeminiPhotoTextExtractor,
    PhotoTextExtractionResult,
    build_photo_text_extractor_from_env,
)


def test_photo_ocr_factory_creates_gemini_provider(monkeypatch):
    monkeypatch.setenv("PHOTO_OCR_PROVIDER", "gemini")
    monkeypatch.setenv("PHOTO_OCR_MODEL", "gemini-test-model")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    extractor = build_photo_text_extractor_from_env()

    assert isinstance(extractor, GeminiPhotoTextExtractor)
    assert extractor.model_name == "gemini-test-model"
    assert extractor.api_key == "test-key"


def test_photo_ocr_factory_uses_gemini_media_model_fallback(monkeypatch):
    monkeypatch.setenv("PHOTO_OCR_PROVIDER", "gemini")
    monkeypatch.delenv("PHOTO_OCR_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_MEDIA_MODEL", "gemini-media-model")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    extractor = build_photo_text_extractor_from_env()

    assert isinstance(extractor, GeminiPhotoTextExtractor)
    assert extractor.model_name == "gemini-media-model"
    assert extractor.api_key == "gemini-key"


def test_photo_ocr_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("PHOTO_OCR_PROVIDER", "other")

    with pytest.raises(ValueError, match="Unsupported PHOTO_OCR_PROVIDER"):
        build_photo_text_extractor_from_env()


def test_photo_text_extraction_result_strips_text():
    result = PhotoTextExtractionResult(text="  深蹲 5x5  ", warnings=["low contrast"])

    assert result.text == "深蹲 5x5"
    assert result.warnings == ["low contrast"]
