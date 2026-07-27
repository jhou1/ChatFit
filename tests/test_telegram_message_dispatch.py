import pytest
import asyncio
from telegram import Update
from tests.telegram_fakes import FakeTelegramRequest
from telegram_input import TelegramSettings
from telegram.ext import ApplicationBuilder


def build_test_application():
    settings = TelegramSettings(
        token="test:token", api_url="http://test", api_clear_url="http://test/clear"
    )
    req = FakeTelegramRequest()
    builder = (
        ApplicationBuilder()
        .token(settings.token)
        .request(req)
        .concurrent_updates(False)
    )
    app = builder.build()

    class FakeInputClient:
        def __init__(self):
            self.voice_calls = []

        async def send_voice(self, user_id, update_id):
            self.voice_calls.append((user_id, update_id))
            return "ok"

        async def send_text(self, *args):
            return "ok"

        async def send_photo(self, *args):
            return "ok"

    client = FakeInputClient()
    app.bot_data["input_client"] = client
    return app, req, client


def call_names(transport):
    return [c[0] for c in transport.calls]


def voice_update(duration_seconds=12, bot=None):
    return Update.de_json(
        {
            "update_id": 1,
            "message": {
                "message_id": 2,
                "date": 1234567,
                "chat": {"id": 7, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Test"},
                "voice": {
                    "file_id": "v1",
                    "file_unique_id": "v1u",
                    "duration": duration_seconds,
                },
            },
        },
        bot,
    )


def make_update(payload, bot=None):
    return Update.de_json(
        {
            "update_id": 1,
            "message": {
                "message_id": 2,
                "date": 1234567,
                "chat": {"id": 7, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Test"},
                **payload,
            },
        },
        bot,
    )


@pytest.mark.asyncio
async def test_voice_update_reaches_voice_route_and_never_disappears():
    application, transport, input_client = build_test_application()
    await application.initialize()
    await application.start()
    calls_after_initialize = len(transport.calls)
    update = voice_update(duration_seconds=12, bot=application.bot)

    await application.process_update(update)
    # The queue task processes the updates, so we wait until it's empty
    while not application.update_queue.empty():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # Give it a little time to finish the task

    assert input_client.voice_calls == [("42", update.update_id)]
    update_calls = call_names(transport)[calls_after_initialize:]
    assert update_calls[0] == "sendChatAction"
    assert "sendMessage" in update_calls
    assert "getFile" not in update_calls
    assert "downloadFile" not in update_calls
    await application.stop()
    await application.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_payload",
    [
        {"audio": {"file_id": "a1", "file_unique_id": "a1u", "duration": 1}},
        {"document": {"file_id": "d1", "file_unique_id": "d1u"}},
    ],
)
async def test_every_unsupported_message_gets_terminal_reply(message_payload):
    application, transport, _ = build_test_application()
    await application.initialize()
    await application.start()
    update = make_update(message_payload, bot=application.bot)
    await application.process_update(update)
    while not application.update_queue.empty():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)

    assert "sendMessage" in call_names(transport)
    await application.stop()
    await application.shutdown()
