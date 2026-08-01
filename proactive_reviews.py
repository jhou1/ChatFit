from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from agents.sqlite_handler import (
    get_meal_records_for_date,
    get_training_records_for_date,
)

WeeklySummaryGenerator = Callable[[date, date], Awaitable[str]]


@dataclass(frozen=True)
class ProactiveReviewResult:
    should_send: bool
    message: str | None

    def __post_init__(self) -> None:
        if self.should_send and not (self.message and self.message.strip()):
            raise ValueError("a send result requires a non-blank message")
        if not self.should_send and self.message is not None:
            raise ValueError("a no-send result requires message=None")


MEAL_LABELS = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "snack": "加餐",
    "extra": "额外",
}
WEEKLY_FAILURE = (
    "## 本周总结\n\n" "本周总结暂时生成失败。你可以稍后发消息让我重新总结。"
)


def weekly_bounds(as_of: date) -> tuple[date, date]:
    days_since_sunday = (as_of.weekday() + 1) % 7
    return as_of - timedelta(days=days_since_sunday), as_of


def today_in_shanghai() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _format_meal_recap(meal: dict[str, Any]) -> str:
    meal_type = meal.get("meal_type")
    label = MEAL_LABELS.get(meal_type) if isinstance(meal_type, str) else None
    detail = meal.get("items") or meal.get("note") or "已记录"
    if label is None:
        return str(detail)
    return f"{label}：{detail}"


def _format_training_recap(session: dict[str, Any]) -> str:
    details = f"{session['total_sets']} 组"
    if session.get("rpe") is not None:
        details += f"，RPE {session['rpe']}"
    return f"{session['practice_name']}（{details}）"


def build_daily_review(
    as_of: date, meals: list[dict[str, Any]], training: list[dict[str, Any]]
) -> str | None:
    if not meals and not training:
        return "今天还没有看到饮食或训练记录。今天吃了什么，练了什么？"
    if meals and not training:
        meal_recap = "；".join(_format_meal_recap(meal) for meal in meals)
        return f"今天记录的饮食：{meal_recap}。今天练了什么？"
    if training and not meals:
        training_recap = "；".join(
            _format_training_recap(session) for session in training
        )
        return f"今天记录的训练：{training_recap}。今天吃了什么？"
    return None


async def build_proactive_review(
    as_of: date, db_path: str, weekly_summary_generator: WeeklySummaryGenerator
) -> ProactiveReviewResult:
    meals = get_meal_records_for_date(as_of, db_path)
    training = get_training_records_for_date(as_of, db_path)
    daily_review = build_daily_review(as_of, meals, training)

    if as_of.weekday() != 5:
        if daily_review is None:
            return ProactiveReviewResult(should_send=False, message=None)
        return ProactiveReviewResult(should_send=True, message=daily_review)

    start_date, end_date = weekly_bounds(as_of)
    try:
        weekly_review = await weekly_summary_generator(start_date, end_date)
    except Exception:
        try:
            weekly_review = await weekly_summary_generator(start_date, end_date)
        except Exception:
            weekly_review = WEEKLY_FAILURE
    if daily_review is None:
        return ProactiveReviewResult(should_send=True, message=weekly_review)
    return ProactiveReviewResult(
        should_send=True, message=f"{weekly_review}\n\n---\n\n{daily_review}"
    )
