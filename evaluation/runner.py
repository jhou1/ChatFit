import argparse
import asyncio
import logging
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver

from agents.roles.supervisor import make_agent_graph
from agents.llm_factory import LLMConfig
from agents.rag import get_or_create_vector_store
from agents.sqlite_handler import init_db, add_training_session
from agents.models import TrainingInputRecorder, TrainingSession, TrainingSet
from agents.utils import extract_text

from evaluation.graders import Trajectory, grade_turn
from evaluation.models import load_evaluation_cases
from evaluation.report import (
    CaseResult,
    ExperimentMetadata,
    ExperimentReport,
    ReleaseThresholds,
)
from scripts.llm_judge import evaluate_trace

# Suppress asyncio's "Task was destroyed but it is pending" stderr prints
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

class MockLangfuse:
    def create_score(self, **kwargs):
        pass

async def evaluate_case(case, llm_config, vector_store, sem, enable_llm_judge):
    async with sem:
        print(f"--- Starting Case: {case.case_id} ---")
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test_eval.db")
            init_db(db_path)
            
            checkpointer = MemorySaver()
            app = make_agent_graph(llm_config, db_path, vector_store, checkpointer=checkpointer)
            
            config = {"configurable": {"thread_id": case.case_id}}
            case_passed = True
            failure_codes = []
            
            case_weighted_scores = []
            case_clarity_scores = []
            case_tone_scores = []

            for turn_idx, turn in enumerate(case.turns):
                user_input = turn.user_input
                
                expected_db_state = [
                    t for t in turn.expected_trajectory_eval
                    if t.eval_type == "db_state"
                ]

                tool_calls_made = []
                routed_assistants = []
                turn_response_text = ""

                async for event in app.astream(
                    {"messages": [HumanMessage(content=user_input)]},
                    config=config,
                    stream_mode="updates",
                ):
                    for node_name, node_output in event.items():
                        if node_name == "assistant_selector":
                            if isinstance(node_output, dict) and "assistant_names" in node_output:
                                routed_assistants.extend(node_output["assistant_names"])
                        if node_name == "__interrupt__":
                            for interrupt in node_output:
                                if hasattr(interrupt, "value") and isinstance(interrupt.value, dict):
                                    for tc in interrupt.value.get("tool_calls", []):
                                        tool_calls_made.append(tc)
                            continue

                        if isinstance(node_output, dict):
                            messages = node_output.get("messages", [])
                        else:
                            continue
                        for msg in messages:
                            if hasattr(msg, "tool_calls"):
                                for tc in msg.tool_calls:
                                    tool_calls_made.append(tc)
                            if msg.type == "ai" and extract_text(msg).strip():
                                turn_response_text += extract_text(msg) + "\n"

                # If the graph was interrupted (awaiting approval), approve it so it finishes DB writes
                state = await app.aget_state(config)
                iterations = 0
                max_iterations = 5
                while state.next and iterations < max_iterations:
                    resume_data = {}
                    for task in state.tasks:
                        for intr in task.interrupts:
                            resume_data[intr.id] = {"approved": True}

                    if not resume_data:
                        break

                    async for event in app.astream(
                        Command(resume=resume_data), config=config, stream_mode="updates"
                    ):
                        for node_name, node_output in event.items():
                            if node_name == "assistant_selector":
                                if isinstance(node_output, dict) and "assistant_names" in node_output:
                                    routed_assistants.extend(node_output["assistant_names"])
                            if node_name == "__interrupt__":
                                for interrupt in node_output:
                                    if hasattr(interrupt, "value") and isinstance(interrupt.value, dict):
                                        for tc in interrupt.value.get("tool_calls", []):
                                            tool_calls_made.append(tc)
                                continue
                            if isinstance(node_output, dict):
                                messages = node_output.get("messages", [])
                            else:
                                continue
                            for msg in messages:
                                if hasattr(msg, "tool_calls"):
                                    for tc in msg.tool_calls:
                                        tool_calls_made.append(tc)
                                if msg.type == "ai" and extract_text(msg).strip():
                                    turn_response_text += extract_text(msg) + "\n"
                    state = await app.aget_state(config)
                    iterations += 1

                grade = grade_turn(
                    turn,
                    Trajectory(
                        tool_calls=tool_calls_made,
                        routes=routed_assistants,
                        response=turn_response_text,
                    ),
                )
                
                if not grade.passed:
                    case_passed = False
                    for failure in grade.failures:
                        failure_codes.append(failure.code)
                        print(f"  [{case.case_id}] [Fail] Turn {turn_idx}: {failure.code} - {failure.message}")

                if expected_db_state:
                    with sqlite3.connect(db_path) as conn:
                        cursor = conn.cursor()
                        for state_check in expected_db_state:
                            cursor.execute(state_check.query)
                            row = cursor.fetchone()
                            if row is None:
                                case_passed = False
                                code = "db_missing"
                                failure_codes.append(code)
                                print(f"  [{case.case_id}] [Fail] Turn {turn_idx}: {code} - DB query {state_check.query} returned no results")
                            else:
                                result = row[0]
                                expected_val = state_check.expected_value
                                if result != expected_val:
                                    case_passed = False
                                    code = "db_mismatch"
                                    failure_codes.append(code)
                                    print(f"  [{case.case_id}] [Fail] Turn {turn_idx}: {code} - DB query {state_check.query} returned {result}, expected {expected_val}")
                
                if enable_llm_judge and turn_response_text.strip() and turn.expected_response_eval and turn.expected_response_eval.rubrics:
                    rubrics_dict = [
                        {
                            "dimension_name": r.dimension_name,
                            "criteria_description": r.criteria_description,
                            "evidence_requirement": r.evidence_requirement,
                            "weight": r.weight
                        }
                        for r in turn.expected_response_eval.rubrics
                    ]
                    try:
                        judge_result = await evaluate_trace(
                            f"{case.case_id}-{turn_idx}",
                            user_input,
                            turn_response_text,
                            rubrics_dict,
                            langfuse_client=MockLangfuse()
                        )
                        case_weighted_scores.append(judge_result.overall_weighted_score)
                        for ev in judge_result.evaluations:
                            if "一致性" in ev.dimension or "澄清" in ev.dimension or "合理性" in ev.dimension or "完成率" in ev.dimension:
                                case_clarity_scores.append(ev.score)
                            elif "交互质量" in ev.dimension or "安全边界" in ev.dimension:
                                case_tone_scores.append(ev.score)
                    except Exception as e:
                        print(f"  [{case.case_id}] [Warn] LLM Judge failed: {e}")

            if case_passed:
                print(f"  [{case.case_id}] [Pass]")
            
            avg_llm = sum(case_weighted_scores) / len(case_weighted_scores) if case_weighted_scores else None
            
            tags = case.capability_tags if case.capability_tags else []
            return CaseResult(
                case_id=case.case_id,
                passed=case_passed,
                tags=tags,
                llm_score=avg_llm,
                failure_codes=failure_codes
            )

async def main():
    parser = argparse.ArgumentParser(description="Run ChatFit Golden Eval Set")
    parser.add_argument("--dataset", default="evaluation/chatfit_golden_test_set.jsonl", help="Path to jsonl dataset")
    parser.add_argument("--model", default="gemini-3.5-flash", help="LLM model name")
    parser.add_argument("--provider", default="google", help="LLM provider")
    parser.add_argument("--concurrency", type=int, default=5, help="Number of concurrent cases to run")
    parser.add_argument("--no-judge", action="store_true", help="Disable LLM-as-a-judge for tone scoring")
    args = parser.parse_args()

    try:
        cases = load_evaluation_cases(args.dataset)
    except Exception as e:
        print(f"Error loading dataset: {e}")
        sys.exit(1)

    enable_llm_judge = not args.no_judge

    print(f"Loaded {len(cases)} cases from {args.dataset}")
    print(f"Running with concurrency: {args.concurrency}")
    print(f"LLM Judge Enabled: {enable_llm_judge}")

    llm_config = LLMConfig(provider=args.provider, model_name=args.model, temperature=0.0)
    vector_store = get_or_create_vector_store("./chroma_test_db")
    
    sem = asyncio.Semaphore(args.concurrency)
    
    tasks = [
        evaluate_case(case, llm_config, vector_store, sem, enable_llm_judge)
        for case in cases
    ]
    
    case_results = await asyncio.gather(*tasks)

    metadata = ExperimentMetadata(
        run_id=datetime.now().strftime("%Y%m%d-%H%M%S"),
        commit_sha="local",
        dataset=args.dataset,
        dataset_version="1",
        model=args.model,
        prompt_version="1",
        grader_version="1",
    )
    report = ExperimentReport(metadata=metadata, cases=case_results)
    
    thresholds = ReleaseThresholds(require_llm_scores=enable_llm_judge, minimum_high_risk_completion_rate=0.0)
    markdown_report = report.to_markdown(thresholds)
    
    report_file = Path("evaluation/latest_report.md")
    with open(report_file, "w") as f:
        f.write(markdown_report)
        
    print("\n\n" + "="*50)
    print("EVALUATION REPORT")
    print("="*50)
    print(markdown_report)
    print(f"\nReport saved to {report_file}")

    gate = report.release_gate(thresholds)
    if not gate.passed:
        print("\nRelease Gate: FAILED")
        sys.exit(1)
    else:
        print("\nRelease Gate: PASSED")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
