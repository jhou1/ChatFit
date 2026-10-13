# Platform-Agnostic Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure recipe RAG embeddings through an explicit OpenAI-compatible endpoint, key, and model instead of Google's implicit environment credentials.

**Architecture:** Add `agents/embedding_settings.py` with fail-fast validation and a small adapter that passes the resolved values to `OpenAIEmbeddings`. `get_or_create_vector_store` will construct one embedding model from this configuration. API and CLI share the same code path.

**Tech Stack:** Python 3.13, LangChain OpenAI embeddings, pytest, Helm.

## Global Constraints

- Canonical variables are `EMBEDDING_API_BASE_URL`, `EMBEDDING_API_KEY`, and `EMBEDDING_API_MODEL`.
- Blank or missing values fail startup with variable names but never expose key values.
- The embedding endpoint may be an API root or a complete `/embeddings` URL.
- Embeddings remain required for recipe RAG startup; they are not disabled like media input.
- `GOOGLE_API_KEY` and `GEMINI_API_KEY` must not become implicit embedding dependencies.

## Tasks

1. Add failing tests for complete/missing configuration, direct-endpoint normalization, secret-safe errors, and generic embedding construction.
2. Implement `EmbeddingConfig` and `build_embedding_model_from_env`.
3. Replace both hardcoded Google constructors in `agents/rag.py` with one configured embedding model.
4. Update `.env.example`, README, architecture docs, Helm values/ConfigMap/API secret wiring, and Helm README.
5. Run focused tests, full tests, `make quality`, independent verification, push, and wait for CI.

> Migration note: changing embedding providers can change vector dimensions or semantic space. Existing `chroma.db` data should be rebuilt or moved to a new directory after switching providers.
