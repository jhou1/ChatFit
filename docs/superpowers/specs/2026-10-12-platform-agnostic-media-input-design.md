# Platform-Agnostic Media Input Design

## Goal

Telegram voice transcription and photo OCR use independently configured OpenAI-compatible media APIs. They no longer require Google-specific environment variables, and absent or incomplete media configuration disables only the affected input modality without preventing Bot startup or text chat.

## Configuration

Voice input is configured with `VOICE_API_BASE_URL`, `VOICE_API_KEY`, and `VOICE_API_MODEL`. Photo OCR is configured with `OCR_API_BASE_URL`, `OCR_API_KEY`, and `OCR_API_MODEL`. Each group is independent. When every value in a group is absent, that provider is disabled. When only some values are present, ChatFit logs a warning that names the variable group, never includes a key value, and disables that provider.

The base URL may point to an API root ending in `/v1` or directly to `/chat/completions`. Media requests use the OpenAI-compatible multimodal chat message format: base64 image data for OCR and base64 `input_audio` data for voice.

## Runtime behavior

The Bot always registers voice and photo handlers. If a provider is disabled, its handler replies with a short instruction to type the message instead and does not download media or call the backend. A configured provider receives only the media bytes, MIME type, and a constrained transcription/OCR prompt. Backend chat behavior remains unchanged.

This change intentionally removes the default Google Gemini media path from deployment documentation and Helm. Users can still select a Gemini-compatible OpenAI endpoint by supplying its URL, key, and model through the generic variables.
