# Proactive Daily and Weekly Reviews Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in Telegram messages that review missing daily meal/training records at 21:00 Asia/Shanghai and send one Insights Agent summary every Saturday.

**Architecture:** The Telegram Bot owns one JobQueue schedule and outbound delivery, while FastAPI owns deterministic record checks and direct, uncheckpointed weekly Insights Agent execution. A new pure orchestration module composes daily and Saturday messages from explicit calendar-date queries; no user-thread or checkpoint logic changes.

**Tech Stack:** Python 3.13, FastAPI, python-telegram-bot 22.8 JobQueue, LangGraph, SQLite, Pydantic, pytest, Ruff, Black, MyPy, Bandit

## Global Constraints

- `PROACTIVE_REVIEWS_ENABLED` accepts case-insensitive `true` or `false` and defaults to `false`.
- Disabled mode registers no proactive job, makes no automatic API call, and does not require `TELEGRAM_CHAT_ID`.
- Enabled mode requires one integer `TELEGRAM_CHAT_ID` and schedules 21:00 in `Asia/Shanghai`.
- Saturday covers exactly the inclusive Sunday-through-Saturday calendar window and produces one combined message.
- Daily completeness is determined only from persisted SQLite rows; an LLM never decides what is missing.
- Weekly generation directly invokes an uncheckpointed Insights Agent and never calls or modifies `get_thread_id`, `user_sessions`, checkpoints, or context-retention code.
- Bot-to-API transport errors and 5xx responses receive at most three total attempts; 4xx responses do not retry.
- Weekly Insights generation receives at most two outer invocations before returning the explicit failure notice.
- Missed schedules are not replayed, and no delivery ledger is introduced.
- Update `README.md`, `docs/architecture.md`, and `docs/index.html` with the shipped behavior.

---

## File Structure

- `agents/sqlite_handler.py`: explicit single-date and inclusive date-range read queries.
- `agents/roles/insights.py`: optional fixed reporting-window tools and the uncheckpointed weekly-summary entry point.
- `proactive_reviews.py`: daily snapshot types, deterministic Chinese message composition, Saturday bounds, weekly retry/fallback orchestration.
- `api.py`: typed `POST /proactive-review` adapter and API application-state wiring.
- `bot.py`: opt-in environment parsing, JobQueue registration, bounded API retries, and proactive Telegram delivery.
- `tests/test_sqlite_handler.py`: exact-date and inclusive-range repository tests.
- `tests/test_proactive_reviews.py`: pure daily/weekly composition and retry tests.
- `tests/test_insights_agent.py`: scheduled reporting-window Agent contract tests.
- `tests/test_api.py`: proactive endpoint contract and thread-isolation regression tests.
- `tests/test_bot.py`: toggle, schedule, retry, no-send, target, and rendering fallback tests.
- `pyproject.toml`, `uv.lock`: enable the `python-telegram-bot[job-queue]` runtime extra.
- `.env.example`, `docker-compose.yml`, `README.md`, `docs/architecture.md`, `docs/index.html`: configuration, deployment, and architecture documentation.

---

### Task 1: Explicit Calendar-Date Data Reads

**Files:**
- Modify: `agents/sqlite_handler.py:219-260`
- Modify: `tests/test_sqlite_handler.py`

**Interfaces:**
- Produces: `get_training_records_for_date(target_date: date, db_path: str) -> list[dict[str, Any]]`
- Produces: `get_meal_records_for_date(target_date: date, db_path: str) -> list[dict[str, Any]]`
- Produces: `get_aggregated_training_between(start_date: date, end_date: date, db_path: str) -> list[dict[str, Any]]`
- Produces: `get_meal_records_between(start_date: date, end_date: date, db_path: str) -> list[dict[str, Any]]`
- Preserves: existing `get_aggregated_training_data` and `get_meal_records_of_last_n_days` behavior for ordinary Insights requests.

- [ ] **Step 1: Add failing exact-date and range tests**

Add imports and tests that seed July 25, July 26, and August 1, then assert only the requested dates are returned:

```python
from datetime import date

from agents.sqlite_handler import (
    get_aggregated_training_between,
    get_meal_records_between,
    get_meal_records_for_date,
    get_training_records_for_date,
)


def test_explicit_date_reads_do_not_use_sqlite_now(temp_db_path):
    add_meal_log(
        MealInfo(
            date=date(2026, 8, 1),
            meal_type="dinner",
            items="rice and fish",
            note="post-training",
        ),
        str(temp_db_path),
    )
    add_training_session(
        TrainingInputRecorder(
            date=date(2026, 8, 1),
            sessions=[
                TrainingSession(
                    practice_name="Squat",
                    practice_type="weighted",
                    rpe=7,
                    note="comfortable",
                    sets=[TrainingSet(set_number=1, weight=100, reps=5)],
                )
            ],
            confirm_new_practices=True,
        ),
        str(temp_db_path),
    )

    meals = get_meal_records_for_date(date(2026, 8, 1), str(temp_db_path))
    training = get_training_records_for_date(date(2026, 8, 1), str(temp_db_path))

    assert [row["items"] for row in meals] == ["rice and fish"]
    assert training == [
        {
            "training_date": "2026-08-01",
            "practice_name": "Squat",
            "rpe": 7,
            "note": "comfortable",
            "total_sets": 1,
        }
    ]


def test_inclusive_week_range_is_exactly_sunday_through_saturday(temp_db_path):
    seed_training_and_meal(temp_db_path, date(2026, 7, 25), "outside")
    seed_training_and_meal(temp_db_path, date(2026, 7, 26), "sunday")
    seed_training_and_meal(temp_db_path, date(2026, 8, 1), "saturday")

    training = get_aggregated_training_between(
        date(2026, 7, 26), date(2026, 8, 1), str(temp_db_path)
    )
    meals = get_meal_records_between(
        date(2026, 7, 26), date(2026, 8, 1), str(temp_db_path)
    )

    assert {row["training_date"] for row in training} == {
        "2026-07-26",
        "2026-08-01",
    }
    assert {row["date"] for row in meals} == {"2026-07-26", "2026-08-01"}
```

Implement the local helper with the existing Pydantic models:

```python
def seed_training_and_meal(db_path, target_date: date, label: str) -> None:
    add_training_session(
        TrainingInputRecorder(
            date=target_date,
            sessions=[
                TrainingSession(
                    practice_name=f"Squat {label}",
                    practice_type="weighted",
                    rpe=7,
                    note=label,
                    sets=[TrainingSet(set_number=1, weight=100, reps=5)],
                )
            ],
            confirm_new_practices=True,
        ),
        str(db_path),
    )
    add_meal_log(
        MealInfo(
            date=target_date,
            meal_type="dinner",
            items=label,
            note=label,
        ),
        str(db_path),
    )
```

- [ ] **Step 2: Run the new repository tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_sqlite_handler.py -q
```

Expected: collection fails because the four new functions do not exist.

- [ ] **Step 3: Implement parameterized explicit-date queries**

Add `date` and `Any` imports, then implement parameterized SQL. The single-date training query groups by session so set rows do not duplicate the recap:

```python
def get_training_records_for_date(
    target_date: date, db_path: str
) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT date(t.date) AS training_date,
                   p.name AS practice_name,
                   t.rpe,
                   t.note,
                   COUNT(s.id) AS total_sets
            FROM training_sessions AS t
            JOIN practices AS p ON p.id = t.practice_id
            LEFT JOIN training_sets AS s ON s.training_session_id = t.id
            WHERE date(t.date) = date(?)
            GROUP BY t.id, date(t.date), p.name, t.rpe, t.note
            ORDER BY t.id ASC
            """,
            (target_date.isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]


def get_meal_records_for_date(
    target_date: date, db_path: str
) -> list[dict[str, Any]]:
    return get_meal_records_between(target_date, target_date, db_path)
```

Implement both inclusive range functions with `WHERE date(...) BETWEEN date(?) AND date(?)`, ordered ascending, and return `sqlite3.Row` values as dictionaries. The training aggregation must preserve the existing keys `training_date`, `practice_type`, `total_weight_volume`, `total_reps`, `total_distance`, `total_duration`, `total_sets`, and `avg_rpe`.

- [ ] **Step 4: Run focused and existing repository tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_sqlite_handler.py tests/test_insights_agent.py -q
```

Expected: all selected default tests pass; the live `e2e` Insights test remains deselected by project configuration.

- [ ] **Step 5: Commit the data-read boundary**

```bash
git add agents/sqlite_handler.py tests/test_sqlite_handler.py
git commit -m "feat: add explicit review date queries"
```

---

### Task 2: Deterministic Daily and Saturday Composition

**Files:**
- Create: `proactive_reviews.py`
- Create: `tests/test_proactive_reviews.py`

**Interfaces:**
- Consumes: the four explicit-date repository functions from Task 1.
- Produces: `ProactiveReviewResult(should_send: bool, message: str | None)`.
- Produces: `weekly_bounds(as_of: date) -> tuple[date, date]`.
- Produces: `today_in_shanghai() -> date`.
- Produces: `build_daily_review(as_of: date, meals: list[dict[str, Any]], training: list[dict[str, Any]]) -> str | None`.
- Produces: `build_proactive_review(as_of: date, db_path: str, weekly_summary_generator: WeeklySummaryGenerator) -> Awaitable[ProactiveReviewResult]`.
- `WeeklySummaryGenerator` is `Callable[[date, date], Awaitable[str]]`.

- [ ] **Step 1: Write failing pure behavior tests**

Create `tests/test_proactive_reviews.py` with one assertion per behavior:

```python
from datetime import date

import pytest

from proactive_reviews import (
    ProactiveReviewResult,
    build_daily_review,
    build_proactive_review,
    weekly_bounds,
)


MEAL = {"meal_type": "dinner", "items": "米饭和鱼", "note": "训练后"}
TRAINING = {
    "practice_name": "深蹲",
    "rpe": 7,
    "note": "状态舒适",
    "total_sets": 5,
}


def test_daily_review_asks_both_when_nothing_is_recorded():
    message = build_daily_review(date(2026, 7, 31), [], [])
    assert message == "今天还没有看到饮食或训练记录。今天吃了什么，练了什么？"


def test_daily_review_recaps_meal_and_only_asks_about_training():
    message = build_daily_review(date(2026, 7, 31), [MEAL], [])
    assert "晚餐：米饭和鱼" in message
    assert message.endswith("今天练了什么？")
    assert "今天吃了什么" not in message


def test_daily_review_recaps_training_and_only_asks_about_meals():
    message = build_daily_review(date(2026, 7, 31), [], [TRAINING])
    assert "深蹲（5 组，RPE 7）" in message
    assert message.endswith("今天吃了什么？")
    assert "今天练了什么" not in message


def test_daily_review_is_silent_when_both_categories_exist():
    assert build_daily_review(date(2026, 7, 31), [MEAL], [TRAINING]) is None


def test_weekly_bounds_are_sunday_through_saturday():
    assert weekly_bounds(date(2026, 8, 1)) == (
        date(2026, 7, 26),
        date(2026, 8, 1),
    )
```

Add async tests that monkeypatch the Task 1 repository functions in the
`proactive_reviews` module. Verify Saturday returns one message, puts the weekly
summary before the daily question, omits the question when both categories
exist, calls the generator with July 26/August 1, retries a failing generator
exactly twice, and then uses this exact heading and notice:

```python
WEEKLY_FAILURE = (
    "## 本周总结\n\n"
    "本周总结暂时生成失败。你可以稍后发消息让我重新总结。"
)
```

- [ ] **Step 2: Run the pure tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_proactive_reviews.py -q
```

Expected: collection fails because `proactive_reviews.py` does not exist.

- [ ] **Step 3: Implement immutable result types and deterministic composition**

Create the module with the exact public types and calendar calculation:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

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


def weekly_bounds(as_of: date) -> tuple[date, date]:
    days_since_sunday = (as_of.weekday() + 1) % 7
    return as_of - timedelta(days=days_since_sunday), as_of
```

Use fixed Chinese labels for `breakfast`, `lunch`, `dinner`, `snack`, and
`extra`. Build recap strings only from persisted dictionary values. Implement
Saturday detection as `as_of.weekday() == 5`, call the generator no more than
twice, and combine sections with `\n\n---\n\n`. Sunday through Friday must not
call the weekly generator.

- [ ] **Step 4: Run pure tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_proactive_reviews.py -q
```

Expected: all proactive composition tests pass.

- [ ] **Step 5: Commit deterministic review orchestration**

```bash
git add proactive_reviews.py tests/test_proactive_reviews.py
git commit -m "feat: compose proactive daily reviews"
```

---

### Task 3: Fixed-Window Weekly Insights Agent

**Files:**
- Modify: `agents/roles/insights.py:1-75`
- Modify: `tests/test_insights_agent.py`

**Interfaces:**
- Consumes: `get_aggregated_training_between` and `get_meal_records_between` from Task 1.
- Extends: `make_insights_agent_graph(llm_config, db_path, *, reporting_window: tuple[date, date] | None = None)`.
- Produces: `generate_weekly_insights(llm_config: LLMConfig, db_path: str, start_date: date, end_date: date) -> Awaitable[str]`.
- Preserves: callers that omit `reporting_window` receive the existing recent-days tools and prompts.

- [ ] **Step 1: Add a failing uncheckpointed reporting-window Agent test**

Patch the safe LLM executor so the first Agent turn calls both fixed-window
tools and the second returns a summary. Patch both repository range functions
to capture the requested dates:

```python
@pytest.mark.asyncio
async def test_weekly_insights_uses_both_fixed_window_tools(monkeypatch, llm_config):
    calls = []

    monkeypatch.setattr(
        insights_module,
        "get_aggregated_training_between",
        lambda start, end, db: calls.append(("training", start, end, db)) or [],
    )
    monkeypatch.setattr(
        insights_module,
        "get_meal_records_between",
        lambda start, end, db: calls.append(("meals", start, end, db)) or [],
    )

    turn = 0

    async def fake_execute(_llm, messages):
        nonlocal turn
        turn += 1
        if turn == 1:
            return {
                "messages": AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "retrieve_recent_training", "args": {}, "id": "t"},
                        {"name": "retrieve_recent_meals", "args": {}, "id": "m"},
                    ],
                )
            }
        return {"messages": AIMessage(content="本周训练和饮食保持稳定。")}

    monkeypatch.setattr(insights_module, "_execute_llm_query_safely", fake_execute)

    summary = await insights_module.generate_weekly_insights(
        llm_config,
        "/tmp/review.db",
        date(2026, 7, 26),
        date(2026, 8, 1),
    )

    assert summary == "本周训练和饮食保持稳定。"
    assert calls == [
        ("training", date(2026, 7, 26), date(2026, 8, 1), "/tmp/review.db"),
        ("meals", date(2026, 7, 26), date(2026, 8, 1), "/tmp/review.db"),
    ]
```

Also assert the generated system prompt contains both ISO dates, and add a test
that blank or `[Error]` final content raises `RuntimeError` so Task 2 can retry.

- [ ] **Step 2: Run the focused Agent tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_insights_agent.py -q
```

Expected: the new test fails because `generate_weekly_insights` and the
`reporting_window` argument do not exist.

- [ ] **Step 3: Add fixed-window tool construction and weekly entry point**

Keep the ordinary branch byte-for-byte compatible in behavior. Assign its tools
to local `training_tool` and `meal_tool` variables. In the scheduled branch,
expose the same public tool names with no caller-controlled day argument while
using distinct Python function names to satisfy static checking:

```python
if reporting_window is None:
    @tool
    def retrieve_recent_training(days: int = 21):
        data = get_aggregated_training_data(days, db_path)
        return json.dumps(data) if data else "No training data found for this period."

    @tool
    def retrieve_recent_meals(days: int = 21):
        data = get_meal_records_of_last_n_days(days, db_path)
        return json.dumps(data) if data else "No meal records found for this period."

    training_tool = retrieve_recent_training
    meal_tool = retrieve_recent_meals
else:
    start_date, end_date = reporting_window

    @tool("retrieve_recent_training")
    def retrieve_fixed_training():
        """Get training data for the fixed scheduled reporting window."""
        data = get_aggregated_training_between(start_date, end_date, db_path)
        return json.dumps(data) if data else "No training data found for this period."

    @tool("retrieve_recent_meals")
    def retrieve_fixed_meals():
        """Get meal data for the fixed scheduled reporting window."""
        data = get_meal_records_between(start_date, end_date, db_path)
        return json.dumps(data) if data else "No meal records found for this period."

    training_tool = retrieve_fixed_training
    meal_tool = retrieve_fixed_meals
```

Append an exact reporting-window instruction to the system prompt in scheduled
mode. Bind and execute `[training_tool, meal_tool]` in both branches.
`generate_weekly_insights` must compile this graph without a checkpointer,
invoke it with a Chinese weekly-summary `HumanMessage`, extract the final text,
and raise `RuntimeError` on blank text or text beginning with `[Error]`.

- [ ] **Step 4: Run Agent and proactive service tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_insights_agent.py tests/test_proactive_reviews.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit scheduled Insights execution**

```bash
git add agents/roles/insights.py tests/test_insights_agent.py
git commit -m "feat: add fixed-window weekly insights"
```

---

### Task 4: Typed Proactive Review API

**Files:**
- Modify: `api.py:1-43,119-160,220-362`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `build_proactive_review` and `generate_weekly_insights` from Tasks 2 and 3.
- Produces: `ProactiveReviewResponse(should_send: bool, message: str | None)`.
- Produces: `POST /proactive-review` with no request body.
- Adds application state: `app.state.db_path: str` during startup.

- [ ] **Step 1: Add failing endpoint contract and isolation tests**

Use the existing ASGI transport and set only the state needed by the endpoint:

```python
@pytest.mark.asyncio
async def test_proactive_review_returns_typed_daily_result_without_thread_changes(
    monkeypatch,
):
    async def fake_build(as_of, db_path, generator):
        assert as_of == date(2026, 7, 31)
        assert db_path == "/tmp/chatfit.db"
        return ProactiveReviewResult(True, "今天练了什么？")

    monkeypatch.setattr(api_module, "today_in_shanghai", lambda: date(2026, 7, 31))
    monkeypatch.setattr(api_module, "build_proactive_review", fake_build)
    api_module.app.state.db_path = "/tmp/chatfit.db"
    api_module.app.state.llm_config = object()
    api_module.user_sessions.clear()

    transport = httpx.ASGITransport(app=api_module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.post("/proactive-review")

    assert response.status_code == 200
    assert response.json() == {"should_send": True, "message": "今天练了什么？"}
    assert api_module.user_sessions == {}
```

Add a no-send response test and a Saturday test whose fake builder invokes its
generator and verifies `generate_weekly_insights` receives the API state's LLM
config, database path, and exact reporting dates. Assert no test calls
`get_thread_id`.

- [ ] **Step 2: Run the endpoint tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_api.py -q
```

Expected: POST returns 404 because `/proactive-review` is absent.

- [ ] **Step 3: Implement the API adapter without thread participation**

Add the response model and endpoint:

```python
class ProactiveReviewResponse(BaseModel):
    should_send: bool
    message: str | None


@app.post("/proactive-review", response_model=ProactiveReviewResponse)
async def proactive_review_endpoint(request: Request) -> ProactiveReviewResponse:
    async def weekly_summary(start_date: date, end_date: date) -> str:
        return await generate_weekly_insights(
            request.app.state.llm_config,
            request.app.state.db_path,
            start_date,
            end_date,
        )

    result = await build_proactive_review(
        today_in_shanghai(), request.app.state.db_path, weekly_summary
    )
    return ProactiveReviewResponse(
        should_send=result.should_send,
        message=result.message,
    )
```

Implement `today_in_shanghai()` with `datetime.now(ZoneInfo("Asia/Shanghai")).date()`
in `proactive_reviews.py` and assign `fastapi_app.state.db_path = db_path` during
startup. Do not add the proactive route to chat correlation middleware and do
not read or write `user_sessions`.

- [ ] **Step 4: Run API, proactive, and Agent tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_api.py tests/test_proactive_reviews.py tests/test_insights_agent.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the API boundary**

```bash
git add api.py proactive_reviews.py tests/test_api.py tests/test_proactive_reviews.py
git commit -m "feat: expose proactive review endpoint"
```

---

### Task 5: Opt-in Telegram Scheduling and Delivery

**Files:**
- Modify: `bot.py:1-124,349-399`
- Modify: `tests/test_bot.py`
- Modify: `pyproject.toml:26`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `ProactiveSettings(enabled: bool, chat_id: int | None, api_url: str)`.
- Produces: `load_proactive_settings(environ: Mapping[str, str] | None = None) -> ProactiveSettings`.
- Produces: `fetch_proactive_review(api_url: str) -> Awaitable[dict[str, Any]]` with three-attempt transport/5xx retry.
- Produces: `register_proactive_review_job(application: Application, settings: ProactiveSettings) -> None`.
- Produces: `send_proactive_review(context: ContextTypes.DEFAULT_TYPE) -> Awaitable[None]`.

- [ ] **Step 1: Add failing toggle and JobQueue registration tests**

Extend the fake application with a `FakeJobQueue` whose `run_daily` records its
arguments. Add these assertions:

```python
def test_proactive_reviews_default_to_disabled():
    settings = bot.load_proactive_settings({})
    assert settings == bot.ProactiveSettings(
        enabled=False,
        chat_id=None,
        api_url="http://127.0.0.1:8000/proactive-review",
    )


def test_disabled_proactive_reviews_do_not_require_chat_id():
    settings = bot.load_proactive_settings(
        {"PROACTIVE_REVIEWS_ENABLED": "false", "TELEGRAM_CHAT_ID": "not-an-id"}
    )
    assert settings.enabled is False
    assert settings.chat_id is None


@pytest.mark.parametrize("value", ["true", "TRUE", " True "])
def test_enabled_proactive_reviews_require_integer_chat_id(value):
    settings = bot.load_proactive_settings(
        {"PROACTIVE_REVIEWS_ENABLED": value, "TELEGRAM_CHAT_ID": "-100123"}
    )
    assert settings.enabled is True
    assert settings.chat_id == -100123


def test_invalid_proactive_toggle_is_rejected():
    with pytest.raises(ValueError, match="PROACTIVE_REVIEWS_ENABLED"):
        bot.load_proactive_settings({"PROACTIVE_REVIEWS_ENABLED": "yes"})


def test_job_is_registered_at_2100_shanghai():
    application = FakeApplication()
    settings = bot.ProactiveSettings(True, 456, "http://api/proactive-review")
    bot.register_proactive_review_job(application, settings)
    registration = application.job_queue.registrations[0]
    assert registration["callback"] is bot.send_proactive_review
    assert registration["time"].hour == 21
    assert registration["time"].minute == 0
    assert registration["time"].tzinfo.key == "Asia/Shanghai"
    assert registration["data"] == settings
```

Add a `main()` test proving disabled mode registers nothing while existing
handlers and polling still start.

- [ ] **Step 2: Add failing retry, no-send, target, and fallback tests**

Test `fetch_proactive_review` with a fake async client sequence:

- transport error, 503, then `{"should_send": true, "message": "回顾"}` results
  in exactly three POST attempts;
- 400 raises after one POST attempt;
- malformed JSON shape raises without retry.

Test the scheduled callback with a fake context:

- `should_send=false, message=null` calls no Telegram method;
- a valid message calls `send_message` with only chat ID `456`, rendered HTML,
  and `ParseMode.HTML`;
- `telegram.error.BadRequest` on HTML causes one plain-text fallback;
- final `telegram.error.NetworkError` is logged and not re-raised as success.

- [ ] **Step 3: Run Bot tests and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_bot.py -q
```

Expected: failures identify the absent settings, scheduler, fetch, and callback
interfaces.

- [ ] **Step 4: Implement settings parsing and disabled-mode short circuit**

Add immutable settings and exact parsing:

```python
@dataclass(frozen=True)
class ProactiveSettings:
    enabled: bool
    chat_id: int | None
    api_url: str


def load_proactive_settings(
    environ: Mapping[str, str] | None = None,
) -> ProactiveSettings:
    values = os.environ if environ is None else environ
    raw_enabled = values.get("PROACTIVE_REVIEWS_ENABLED", "false").strip().lower()
    if raw_enabled not in {"true", "false"}:
        raise ValueError("PROACTIVE_REVIEWS_ENABLED must be true or false")

    port = values.get("PORT", "8000")
    api_url = values.get(
        "API_PROACTIVE_REVIEW_URL",
        f"http://127.0.0.1:{port}/proactive-review",
    )
    if raw_enabled == "false":
        return ProactiveSettings(False, None, api_url)

    raw_chat_id = values.get("TELEGRAM_CHAT_ID", "").strip()
    if not raw_chat_id:
        raise ValueError("TELEGRAM_CHAT_ID is required when proactive reviews are enabled")
    try:
        chat_id = int(raw_chat_id)
    except ValueError as error:
        raise ValueError("TELEGRAM_CHAT_ID must be an integer") from error
    return ProactiveSettings(True, chat_id, api_url)
```

Call this from `main()`. Catch `ValueError`, print the non-sensitive error, and
exit with status 1. Only call `register_proactive_review_job` when enabled.

- [ ] **Step 5: Implement bounded fetch, schedule, and Telegram delivery**

`fetch_proactive_review` must validate that `should_send` is a boolean and that
message presence agrees with it. Retry only `httpx.TransportError` and
`HTTPStatusError` responses whose status is at least 500, sleeping 1 second then
2 seconds. Register:

```python
application.job_queue.run_daily(
    send_proactive_review,
    time=time(hour=21, minute=0, tzinfo=ZoneInfo("Asia/Shanghai")),
    data=settings,
    name="proactive-review",
)
```

The callback uses `context.job.data`, returns immediately on no-send, converts
Markdown with `markdown_to_tg_html`, sends HTML with `context.bot.send_message`,
and falls back to the original plain text only for `telegram.error.BadRequest`.
Log schedule execution, retry number, no-send, and final delivery state without
including raw chat ID or message content.

- [ ] **Step 6: Enable JobQueue dependencies and refresh the lock file**

Change the dependency to:

```toml
"python-telegram-bot[job-queue]>=22.8",
```

Then run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv lock
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv sync
```

Expected: APScheduler and its locked transitive dependencies are present.

- [ ] **Step 7: Run Bot and full default tests and verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_bot.py -q
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest -q
```

Expected: all default tests pass, including all existing Telegram dispatch tests.

- [ ] **Step 8: Commit opt-in scheduling**

```bash
git add bot.py tests/test_bot.py pyproject.toml uv.lock
git commit -m "feat: schedule opt-in proactive Telegram reviews"
```

---

### Task 6: Deployment and User Documentation

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml:22-34`
- Modify: `README.md:65-88,260-300`
- Modify: `docs/architecture.md`
- Modify: `docs/index.html`

**Interfaces:**
- Documents: disabled-by-default behavior and conditional chat ID requirement.
- Configures: `API_PROACTIVE_REVIEW_URL=http://api:8000/proactive-review` for the Bot container.

- [ ] **Step 1: Add documentation/configuration assertions**

Add a lightweight test in `tests/test_bot.py` that reads `.env.example` and
`docker-compose.yml` and asserts these exact strings exist:

```python
assert "PROACTIVE_REVIEWS_ENABLED=false" in env_example
assert "TELEGRAM_CHAT_ID=" in env_example
assert "API_PROACTIVE_REVIEW_URL=http://api:8000/proactive-review" in compose
```

- [ ] **Step 2: Run the documentation assertion and verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_bot.py -q
```

Expected: the new assertion fails because configuration examples are absent.

- [ ] **Step 3: Update environment and Compose configuration**

Add this exact block to `.env.example`:

```dotenv
# Optional proactive daily/weekly Telegram reviews; disabled by default.
PROACTIVE_REVIEWS_ENABLED=false
# Required only when PROACTIVE_REVIEWS_ENABLED=true.
TELEGRAM_CHAT_ID=
```

Add this Bot environment entry to `docker-compose.yml`:

```yaml
- API_PROACTIVE_REVIEW_URL=http://api:8000/proactive-review
```

- [ ] **Step 4: Update all three user-facing architecture sources**

Document in Chinese:

- how to enable with `PROACTIVE_REVIEWS_ENABLED=true` and an integer chat ID;
- 21:00 Asia/Shanghai scheduling;
- missing-category logic;
- Saturday's single combined Sunday-through-Saturday Insights summary;
- disabled default, no catch-up, and single-user limitation;
- the Bot JobQueue → proactive API → SQLite/Insights Agent → Telegram data flow.

Update both Mermaid architecture diagrams and the relevant configuration tables;
keep `docs/index.html` semantically aligned with `README.md` and
`docs/architecture.md`.

- [ ] **Step 5: Run focused tests and documentation drift checks**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache uv run pytest tests/test_bot.py -q
rg -n "PROACTIVE_REVIEWS_ENABLED|TELEGRAM_CHAT_ID|proactive-review|21:00|Asia/Shanghai" .env.example docker-compose.yml README.md docs/architecture.md docs/index.html
```

Expected: tests pass and every documentation/configuration file contains its
applicable proactive-review description.

- [ ] **Step 6: Commit deployment and documentation updates**

```bash
git add .env.example docker-compose.yml README.md docs/architecture.md docs/index.html tests/test_bot.py
git commit -m "docs: explain proactive review scheduling"
```

---

### Task 7: Independent Repository Quality Gate

**Files:**
- Verify: all files changed by Tasks 1-6
- Follow: `docs/quality.md`

**Interfaces:**
- Produces: an independent subagent verification report containing command, exit status, test counts, and every error, failure, or warning.

- [ ] **Step 1: Run local pre-verification**

Run:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache make quality
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache make verify
git diff --check main...HEAD
git status --short
```

Expected: quality and verification exit 0 with no warning; diff check is clean;
only intentional worktree files are tracked or modified.

- [ ] **Step 2: Dispatch the mandatory independent verification subagent**

Give the subagent `docs/quality.md`, the approved design specification, this
implementation plan, and the worktree path. Require it to run at minimum:

```bash
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache make quality
UV_CACHE_DIR=/private/tmp/chatfit-uv-cache make verify
git diff --check main...HEAD
```

Require explicit inspection that README and `docs/index.html` match the code and
that no thread/checkpoint/context-retention file changed.

- [ ] **Step 3: Fix every reported error, failure, or warning and repeat**

For each finding, first add or adjust the smallest regression test that
demonstrates it, verify RED, implement the minimal fix, verify GREEN, and commit:

```bash
git add -u
git commit -m "fix: resolve proactive review verification finding"
```

Dispatch a fresh verification pass after fixes. Continue until the independent
report has zero errors, failures, and warnings.

- [ ] **Step 4: Record final branch state**

Run:

```bash
git status --short --branch
git log --oneline --decorate -10
```

Expected: the worktree is clean on `codex/proactive-reviews`, and all design,
implementation, tests, dependency, configuration, and documentation commits are
present.
