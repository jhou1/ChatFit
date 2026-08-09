# Container Timezone Environment Configuration

## Problem

The API container currently runs in UTC. Training, meal, and conversational
insights prompts use `datetime.now().date()` to tell the LLM what “today” means.
Between local midnight and the corresponding UTC midnight, records whose date
is omitted can therefore be saved under the previous local calendar day.

The database is not the source of the defect: it persists the date supplied by
the Agent. The missing boundary is the container process timezone.

## Decision

Use the conventional `TZ` environment variable to set the local timezone of
both Compose services. `docker-compose.yml` will explicitly provide:

```yaml
TZ=${TZ:-Asia/Shanghai}
```

The value can come from the project's `.env` file or the shell running Compose.
When neither supplies a value, the existing single-user deployment behavior
defaults to `Asia/Shanghai`.

Add `TZ=Asia/Shanghai` to `.env.example`. Users in another timezone replace it
with a valid IANA timezone name such as `Europe/Berlin` or `America/New_York`
before creating the containers. If they change it after deployment, they must
recreate the API and Bot containers; a plain restart does not apply environment
variable changes.

No Python timezone abstraction is added. The existing `datetime.now()` calls
already honor `TZ` when the process starts; this was reproduced inside the
current API image, where `TZ=Asia/Shanghai` returned local time eight hours
ahead of `TZ=UTC`.

## Scope

The change applies to the API and Bot container process timezone. It fixes the
default dates inserted into training, meal, and insights prompts because those
prompts are produced by the API process. Explicit dates supplied by the user
remain authoritative.

The proactive review schedule remains explicitly defined as 21:00
`Asia/Shanghai`, matching its existing product contract. Generalizing that
schedule is outside this recording-date bug fix.

## Alternatives Considered

### Host timezone detection wrapper

A wrapper could inspect each host operating system and inject its timezone.
This adds platform-specific detection and a new startup path. It was rejected
in favor of the requested environment-variable-only solution.

### Bind-mount `/etc/localtime`

This is common on native Linux but is not portable to remote container engines
on macOS or Windows, where the mounted path may come from a Linux VM rather than
the client host. It was rejected.

### Application timezone service

A shared Python module could parse and validate an application-specific
timezone setting. It would be useful for per-user timezones, but is unnecessary
for this single deployment-level fix.

## Components and Data Flow

1. The operator sets `TZ` in `.env`; the documented default is
   `Asia/Shanghai`.
2. Compose resolves `TZ` and passes it to both service processes.
3. Python initializes the process-local timezone from `TZ`.
4. Existing `datetime.now().date()` calls produce the configured local date.
5. The LLM uses that date for messages with no explicit date, and SQLite
   persists the resulting typed date unchanged.

## Error Handling

- A missing variable uses the Compose default `Asia/Shanghai` instead of UTC.
- Documentation requires an IANA timezone identifier. Invalid values are an
  operator configuration error; this minimal fix does not add runtime parsing.
- Changes to `.env` take effect only after the API and Bot containers are
  recreated.

## Tests

This configuration-only change uses the user-approved TDD exception rather than
adding a source-text assertion. Capture the pre-change behavior with the real
`podman-compose config` consumer and confirm neither service receives `TZ`.
After the change, resolve Compose once without `TZ` and once with
`TZ=Europe/Berlin`; both `api` and `bot` must receive `Asia/Shanghai` in the
first result and `Europe/Berlin` in the second. Verify `.env.example` and README
documentation during the independent documentation-quality review.

Run the full non-E2E suite and `make quality`. An independent verification Agent
must repeat the Compose resolution checks and the checks in `docs/quality.md`,
and report no errors, failures, or warnings.

## Documentation

Update README's environment table and startup instructions to explain:

- `TZ` controls the containers' local calendar date;
- the default is `Asia/Shanghai`;
- values must be IANA timezone identifiers;
- containers must be recreated after changing it because a plain restart does
  not apply environment variable changes.

Review `docs/index.html` for consistency. No landing-page change is expected
because it does not document deployment environment variables or calendar-date
semantics.

## Acceptance Criteria

- Both Compose services receive `TZ` from `.env` or the shell.
- Missing configuration defaults to `Asia/Shanghai` rather than UTC.
- Existing training and meal recording code uses the configured local date
  without Python production-code changes.
- Explicit user-provided dates remain unchanged.
- README and `.env.example` document configuration and container-recreation
  behavior.
- Both real Compose resolution checks, the complete non-E2E suite, and all
  static quality checks pass with zero errors, failures, or warnings.
