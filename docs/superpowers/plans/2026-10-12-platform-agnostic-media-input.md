# Platform-Agnostic Media Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Telegram voice and photo OCR optional, independently configured OpenAI-compatible media APIs that never block Bot startup when credentials are absent.

**Architecture:** Add a shared media-provider configuration/client layer, then implement OCR and speech adapters over it. Register Bot handlers regardless of provider availability and store optional providers in application data; disabled handlers reply without downloading media or invoking the API. Deployment manifests expose separate optional voice and OCR settings.

**Tech Stack:** Python 3.13, httpx, Telegram Bot API, pytest, Helm.

**Spec:** `docs/superpowers/specs/2026-10-12-platform-agnostic-media-input-design.md`

## Global Constraints

- Canonical voice variables are `VOICE_API_BASE_URL`, `VOICE_API_KEY`, and `VOICE_API_MODEL`.
- Canonical OCR variables are `OCR_API_BASE_URL`, `OCR_API_KEY`, and `OCR_API_MODEL`.
- Missing or incomplete media configuration disables that modality; it must not stop Bot startup.
- Diagnostics may name variable groups but must never expose key values.
- Disabled handlers must not download Telegram media or call the ChatFit API.
- Voice and OCR configuration groups remain independent.
- Project quality requires `make quality` before completion.

## Review Focus

- A completely unset provider must yield `None`, not an exception.
- A partially configured provider must yield `None` and warn without exposing its key.
- Bot startup with no media credentials must still register both handlers.
- Disabled voice/photo requests must not call `getFile`, download media, or post to the backend.
- Helm must make voice and OCR secrets optional and no longer require a Google key.

### Task 1: Shared provider configuration and clients

**Files:**
- Create: `inputs/media_providers.py`
- Test: `tests/test_media_providers.py`

**Interfaces:**
- Produces: `MediaProviderConfig.from_env(prefix: str) -> MediaProviderConfig | None`
- Produces: `OpenAICompatibleMediaClient.chat(content: Sequence[dict[str, Any]]) -> str`

**Steps:**

- [ ] Write failing tests for absent, complete, partial, and already-complete endpoint configurations plus response parsing and errors.
- [ ] Confirm the module import/factory tests fail.
- [ ] Implement fail-soft environment resolution and the shared chat-completions client.
- [ ] Run focused tests and commit.

### Task 2: OCR and speech adapters

**Files:**
- Modify: `inputs/photo_ocr.py`
- Create: `inputs/speech.py`
- Test: `tests/test_photo_ocr.py`
- Test: `tests/test_speech.py`

**Interfaces:**
- Produces: `build_photo_text_extractor_from_env() -> PhotoTextExtractor | None`
- Produces: `build_speech_transcriber_from_env() -> SpeechTranscriber | None`

**Steps:**

- [ ] Write failing factory and message-construction tests for OCR and speech.
- [ ] Replace the Gemini-only OCR implementation with the generic adapter.
- [ ] Add the generic speech transcriber.
- [ ] Run focused tests and commit.

### Task 3: Bot fail-soft integration

**Files:**
- Modify: `bot.py`
- Test: `tests/test_bot.py`

**Interfaces:**
- Consumes: optional factories from Task 2.
- Produces: `build_telegram_application(..., speech_transcriber=None, photo_text_extractor=None)`

**Steps:**

- [ ] Write failing startup and disabled-handler tests.
- [ ] Store both optional providers and always register handlers.
- [ ] Replace direct Gemini voice code with the speech adapter.
- [ ] Add an enabled voice test proving transcription is forwarded to the backend.
- [ ] Run focused Bot tests and commit.

### Task 4: Deployment/docs and full verification

**Files:**
- Modify: `.env.example`, README, architecture docs, landing copy as needed.
- Modify: Helm values, ConfigMap, Bot Deployment, and Helm README.
- Test: documentation and Helm tests.

**Steps:**

- [ ] Update tests first for optional generic media variables and removal of Google-only deployment.
- [ ] Update configuration examples and manifests.
- [ ] Run focused documentation/Helm tests.
- [ ] Run the full suite and `make quality`.
- [ ] Spawn an independent verification subagent, fix every finding, push to PR #8, and wait for CI.
