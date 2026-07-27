"""Score a real Agent input/output pair and attach the score to a Langfuse trace."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langfuse import Langfuse

from agents.llm_factory import LLMConfig, create_chat_model
from agents.utils import extract_text

JUDGE_PROMPT = """
You are an expert conversational AI evaluator.
Score the assistant's response on a scale of 1 to 5 for Conversational Tone.
Tone definition:
5 = Highly helpful, friendly, natural, and encouraging.
1 = Robotic, unhelpful, or rude.

Provide your output strictly in this format:
SCORE: [1-5]
REASON: [Brief explanation]
"""


@dataclass(frozen=True)
class JudgeResult:
    score: int
    reason: str


def parse_judge_response(response_text: str) -> JudgeResult:
    """Parse and validate the stable judge response contract."""

    match = re.fullmatch(
        r"\s*SCORE:[ \t]*([1-5])[ \t]*\r?\n"
        r"REASON:[ \t]*(\S(?:[^\r\n]*\S)?)[ \t]*\s*",
        response_text,
    )
    if match is None:
        raise ValueError(
            "judge response must contain exactly SCORE: <1-5> and REASON: <text>"
        )
    score = int(match.group(1))
    reason = match.group(2)
    return JudgeResult(score=score, reason=reason)


async def evaluate_trace(
    trace_id: str,
    input_msg: str,
    output_msg: str,
    *,
    judge_llm: Any | None = None,
    langfuse_client: Any | None = None,
) -> JudgeResult:
    """Evaluate supplied content and write conversational_tone to a trace."""

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
    langfuse_client.create_score(
        trace_id=trace_id,
        name="conversational_tone",
        value=result.score,
        comment=result.reason,
    )
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
    print(f"Scored trace {args.trace_id}: {result.score}/5 — {result.reason}")
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
