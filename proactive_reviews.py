from __future__ import annotations

import asyncio
import logging
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from telegram.constants import MessageLimit

from agents.sqlite_handler import (
    get_meal_records_for_date,
    get_training_records_for_date,
)

WeeklySummaryGenerator = Callable[[date, date], Awaitable[str]]
logger = logging.getLogger(__name__)


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
WEEKLY_HEADING = "## 本周总结"
WEEKLY_SEPARATOR = "\n\n---\n\n"
WEEKLY_INSIGHTS_MAX_ATTEMPTS = 2
WEEKLY_INSIGHTS_TIMEOUT_SECONDS = 75.0
MAX_PROACTIVE_MESSAGE_LENGTH = int(MessageLimit.MAX_TEXT_LENGTH)
TRUNCATION_MARKER = "…"


@dataclass(frozen=True)
class _DailyReviewParts:
    prefix: str
    recap: str
    question: str

    def render(self, max_length: int) -> str:
        recap_budget = max_length - len(self.prefix) - len(self.question)
        return (
            self.prefix
            + truncate_proactive_text(self.recap, max(0, recap_budget))
            + self.question
        )


def weekly_bounds(as_of: date) -> tuple[date, date]:
    days_since_sunday = (as_of.weekday() + 1) % 7
    return as_of - timedelta(days=days_since_sunday), as_of


def today_in_shanghai() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _is_unicode_extension(character: str) -> bool:
    codepoint = ord(character)
    return bool(unicodedata.combining(character)) or (
        0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
    )


def _is_regional_indicator(character: str) -> bool:
    return 0x1F1E6 <= ord(character) <= 0x1F1FF


def _unicode_clusters(text: str):
    cluster = ""
    for character in text:
        joins_cluster = (
            not cluster
            or _is_unicode_extension(character)
            or character == "\u200d"
            or cluster.endswith("\u200d")
            or (
                len(cluster) == 1
                and _is_regional_indicator(cluster)
                and _is_regional_indicator(character)
            )
        )
        if joins_cluster:
            cluster += character
            continue
        yield cluster
        cluster = character
    if cluster:
        yield cluster


def truncate_proactive_text(text: str, max_length: int) -> str:
    """Truncate by Unicode clusters while preserving room for an ellipsis."""
    if len(text) <= max_length:
        return text
    if max_length <= 0:
        return ""
    cluster_budget = max_length - len(TRUNCATION_MARKER)
    clusters: list[str] = []
    used = 0
    for cluster in _unicode_clusters(text):
        if used + len(cluster) > cluster_budget:
            break
        clusters.append(cluster)
        used += len(cluster)
    return "".join(clusters) + TRUNCATION_MARKER


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


def _strip_weekly_heading(weekly_review: str) -> str:
    narrative = weekly_review
    while narrative.startswith(WEEKLY_HEADING):
        narrative = narrative[len(WEEKLY_HEADING) :].lstrip("\r\n")
    return narrative.lstrip("\r\n")


def _daily_review_parts(
    meals: list[dict[str, Any]], training: list[dict[str, Any]]
) -> _DailyReviewParts | None:
    if not meals and not training:
        return _DailyReviewParts(
            prefix="",
            recap="",
            question="今天还没有看到饮食或训练记录。今天吃了什么，练了什么？",
        )
    if meals and not training:
        return _DailyReviewParts(
            prefix="今天记录的饮食：",
            recap="；".join(_format_meal_recap(meal) for meal in meals),
            question="。今天练了什么？",
        )
    if training and not meals:
        return _DailyReviewParts(
            prefix="今天记录的训练：",
            recap="；".join(_format_training_recap(session) for session in training),
            question="。今天吃了什么？",
        )
    return None


def _compose_weekly_review(
    weekly_review: str, daily_parts: _DailyReviewParts | None
) -> str:
    heading = f"{WEEKLY_HEADING}\n\n"
    narrative = _strip_weekly_heading(weekly_review)
    if daily_parts is None:
        narrative_budget = MAX_PROACTIVE_MESSAGE_LENGTH - len(heading)
        return heading + truncate_proactive_text(narrative, narrative_budget)

    fixed_length = (
        len(heading)
        + len(WEEKLY_SEPARATOR)
        + len(daily_parts.prefix)
        + len(daily_parts.question)
    )
    variable_budget = MAX_PROACTIVE_MESSAGE_LENGTH - fixed_length
    if daily_parts.recap:
        recap_budget = max(1, variable_budget // 3)
        narrative_budget = variable_budget - recap_budget
        if len(narrative) < narrative_budget:
            recap_budget += narrative_budget - len(narrative)
            narrative_budget = len(narrative)
        if len(daily_parts.recap) < recap_budget:
            narrative_budget += recap_budget - len(daily_parts.recap)
            recap_budget = len(daily_parts.recap)
    else:
        narrative_budget = variable_budget
        recap_budget = 0

    limited_narrative = truncate_proactive_text(narrative, narrative_budget)
    limited_daily = (
        daily_parts.prefix
        + truncate_proactive_text(daily_parts.recap, recap_budget)
        + daily_parts.question
    )
    return heading + limited_narrative + WEEKLY_SEPARATOR + limited_daily


async def _generate_weekly_review(
    generator: WeeklySummaryGenerator, start_date: date, end_date: date
) -> str:
    attempt = 1
    try:
        async with asyncio.timeout(WEEKLY_INSIGHTS_TIMEOUT_SECONDS):
            for attempt in range(1, WEEKLY_INSIGHTS_MAX_ATTEMPTS + 1):
                metadata = {
                    "weekly_attempt": attempt,
                    "weekly_max_attempts": WEEKLY_INSIGHTS_MAX_ATTEMPTS,
                }
                logger.info(
                    "Weekly insights generation attempt started", extra=metadata
                )
                try:
                    review = await generator(start_date, end_date)
                except Exception:
                    if attempt < WEEKLY_INSIGHTS_MAX_ATTEMPTS:
                        logger.warning(
                            "Weekly insights generation retry scheduled",
                            extra={
                                **metadata,
                                "weekly_next_attempt": attempt + 1,
                            },
                        )
                        continue
                    logger.error(
                        "Weekly insights generation fallback selected",
                        extra=metadata,
                    )
                    return WEEKLY_FAILURE
                logger.info(
                    "Weekly insights generation attempt succeeded", extra=metadata
                )
                return review
    except TimeoutError:
        logger.error(
            "Weekly insights generation timeout fallback selected",
            extra={
                "weekly_attempt": attempt,
                "weekly_max_attempts": WEEKLY_INSIGHTS_MAX_ATTEMPTS,
            },
        )
    return WEEKLY_FAILURE


def build_daily_review(
    as_of: date, meals: list[dict[str, Any]], training: list[dict[str, Any]]
) -> str | None:
    daily_parts = _daily_review_parts(meals, training)
    if daily_parts is None:
        return None
    return daily_parts.render(MAX_PROACTIVE_MESSAGE_LENGTH)


async def build_proactive_review(
    as_of: date, db_path: str, weekly_summary_generator: WeeklySummaryGenerator
) -> ProactiveReviewResult:
    meals = get_meal_records_for_date(as_of, db_path)
    training = get_training_records_for_date(as_of, db_path)
    daily_parts = _daily_review_parts(meals, training)

    if as_of.weekday() != 5:
        if daily_parts is None:
            return ProactiveReviewResult(should_send=False, message=None)
        return ProactiveReviewResult(
            should_send=True,
            message=daily_parts.render(MAX_PROACTIVE_MESSAGE_LENGTH),
        )

    start_date, end_date = weekly_bounds(as_of)
    weekly_review = await _generate_weekly_review(
        weekly_summary_generator, start_date, end_date
    )
    return ProactiveReviewResult(
        should_send=True,
        message=_compose_weekly_review(weekly_review, daily_parts),
    )
