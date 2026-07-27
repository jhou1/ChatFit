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


class DimensionStat(BaseModel):
    dimension: str
    score: float
    weight: float


class CaseResult(BaseModel):
    case_id: str
    passed: bool
    tags: list[str] = Field(default_factory=list)
    overall_llm_score: float | None = Field(default=None, ge=1, le=5)
    dimension_stats: list[DimensionStat] = Field(default_factory=list)
    latency_ms: float | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    failure_codes: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    tool_eval_total: int = Field(default=0)
    tool_eval_passed: int = Field(default=0)


class ReleaseThresholds(BaseModel):
    minimum_completion_rate: float = Field(default=0.90, ge=0, le=1) 
    minimum_tool_accuracy: float = Field(default=0.95, ge=0, le=1) 
    minimum_context_consistency: float = Field(default=0.85, ge=0, le=1) 
    minimum_recovery_rate: float = Field(default=0.80, ge=0, le=1) 
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
        
        # 1. 任务完成率
        completion_rate = passed / total if total else 0.0
        
        # 2. 工具选择与参数正确率
        tool_cases = [c for c in self.cases if "Tool Calling" in c.tags or "Multi-Agent Collaboration" in c.tags]
        tool_passed = sum(1 for c in tool_cases if not any(fc in ["missing_tool", "tool_params", "tool_args", "avoid_tool"] for fc in c.failure_codes))
        tool_accuracy = tool_passed / len(tool_cases) if tool_cases else 1.0

        # 3. 上下文一致性
        context_cases = [c for c in self.cases if "Memory & Cross-turn" in c.tags or "Multi-turn Dialogue" in c.tags]
        context_consistency = sum(c.passed for c in context_cases) / len(context_cases) if context_cases else 1.0

        # 4. 异常恢复率
        recovery_cases = [c for c in self.cases if "Intent Routing" in c.tags or any(fc == "clarification_failed" for fc in c.failure_codes)]
        recovery_rate = sum(c.passed for c in recovery_cases) / len(recovery_cases) if recovery_cases else 1.0
        
        # LLM Scores
        overall_llm_scores = [c.overall_llm_score for c in self.cases if c.overall_llm_score is not None]
        
        # Rubric Dimensions aggregation
        dimension_summary = {}
        for case in self.cases:
            for stat in case.dimension_stats:
                dim = stat.dimension
                if dim not in dimension_summary:
                    dimension_summary[dim] = {"count": 0, "total_score": 0.0, "total_weight": 0.0}
                dimension_summary[dim]["count"] += 1
                dimension_summary[dim]["total_score"] += stat.score
                dimension_summary[dim]["total_weight"] += stat.weight
                
        for dim, data in dimension_summary.items():
            data["avg_score"] = data["total_score"] / data["count"]
            data["avg_weight"] = data["total_weight"] / data["count"]

        latencies = sorted(
            case.latency_ms for case in self.cases if case.latency_ms is not None
        )
        
        return {
            "total_cases": total,
            "passed_cases": passed,
            "completion_rate": completion_rate,
            "high_risk_case_count": len(high_risk),
            "high_risk_completion_rate": (
                sum(case.passed for case in high_risk) / len(high_risk)
                if high_risk
                else 1.0
            ),
            "tool_accuracy": tool_accuracy,
            "context_consistency": context_consistency,
            "recovery_rate": recovery_rate,
            "average_llm_score": (
                sum(overall_llm_scores) / len(overall_llm_scores) if overall_llm_scores else None
            ),
            "dimension_summary": dimension_summary,
            "llm_coverage": len(overall_llm_scores) / total if total else 0.0,
            "p10_llm_score": _percentile(sorted(overall_llm_scores), 0.10),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "total_cost": sum(
                case.cost for case in self.cases if case.cost is not None
            ),
        }

    def release_gate(self, thresholds: ReleaseThresholds | None = None) -> ReleaseGate:
        configured = thresholds or ReleaseThresholds()
        metrics = self.metrics()
        failures: list[str] = []
        
        if metrics["completion_rate"] < configured.minimum_completion_rate:
            failures.append(
                "任务完成率 "
                f"{metrics['completion_rate']:.3f} < "
                f"{configured.minimum_completion_rate:.3f}"
            )
        if metrics["tool_accuracy"] < configured.minimum_tool_accuracy:
            failures.append(
                "工具与参数准确率 "
                f"{metrics['tool_accuracy']:.3f} < "
                f"{configured.minimum_tool_accuracy:.3f}"
            )
        if metrics["context_consistency"] < configured.minimum_context_consistency:
            failures.append(
                "上下文一致性 "
                f"{metrics['context_consistency']:.3f} < "
                f"{configured.minimum_context_consistency:.3f}"
            )
        if metrics["recovery_rate"] < configured.minimum_recovery_rate:
            failures.append(
                "异常恢复率 "
                f"{metrics['recovery_rate']:.3f} < "
                f"{configured.minimum_recovery_rate:.3f}"
            )

        if (
            configured.minimum_high_risk_completion_rate > 0
            and metrics["high_risk_case_count"] == 0
        ):
            failures.append("高风险用例缺失")
            
        if (
            metrics["high_risk_completion_rate"]
            < configured.minimum_high_risk_completion_rate
        ):
            failures.append(
                "高风险用例完成率 "
                f"{metrics['high_risk_completion_rate']:.3f} < "
                f"{configured.minimum_high_risk_completion_rate:.3f}"
            )

        if configured.require_llm_scores:
            if metrics["llm_coverage"] < configured.minimum_llm_coverage:
                failures.append(
                    "大模型打分覆盖率 "
                    f"{metrics['llm_coverage']:.3f} < "
                    f"{configured.minimum_llm_coverage:.3f}"
                )
            average_llm = metrics["average_llm_score"]
            if average_llm is None:
                failures.append("总体综合得分缺失")
            elif average_llm < configured.minimum_average_llm_score:
                failures.append(
                    f"总体综合得分 {average_llm:.3f} < "
                    f"{configured.minimum_average_llm_score:.3f}"
                )
            p10_llm = metrics["p10_llm_score"]
            if p10_llm is not None and p10_llm < configured.minimum_p10_llm_score:
                failures.append(
                    f"打分 P10 分位数 {p10_llm:.3f} < " f"{configured.minimum_p10_llm_score:.3f}"
                )
        return ReleaseGate(passed=not failures, failures=failures)

    def to_markdown(self, thresholds: ReleaseThresholds | None = None) -> str:
        configured = thresholds or ReleaseThresholds()
        metrics = self.metrics()
        gate = self.release_gate(configured)
        
        lines = [
            f"# Evaluation Report: {self.metadata.run_id}",
            "",
            f"- **Commit**: `{self.metadata.commit_sha}`",
            f"- **Dataset**: `{self.metadata.dataset}@{self.metadata.dataset_version}`",
            f"- **Model**: `{self.metadata.model}`",
            f"- **发版状态门禁**: **{'✅ 通过' if gate.passed else '❌ 拦截'}**",
            "",
            "## 1. 定量核心能力指标",
            "",
            "| 评价维度 | 得分 | 阈值 | 评估公式与意义 |",
            "| --- | --- | --- | --- |",
            f"| **任务完成率** | {metrics['completion_rate']:.1%} | {configured.minimum_completion_rate:.1%} | **意义**: 衡量智能体成功结束会话闭环的能力。<br>**公式**: 无失败的用例数 / 总用例数 |",
            f"| **工具选择与参数正确率** | {metrics['tool_accuracy']:.1%} | {configured.minimum_tool_accuracy:.1%} | **意义**: 衡量调用动作、参数提取及防呆机制的精确度，防止污染数据库。<br>**公式**: 1 - (相关报错数 / 依赖工具的用例总数) |",
            f"| **上下文一致性** | {metrics['context_consistency']:.1%} | {configured.minimum_context_consistency:.1%} | **意义**: 衡量长程对话中的记忆穿透和多轮状态承接能力。<br>**公式**: 记忆与多轮场景通过数 / 对应场景用例总数 |",
            f"| **异常恢复率** | {metrics['recovery_rate']:.1%} | {configured.minimum_recovery_rate:.1%} | **意义**: 衡量模糊意图下的反问澄清能力以及功能越界时的拦截能力。<br>**公式**: 异常澄清场景通过数 / 对应场景用例总数 |",
            "",
            "## 2. 大模型作为裁判 (LLM-as-a-Judge) 软性体验指标",
            "",
        ]
        
        # Dimensions table
        lines.append("| 评价维度 | 评测用例数 | 平均权重 | 得分 |")
        lines.append("| --- | --- | --- | --- |")
        
        dim_summary = metrics.get("dimension_summary", {})
        if not dim_summary:
            lines.append("| 无数据 | 0 | 0.0 | 0.0 / 5.0 |")
        else:
            # Sort alphabetically or by count
            for dim_name in sorted(dim_summary.keys()):
                d = dim_summary[dim_name]
                lines.append(f"| {dim_name} | {d['count']} | {d['avg_weight']:.2f} | **{d['avg_score']:.2f}** / 5.0 |")
                
        overall_score = metrics['average_llm_score']
        overall_str = f"**{overall_score:.2f}**" if overall_score is not None else "未打分"
        
        lines.extend([
            "",
            f"- **总体 Rubric 综合加权得分**: {overall_str} / 5.0",
            f"- **大模型裁判打分覆盖率**: {metrics['llm_coverage']:.1%}",
            "",
            "## 3. 失败用例追踪",
            "",
        ])
        
        failed_cases = [case for case in self.cases if not case.passed]
        if failed_cases:
            code_map = {}
            for case in failed_cases:
                for code in case.failure_codes:
                    if code not in code_map:
                        code_map[code] = []
                    if case.case_id not in code_map[code]:
                        code_map[code].append(case.case_id)
            
            for code, case_ids in code_map.items():
                lines.append(f"- **报错代号 {code}**: 用例编号涵盖 {', '.join(case_ids)}")
        else:
            lines.append("- ✨ **所有评测用例完美通过！没有任何报错！** ✨")
            
        if gate.failures:
            lines.extend(["", "## 🚨 发版拦截明细", ""])
            lines.extend(f"- {failure}" for failure in gate.failures)
            
        return "\n".join(lines) + "\n"


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.ceil(len(values) * quantile) - 1))
    return values[index]
