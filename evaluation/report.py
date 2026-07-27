"""Experiment scorecards and release-gate evaluation."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ExperimentMetadata(BaseModel):
    run_id: str
    commit_sha: str
    dataset: str
    dataset_version: str
    model: str
    prompt_version: str
    grader_version: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class CaseResult(BaseModel):
    case_id: str
    passed: bool
    tags: list[str] = Field(default_factory=list)
    llm_score: float | None = Field(default=None, ge=1, le=5)
    clarity_score: float | None = Field(default=None, ge=1, le=5)
    tone_score: float | None = Field(default=None, ge=1, le=5)
    latency_ms: float | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    failure_codes: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    tool_eval_total: int = Field(default=0)
    tool_eval_passed: int = Field(default=0)


class ReleaseThresholds(BaseModel):
    minimum_completion_rate: float = Field(default=0.90, ge=0, le=1) # 任务完成率
    minimum_tool_accuracy: float = Field(default=0.95, ge=0, le=1) # 工具选择与参数正确率
    minimum_context_consistency: float = Field(default=0.85, ge=0, le=1) # 上下文一致性
    minimum_recovery_rate: float = Field(default=0.80, ge=0, le=1) # 异常恢复率
    minimum_high_risk_completion_rate: float = Field(default=1.0, ge=0, le=1)
    
    minimum_llm_coverage: float = Field(default=1.0, ge=0, le=1)
    minimum_average_llm_score: float = Field(default=4.0, ge=1, le=5)
    minimum_p10_llm_score: float = Field(default=3.0, ge=1, le=5)
    require_llm_scores: bool = True


class ReleaseGate(BaseModel):
    passed: bool
    failures: list[str]


class ExperimentReport(BaseModel):
    metadata: ExperimentMetadata
    cases: list[CaseResult]

    def metrics(self) -> dict[str, Any]:
        total = len(self.cases)
        passed = sum(case.passed for case in self.cases)
        high_risk = [case for case in self.cases if "high_risk" in case.tags]
        
        # 1. 任务完成率 (Task Completion Rate - TCR)
        tcr = passed / total if total else 0.0
        
        # 2. 工具选择与参数正确率 (Tool Accuracy - TA)
        # We classify cases tagged with "Tool Calling" or cases that evaluated tools.
        # Alternatively, we can check if they failed due to missing_tool, tool_params, avoid_tool.
        tool_cases = [c for c in self.cases if "Tool Calling" in c.tags or "Multi-Agent Collaboration" in c.tags]
        tool_passed = sum(1 for c in tool_cases if not any(fc in ["missing_tool", "tool_params", "tool_args", "avoid_tool"] for fc in c.failure_codes))
        ta = tool_passed / len(tool_cases) if tool_cases else 1.0

        # 3. 上下文一致性 (Context Consistency Rate - CCR)
        # Cases tagged with "Memory & Cross-turn" or "Multi-turn Dialogue"
        context_cases = [c for c in self.cases if "Memory & Cross-turn" in c.tags or "Multi-turn Dialogue" in c.tags]
        ccr = sum(c.passed for c in context_cases) / len(context_cases) if context_cases else 1.0

        # 4. 异常恢复率 (Error Recovery Rate - ERR)
        # Cases where the agent needs to ask for clarification, handle edge cases, or handle vague routing
        recovery_cases = [c for c in self.cases if "Intent Routing" in c.tags or any(fc == "clarification_failed" for fc in c.failure_codes)]
        # Add Edge Cases if we had explicitly tagged them, but Intent Routing handles ambiguities in our dataset
        err = sum(c.passed for c in recovery_cases) / len(recovery_cases) if recovery_cases else 1.0
        
        llm_scores = [c.llm_score for c in self.cases if c.llm_score is not None]
        clarity_scores = [c.clarity_score for c in self.cases if c.clarity_score is not None]
        tone_scores = [c.tone_score for c in self.cases if c.tone_score is not None]

        return {
            "total_cases": total,
            "passed_cases": passed,
            "completion_rate": tcr,
            "high_risk_case_count": len(high_risk),
            "high_risk_completion_rate": (
                sum(case.passed for case in high_risk) / len(high_risk)
                if high_risk
                else 1.0
            ),
            "tool_accuracy": ta,
            "context_consistency": ccr,
            "recovery_rate": err,
            "average_llm_score": (
                sum(llm_scores) / len(llm_scores) if llm_scores else None
            ),
            "average_clarity_score": (
                sum(clarity_scores) / len(clarity_scores) if clarity_scores else None
            ),
            "average_tone_score": (
                sum(tone_scores) / len(tone_scores) if tone_scores else None
            ),
            "llm_coverage": len(llm_scores) / total if total else 0.0,
            "p10_llm_score": _percentile(sorted(llm_scores), 0.10),
        }

    def release_gate(self, thresholds: ReleaseThresholds | None = None) -> ReleaseGate:
        configured = thresholds or ReleaseThresholds()
        metrics = self.metrics()
        failures: list[str] = []
        
        if metrics["completion_rate"] < configured.minimum_completion_rate:
            failures.append(
                "Task Completion Rate (TCR) "
                f"{metrics['completion_rate']:.3f} < "
                f"{configured.minimum_completion_rate:.3f}"
            )
        if metrics["tool_accuracy"] < configured.minimum_tool_accuracy:
            failures.append(
                "Tool Accuracy (TA) "
                f"{metrics['tool_accuracy']:.3f} < "
                f"{configured.minimum_tool_accuracy:.3f}"
            )
        if metrics["context_consistency"] < configured.minimum_context_consistency:
            failures.append(
                "Context Consistency Rate (CCR) "
                f"{metrics['context_consistency']:.3f} < "
                f"{configured.minimum_context_consistency:.3f}"
            )
        if metrics["recovery_rate"] < configured.minimum_recovery_rate:
            failures.append(
                "Error Recovery Rate (ERR) "
                f"{metrics['recovery_rate']:.3f} < "
                f"{configured.minimum_recovery_rate:.3f}"
            )

        if (
            configured.minimum_high_risk_completion_rate > 0
            and metrics["high_risk_case_count"] == 0
        ):
            failures.append("high_risk_cases missing")
            
        if (
            metrics["high_risk_completion_rate"]
            < configured.minimum_high_risk_completion_rate
        ):
            failures.append(
                "high_risk_completion_rate "
                f"{metrics['high_risk_completion_rate']:.3f} < "
                f"{configured.minimum_high_risk_completion_rate:.3f}"
            )

        if configured.require_llm_scores:
            if metrics["llm_coverage"] < configured.minimum_llm_coverage:
                failures.append(
                    "llm_coverage "
                    f"{metrics['llm_coverage']:.3f} < "
                    f"{configured.minimum_llm_coverage:.3f}"
                )
            average_llm = metrics["average_llm_score"]
            if average_llm is None:
                failures.append("average_llm_score not_scored")
            elif average_llm < configured.minimum_average_llm_score:
                failures.append(
                    f"average_llm_score {average_llm:.3f} < "
                    f"{configured.minimum_average_llm_score:.3f}"
                )
            p10_llm = metrics["p10_llm_score"]
            if p10_llm is not None and p10_llm < configured.minimum_p10_llm_score:
                failures.append(
                    f"p10_llm_score {p10_llm:.3f} < " f"{configured.minimum_p10_llm_score:.3f}"
                )
        return ReleaseGate(passed=not failures, failures=failures)

    def to_markdown(self, thresholds: ReleaseThresholds | None = None) -> str:
        metrics = self.metrics()
        gate = self.release_gate(thresholds)
        
        lines = [
            f"# Evaluation Report: {self.metadata.run_id}",
            "",
            f"- **Commit**: `{self.metadata.commit_sha}`",
            f"- **Dataset**: `{self.metadata.dataset}@{self.metadata.dataset_version}`",
            f"- **Model**: `{self.metadata.model}`",
            f"- **Release Gate**: **{'✅ PASS' if gate.passed else '❌ FAIL'}**",
            "",
            "## 1. 定量核心能力指标 (Quantitative Agent Metrics)",
            "",
            "| 指标名称 (Metric) | 得分 (Score) | 阈值 (Threshold) | 评估公式与意义 (Rationale & Formula) |",
            "| --- | --- | --- | --- |",
            f"| **任务完成率 (TCR)** | {metrics['completion_rate']:.1%} | 90.0% | **意义**: 衡量Agent成功结束会话闭环的能力。<br>**公式**: `Passed Cases / Total Cases` |",
            f"| **工具与参数准确率 (TA)** | {metrics['tool_accuracy']:.1%} | 95.0% | **意义**: 衡量调用动作、参数提取及防呆机制的精确度，防止污染数据库。<br>**公式**: `1 - (Tool Failures / Tool Dependent Cases)` |",
            f"| **上下文一致性 (CCR)** | {metrics['context_consistency']:.1%} | 85.0% | **意义**: 衡量长程对话中的记忆穿透和多轮状态承接能力，低于85%会产生“智障感”。<br>**公式**: `Passed Memory & Multi-turn Cases / Total Such Cases` |",
            f"| **异常恢复率 (ERR)** | {metrics['recovery_rate']:.1%} | 80.0% | **意义**: 衡量模糊意图下的反问澄清能力以及功能越界时的拦截能力。<br>**公式**: `Passed Edge & Clarification Cases / Total Such Cases` |",
            "",
            "## 2. LLM-as-a-Judge 软性体验指标 (Qualitative Rubric Metrics)",
            "",
            (
                f"- **总体 Rubric 综合得分**: **{metrics['average_llm_score']:.2f} / 5.0**"
                if metrics["average_llm_score"] is not None
                else "- Average Weighted LLM Score: not scored"
            ),
            (
                f"  - **语义准确与清晰度 (Clarity)**: {metrics['average_clarity_score']:.2f} / 5.0"
                if metrics["average_clarity_score"] is not None
                else "  - Average Clarity Score: not scored"
            ),
            (
                f"  - **对话口吻 (Tone)**: {metrics['average_tone_score']:.2f} / 5.0"
                if metrics["average_tone_score"] is not None
                else "  - Average Tone Score: not scored"
            ),
            f"- **LLM 裁判覆盖率**: {metrics['llm_coverage']:.1%}",
            "",
            "## 3. 失败用例追踪 (Failed Cases Trace)",
            "",
        ]
        
        failed_cases = [case for case in self.cases if not case.passed]
        if failed_cases:
            # Group by failure codes
            code_map = {}
            for case in failed_cases:
                for code in case.failure_codes:
                    if code not in code_map:
                        code_map[code] = []
                    if case.case_id not in code_map[code]:
                        code_map[code].append(case.case_id)
            
            for code, case_ids in code_map.items():
                lines.append(f"- **{code}**: {', '.join(case_ids)}")
        else:
            lines.append("- ✨ **All cases passed perfectly!** ✨")
            
        if gate.failures:
            lines.extend(["", "## 🚨 Release Gate Failures", ""])
            lines.extend(f"- {failure}" for failure in gate.failures)
            
        return "\n".join(lines) + "\n"


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.ceil(len(values) * quantile) - 1))
    return values[index]
