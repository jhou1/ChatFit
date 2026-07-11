# ChatFit Agent Evaluation Framework Design

## Overview
This document outlines the architecture and execution flow for evaluating the ChatFit AI agent. The framework is designed to be AI-native, secure (local-first data), and reliable. It splits evaluation into two distinct pipelines: deterministic code-based grading for integration/CI, and probabilistic LLM-as-a-judge grading for production releases.

## Core Architecture & Tracing

### Langfuse Integration
To satisfy the requirement for high-fidelity traceability (learning agent trajectories, token usage, decision-making, and tool calls), the core agent will be instrumented with **Langfuse**. 

* **Why Langfuse:** It is an open-source, AI-native tracing platform that can be self-hosted via Docker. It natively understands LLM interactions (prompts, tool calls, token usage) unlike traditional microservice tracing tools like OpenTelemetry.
* **Security & Reliability:** By adding Langfuse to the existing `docker-compose.yml`, all traces remain completely local, adhering to ChatFit's privacy-first architecture (local SQLite/Chroma). No data leaves the user's infrastructure.
* **Eval Integration:** During evaluations, test runs will be tagged with a specific `session_id` or `trace_id` correlating to the test case, permanently linking the test output to its visual trace in Langfuse.

## Evaluation Flow

### 1. The Dataset (YAML)
Evaluation datasets will be managed as YAML files (e.g., `tests/eval_datasets/cases.yaml`). This format is human-readable and easily version-controlled.

**Example Schema:**
```yaml
- id: test_meal_extraction_01
  input: "I just had 3 scrambled eggs and a piece of toast for breakfast."
  eval_type: integration
  expected_tools: 
    - name: log_meal
      args_contain: ["scrambled eggs", "toast"]
  expected_rag_invocation: false
```

### 2. Code Grader Pipeline (Integration / CI)
* **Runner:** Custom `pytest` framework.
* **Purpose:** Fast, deterministic evaluation of agent mechanics.
* **Execution:**
  1. `pytest` reads the YAML cases.
  2. Injects a test prompt into the agent (passing a `trace_id` for Langfuse).
  3. Captures the generated trajectory (sequence of events/tool calls).
  4. Asserts that the expected tools were called with the correct arguments.
* **Outcome:** Passes or fails the CI build instantly without requiring LLM-judge tokens.

### 3. LLM-as-a-Judge Pipeline (Production Release)
* **Runner:** Standalone evaluation script evaluating against Langfuse traces.
* **Purpose:** Nuanced evaluation of conversation quality and retrieval accuracy.
* **Execution:**
  1. The script fetches recent execution traces from Langfuse (either from live production or a generated golden run).
  2. Passes the user input, agent trajectory, retrieved RAG context, and final response to a dedicated LLM prompt.
  3. The LLM scores specific metrics on a 1-5 scale:
     * **RAG Contextual Relevancy:** Did the retrieved ChromaDB context contain the information needed?
     * **Conversational Tone:** Was the assistant encouraging, friendly, and helpful?
  4. The script pushes these scores back to Langfuse using the Langfuse SDK's evaluation API.
* **Outcome:** Developers can filter traces in the Langfuse UI by score to investigate low-quality interactions and adjust prompts or RAG strategies accordingly.
