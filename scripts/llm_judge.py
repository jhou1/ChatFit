import asyncio
from langfuse import Langfuse
from agents.llm_factory import LLMConfig, create_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

langfuse = Langfuse()

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


async def evaluate_trace(trace_id: str, input_msg: str, output_msg: str):
    llm_config = LLMConfig(
        provider="google", model_name="gemini-3.5-flash", temperature=0.0
    )
    judge_llm = create_chat_model(llm_config)

    evaluation_content = f"User: {input_msg}\nAssistant: {output_msg}"

    response = await judge_llm.ainvoke(
        [SystemMessage(content=JUDGE_PROMPT), HumanMessage(content=evaluation_content)]
    )

    score_text = response.content
    try:
        score_line = [
            line for line in score_text.splitlines() if line.startswith("SCORE:")
        ][0]
        score = int(score_line.split(":")[1].strip())
        reason = score_text.replace(score_line, "").strip()

        langfuse.score(  # type: ignore
            trace_id=trace_id, name="conversational_tone", value=score, comment=reason
        )
        print(f"Scored trace {trace_id}: {score}/5")
    except Exception as e:
        print(f"Failed to parse score for {trace_id}: {e}\nRaw output: {score_text}")


if __name__ == "__main__":
    # Example usage: python scripts/llm_judge.py <trace_id>
    import sys

    if len(sys.argv) > 1:
        trace_id = sys.argv[1]
        asyncio.run(evaluate_trace(trace_id, "User Input", "Assistant Output"))
    else:
        print("Usage: python scripts/llm_judge.py <trace_id>")
