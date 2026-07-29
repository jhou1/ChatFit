import json
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from telegram import Update
from telegram.request import BaseRequest, RequestData

import bot


class FakeApplication:
    def __init__(self) -> None:
        self.handlers: list[Any] = []
        self.polling_started = False
        self.polling_kwargs: dict[str, Any] = {}

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)

    def run_polling(self, **kwargs: Any) -> None:
        self.polling_started = True
        self.polling_kwargs = kwargs


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


def test_main_registers_photo_message_handler(monkeypatch):
    app = FakeApplication()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.delenv("TELEGRAM_PROXY", raising=False)
    monkeypatch.delenv("LLM_PROXY", raising=False)
    monkeypatch.setattr(bot, "ApplicationBuilder", lambda: FakeApplicationBuilder(app))

    bot.main()

    callbacks = [
        handler.callback for handler in app.handlers if hasattr(handler, "callback")
    ]
    callback_names = [callback.__name__ for callback in callbacks]
    assert "handle_photo" in callback_names
    assert app.polling_started
    assert app.polling_kwargs == {}


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
async def test_actual_jpeg_photo_update_e2e_reaches_photo_route_through_dispatcher():
    request = FakeTelegramRequest()
    application = bot.build_telegram_application("123:ABC", request=request)
    await application.initialize()

    try:
        calls_after_initialize = len(request.calls)
        update = Update.de_json(photo_update_payload(), application.bot)

        await application.process_update(update)

        update_calls = request.calls[calls_after_initialize:]
        call_methods = [call["method"] for call in update_calls]
        assert call_methods == ["sendMessage"]
        assert update_calls[0]["parameters"]["chat_id"] == 456
        assert update_calls[0]["parameters"]["text"] == (
            "我现在还不能可靠地识别图片内容。"
            "请先把训练或饮食内容用文字发给我，我会继续处理。"
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


@pytest.mark.asyncio
async def test_photo_message_gets_explicit_unsupported_reply():
    replies = []

    async def reply_text(text: str) -> None:
        replies.append(text)

    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="photo-file-id")],
        reply_text=reply_text,
    )
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=456),
    )

    await bot.handle_photo(update, SimpleNamespace())

    assert replies == [
        "我现在还不能可靠地识别图片内容。请先把训练或饮食内容用文字发给我，我会继续处理。"
    ]
