"""Score a real Agent input/output pair using a rigorous Dynamic Rubric-based LLM Judge."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from agents.llm_factory import LLMConfig, create_chat_model
from agents.utils import extract_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DimensionEval:
    dimension: str
    evidence: str
    score: int
    weight: float


@dataclass(frozen=True)
class JudgeResult:
    evaluations: list[DimensionEval]
    overall_weighted_score: float


def build_dynamic_prompt(rubrics: list[dict]) -> str:
    prompt = "You are an expert AI evaluator for a Fitness & Nutrition Agent. Evaluate the response against this strict Rubric.\n\nRUBRIC:\n"
    for idx, r in enumerate(rubrics, 1):
        prompt += f"{idx}. Dimension: {r['dimension_name']} (Weight: {r['weight']})\n"
        prompt += f"   - Criteria: {r['criteria_description']}\n"
        prompt += f"   - Evidence Required: {r['evidence_requirement']}\n"

    prompt += "\nOutput strictly in valid JSON matching this exact structure (NO markdown code blocks, just raw JSON):\n"
    prompt += '{\n  "evaluations": [\n'
    prompt += '    {"dimension": "<name>", "evidence": "<quote>", "score": <1-5 int>, "weight": <float>}\n  ],\n'
    prompt += '  "overall_weighted_score": <float>\n}'
    return prompt


def parse_judge_response(response_text: str) -> JudgeResult:
    """Parse and validate the JSON judge response contract."""
    clean_text = response_text.strip()
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    if clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()

    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Judge response must be valid JSON. Error: {e}\nResponse: {clean_text}"
        )

    if "evaluations" not in data or "overall_weighted_score" not in data:
        raise ValueError("Judge response missing required fields.")

    evals = []
    for ev in data["evaluations"]:
        evals.append(
            DimensionEval(
                dimension=ev["dimension"],
                evidence=ev["evidence"],
                score=int(ev["score"]),
                weight=float(ev["weight"]),
            )
        )

    return JudgeResult(
        evaluations=evals, overall_weighted_score=float(data["overall_weighted_score"])
    )


async def evaluate_trace(
    trace_id: str,
    input_msg: str,
    output_msg: str,
    rubrics: list[dict],
    *,
    judge_llm: Any | None = None,
    langfuse_client: Any | None = None,
) -> JudgeResult:
    """Evaluate supplied content using dynamic Rubrics and write to a trace."""

    if not trace_id.strip() or not input_msg.strip() or not output_msg.strip():
        raise ValueError("trace_id, input_msg, and output_msg are required")

    if not rubrics:
        raise ValueError("Rubrics cannot be empty")

    if judge_llm is None:
        llm_config = LLMConfig(
            provider="google", model_name="gemini-3.5-flash", temperature=0.0
        )
        judge_llm = create_chat_model(llm_config)

    prompt = build_dynamic_prompt(rubrics)
    evaluation_content = f"User: {input_msg}\nAssistant: {output_msg}"

    response = await judge_llm.ainvoke(
        [SystemMessage(content=prompt), HumanMessage(content=evaluation_content)]
    )
    result = parse_judge_response(extract_text(response))

    if langfuse_client is not None:
        try:
            langfuse_client.create_score(
                trace_id=trace_id,
                name="overall_score",
                value=result.overall_weighted_score,
            )
            for ev in result.evaluations:
                langfuse_client.create_score(
                    trace_id=trace_id,
                    name=ev.dimension.replace(" ", "_").lower(),
                    value=ev.score,
                    comment=ev.evidence,
                )
        except Exception:
            logger.warning("Failed to export LLM judge scores to Langfuse")

    return result
