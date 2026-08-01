import asyncio
import importlib
import logging
from datetime import date
from pathlib import Path

import pytest
from telegram.constants import MessageLimit

import proactive_reviews
from agents.models import MealInfo, TrainingInputRecorder, TrainingSession, TrainingSet
from agents.sqlite_handler import add_meal_log, add_training_session, init_db

MEAL = {"meal_type": "dinner", "items": "米饭和鱼", "note": "训练后"}
TRAINING = {
    "practice_name": "深蹲",
    "rpe": 7,
    "note": "状态舒适",
    "total_sets": 5,
}
WEEKLY_FAILURE = (
    "## 本周总结\n\n" "本周总结暂时生成失败。你可以稍后发消息让我重新总结。"
)
TELEGRAM_TEXT_LIMIT = int(MessageLimit.MAX_TEXT_LENGTH)


def test_daily_review_asks_both_when_nothing_is_recorded():
    """Breaks if an empty day does not request both kinds of records."""
    proactive_reviews = importlib.import_module("proactive_reviews")

    message = proactive_reviews.build_daily_review(date(2026, 7, 31), [], [])

    assert message == "今天还没有看到饮食或训练记录。今天吃了什么，练了什么？"


def test_daily_review_recaps_meal_and_only_asks_about_training():
    """Breaks if recorded meals are not recapped before requesting training."""
    message = proactive_reviews.build_daily_review(date(2026, 7, 31), [MEAL], [])

    assert "晚餐：米饭和鱼" in message
    assert message.endswith("今天练了什么？")
    assert "今天吃了什么" not in message


def test_daily_review_truncates_long_unicode_meal_recap_before_question():
    """Breaks if an oversized meal recap displaces its missing-training prompt."""
    meal = {**MEAL, "items": "🍜" * (TELEGRAM_TEXT_LIMIT + 100)}

    message = proactive_reviews.build_daily_review(date(2026, 7, 31), [meal], [])

    assert message is not None
    assert len(message) == TELEGRAM_TEXT_LIMIT
    assert message.startswith("今天记录的饮食：晚餐：🍜")
    assert message.endswith("…。今天练了什么？")


def test_daily_review_uses_meal_note_when_optional_fields_are_null():
    """Breaks if persisted meals with null optional fields crash or leak None."""
    persisted_meal = {
        "date": "2026-07-31",
        "meal_type": None,
        "items": None,
        "note": "午餐吃了米饭和鱼",
        "created_at": "2026-07-31 12:30:00",
    }

    message = proactive_reviews.build_daily_review(
        date(2026, 7, 31), [persisted_meal], []
    )

    assert message == "今天记录的饮食：午餐吃了米饭和鱼。今天练了什么？"
    assert "今天吃了什么" not in message
    assert "None" not in message


def test_daily_review_recaps_training_and_only_asks_about_meals():
    """Breaks if recorded training is not recapped before requesting meals."""
    message = proactive_reviews.build_daily_review(date(2026, 7, 31), [], [TRAINING])

    assert "深蹲（5 组，RPE 7）" in message
    assert message.endswith("今天吃了什么？")
    assert "今天练了什么" not in message


def test_daily_review_truncates_training_recap_on_unicode_cluster_boundary():
    """Breaks if truncation splits an emoji cluster or loses the meal question."""
    training = {**TRAINING, "practice_name": "🏋️" * TELEGRAM_TEXT_LIMIT}

    message = proactive_reviews.build_daily_review(date(2026, 7, 31), [], [training])

    assert message is not None
    assert len(message) <= TELEGRAM_TEXT_LIMIT
    assert message.startswith("今天记录的训练：🏋️")
    assert "🏋️…" in message
    assert "🏋…" not in message
    assert message.endswith("…。今天吃了什么？")


def test_daily_review_omits_null_rpe_from_training_recap():
    """Breaks if a persisted training session renders its optional RPE as None."""
    persisted_training = {
        "training_date": "2026-07-31",
        "practice_name": "深蹲",
        "rpe": None,
        "note": "状态舒适",
        "total_sets": 5,
    }

    message = proactive_reviews.build_daily_review(
        date(2026, 7, 31), [], [persisted_training]
    )

    assert message == "今天记录的训练：深蹲（5 组）。今天吃了什么？"
    assert "今天练了什么" not in message
    assert "None" not in message


def test_daily_review_is_silent_when_both_categories_exist():
    """Breaks if complete daily records trigger an unnecessary prompt."""
    assert (
        proactive_reviews.build_daily_review(date(2026, 7, 31), [MEAL], [TRAINING])
        is None
    )


def test_weekly_bounds_are_sunday_through_saturday():
    """Breaks if a Saturday review does not include the preceding Sunday."""
    assert proactive_reviews.weekly_bounds(date(2026, 8, 1)) == (
        date(2026, 7, 26),
        date(2026, 8, 1),
    )


@pytest.mark.asyncio
async def test_saturday_adds_weekly_heading_to_plain_summary(tmp_path: Path):
    """Breaks if a successful plain Insights narrative lacks the weekly heading."""
    db_path = tmp_path / "plain_weekly_summary.db"
    init_db(db_path)

    async def weekly_summary_generator(start: date, end: date) -> str:
        return "本周训练稳定。"

    result = await proactive_reviews.build_proactive_review(
        date(2026, 8, 1), str(db_path), weekly_summary_generator
    )

    assert result == proactive_reviews.ProactiveReviewResult(
        True,
        "## 本周总结\n\n本周训练稳定。"
        "\n\n---\n\n今天还没有看到饮食或训练记录。今天吃了什么，练了什么？",
    )


@pytest.mark.asyncio
async def test_saturday_normalizes_single_newline_after_weekly_heading(tmp_path: Path):
    """Breaks if a generator heading is not followed by exactly one blank line."""
    db_path = tmp_path / "malformed_weekly_heading.db"
    init_db(db_path)

    async def weekly_summary_generator(start: date, end: date) -> str:
        return "## 本周总结\n内容"

    result = await proactive_reviews.build_proactive_review(
        date(2026, 8, 1), str(db_path), weekly_summary_generator
    )

    assert result == proactive_reviews.ProactiveReviewResult(
        True,
        "## 本周总结\n\n内容"
        "\n\n---\n\n今天还没有看到饮食或训练记录。今天吃了什么，练了什么？",
    )


@pytest.mark.asyncio
async def test_saturday_puts_weekly_summary_before_daily_question(tmp_path: Path):
    """Breaks if Saturday skips its summary, uses wrong bounds, or reverses sections."""
    db_path = tmp_path / "proactive_review.db"
    init_db(db_path)
    add_meal_log(
        MealInfo(
            date=date(2026, 8, 1),
            meal_type="dinner",
            items="米饭和鱼",
            note="训练后",
        ),
        str(db_path),
    )
    calls: list[tuple[date, date]] = []

    async def weekly_summary_generator(start: date, end: date) -> str:
        calls.append((start, end))
        return "## 本周总结\n\n本周训练稳定。"

    result = await proactive_reviews.build_proactive_review(
        date(2026, 8, 1), str(db_path), weekly_summary_generator
    )

    assert result.should_send is True
    assert result.message is not None
    assert result.message.startswith("## 本周总结\n\n本周训练稳定。")
    assert result.message.count("## 本周总结") == 1
    assert result.message.endswith("今天练了什么？")
    assert calls == [(date(2026, 7, 26), date(2026, 8, 1))]


@pytest.mark.asyncio
async def test_saturday_truncates_weekly_narrative_and_recap_but_keeps_question(
    tmp_path: Path,
):
    """Breaks if variable Saturday sections consume Telegram's fixed envelope."""
    db_path = tmp_path / "long_saturday_review.db"
    init_db(db_path)
    add_meal_log(
        MealInfo(
            date=date(2026, 8, 1),
            meal_type="dinner",
            items="🍜" * TELEGRAM_TEXT_LIMIT,
            note="private meal note",
        ),
        str(db_path),
    )

    async def weekly_summary_generator(start: date, end: date) -> str:
        return "周" * (TELEGRAM_TEXT_LIMIT + 100)

    result = await proactive_reviews.build_proactive_review(
        date(2026, 8, 1), str(db_path), weekly_summary_generator
    )

    assert result.message is not None
    assert len(result.message) <= TELEGRAM_TEXT_LIMIT
    assert result.message.startswith("## 本周总结\n\n周")
    assert result.message.count("\n\n---\n\n") == 1
    assert "今天记录的饮食：晚餐：🍜" in result.message
    assert result.message.endswith("…。今天练了什么？")
    assert result.message.count("…") == 2


@pytest.mark.asyncio
async def test_saturday_slow_success_stays_within_one_outer_attempt(tmp_path: Path):
    """Breaks if a cooperative slow summary is retried before its server budget."""
    db_path = tmp_path / "slow_success.db"
    init_db(db_path)
    calls = 0

    async def weekly_summary_generator(start: date, end: date) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return "本周总结生成成功。"

    result = await proactive_reviews.build_proactive_review(
        date(2026, 8, 1), str(db_path), weekly_summary_generator
    )

    assert result.message is not None
    assert "本周总结生成成功。" in result.message
    assert calls == 1


@pytest.mark.asyncio
async def test_saturday_server_timeout_uses_failure_notice_and_daily_question(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Breaks if the whole weekly path has no deadline or loses the daily suffix."""
    db_path = tmp_path / "weekly_timeout.db"
    init_db(db_path)
    calls = 0
    monkeypatch.setattr(
        proactive_reviews,
        "WEEKLY_INSIGHTS_TIMEOUT_SECONDS",
        0.0,
        raising=False,
    )

    async def weekly_summary_generator(start: date, end: date) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return "private summary that arrived beyond the deadline"

    result = await proactive_reviews.build_proactive_review(
        date(2026, 8, 1), str(db_path), weekly_summary_generator
    )

    assert result == proactive_reviews.ProactiveReviewResult(
        True,
        WEEKLY_FAILURE
        + "\n\n---\n\n今天还没有看到饮食或训练记录。今天吃了什么，练了什么？",
    )
    assert calls == 1


@pytest.mark.asyncio
async def test_saturday_retries_failed_summary_twice_then_uses_failure_notice(
    tmp_path: Path,
):
    """Breaks if generator errors are retried the wrong number of times or leak."""
    db_path = tmp_path / "failed_summary.db"
    init_db(db_path)
    calls = 0

    async def weekly_summary_generator(start: date, end: date) -> str:
        nonlocal calls
        calls += 1
        raise RuntimeError("summary service unavailable")

    result = await proactive_reviews.build_proactive_review(
        date(2026, 8, 1), str(db_path), weekly_summary_generator
    )

    assert result == proactive_reviews.ProactiveReviewResult(
        True,
        WEEKLY_FAILURE
        + "\n\n---\n\n今天还没有看到饮食或训练记录。今天吃了什么，练了什么？",
    )
    assert calls == 2


@pytest.mark.asyncio
async def test_saturday_retry_logs_are_attempt_scoped_and_privacy_safe(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
):
    """Breaks if weekly retry metadata is absent or includes private failures."""
    db_path = tmp_path / "private_retry_logs.db"
    init_db(db_path)
    private_values = (
        "private-trace-id",
        "private meal input",
        "private weekly output",
    )

    async def weekly_summary_generator(start: date, end: date) -> str:
        raise RuntimeError(" | ".join(private_values))

    with caplog.at_level(logging.INFO, logger=proactive_reviews.__name__):
        result = await proactive_reviews.build_proactive_review(
            date(2026, 8, 1), str(db_path), weekly_summary_generator
        )

    assert result.message is not None
    assert result.message.startswith(WEEKLY_FAILURE)
    assert [record.getMessage() for record in caplog.records] == [
        "Weekly insights generation attempt started",
        "Weekly insights generation retry scheduled",
        "Weekly insights generation attempt started",
        "Weekly insights generation fallback selected",
    ]
    assert [getattr(record, "weekly_attempt") for record in caplog.records] == [
        1,
        1,
        2,
        2,
    ]
    assert all(getattr(record, "weekly_max_attempts") == 2 for record in caplog.records)
    assert getattr(caplog.records[1], "weekly_next_attempt") == 2
    assert all(record.exc_info is None for record in caplog.records)
    for private_value in private_values:
        assert private_value not in caplog.text


@pytest.mark.asyncio
async def test_saturday_omits_daily_question_when_both_categories_exist(
    tmp_path: Path,
):
    """Breaks if complete Saturday records still append a daily prompt."""
    db_path = tmp_path / "complete_saturday.db"
    init_db(db_path)
    add_meal_log(
        MealInfo(
            date=date(2026, 8, 1),
            meal_type="dinner",
            items="米饭和鱼",
            note="训练后",
        ),
        str(db_path),
    )
    add_training_session(
        TrainingInputRecorder(
            date=date(2026, 8, 1),
            sessions=[
                TrainingSession(
                    practice_name="深蹲",
                    practice_type="weighted",
                    rpe=7,
                    note="状态舒适",
                    sets=[TrainingSet(set_number=1, weight=100, reps=5)],
                )
            ],
            confirm_new_practices=True,
        ),
        str(db_path),
    )

    async def weekly_summary_generator(start: date, end: date) -> str:
        return "## 本周总结\n\n本周训练稳定。"

    result = await proactive_reviews.build_proactive_review(
        date(2026, 8, 1), str(db_path), weekly_summary_generator
    )

    assert result == proactive_reviews.ProactiveReviewResult(
        True, "## 本周总结\n\n本周训练稳定。"
    )


@pytest.mark.asyncio
async def test_weekday_does_not_call_weekly_summary_generator(tmp_path: Path):
    """Breaks if Sunday through Friday invoke the Saturday-only generator."""
    db_path = tmp_path / "weekday_review.db"
    init_db(db_path)

    async def weekly_summary_generator(start: date, end: date) -> str:
        raise AssertionError("weekday review must not generate a weekly summary")

    result = await proactive_reviews.build_proactive_review(
        date(2026, 7, 31), str(db_path), weekly_summary_generator
    )

    assert result == proactive_reviews.ProactiveReviewResult(
        True, "今天还没有看到饮食或训练记录。今天吃了什么，练了什么？"
    )
