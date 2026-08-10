# Telegram Bootstrap Retries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Telegram bot available through transient bootstrap connection timeouts by retrying bootstrap five times and recovering the container after longer outages.

**Architecture:** `bot.py` will always construct explicit Telegram request clients with 30-second connection/read timeouts and will pass a bounded retry count to `Application.run_polling`. Compose will restart only the bot service after exhausted retries, while ChatFit backend POST behavior remains unchanged to avoid duplicate agent operations.

**Tech Stack:** Python 3.13, python-telegram-bot 22.8, pytest, Docker/Podman Compose, Markdown, HTML

## Global Constraints

- Set `bootstrap_retries=5`; do not use unlimited retries.
- Use 30-second connect and read timeouts for both ordinary Telegram requests and long-polling update requests, with or without `TELEGRAM_PROXY`.
- Set `restart: unless-stopped` only on the `bot` Compose service.
- Do not add retries around ChatFit API POSTs or Telegram message delivery.
- Do not add new dependencies, environment variables, polling intervals, health checks, or custom backoff loops.
- Preserve injected `BaseRequest` support for tests.
- Keep `README.md` and `docs/index.html` aligned with runtime behavior.

---

### Task 1: Bounded Telegram Bootstrap Recovery

**Files:**
- Modify: `tests/test_bot.py:840-954`
- Modify: `bot.py:93-104`
- Modify: `bot.py:485-555`

**Interfaces:**
- Consumes: `Application.run_polling(bootstrap_retries: int)` and `HTTPXRequest(proxy: str | None, connect_timeout: float, read_timeout: float)` from python-telegram-bot 22.8.
- Produces: module constants `TELEGRAM_BOOTSTRAP_RETRIES: int`, `TELEGRAM_CONNECT_TIMEOUT_SECONDS: float`, and `TELEGRAM_READ_TIMEOUT_SECONDS: float`; `build_telegram_application` continues returning `Application[Any, Any, Any, Any, Any, Any]`.

- [ ] **Step 1: Write failing tests for direct and proxied request clients**

Add a reusable fake near the request configuration tests and assert both clients receive the exact settings:

```python
class RecordingHTTPXRequest:
    instances: list["RecordingHTTPXRequest"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.instances.append(self)


def test_direct_telegram_requests_configure_resilient_timeouts(monkeypatch):
    app = FakeApplication()
    builder = FakeApplicationBuilder(app)
    RecordingHTTPXRequest.instances = []
    monkeypatch.setattr(bot, "ApplicationBuilder", lambda: builder)
    monkeypatch.setattr(bot, "HTTPXRequest", RecordingHTTPXRequest)

    assert bot.build_telegram_application("test-token") is app

    assert [request.kwargs for request in RecordingHTTPXRequest.instances] == [
        {"connect_timeout": 30.0, "read_timeout": 30.0, "proxy": None},
        {"connect_timeout": 30.0, "read_timeout": 30.0, "proxy": None},
    ]
    assert builder.bot_request is RecordingHTTPXRequest.instances[0]
    assert builder.updates_request is RecordingHTTPXRequest.instances[1]
```

Replace the local fake in `test_telegram_proxy_configures_polling_request` with `RecordingHTTPXRequest` and assert:

```python
assert [request.kwargs for request in RecordingHTTPXRequest.instances] == [
    {
        "connect_timeout": 30.0,
        "read_timeout": 30.0,
        "proxy": "socks5h://host.docker.internal:8990",
    },
    {
        "connect_timeout": 30.0,
        "read_timeout": 30.0,
        "proxy": "socks5h://host.docker.internal:8990",
    },
]
```

- [ ] **Step 2: Run the request tests and verify RED**

Run:

```bash
uv run pytest tests/test_bot.py::test_direct_telegram_requests_configure_resilient_timeouts tests/test_bot.py::test_telegram_proxy_configures_polling_request -v
```

Expected: the direct test fails because no `HTTPXRequest` instances are created without a proxy. The proxied test remains a regression assertion for the shared path.

- [ ] **Step 3: Write the failing bootstrap retry assertion**

Change both successful `main` tests to require the bounded retry setting:

```python
assert app.polling_kwargs == {"bootstrap_retries": 5}
```

- [ ] **Step 4: Run the main tests and verify RED**

Run:

```bash
uv run pytest tests/test_bot.py::test_main_registers_photo_message_handler tests/test_bot.py::test_main_registers_enabled_proactive_review_job -v
```

Expected: both tests fail because `main` currently calls `run_polling()` without keyword arguments.

- [ ] **Step 5: Implement the minimum Telegram request and retry configuration**

Add constants with the other bot settings:

```python
TELEGRAM_BOOTSTRAP_RETRIES = 5
TELEGRAM_CONNECT_TIMEOUT_SECONDS = 30.0
TELEGRAM_READ_TIMEOUT_SECONDS = 30.0
```

Keep injected request behavior unchanged, but always create both production request objects otherwise:

```python
if request is not None:
    builder = builder.request(request)
else:
    if proxy_url:
        print(f"Using proxy: {proxy_url}")
    telegram_request = HTTPXRequest(
        proxy=proxy_url,
        connect_timeout=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
        read_timeout=TELEGRAM_READ_TIMEOUT_SECONDS,
    )
    updates_request = HTTPXRequest(
        proxy=proxy_url,
        connect_timeout=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
        read_timeout=TELEGRAM_READ_TIMEOUT_SECONDS,
    )
    builder = builder.request(telegram_request).get_updates_request(updates_request)
```

Start polling with the bounded retry constant:

```python
app.run_polling(bootstrap_retries=TELEGRAM_BOOTSTRAP_RETRIES)
```

- [ ] **Step 6: Run focused bot tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_bot.py -v
```

Expected: all `tests/test_bot.py` tests pass without errors or warnings.

- [ ] **Step 7: Commit the bounded application recovery**

```bash
git add bot.py tests/test_bot.py
git commit -m "fix: retry Telegram bootstrap timeouts"
```

### Task 2: Container Recovery After Exhausted Retries

**Files:**
- Modify: `tests/test_documentation.py`
- Modify: `docker-compose.yml:25-40`

**Interfaces:**
- Consumes: the Compose service named `bot`.
- Produces: `services.bot.restart` with the exact value `unless-stopped`; no change to the `api` service.

- [ ] **Step 1: Write a failing Compose recovery test**

Add PyYAML and the Compose path to `tests/test_documentation.py`:

```python
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"
```

Add the behavior assertion:

```python
def test_only_bot_service_restarts_after_exhausted_bootstrap_retries() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))

    assert compose["services"]["bot"]["restart"] == "unless-stopped"
    assert "restart" not in compose["services"]["api"]
```

- [ ] **Step 2: Run the Compose test and verify RED**

Run:

```bash
uv run pytest tests/test_documentation.py::test_only_bot_service_restarts_after_exhausted_bootstrap_retries -v
```

Expected: FAIL with a missing `restart` key for the bot service.

- [ ] **Step 3: Add the bot restart policy**

Add the setting directly below `container_name` for the bot service:

```yaml
  bot:
    build: .
    container_name: chatfit_bot
    restart: unless-stopped
```

- [ ] **Step 4: Run documentation tests and verify GREEN**

Run:

```bash
uv run pytest tests/test_documentation.py -v
```

Expected: all documentation tests pass without errors or warnings.

- [ ] **Step 5: Commit container recovery**

```bash
git add docker-compose.yml tests/test_documentation.py
git commit -m "fix: restart Telegram bot after bootstrap failure"
```

### Task 3: Document Recovery Semantics and Verify the Change

**Files:**
- Modify: `README.md:150-170`
- Modify: `docs/index.html:416-425`
- Modify: `docs/superpowers/plans/2026-08-11-telegram-bootstrap-retries.md`

**Interfaces:**
- Consumes: `bootstrap_retries=5`, 30-second Telegram request timeouts, and `restart: unless-stopped` from Tasks 1 and 2.
- Produces: user-facing deployment documentation that explains bounded process retries and container recovery without claiming user-message replay.

- [ ] **Step 1: Update operator documentation**

After the Podman Compose startup command in `README.md`, add:

```markdown
Bot 连接 Telegram 时使用 30 秒连接/读取超时；启动连接失败后最多重试 5 次。
若重试耗尽或 Bot 进程意外退出，Compose 的 `unless-stopped` 策略会自动重启 Bot，
直到操作者明确停止容器。该机制不会自动重发已经提交给 ChatFit API 的用户消息。
```

Extend the existing deployment paragraph in `docs/index.html` with a concise
reliability statement, keeping it inside the current deployment text container:

```html
<div><h2>Make ChatFit yours.</h2><p>Create a Telegram bot and deploy the source-available app. Transient Telegram startup failures are retried five times; if they persist, the bot container restarts automatically unless you explicitly stop it.</p></div>
```

- [ ] **Step 2: Run focused documentation and landing-page tests**

Run:

```bash
uv run pytest tests/test_documentation.py tests/test_landing_page.py -v
```

Expected: all selected tests pass without errors or warnings.

- [ ] **Step 3: Run the full verification suite**

Run:

```bash
make verify
```

Expected: 446 or more selected tests pass, with only the configured e2e deselections.

- [ ] **Step 4: Commit documentation and the tracked implementation plan**

```bash
git add README.md docs/index.html docs/superpowers/plans/2026-08-11-telegram-bootstrap-retries.md
git commit -m "docs: explain Telegram connection recovery"
```

### Task 4: Independent Quality Gate

**Files:**
- Review: all branch changes since `main`
- Verify: `README.md`, `docs/index.html`, `docs/quality.md`

**Interfaces:**
- Consumes: the completed branch diff and repository verification commands.
- Produces: an independent subagent report with no error, failure, or warning before completion.

- [ ] **Step 1: Dispatch the required independent verifier**

Ask a fresh verification subagent to inspect the branch diff, confirm README and landing-page freshness, and run:

```bash
make quality
make verify
```

- [ ] **Step 2: Fix every reported error, failure, or warning using TDD**

For each reported behavioral defect, add or tighten a focused failing test, run it to confirm the expected failure, make the minimum production change, and rerun the focused test. Repeat independent verification until the report is clean.

- [ ] **Step 3: Commit any verification fixes**

If the verifier required changes, stage only the affected files and commit them with a message describing the corrected behavior. If no files changed, do not create an empty commit.

- [ ] **Step 4: Confirm clean branch state**

Run:

```bash
git status --short --branch
git log --oneline --decorate main..HEAD
```

Expected: no unstaged or untracked implementation files and a linear series of focused commits on `codex/telegram-bootstrap-retries`.
