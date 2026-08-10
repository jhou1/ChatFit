# Telegram Bootstrap Retry Design

**Date:** 2026-08-11

## Problem

The Telegram bot currently calls `Application.run_polling()` with the
`python-telegram-bot` 22.8 defaults. The library therefore performs no retry
when application initialization fails. In addition, ChatFit only constructs
custom `HTTPXRequest` objects when `TELEGRAM_PROXY` is configured, so direct
connections retain the library's five-second connect timeout.

The observed failure is a TLS `ConnectTimeout` while the application is
initializing its bot with Telegram's `getMe` endpoint. Because
`bootstrap_retries` defaults to zero, that single transient failure terminates
the process. The Compose service has no restart policy, so a terminated bot
may require manual intervention.

## Selected Design

ChatFit will use two bounded layers of recovery:

1. The bot process will call `run_polling(bootstrap_retries=5)`. In
   `python-telegram-bot`, this means the initial attempt plus up to five retry
   attempts during application and polling bootstrap.
2. The `chatfit_bot` Compose service will use `restart: unless-stopped`. If all
   bootstrap attempts are exhausted, or the process exits unexpectedly for
   another reason, the container runtime can start a fresh process until the
   operator explicitly stops it.

Both proxied and direct Telegram traffic will use explicit `HTTPXRequest`
instances with 30-second connect and read timeouts. The ordinary bot request
and the separate long-polling updates request will receive the same proxy and
timeout settings.

## Components and Data Flow

- `build_telegram_application` owns construction of both Telegram request
  clients. It will always build them when test injection is not used, whether
  or not a proxy URL is configured.
- `main` continues to own process startup and will pass the bounded bootstrap
  retry count to `run_polling`.
- `docker-compose.yml` owns recovery after process exit through the service
  restart policy.

No ChatFit API request behavior changes. In particular, user message POSTs are
not automatically retried because doing so could apply a non-idempotent agent
operation twice.

## Error Handling and Operations

Transient Telegram connection timeouts during bootstrap remain visible in the
library logs but no longer terminate the process on the first occurrence. If
six total attempts fail, the exception still terminates the process so that
Compose can restart it. Invalid bot tokens remain fatal to each process start;
the library does not classify them as retryable network errors.

The restart policy is intentionally limited to the bot service. The API
service is outside the scope of this incident.

## Testing

Automated tests will verify that:

- `main` calls `run_polling` with `bootstrap_retries=5`;
- direct Telegram connections receive explicit 30-second timeout request
  objects;
- proxied connections retain the proxy while receiving the same timeouts;
- the bot Compose service declares `restart: unless-stopped`;
- existing handlers, proactive review scheduling, and dependency injection
  continue to work.

The full project test suite and the checks required by `docs/quality.md` must
pass before completion. `README.md` and `docs/index.html` will document the
runtime recovery behavior.

## Non-goals

- Retrying ChatFit API POSTs or Telegram message delivery calls.
- Adding a custom retry/backoff implementation around
  `python-telegram-bot`.
- Adding new health checks, alerting, or retry-related environment variables.
- Changing Telegram polling intervals or pending-update behavior.
