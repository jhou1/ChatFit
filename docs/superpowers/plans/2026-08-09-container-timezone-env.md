# Container Timezone Environment Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure ChatFit's API and Bot containers use a configurable local timezone so default record dates do not silently follow UTC.

**Architecture:** Compose passes the conventional `TZ` environment variable to both services, resolving it from `.env` or the invoking shell and defaulting to `Asia/Shanghai`. Existing Python `datetime.now()` calls remain unchanged because the container runtime applies `TZ` at process startup.

**Tech Stack:** Docker Compose / Podman Compose YAML, POSIX `TZ`, Python 3.13, PyYAML, pytest.

## Global Constraints

- Use only the standard `TZ` environment variable; do not add host-detection scripts, bind mounts, or Python timezone modules.
- Both `api` and `bot` must receive `TZ=${TZ:-Asia/Shanghai}`.
- `.env.example` must document `TZ=Asia/Shanghai` as the default example.
- README must require an IANA timezone identifier and explain that containers must be recreated or restarted after a change.
- Do not change Agent, API, database, or proactive-review scheduling code.
- Explicit user-supplied record dates remain authoritative.

## File Structure

- `tests/test_compose_config.py`: parse Compose configuration and protect the timezone contract for both services.
- `docker-compose.yml`: inject the resolved `TZ` value into API and Bot.
- `.env.example`: expose the supported timezone setting.
- `README.md`: document timezone semantics and restart behavior.
- `docs/index.html`: inspect for freshness; no change is expected because it has no deployment configuration section.

---

### Task 1: Configure Container Local Timezone

**Files:**
- Create: `tests/test_compose_config.py`
- Modify: `docker-compose.yml:17-20,29-34`
- Modify: `.env.example:1-8`
- Modify: `README.md:73-103,134-147`

**Interfaces:**
- Produces: Compose service environment entry `TZ=${TZ:-Asia/Shanghai}` for `api` and `bot`.
- Consumes: `TZ` from the Compose interpolation environment or project `.env` file.
- Preserves: all Python Agent, API, database, and scheduled-review interfaces.

- [ ] **Step 1: Write the failing Compose regression test**

Create `tests/test_compose_config.py`:

```python
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker-compose.yml"


@pytest.mark.parametrize("service_name", ["api", "bot"])
def test_compose_services_receive_configurable_local_timezone(service_name: str):
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))

    environment = compose["services"][service_name]["environment"]

    assert "TZ=${TZ:-Asia/Shanghai}" in environment
```

This catches removal of the service-level timezone environment entry, a wrong
default, or configuration of only one container.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run pytest tests/test_compose_config.py -q
```

Expected: `2 failed`; both service parameter cases fail because the current
Compose environment lists do not contain `TZ=${TZ:-Asia/Shanghai}`.

- [ ] **Step 3: Add the minimal Compose configuration**

In `docker-compose.yml`, add this entry to the existing `environment` list for
both `api` and `bot`:

```yaml
- TZ=${TZ:-Asia/Shanghai}
```

Do not alter any existing service commands, volumes, URLs, or settings.

- [ ] **Step 4: Document the environment variable**

Near the required credentials in `.env.example`, add:

```dotenv
# Container local timezone used for default training and meal dates.
# Use an IANA timezone name and restart containers after changing it.
TZ=Asia/Shanghai
```

In README's optional environment table, add:

```markdown
| `TZ` | API/Bot 容器的本地时区，用于未明确指定日期的训练和饮食记录；默认 `Asia/Shanghai`，应使用 IANA 时区名称 |
```

Immediately after the Docker and Podman startup examples, add this explanation:

```markdown
容器通过 `.env` 中的 `TZ` 计算本地日期。修改时请使用 IANA 时区名称（例如
`Asia/Shanghai`、`Europe/Berlin`），然后重新创建或重启 API 与 Bot 容器使其生效。
```

- [ ] **Step 5: Run the focused test and verify GREEN**

Run:

```bash
uv run pytest tests/test_compose_config.py -q
```

Expected: `2 passed` with no warnings.

- [ ] **Step 6: Verify the resolved Compose value**

Run:

```bash
TZ=Europe/Berlin podman-compose config
```

Inspect the resolved output and confirm both `api.environment.TZ` and
`bot.environment.TZ` equal `Europe/Berlin`, while the configured commands,
volumes, and URLs remain present.

- [ ] **Step 7: Run the full non-E2E suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass with the configured E2E cases deselected and no
warnings.

- [ ] **Step 8: Review documentation freshness**

Read `README.md` and `docs/index.html`. Confirm README explains `TZ`, its IANA
format, default, affected records, and restart requirement. Confirm
`docs/index.html` remains accurate because it does not expose deployment
environment configuration or timezone-specific date promises.

- [ ] **Step 9: Commit the implementation**

```bash
git add tests/test_compose_config.py docker-compose.yml .env.example README.md
git commit -m "fix: configure container local timezone"
```

---

### Task 2: Static Quality and Independent Verification

**Files:**
- Inspect: all tracked files selected by the project quality commands.
- Inspect: `README.md` and `docs/index.html`.

**Interfaces:**
- Consumes: the committed Compose timezone configuration from Task 1.
- Produces: independent verification evidence with zero errors, failures, or warnings.

- [ ] **Step 1: Run the mandatory static-quality gate**

Run:

```bash
make quality
```

Expected: Ruff, Black, MyPy, and Bandit exit zero without warnings and the
command ends with `All static check passed.`

- [ ] **Step 2: Dispatch independent verification**

Give a fresh verification Agent the worktree path. Require it to read
`docs/quality.md`, run `uv run pytest -q`, run
`uv run pytest tests/test_compose_config.py -q`, run `make quality`, resolve
Compose once with `TZ=Europe/Berlin`, and inspect README/docs/index.html
freshness. Any error, failure, warning, unresolved timezone, or stale
documentation is a failed verification and must be reported.

- [ ] **Step 3: Fix and repeat if verification is not pristine**

If the independent Agent reports any repository error, failure, or warning,
resume the Task 1 implementer with the exact findings. Require a covering RED
test for behavioral defects, the minimal fix, a GREEN rerun, and another commit.
Then dispatch a fresh independent verification round and repeat until it reports
zero errors, failures, and warnings.

- [ ] **Step 4: Record final branch state**

Run:

```bash
git status --short
git log -3 --oneline --decorate
```

Expected: the worktree is clean and the branch contains the design, plan, and
implementation commits.
