from enum import Enum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputModality(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    PHOTO = "photo"


class SourceMetadata(BaseModel):
    update_id: int
    model_config = ConfigDict(extra="forbid")


class InputEnvelope(BaseModel):
    input_id: str
    user_id: str
    modality: InputModality
    text: str | None = None
    media: bytes | None = None
    source_metadata: SourceMetadata

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def check_exactly_one_payload(self) -> "InputEnvelope":
        if (self.text is None and self.media is None) or (
            self.text is not None and self.media is not None
        ):
            raise ValueError("InputEnvelope requires exactly one payload")
        return self


class CanonicalAudio(BaseModel):
    path: Path
    encoding: str = "flac"
    channels: int = 1
    sample_rate_hz: int = 16_000
    model_config = ConfigDict(extra="forbid")


class CanonicalImage(BaseModel):
    path: Path
    encoding: str = "jpeg"
    color_space: str = "RGB"
    model_config = ConfigDict(extra="forbid")


class VoiceParseResult(BaseModel):
    transcript: str
    is_domain_relevant: bool = True
    warnings: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


class PhotoTrainingFact(BaseModel):
    exercise_alias_candidate: str
    sets: int | float | None = None
    reps: int | float | None = None
    weight: int | float | None = None
    weight_unit: str | None = None
    model_config = ConfigDict(extra="forbid")


class PhotoParseResult(BaseModel):
    training_facts: list[PhotoTrainingFact]
    uncertain_fragments: list[str] = Field(default_factory=list)
    ignored_non_domain_text_present: bool = False
    warnings: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


class ValidatedTrainingFact(BaseModel):
    exercise_id: str
    exercise_display_name: str
    sets: int | float | None = None
    reps: int | float | None = None
    weight: int | float | None = None
    weight_unit: str | None = None
    model_config = ConfigDict(extra="forbid")


class ReadyInput(BaseModel):
    safe_agent_text: str
    model_config = ConfigDict(extra="forbid")


class PendingInputClarification(BaseModel):
    question: str
    candidate_values: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid")


class NormalizationDecision(BaseModel):
    ready: ReadyInput | None = None
    clarification_required: PendingInputClarification | None = None
    rejected: str | None = None
    model_config = ConfigDict(extra="forbid")


class MediaParseContext(BaseModel):
    user_id: str
    model_config = ConfigDict(extra="forbid")


class EphemeralMedia(BaseModel):
    path: Path
    mime_type: str
    model_config = ConfigDict(extra="forbid")
