# Platform-Agnostic Chat LLM Design

## Goal

ChatFit's chat agents should use any OpenAI-compatible chat API selected by an explicit base URL, API key, and model name. GLM deployments may use `GLM_API_KEY` as a compatibility alias for the canonical `LLM_API_KEY`.

## Configuration

The canonical runtime settings are `LLM_API_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`. Optional settings are `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, and the existing `LLM_PROXY`. Chat-model startup fails fast if any required value is absent or blank. The key resolution order is `LLM_API_KEY`, then `GLM_API_KEY`; key values are never included in diagnostics.

The configured endpoint must support OpenAI-compatible chat requests, tool calling, and structured output because ChatFit's specialist agents rely on those capabilities.

## Architecture

A shared resolver converts environment values into the existing `LLMConfig`. The resolver returns an `openai_compatible` configuration implemented by `ChatOpenAI`. API startup, local CLI startup, and the LLM judge use that resolver rather than independently hardcoding Google Gemini. Native Google, Anthropic, OpenAI, and local providers remain available for explicit programmatic configurations, but they are no longer the default runtime path.

Voice transcription and photo OCR remain Gemini-specific. Their provider boundary can be made generic in a later change; conflating chat-model compatibility with multimodal media APIs would overstate what this change supports.

## Compatibility and Deployment

Existing programmatic `LLMConfig` uses remain valid. Runtime deployments must migrate from a chat-only `GOOGLE_API_KEY` to the three canonical chat variables (or canonical endpoint/model plus `GLM_API_KEY`). Compose, `.env.example`, README guidance, and Helm API deployment configuration expose those variables. The Bot continues to receive `GOOGLE_API_KEY` for Gemini media parsing.
