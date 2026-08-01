import importlib
from datetime import date
from pathlib import Path

import pytest

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
