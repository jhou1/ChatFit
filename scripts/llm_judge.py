"""Score a real Agent input/output pair using a rigorous Rubric-based LLM Judge."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langfuse import Langfuse

from agents.llm_factory import LLMConfig, create_chat_model
from agents.utils import extract_text

JUDGE_PROMPT = """
You are an expert conversational AI evaluator for a Fitness & Nutrition Agent.
Your task is to evaluate the assistant's response against a strict Rubric.
The Rubric consists of Evaluation Dimensions, Score Levels (1-5), Criteria, and Evidence Requirements.

RUBRIC:
1. Dimension: Semantic Accuracy & Clarity (Weight: 0.6)
   - Evidence Requirement: Extract quotes showing how the assistant addressed the core intent, extracted parameters, or asked for clarifications.
   - Level 5: Addresses the intent perfectly with zero ambiguity. Clear, direct, and leaves no confusion. If clarification is needed, asks exactly the right questions.
   - Level 4: Addresses the intent well, but slightly verbose or mildly indirect.
   - Level 3: Partially addresses the intent. Missing some context but functionally acceptable.
   - Level 2: Vague or confusing. Misses the point or hallucinates parameters.
   - Level 1: Completely hallucinates or fails to address the user's intent.

2. Dimension: Conversational Tone (Weight: 0.4)
   - Evidence Requirement: Extract quotes showing empathy, encouragement, or robotic language.
   - Level 5: Highly helpful, friendly, natural, and encouraging. Fits a premium fitness coach perfectly.
   - Level 4: Polite and helpful, but slightly generic.
   - Level 3: Neutral. Neither friendly nor rude. Just transactional.
   - Level 2: Robotic, rigid, or slightly dismissive.
   - Level 1: Rude, unhelpful, or completely inappropriate.

Output strictly in valid JSON format matching the following structure exactly (NO markdown code blocks, just raw JSON):
{
  "evaluations": [
    {
      "dimension": "Semantic Accuracy & Clarity",
      "evidence": "...",
      "score": <int>,
      "weight": 0.6
    },
    {
      "dimension": "Conversational Tone",
      "evidence": "...",
      "score": <int>,
      "weight": 0.4
    }
  ],
  "overall_weighted_score": <float>,
  "reasoning_summary": "<string>"
}
"""

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
    reasoning_summary: str

def parse_judge_response(response_text: str) -> JudgeResult:
    """Parse and validate the JSON judge response contract."""
    # Strip markdown code blocks if the LLM adds them despite instructions
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
        raise ValueError(f"Judge response must be valid JSON. Error: {e}\nResponse: {clean_text}")

    if "evaluations" not in data or "overall_weighted_score" not in data or "reasoning_summary" not in data:
        raise ValueError("Judge response missing required fields.")

    evaluations = []
    for ev in data["evaluations"]:
        evaluations.append(
            DimensionEval(
                dimension=ev["dimension"],
                evidence=ev["evidence"],
                score=int(ev["score"]),
                weight=float(ev["weight"])
            )
        )

    return JudgeResult(
        evaluations=evaluations,
        overall_weighted_score=float(data["overall_weighted_score"]),
        reasoning_summary=data["reasoning_summary"]
    )

async def evaluate_trace(
    trace_id: str,
    input_msg: str,
    output_msg: str,
    *,
    judge_llm: Any | None = None,
    langfuse_client: Any | None = None,
) -> JudgeResult:
    """Evaluate supplied content using the Rubric and write to a trace."""

    if not trace_id.strip() or not input_msg.strip() or not output_msg.strip():
        raise ValueError("trace_id, input_msg, and output_msg are required")

    if judge_llm is None:
        llm_config = LLMConfig(
            provider="google", model_name="gemini-3.5-flash", temperature=0.0
        )
        judge_llm = create_chat_model(llm_config)
    if langfuse_client is None:
        langfuse_client = Langfuse()

    evaluation_content = f"User: {input_msg}\nAssistant: {output_msg}"
    response = await judge_llm.ainvoke(
        [SystemMessage(content=JUDGE_PROMPT), HumanMessage(content=evaluation_content)]
    )
    result = parse_judge_response(extract_text(response))
    
    # Optional: Log to Langfuse
    try:
        langfuse_client.create_score(
            trace_id=trace_id,
            name="rubric_weighted_score",
            value=result.overall_weighted_score,
            comment=result.reasoning_summary,
        )
        for ev in result.evaluations:
            langfuse_client.create_score(
                trace_id=trace_id,
                name=ev.dimension.replace(" ", "_").lower(),
                value=ev.score,
                comment=ev.evidence,
            )
    except Exception:
        pass

    return result

async def _run_cli(args: argparse.Namespace) -> int:
    try:
        result = await evaluate_trace(
            args.trace_id,
            args.input,
            args.output,
        )
    except Exception as error:
        print(f"Judge failed: {type(error).__name__}: {error}")
        return 1
    print(f"Scored trace {args.trace_id}: {result.overall_weighted_score}/5.0 — {result.reasoning_summary}")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_id", help="Langfuse trace to receive the score")
    parser.add_argument("--input", required=True, help="Actual user input")
    parser.add_argument("--output", required=True, help="Actual Agent response")
    args = parser.parse_args()

    import asyncio
    return asyncio.run(_run_cli(args))

if __name__ == "__main__":
    raise SystemExit(main())
