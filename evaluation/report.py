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
    tone_score: float | None = Field(default=None, ge=1, le=5)
    latency_ms: float | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    failure_codes: list[str] = Field(default_factory=list)
    trace_id: str | None = None


class ReleaseThresholds(BaseModel):
    minimum_completion_rate: float = Field(default=0.95, ge=0, le=1)
    minimum_high_risk_completion_rate: float = Field(default=1.0, ge=0, le=1)
    minimum_tone_coverage: float = Field(default=1.0, ge=0, le=1)
    minimum_average_tone: float = Field(default=4.0, ge=1, le=5)
    minimum_p10_tone: float = Field(default=3.0, ge=1, le=5)
    require_tone_scores: bool = True


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
        tone_scores = [
            case.tone_score for case in self.cases if case.tone_score is not None
        ]
        latencies = sorted(
            case.latency_ms for case in self.cases if case.latency_ms is not None
        )
        return {
            "total_cases": total,
            "passed_cases": passed,
            "completion_rate": passed / total if total else 0.0,
            "high_risk_case_count": len(high_risk),
            "high_risk_completion_rate": (
                sum(case.passed for case in high_risk) / len(high_risk)
                if high_risk
                else 1.0
            ),
            "average_tone": (
                sum(tone_scores) / len(tone_scores) if tone_scores else None
            ),
            "tone_coverage": len(tone_scores) / total if total else 0.0,
            "p10_tone": _percentile(sorted(tone_scores), 0.10),
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
                "completion_rate "
                f"{metrics['completion_rate']:.3f} < "
                f"{configured.minimum_completion_rate:.3f}"
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
        if configured.require_tone_scores:
            if metrics["tone_coverage"] < configured.minimum_tone_coverage:
                failures.append(
                    "tone_coverage "
                    f"{metrics['tone_coverage']:.3f} < "
                    f"{configured.minimum_tone_coverage:.3f}"
                )
            average_tone = metrics["average_tone"]
            if average_tone is None:
                failures.append("average_tone not_scored")
            elif average_tone < configured.minimum_average_tone:
                failures.append(
                    f"average_tone {average_tone:.3f} < "
                    f"{configured.minimum_average_tone:.3f}"
                )
            p10_tone = metrics["p10_tone"]
            if p10_tone is not None and p10_tone < configured.minimum_p10_tone:
                failures.append(
                    f"p10_tone {p10_tone:.3f} < " f"{configured.minimum_p10_tone:.3f}"
                )
        return ReleaseGate(passed=not failures, failures=failures)

    def to_markdown(self, thresholds: ReleaseThresholds | None = None) -> str:
        metrics = self.metrics()
        gate = self.release_gate(thresholds)
        lines = [
            f"# Evaluation Report: {self.metadata.run_id}",
            "",
            f"- Commit: `{self.metadata.commit_sha}`",
            f"- Dataset: `{self.metadata.dataset}@{self.metadata.dataset_version}`",
            f"- Model: `{self.metadata.model}`",
            f"- Release gate: **{'PASS' if gate.passed else 'FAIL'}**",
            f"- Completion rate: {metrics['completion_rate']:.1%}",
            (
                "- High-risk completion rate: "
                f"{metrics['high_risk_completion_rate']:.1%}"
            ),
            (
                "- Average tone: "
                + (
                    f"{metrics['average_tone']:.2f}"
                    if metrics["average_tone"] is not None
                    else "not scored"
                )
            ),
            f"- Tone coverage: {metrics['tone_coverage']:.1%}",
            (
                "- P10 tone: "
                + (
                    f"{metrics['p10_tone']:.2f}"
                    if metrics["p10_tone"] is not None
                    else "not scored"
                )
            ),
            "",
            "## Failed cases",
            "",
        ]
        failed_cases = [case for case in self.cases if not case.passed]
        if failed_cases:
            lines.extend(
                f"- `{case.case_id}`: {', '.join(case.failure_codes) or 'failed'}"
                for case in failed_cases
            )
        else:
            lines.append("- None")
        if gate.failures:
            lines.extend(["", "## Gate failures", ""])
            lines.extend(f"- {failure}" for failure in gate.failures)
        return "\n".join(lines) + "\n"


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.ceil(len(values) * quantile) - 1))
    return values[index]
