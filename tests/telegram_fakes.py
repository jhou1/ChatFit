import json
from telegram.request import BaseRequest, RequestData


class FakeTelegramRequest(BaseRequest):
    def __init__(self) -> None:
        self.calls: list[tuple[str, RequestData | None]] = []

    @property
    def read_timeout(self) -> float:
        return 5.0

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def do_request(self, url, method, request_data=None, **timeouts):
        if "/file/bot" in url:
            self.calls.append(("downloadFile", request_data))
            return 200, b"synthetic-media-bytes"
        telegram_method = url.rsplit("/", 1)[-1]
        self.calls.append((telegram_method, request_data))
        payloads = {
            "getMe": {
                "id": 123,
                "is_bot": True,
                "first_name": "ChatFit",
                "username": "chatfit_test_bot",
            },
            "sendChatAction": True,
            "sendMessage": {
                "message_id": 999,
                "date": 0,
                "chat": {"id": 7, "type": "private"},
                "text": "ok",
            },
            "getFile": {
                "file_id": "voice-file",
                "file_unique_id": "voice-unique",
                "file_size": 21,
                "file_path": "voice/synthetic.ogg",
            },
        }
        payload = payloads.get(telegram_method, {"ok": True})
        body = json.dumps({"ok": True, "result": payload})
        return 200, body.encode("utf-8")
