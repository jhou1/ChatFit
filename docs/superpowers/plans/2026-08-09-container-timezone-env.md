# Container Timezone Environment Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure ChatFit's API and Bot containers use a configurable local timezone so default record dates do not silently follow UTC.

**Architecture:** Compose passes the conventional `TZ` environment variable to both services, resolving it from `.env` or the invoking shell and defaulting to `Asia/Shanghai`. Existing Python `datetime.now()` calls remain unchanged because the container runtime applies `TZ` at process startup.

**Tech Stack:** Docker Compose / Podman Compose YAML, POSIX `TZ`, Python 3.13, pytest.

## Global Constraints

- Use only the standard `TZ` environment variable; do not add host-detection scripts, bind mounts, or Python timezone modules.
- Both `api` and `bot` must receive `TZ=${TZ:-Asia/Shanghai}`.
- `.env.example` must document `TZ=Asia/Shanghai` as the default example.
- README must require an IANA timezone identifier and explain that containers must be recreated after a change.
- Do not change Agent, API, database, or proactive-review scheduling code.
- Explicit user-supplied record dates remain authoritative.
- This configuration-only change uses the user-approved TDD exception: verify
  behavior through the real Compose resolver instead of adding a YAML source
  assertion test.

## File Structure

- `docker-compose.yml`: inject the resolved `TZ` value into API and Bot.
- `.env.example`: expose the supported timezone setting.
- `README.md`: document timezone semantics and container-recreation behavior.
- `docs/index.html`: inspect for freshness; no change is expected because it has no deployment configuration section.

---

### Task 1: Configure Container Local Timezone

**Files:**
- Modify: `docker-compose.yml:17-20,29-34`
- Modify: `.env.example:1-8`
- Modify: `README.md:73-103,134-147`

**Interfaces:**
- Produces: Compose service environment entry `TZ=${TZ:-Asia/Shanghai}` for `api` and `bot`.
- Consumes: `TZ` from the Compose interpolation environment or project `.env` file.
- Preserves: all Python Agent, API, database, and scheduled-review interfaces.

- [ ] **Step 1: Capture the failing Compose behavior**

Run:

```bash
TZ=Europe/Berlin podman-compose config
```

Expected RED evidence: the resolved `api.environment` and `bot.environment`
contain no `TZ` entry even though the invoking environment supplies
`Europe/Berlin`. Record the relevant output in the implementation report.

- [ ] **Step 2: Add the minimal Compose configuration**

In `docker-compose.yml`, add this entry to the existing `environment` list for
both `api` and `bot`:

```yaml
- TZ=${TZ:-Asia/Shanghai}
```

Do not alter any existing service commands, volumes, URLs, or settings.

- [ ] **Step 3: Document the environment variable**

Near the required credentials in `.env.example`, add:

```dotenv
# Container local timezone used for default training and meal dates.
# Use an IANA timezone name and recreate containers after changing it.
TZ=Asia/Shanghai
```

In README's optional environment table, add:

```markdown
| `TZ` | API/Bot 容器的本地时区，用于未明确指定日期的训练和饮食记录；默认 `Asia/Shanghai`，应使用 IANA 时区名称 |
```

Immediately after the Docker and Podman startup examples, add this explanation:

````markdown
容器通过 `.env` 中的 `TZ` 计算本地日期。修改时请使用 IANA 时区名称（例如
`Asia/Shanghai`、`Europe/Berlin`），然后必须重新创建 API 与 Bot 容器；普通的
`restart` 不会应用环境变量变更。

使用 Docker Compose 重新创建：

```bash
docker compose up -d --force-recreate api bot
```

使用 Podman Compose 重新创建：

```bash
podman-compose up -d --force-recreate api bot
```
````

- [ ] **Step 4: Verify an explicit timezone override**

Run:

```bash
TZ=Europe/Berlin podman-compose config
```

Expected GREEN evidence: both `api.environment.TZ` and `bot.environment.TZ`
resolve to `Europe/Berlin`; all existing commands, volumes, URLs, and settings
remain present.

- [ ] **Step 5: Verify the default timezone**

Run:

```bash
env -u TZ podman-compose config
```

Expected: both `api.environment.TZ` and `bot.environment.TZ` resolve to
`Asia/Shanghai`.

- [ ] **Step 6: Run the full non-E2E suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass with the configured E2E cases deselected and no
warnings.

- [ ] **Step 7: Review documentation freshness**

Read `README.md` and `docs/index.html`. Confirm README explains `TZ`, its IANA
format, default, affected records, and container-recreation requirement. Confirm
`docs/index.html` remains accurate because it does not expose deployment
environment configuration or timezone-specific date promises.

- [ ] **Step 8: Commit the implementation**

```bash
git add docker-compose.yml .env.example README.md
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
`make quality`, resolve Compose without `TZ` and once with
`TZ=Europe/Berlin`, and inspect README/docs/index.html freshness. Any error,
failure, warning, unresolved timezone, or stale documentation is a failed
verification and must be reported.

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
