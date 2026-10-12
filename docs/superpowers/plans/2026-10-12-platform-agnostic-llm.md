# Platform-Agnostic Chat LLM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure ChatFit's chat agents through any OpenAI-compatible endpoint using an API base URL, API key, and model name, with `GLM_API_KEY` accepted as a key alias.

**Architecture:** Add one environment resolver that constructs the existing `LLMConfig`; add an `openai_compatible` factory mapping to `ChatOpenAI`; then make API, CLI, and judge startup consume the resolver. Update deployment and documentation while keeping Gemini media parsing explicitly Gemini-specific.

**Tech Stack:** Python 3.13, Pydantic, LangChain OpenAI, pytest, Helm.

**Spec:** `docs/superpowers/specs/2026-10-12-platform-agnostic-llm-design.md`

## Global Constraints

- Canonical required variables are exactly `LLM_API_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`.
- `GLM_API_KEY` is accepted only as an alias when `LLM_API_KEY` is absent or blank.
- Blank required values are invalid; diagnostics name variables but never expose key values.
- API, CLI, and LLM judge share the same resolver.
- Voice and photo parsing remain Gemini providers.
- Project quality requires `make quality` before completion.

## Review Focus

- Partial configuration must fail with all missing names rather than selecting Google silently.
- `LLM_API_KEY` must win over `GLM_API_KEY` when both are present.
- API and CLI must receive identical base URL, model, key, temperature, token, and proxy settings.
- Helm API and Bot must expose chat credentials without removing Gemini credentials needed by Bot media parsing.
- README local startup checks must reject blank generic chat configuration.

### Task 1: Shared resolver and compatible factory

**Files:**
- Create: `agents/llm_settings.py`
- Modify: `agents/llm_factory.py`
- Test: `tests/test_llm_settings.py`
- Test: `tests/test_utils.py`

**Interfaces:**
- Produces: `resolve_llm_config_from_env(env: Mapping[str, str] | None = None) -> LLMConfig`
- Produces: `LLMConfig.provider == "openai_compatible"`

**Steps:**

- [ ] Add failing resolver tests for canonical values, GLM fallback and precedence, optional numeric parsing, proxy injection, and all missing/blank errors.
- [ ] Run those tests and confirm they fail because the resolver does not exist.
- [ ] Implement `resolve_llm_config_from_env` with fail-fast validation.
- [ ] Add a failing factory test for an OpenAI-compatible endpoint.
- [ ] Add `openai_compatible` to `LLMConfig` and map it to `ChatOpenAI`.
- [ ] Run resolver and factory tests; commit Task 1.

### Task 2: Runtime integration

**Files:**
- Modify: `api.py`
- Modify: `main.py`
- Modify: `scripts/llm_judge.py`
- Test: `tests/test_api.py`
- Test: `tests/test_utils.py` or `tests/test_llm_settings.py`

**Interfaces:**
- Consumes: `resolve_llm_config_from_env()`
- Produces: API startup and CLI construction using the shared `LLMConfig`.

**Steps:**

- [ ] Add a failing API startup test proving the app-state config comes from generic environment variables.
- [ ] Replace API's hardcoded Google config with the resolver while preserving proxy handling.
- [ ] Add or adapt a CLI test proving `main()` passes the resolver-produced config into the graph.
- [ ] Replace CLI's hardcoded Google config with the resolver.
- [ ] Add a judge test or direct call assertion proving `judge_llm=None` constructs from the shared resolver.
- [ ] Run focused tests; commit Task 2.

### Task 3: Deployment and documentation

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `deploy/helm/chatfit/values.yaml`
- Modify: `deploy/helm/chatfit/templates/configmap.yaml`
- Modify: `deploy/helm/chatfit/templates/api-deployment.yaml`
- Test: `tests/test_documentation.py`
- Test: `tests/test_helm_chart.py`

**Interfaces:**
- Consumes: canonical environment names from Task 1.
- Produces: Compose/Helm-ready generic chat configuration and Gemini media configuration.

**Steps:**

- [ ] Update documentation tests first to require generic local startup checks and Helm API env names.
- [ ] Run documentation and Helm tests to observe failures.
- [ ] Update `.env.example`, README, Helm values/config map/API deployment.
- [ ] Keep Bot's `GOOGLE_API_KEY` for Gemini voice/photo parsing and document that distinction.
- [ ] Run documentation and Helm tests; commit Task 3.

### Task 4: Full verification and review

**Files:**
- All changed files.

**Steps:**

- [ ] Run the complete default test suite.
- [ ] Run `make quality`.
- [ ] Spawn a verification subagent to independently run tests and quality and review the diff against this plan.
- [ ] Fix every reported error, failure, or warning and repeat verification.
- [ ] Push the branch, open a PR, attach it, and wait for GitHub Actions results.
