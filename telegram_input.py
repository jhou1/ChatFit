from enum import Enum
import httpx
from typing import Protocol
from telegram import Message, Update
from telegram.ext import (
    ApplicationBuilder,
    Application,
    MessageHandler,
    filters,
    ContextTypes,
)
import os
import telegram.error
from pydantic import BaseModel


class InputModality(Enum):
    TEXT = "text"
    VOICE = "voice"
    PHOTO = "photo"


class TelegramSettings(BaseModel):
    token: str
    proxy_url: str | None = None
    api_url: str
    api_clear_url: str

    @classmethod
    def from_env(cls) -> "TelegramSettings":
        port = os.environ.get("PORT", "8000")
        return cls(
            token=os.environ.get("TELEGRAM_BOT_TOKEN", "test-token"),
            proxy_url=os.environ.get("TELEGRAM_PROXY"),
            api_url=os.environ.get("API_URL", f"http://127.0.0.1:{port}/chat"),
            api_clear_url=os.environ.get(
                "API_CLEAR_URL", f"http://127.0.0.1:{port}/clear"
            ),
        )


def classify_message(message: Message) -> InputModality | None:
    if message.voice:
        return InputModality.VOICE
    if message.photo:
        return InputModality.PHOTO
    if message.text:
        return InputModality.TEXT
    return None


class TelegramInputClient(Protocol):
    async def send_text(self, user_id: str, text: str) -> str: ...
    async def send_voice(self, user_id: str, update_id: int) -> str: ...
    async def send_photo(self, user_id: str, update_id: int) -> str: ...


class HTTPTelegramInputClient:
    def __init__(self, api_url: str):
        self.api_url = api_url

    async def send_text(self, user_id: str, text: str) -> str:
        async with httpx.AsyncClient(timeout=120.0, proxy=None) as client:
            response = await client.post(
                self.api_url, json={"user_id": user_id, "message": text}
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")

    async def send_voice(self, user_id: str, update_id: int) -> str:
        return "Not implemented"

    async def send_photo(self, user_id: str, update_id: int) -> str:
        return "Not implemented"


async def dispatch_non_command_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    print("HANDLER CALLED!")
    if not update.message or not update.effective_user or not update.effective_chat:
        return

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
    except telegram.error.NetworkError:
        pass

    modality = classify_message(update.message)
    client = context.bot_data.get("input_client")

    if modality == InputModality.VOICE:
        if client:
            await client.send_voice(str(update.effective_user.id), update.update_id)
        await update.message.reply_text(
            "Voice message received but not yet processed by backend."
        )
    elif modality == InputModality.PHOTO:
        if client:
            await client.send_photo(str(update.effective_user.id), update.update_id)
        await update.message.reply_text(
            "Photo received but not yet processed by backend."
        )
    elif modality == InputModality.TEXT:
        if client:
            try:
                reply = await client.send_text(
                    str(update.effective_user.id), update.message.text or ""
                )
                await update.message.reply_text(reply)
            except Exception:
                await update.message.reply_text(
                    "Sorry, I'm having trouble connecting to the backend right now."
                )
    else:
        await update.message.reply_text("Unsupported message type.")


def build_telegram_application(settings: TelegramSettings) -> Application:
    builder = ApplicationBuilder().token(settings.token)
    app = builder.build()
    app.bot_data["input_client"] = HTTPTelegramInputClient(settings.api_url)
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, dispatch_non_command_message)
    )
    return app
