import asyncio
import json
import logging
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image
import telegram.error
from telegram import Update
from telegram.constants import ParseMode
from telegram.request import BaseRequest, RequestData
from telegram.warnings import PTBUserWarning

import bot


class FakeApplication:
    def __init__(self) -> None:
        self.handlers: list[Any] = []
        self.bot_data: dict[str, Any] = {}
        self.job_queue = FakeJobQueue()
        self.polling_started = False
        self.polling_kwargs: dict[str, Any] = {}

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)

    def run_polling(self, **kwargs: Any) -> None:
        self.polling_started = True
        self.polling_kwargs = kwargs


class FakeJobQueue:
    def __init__(self) -> None:
        self.registrations: list[dict[str, Any]] = []

    def run_daily(self, callback: Any, *, time: Any, data: Any, name: str) -> None:
        self.registrations.append(
            {"callback": callback, "time": time, "data": data, "name": name}
        )


class FakeApplicationBuilder:
    def __init__(self, app: FakeApplication) -> None:
        self.app = app
        self.bot_request: Any | None = None
        self.updates_request: Any | None = None

    def token(self, token: str) -> "FakeApplicationBuilder":
        return self

    def request(self, request: Any) -> "FakeApplicationBuilder":
        self.bot_request = request
        return self

    def get_updates_request(self, request: Any) -> "FakeApplicationBuilder":
        self.updates_request = request
        return self

    def job_queue(self, job_queue: Any) -> "FakeApplicationBuilder":
        self.app.job_queue = job_queue
        return self

    def build(self) -> FakeApplication:
        return self.app


class FakeTelegramRequest(BaseRequest):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def read_timeout(self) -> float | None:
        return None

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout: Any = None,
        write_timeout: Any = None,
        connect_timeout: Any = None,
        pool_timeout: Any = None,
    ) -> tuple[int, bytes]:
        if "/file/bot" in url and url.endswith("photos/photo-file-id.jpg"):
            self.calls.append({"method": "downloadFile", "parameters": {}})
            return 200, synthetic_jpeg_bytes()

        telegram_method = url.rsplit("/", maxsplit=1)[-1]
        parameters = request_data.parameters if request_data else {}
        self.calls.append({"method": telegram_method, "parameters": parameters})

        payloads: dict[str, Any] = {
            "getMe": {
                "id": 999,
                "is_bot": True,
                "first_name": "ChatFit",
                "username": "chatfit_test_bot",
            },
            "getFile": {
                "file_id": parameters.get("file_id", "photo-file-id"),
                "file_unique_id": "photo-unique-id",
                "file_size": len(synthetic_jpeg_bytes()),
                "file_path": "photos/photo-file-id.jpg",
            },
            "sendChatAction": True,
            "sendMessage": {
                "message_id": 321,
                "date": 0,
                "chat": {"id": 456, "type": "private"},
                "text": parameters.get("text", ""),
            },
        }
        body = json.dumps({"ok": True, "result": payloads[telegram_method]})
        return 200, body.encode("utf-8")


def photo_update_payload() -> dict[str, Any]:
    image_bytes = synthetic_jpeg_bytes()
    image = Image.open(BytesIO(image_bytes))
    return {
        "update_id": 1000,
        "message": {
            "message_id": 1,
            "date": 0,
            "chat": {"id": 456, "type": "private"},
            "from": {"id": 123, "is_bot": False, "first_name": "Tester"},
            "photo": [
                {
                    "file_id": "photo-file-id",
                    "file_unique_id": "photo-unique-id",
                    "width": image.width,
                    "height": image.height,
                    "file_size": len(image_bytes),
                }
            ],
        },
    }


def unsupported_contact_update_payload() -> dict[str, Any]:
    return {
        "update_id": 1001,
        "message": {
            "message_id": 2,
            "date": 0,
            "chat": {"id": 456, "type": "private"},
            "from": {"id": 123, "is_bot": False, "first_name": "Tester"},
            "contact": {"phone_number": "+15550100", "first_name": "Tester"},
        },
    }


def synthetic_jpeg_bytes() -> bytes:
    image = Image.new("RGB", (640, 480), color=(40, 120, 200))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


class FakePhotoTextExtractor:
    def __init__(self, text: str = "深蹲 5x5 100kg") -> None:
        self.calls: list[tuple[bytes, str]] = []
        self.text = text

    async def extract_text(self, image_bytes: bytes, mime_type: str):
        from inputs.photo_ocr import PhotoTextExtractionResult

        self.calls.append((image_bytes, mime_type))
        return PhotoTextExtractionResult(text=self.text)


class FailingPhotoTextExtractor:
    async def extract_text(self, image_bytes: bytes, mime_type: str):
        raise RuntimeError("provider unavailable")


class FakeAsyncClient:
    def __init__(self, outcomes: list[httpx.Response | Exception]) -> None:
        self.outcomes = outcomes.copy()
        self.posts: list[str] = []

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str) -> httpx.Response:
        self.posts.append(url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeScheduledBot:
    def __init__(self, outcomes: list[Exception | None] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.messages: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if outcome is not None:
                raise outcome


def scheduled_context(
    settings: "bot.ProactiveSettings", telegram_bot: FakeScheduledBot
) -> Any:
    return SimpleNamespace(job=SimpleNamespace(data=settings), bot=telegram_bot)


def patch_proactive_http_client(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: list[httpx.Response | Exception],
) -> FakeAsyncClient:
    client = FakeAsyncClient(outcomes)
    monkeypatch.setattr(bot.httpx, "AsyncClient", lambda **kwargs: client)
    return client


def patch_backend_post(
    monkeypatch: pytest.MonkeyPatch,
    backend_posts: list[dict[str, Any]],
    *,
    response_text: str = "已记录深蹲",
) -> None:
    async def fake_post_message_to_api(user_id: str, message: str) -> str:
        backend_posts.append(
            {"url": bot.API_URL, "json": {"user_id": user_id, "message": message}}
        )
        return response_text

    monkeypatch.setattr(bot, "post_message_to_api", fake_post_message_to_api)


@pytest.mark.asyncio
async def test_scheduled_review_requires_proactive_settings_job_data():
    context = SimpleNamespace(job=None, bot=FakeScheduledBot())

    with pytest.raises(RuntimeError, match="ProactiveSettings job data"):
        await bot.send_proactive_review(context)


@pytest.mark.asyncio
async def test_scheduled_review_requires_configured_chat_id(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = bot.ProactiveSettings(True, None, "http://api/proactive-review")

    async def fake_fetch(api_url: str) -> dict[str, Any]:
        return {"should_send": True, "message": "回顾"}

    monkeypatch.setattr(bot, "fetch_proactive_review", fake_fetch)

    with pytest.raises(RuntimeError, match="configured chat ID"):
        await bot.send_proactive_review(scheduled_context(settings, FakeScheduledBot()))


@pytest.mark.asyncio
async def test_scheduled_review_requires_validated_message(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = bot.ProactiveSettings(True, 456, "http://api/proactive-review")

    async def fake_fetch(api_url: str) -> dict[str, Any]:
        return {"should_send": True, "message": None}

    monkeypatch.setattr(bot, "fetch_proactive_review", fake_fetch)

    with pytest.raises(RuntimeError, match="validated non-blank message"):
        await bot.send_proactive_review(scheduled_context(settings, FakeScheduledBot()))


@pytest.mark.asyncio
async def test_fetch_proactive_review_retries_transport_and_server_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    api_url = "http://api/proactive-review"
    request = httpx.Request("POST", api_url)
    client = patch_proactive_http_client(
        monkeypatch,
        [
            httpx.ConnectError("offline", request=request),
            httpx.Response(503, request=request, json={"detail": "unavailable"}),
            httpx.Response(
                200,
                request=request,
                json={"should_send": True, "message": "回顾"},
            ),
        ],
    )
    sleeps: list[int] = []

    async def fake_sleep(delay: int) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    result = await bot.fetch_proactive_review(api_url)

    assert result == {"should_send": True, "message": "回顾"}
    assert client.posts == [api_url, api_url, api_url]
    assert sleeps == [1, 2]


@pytest.mark.asyncio
async def test_fetch_proactive_review_does_not_retry_client_error(
    monkeypatch: pytest.MonkeyPatch,
):
    api_url = "http://api/proactive-review"
    request = httpx.Request("POST", api_url)
    client = patch_proactive_http_client(
        monkeypatch,
        [httpx.Response(400, request=request, json={"detail": "bad request"})],
    )

    with pytest.raises(httpx.HTTPStatusError):
        await bot.fetch_proactive_review(api_url)

    assert client.posts == [api_url]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "expected_posts"),
    [
        ("transport", 3),
        ("server", 3),
        ("client", 1),
        ("contract", 1),
    ],
)
async def test_scheduled_review_logs_final_fetch_failure_without_private_data(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_kind: str,
    expected_posts: int,
):
    api_url = "http://private.internal/proactive-review?token=url-secret"
    request = httpx.Request("POST", api_url)
    if failure_kind == "transport":
        outcomes: list[httpx.Response | Exception] = [
            httpx.ConnectError("transport-secret", request=request) for _ in range(3)
        ]
    elif failure_kind == "server":
        outcomes = [
            httpx.Response(
                503,
                request=request,
                json={"detail": "response-secret"},
            )
            for _ in range(3)
        ]
    elif failure_kind == "client":
        outcomes = [
            httpx.Response(
                400,
                request=request,
                json={"detail": "response-secret"},
            )
        ]
    else:
        outcomes = [
            httpx.Response(
                200,
                request=request,
                json={"should_send": "response-secret", "message": None},
            )
        ]
    client = patch_proactive_http_client(monkeypatch, outcomes)
    telegram_bot = FakeScheduledBot()
    settings = bot.ProactiveSettings(True, 987654321, api_url)

    async def fake_sleep(_delay: int) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.ERROR, logger=bot.__name__):
        await bot.send_proactive_review(scheduled_context(settings, telegram_bot))

    assert len(client.posts) == expected_posts
    assert telegram_bot.messages == []
    assert [record.getMessage() for record in caplog.records] == [
        "Proactive review fetch failed"
    ]
    assert all(record.exc_info is None for record in caplog.records)
    assert "private.internal" not in caplog.text
    assert "url-secret" not in caplog.text
    assert "987654321" not in caplog.text
    assert "transport-secret" not in caplog.text
    assert "response-secret" not in caplog.text


@pytest.mark.asyncio
async def test_scheduled_review_no_send_calls_no_telegram_method(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    settings = bot.ProactiveSettings(True, 456, "http://api/proactive-review")
    telegram_bot = FakeScheduledBot()
    api_calls: list[str] = []

    async def fake_fetch(api_url: str) -> dict[str, Any]:
        api_calls.append(api_url)
        return {"should_send": False, "message": None}

    monkeypatch.setattr(bot, "fetch_proactive_review", fake_fetch)

    with caplog.at_level("INFO", logger=bot.__name__):
        await bot.send_proactive_review(scheduled_context(settings, telegram_bot))

    assert api_calls == ["http://api/proactive-review"]
    assert telegram_bot.messages == []
    assert "Proactive review schedule execution started" in caplog.text
    assert "Proactive review completed with no send" in caplog.text
    assert "456" not in caplog.text


@pytest.mark.asyncio
async def test_scheduled_review_sends_rendered_html_to_configured_target(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    settings = bot.ProactiveSettings(True, 456, "http://api/proactive-review")
    telegram_bot = FakeScheduledBot()

    async def fake_fetch(api_url: str) -> dict[str, Any]:
        return {"should_send": True, "message": "**回顾**"}

    monkeypatch.setattr(bot, "fetch_proactive_review", fake_fetch)

    with caplog.at_level("INFO", logger=bot.__name__):
        await bot.send_proactive_review(scheduled_context(settings, telegram_bot))

    assert telegram_bot.messages == [
        {"chat_id": 456, "text": "<b>回顾</b>", "parse_mode": ParseMode.HTML}
    ]
    assert "Proactive review delivered as HTML" in caplog.text
    assert "456" not in caplog.text
    assert "回顾" not in caplog.text


@pytest.mark.asyncio
async def test_scheduled_review_falls_back_to_original_plain_text_on_bad_html(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    settings = bot.ProactiveSettings(True, 456, "http://api/proactive-review")
    telegram_bot = FakeScheduledBot([telegram.error.BadRequest("bad html"), None])

    async def fake_fetch(api_url: str) -> dict[str, Any]:
        return {"should_send": True, "message": "**回顾**"}

    monkeypatch.setattr(bot, "fetch_proactive_review", fake_fetch)

    with caplog.at_level("INFO", logger=bot.__name__):
        await bot.send_proactive_review(scheduled_context(settings, telegram_bot))

    assert telegram_bot.messages == [
        {"chat_id": 456, "text": "<b>回顾</b>", "parse_mode": ParseMode.HTML},
        {"chat_id": 456, "text": "**回顾**"},
    ]
    assert "Proactive review delivered as plain text fallback" in caplog.text


@pytest.mark.asyncio
async def test_scheduled_review_logs_final_network_error_without_reraising(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    settings = bot.ProactiveSettings(True, 456, "http://api/proactive-review")
    telegram_bot = FakeScheduledBot(
        [
            telegram.error.BadRequest("bad html"),
            telegram.error.NetworkError("offline"),
        ]
    )

    async def fake_fetch(api_url: str) -> dict[str, Any]:
        return {"should_send": True, "message": "private review"}

    monkeypatch.setattr(bot, "fetch_proactive_review", fake_fetch)

    with caplog.at_level("ERROR", logger=bot.__name__):
        await bot.send_proactive_review(scheduled_context(settings, telegram_bot))

    assert len(telegram_bot.messages) == 2
    assert "Proactive review delivery failed due to network error" in caplog.text
    assert "456" not in caplog.text
    assert "private review" not in caplog.text


@pytest.mark.asyncio
async def test_fetch_proactive_review_rejects_non_boolean_should_send_without_retry(
    monkeypatch: pytest.MonkeyPatch,
):
    api_url = "http://api/proactive-review"
    request = httpx.Request("POST", api_url)
    client = patch_proactive_http_client(
        monkeypatch,
        [
            httpx.Response(
                200,
                request=request,
                json={"should_send": "true", "message": "回顾"},
            )
        ],
    )

    with pytest.raises(ValueError, match="should_send must be a boolean"):
        await bot.fetch_proactive_review(api_url)

    assert client.posts == [api_url]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "error_match"),
    [
        ({"should_send": True, "message": None}, "non-blank message"),
        ({"should_send": True, "message": "  "}, "non-blank message"),
        ({"should_send": False, "message": "unexpected"}, "message=None"),
    ],
)
async def test_fetch_proactive_review_rejects_inconsistent_message_presence(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    error_match: str,
):
    api_url = "http://api/proactive-review"
    request = httpx.Request("POST", api_url)
    client = patch_proactive_http_client(
        monkeypatch,
        [httpx.Response(200, request=request, json=payload)],
    )

    with pytest.raises(ValueError, match=error_match):
        await bot.fetch_proactive_review(api_url)

    assert client.posts == [api_url]


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


@pytest.mark.parametrize(
    "environ",
    [{}, {"PROACTIVE_REVIEWS_ENABLED": "false"}],
    ids=["default", "explicit-false"],
)
def test_disabled_proactive_reviews_build_application_without_job_queue(environ):
    settings = bot.load_proactive_settings(environ)

    application = bot.build_telegram_application(
        "123:ABC",
        request=FakeTelegramRequest(),
        proactive_reviews_enabled=settings.enabled,
    )

    with pytest.warns(PTBUserWarning, match="No `JobQueue` set up"):
        assert application.job_queue is None


def test_enabled_proactive_reviews_build_application_with_registered_job():
    settings = bot.load_proactive_settings(
        {"PROACTIVE_REVIEWS_ENABLED": "true", "TELEGRAM_CHAT_ID": "456"}
    )
    application = bot.build_telegram_application(
        "123:ABC",
        request=FakeTelegramRequest(),
        proactive_reviews_enabled=settings.enabled,
    )

    assert application.job_queue is not None
    bot.register_proactive_review_job(application, settings)
    assert [job.name for job in application.job_queue.jobs()] == ["proactive-review"]


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


def test_enabled_proactive_reviews_require_chat_id():
    with pytest.raises(ValueError, match="TELEGRAM_CHAT_ID is required"):
        bot.load_proactive_settings({"PROACTIVE_REVIEWS_ENABLED": "true"})


def test_enabled_proactive_reviews_reject_non_integer_chat_id():
    with pytest.raises(ValueError, match="TELEGRAM_CHAT_ID must be an integer"):
        bot.load_proactive_settings(
            {"PROACTIVE_REVIEWS_ENABLED": "true", "TELEGRAM_CHAT_ID": "not-an-id"}
        )


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
    assert registration["name"] == "proactive-review"


def test_main_registers_photo_message_handler(monkeypatch):
    app = FakeApplication()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("PROACTIVE_REVIEWS_ENABLED", "false")
    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)
    monkeypatch.delenv("LLM_PROXY", raising=False)
    monkeypatch.setattr(bot, "ApplicationBuilder", lambda: FakeApplicationBuilder(app))
    monkeypatch.setattr(
        bot, "build_photo_text_extractor_from_env", FakePhotoTextExtractor
    )

    bot.main()

    callbacks = [
        handler.callback for handler in app.handlers if hasattr(handler, "callback")
    ]
    callback_names = [callback.__name__ for callback in callbacks]
    assert "handle_photo" in callback_names
    assert app.job_queue is None
    assert app.polling_started
    assert app.polling_kwargs == {}


def test_main_registers_enabled_proactive_review_job(monkeypatch):
    app = FakeApplication()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("PROACTIVE_REVIEWS_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "456")
    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)
    monkeypatch.setattr(bot, "ApplicationBuilder", lambda: FakeApplicationBuilder(app))
    monkeypatch.setattr(
        bot, "build_photo_text_extractor_from_env", FakePhotoTextExtractor
    )

    bot.main()

    assert len(app.job_queue.registrations) == 1
    assert app.polling_started


def test_main_exits_with_non_sensitive_settings_error(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("PROACTIVE_REVIEWS_ENABLED", "yes")

    with pytest.raises(SystemExit) as error:
        bot.main()

    assert error.value.code == 1
    output = capsys.readouterr().out
    assert "PROACTIVE_REVIEWS_ENABLED must be true or false" in output
    assert "secret-token" not in output


def test_telegram_proxy_does_not_reuse_llm_proxy(monkeypatch):
    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)
    monkeypatch.setenv("LLM_PROXY", "socks5://host.docker.internal:8990")

    assert bot.get_telegram_proxy_url() is None


def test_telegram_proxy_configures_polling_request(monkeypatch):
    app = FakeApplication()
    builder = FakeApplicationBuilder(app)
    created_requests = []

    class FakeHTTPXRequest:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            created_requests.append(self)

    monkeypatch.setattr(bot, "ApplicationBuilder", lambda: builder)
    monkeypatch.setattr(bot, "HTTPXRequest", FakeHTTPXRequest)

    assert (
        bot.build_telegram_application(
            "test-token", proxy_url="socks5h://host.docker.internal:8990"
        )
        is app
    )

    assert len(created_requests) == 2
    assert builder.bot_request is created_requests[0]
    assert builder.updates_request is created_requests[1]
    assert created_requests[0].kwargs["proxy"] == "socks5h://host.docker.internal:8990"
    assert created_requests[1].kwargs["proxy"] == "socks5h://host.docker.internal:8990"


@pytest.mark.asyncio
async def test_photo_update_extracts_text_and_forwards_to_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = FakeTelegramRequest()
    extractor = FakePhotoTextExtractor()
    backend_posts: list[dict[str, Any]] = []
    patch_backend_post(monkeypatch, backend_posts)

    application = bot.build_telegram_application(
        "123:ABC", request=request, photo_text_extractor=extractor
    )
    await application.initialize()

    try:
        calls_after_initialize = len(request.calls)
        update = Update.de_json(photo_update_payload(), application.bot)

        await application.process_update(update)

        assert len(extractor.calls) == 1
        assert extractor.calls[0][0] == synthetic_jpeg_bytes()
        assert extractor.calls[0][1] == "image/jpeg"
        assert backend_posts == [
            {
                "url": bot.API_URL,
                "json": {
                    "user_id": "123",
                    "message": (
                        "请根据这张图片中识别出的内容继续处理。图片文字如下：\n"
                        "深蹲 5x5 100kg"
                    ),
                },
            }
        ]
        update_calls = request.calls[calls_after_initialize:]
        call_methods = [call["method"] for call in update_calls]
        assert call_methods == [
            "sendChatAction",
            "getFile",
            "downloadFile",
            "sendMessage",
        ]
        assert update_calls[-1]["parameters"]["chat_id"] == 456
        assert update_calls[-1]["parameters"]["text"] == "已记录深蹲"
    finally:
        await application.shutdown()


@pytest.mark.asyncio
async def test_photo_update_with_empty_ocr_replies_without_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = FakeTelegramRequest()
    extractor = FakePhotoTextExtractor(text="   ")
    backend_posts: list[dict[str, Any]] = []
    patch_backend_post(monkeypatch, backend_posts)

    application = bot.build_telegram_application(
        "123:ABC", request=request, photo_text_extractor=extractor
    )
    await application.initialize()

    try:
        calls_after_initialize = len(request.calls)
        update = Update.de_json(photo_update_payload(), application.bot)

        await application.process_update(update)

        assert backend_posts == []
        update_calls = request.calls[calls_after_initialize:]
        assert update_calls[-1]["parameters"]["text"] == (
            "我没有从这张图片里识别到可处理的文字。"
            "请换一张更清晰的图片，或者直接把内容打出来。"
        )
    finally:
        await application.shutdown()


@pytest.mark.asyncio
async def test_photo_update_with_ocr_failure_replies_without_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = FakeTelegramRequest()
    backend_posts: list[dict[str, Any]] = []
    patch_backend_post(monkeypatch, backend_posts)

    application = bot.build_telegram_application(
        "123:ABC", request=request, photo_text_extractor=FailingPhotoTextExtractor()
    )
    await application.initialize()

    try:
        calls_after_initialize = len(request.calls)
        update = Update.de_json(photo_update_payload(), application.bot)

        await application.process_update(update)

        assert backend_posts == []
        update_calls = request.calls[calls_after_initialize:]
        assert update_calls[-1]["parameters"]["text"] == (
            "我读取这张图片时遇到了问题。"
            "请重发一次，或者直接把训练或饮食内容打出来。"
        )
    finally:
        await application.shutdown()


@pytest.mark.asyncio
async def test_unsupported_non_command_update_gets_explicit_reply():
    request = FakeTelegramRequest()
    application = bot.build_telegram_application("123:ABC", request=request)
    await application.initialize()

    try:
        calls_after_initialize = len(request.calls)
        update = Update.de_json(unsupported_contact_update_payload(), application.bot)

        await application.process_update(update)

        update_calls = request.calls[calls_after_initialize:]
        call_methods = [call["method"] for call in update_calls]
        assert call_methods == ["sendMessage"]
        assert update_calls[0]["parameters"]["chat_id"] == 456
        assert update_calls[0]["parameters"]["text"] == (
            "我现在只能处理文字、语音和图片消息。请把训练或饮食内容用这些方式发给我。"
        )
    finally:
        await application.shutdown()


def test_build_ocr_agent_message_marks_text_as_photo_derived():
    assert bot.build_ocr_agent_message("  深蹲 5x5 100kg  ") == (
        "请根据这张图片中识别出的内容继续处理。图片文字如下：\n" "深蹲 5x5 100kg"
    )
