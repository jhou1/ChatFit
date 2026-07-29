import os
from collections.abc import Sequence
from typing import Any

import httpx
import mistune
import telegram.error
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.request import BaseRequest, HTTPXRequest
from dotenv import load_dotenv

from inputs.photo_ocr import PhotoTextExtractor, build_photo_text_extractor_from_env


class TelegramRenderer(mistune.HTMLRenderer):
    def heading(self, text, level, **attrs):
        return f"<b>{text}</b>\n\n"

    def paragraph(self, text):
        return f"{text}\n\n"

    def list(self, text, ordered, **attrs):
        return f"{text.strip()}\n\n"

    def list_item(self, text, **attrs):
        return f"• {text.strip()}\n"

    def strong(self, text):
        return f"<b>{text}</b>"

    def emphasis(self, text):
        return f"<i>{text}</i>"

    def block_code(self, code, info=None):
        return f"<pre><code>{mistune.escape(code)}</code></pre>\n\n"

    def codespan(self, text):
        return f"<code>{mistune.escape(text)}</code>"

    def thematic_break(self):
        return "───────────────\n\n"

    def block_text(self, text):
        return f"{text}\n"

    def block_quote(self, text):
        return f"<i>{text}</i>\n"

    def block_html(self, html):
        return mistune.escape(html)

    def inline_html(self, html):
        return mistune.escape(html)

    def image(self, src, alt="", title=None):
        return f"[Image: {alt}]"

    def link(self, link, text=None, title=None):
        return f'<a href="{link}">{text or link}</a>'


markdown_to_tg_html = mistune.create_markdown(renderer=TelegramRenderer())

# Load variables from .env
load_dotenv()

# In PaaS environments (like Railway), the port is often dynamically assigned.
# We connect to localhost since both processes will run in the same container.
api_port = os.environ.get("PORT", "8000")
API_URL = os.environ.get("API_URL", f"http://127.0.0.1:{api_port}/chat")
API_CLEAR_URL = os.environ.get("API_CLEAR_URL", f"http://127.0.0.1:{api_port}/clear")
NO_TEXT_IN_PHOTO_REPLY = (
    "我没有从这张图片里识别到可处理的文字。"
    "请换一张更清晰的图片，或者直接把内容打出来。"
)
PHOTO_READ_FAILED_REPLY = (
    "我读取这张图片时遇到了问题。" "请重发一次，或者直接把训练或饮食内容打出来。"
)
UNSUPPORTED_INPUT_REPLY = (
    "我现在只能处理文字、语音和图片消息。请把训练或饮食内容用这些方式发给我。"
)


def get_telegram_proxy_url() -> str | None:
    return os.environ.get("TELEGRAM_PROXY")


def build_ocr_agent_message(extracted_text: str) -> str:
    return (
        "请根据这张图片中识别出的内容继续处理。图片文字如下：\n"
        f"{extracted_text.strip()}"
    )


def select_largest_photo(photo_sizes: Sequence[Any]) -> Any:
    return max(
        photo_sizes,
        key=lambda photo: (
            photo.width * photo.height,
            photo.file_size or 0,
        ),
    )


async def post_message_to_api(user_id: str, message: str) -> str:
    async with httpx.AsyncClient(timeout=120.0, proxy=None) as client:
        response = await client.post(
            API_URL,
            json={"user_id": user_id, "message": message},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("response") or (
            "Sorry, I processed that but didn't generate a response."
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command."""
    if not update.message:
        return
    welcome_text = (
        "Hello! I am ChatFit, your personal fitness and diet assistant.\n"
        "Tell me about your workouts, what you ate, or ask for analysis on your progress!"
    )
    await update.message.reply_text(welcome_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for all standard text messages."""
    if not update.message or not update.effective_user or not update.effective_chat:
        return
    user_id = str(update.effective_user.id)
    user_message = update.message.text or ""

    # Send a typing action to let the user know the bot is thinking
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
    except telegram.error.NetworkError as ne:
        print(f"Network error while sending typing action: {ne}")

    try:
        # Increase timeout because agent chains can take a while to complete.
        # Explicitly set proxy to None for the local API call so it doesn't get routed through the SOCKS5 proxy.
        async with httpx.AsyncClient(timeout=120.0, proxy=None) as client:
            response = await client.post(
                API_URL, json={"user_id": user_id, "message": user_message}
            )
            response.raise_for_status()
            data = response.json()
            bot_reply = data.get("response")

            if not bot_reply:
                bot_reply = "Sorry, I processed that but didn't generate a response."

    except httpx.HTTPError as e:
        bot_reply = (
            f"Sorry, I'm having trouble connecting to the backend right now. Error: {e}"
        )
    except Exception as e:
        bot_reply = f"An unexpected error occurred: {e}"

    try:
        html_reply = str(markdown_to_tg_html(bot_reply)).strip()
        if update.message:
            await update.message.reply_text(html_reply, parse_mode=ParseMode.HTML)
    except telegram.error.BadRequest:
        # Fallback to plain text if Telegram rejects the HTML
        try:
            if update.message:
                await update.message.reply_text(bot_reply)
        except telegram.error.NetworkError as ne:
            print(f"Network error during fallback reply: {ne}")
    except telegram.error.NetworkError as ne:
        print(f"Network error while sending reply to Telegram: {ne}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for voice messages."""
    if (
        not update.message
        or not update.effective_user
        or not update.effective_chat
        or not update.message.voice
    ):
        return
    user_id = str(update.effective_user.id)

    # Send a typing action to let the user know the bot is thinking
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
    except telegram.error.NetworkError as ne:
        print(f"Network error while sending typing action: {ne}")

    try:
        # Download voice file
        voice_file = await context.bot.get_file(update.message.voice.file_id)

        import tempfile
        import os
        from google import genai
        from google.genai import types

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        await voice_file.download_to_drive(tmp_path)

        # Transcribe voice using Gemini
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)

        with open(tmp_path, "rb") as f:
            audio_bytes = f.read()

        contents: Any = [
            "Transcribe this voice message exactly as spoken in its original language. Do not add any extra commentary or text.",
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"),
        ]
        response = await client.aio.models.generate_content(
            model="gemini-3.5-flash",
            contents=contents,
        )

        user_message = (response.text or "").strip()
        os.remove(tmp_path)

        if not user_message:
            bot_reply = "Could not transcribe the voice message."
        else:
            # Forward transcribed text to the API
            async with httpx.AsyncClient(timeout=120.0, proxy=None) as http_client:
                api_res = await http_client.post(
                    API_URL, json={"user_id": user_id, "message": user_message}
                )
                api_res.raise_for_status()
                data = api_res.json()
                bot_reply = data.get("response")

                if not bot_reply:
                    bot_reply = (
                        "Sorry, I processed that but didn't generate a response."
                    )

    except httpx.HTTPError as e:
        bot_reply = (
            f"Sorry, I'm having trouble connecting to the backend right now. Error: {e}"
        )
    except Exception as e:
        bot_reply = f"An unexpected error occurred processing voice: {e}"

    try:
        html_reply = str(markdown_to_tg_html(bot_reply)).strip()
        if update.message:
            await update.message.reply_text(html_reply, parse_mode=ParseMode.HTML)
    except telegram.error.BadRequest:
        # Fallback to plain text if Telegram rejects the HTML
        try:
            if update.message:
                await update.message.reply_text(bot_reply)
        except telegram.error.NetworkError as ne:
            print(f"Network error during fallback reply: {ne}")
    except telegram.error.NetworkError as ne:
        print(f"Network error while sending reply to Telegram: {ne}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for photo messages."""
    if (
        not update.message
        or not update.effective_user
        or not update.effective_chat
        or not update.message.photo
    ):
        return
    user_id = str(update.effective_user.id)

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
    except telegram.error.NetworkError as ne:
        print(f"Network error while sending photo typing action: {ne}")

    try:
        extractor = context.application.bot_data.get("photo_text_extractor")
        if extractor is None:
            raise RuntimeError("photo_text_extractor is not configured")

        selected_photo = select_largest_photo(update.message.photo)
        photo_file = await context.bot.get_file(selected_photo.file_id)
        image_bytes = bytes(await photo_file.download_as_bytearray())
        extraction = await extractor.extract_text(image_bytes, "image/jpeg")

        if not extraction.text:
            await update.message.reply_text(NO_TEXT_IN_PHOTO_REPLY)
            return

        bot_reply = await post_message_to_api(
            user_id, build_ocr_agent_message(extraction.text)
        )

    except httpx.HTTPError as e:
        bot_reply = (
            f"Sorry, I'm having trouble connecting to the backend right now. Error: {e}"
        )
    except Exception as e:
        print(f"Photo OCR processing failed: {e}")
        bot_reply = PHOTO_READ_FAILED_REPLY

    try:
        html_reply = str(markdown_to_tg_html(bot_reply)).strip()
        await update.message.reply_text(html_reply, parse_mode=ParseMode.HTML)
    except telegram.error.BadRequest:
        try:
            await update.message.reply_text(bot_reply)
        except telegram.error.NetworkError as ne:
            print(f"Network error during photo fallback reply: {ne}")
    except telegram.error.NetworkError as ne:
        print(f"Network error while sending photo reply to Telegram: {ne}")


async def handle_unsupported_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Handler for non-command Telegram updates that ChatFit cannot process yet."""
    if not update.message:
        return

    try:
        await update.message.reply_text(UNSUPPORTED_INPUT_REPLY)
    except telegram.error.NetworkError as ne:
        print(f"Network error while sending unsupported input reply: {ne}")


def build_telegram_application(
    token: str,
    *,
    proxy_url: str | None = None,
    request: BaseRequest | None = None,
    photo_text_extractor: PhotoTextExtractor | None = None,
) -> Application[Any, Any, Any, Any, Any, Any]:
    builder = ApplicationBuilder().token(token)

    if request is not None:
        builder = builder.request(request)
    elif proxy_url:
        print(f"Using proxy: {proxy_url}")
        telegram_request = HTTPXRequest(
            proxy=proxy_url, connect_timeout=30.0, read_timeout=30.0
        )
        updates_request = HTTPXRequest(
            proxy=proxy_url, connect_timeout=30.0, read_timeout=30.0
        )
        builder = builder.request(telegram_request).get_updates_request(updates_request)

    app = builder.build()
    app.bot_data["photo_text_extractor"] = photo_text_extractor
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear_context))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(
        MessageHandler(filters.ALL & (~filters.COMMAND), handle_unsupported_message)
    )
    return app


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not found in environment variables.")
        print("Please add it to your .env file.")
        exit(1)

    print("Initializing Telegram Bot...")

    proxy_url = get_telegram_proxy_url()
    photo_text_extractor = build_photo_text_extractor_from_env()
    app = build_telegram_application(
        token, proxy_url=proxy_url, photo_text_extractor=photo_text_extractor
    )

    print("Bot is polling for messages. Press Ctrl+C to stop.")
    app.run_polling()


async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /clear command."""
    if not update.effective_user or not update.message:
        return
    user_id = str(update.effective_user.id)

    try:
        async with httpx.AsyncClient(timeout=30.0, proxy=None) as client:
            response = await client.post(
                API_CLEAR_URL, json={"user_id": user_id, "message": "/clear"}
            )
            response.raise_for_status()
            data = response.json()
            reply = data.get("response", "Context cleared.")
            await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"Failed to clear context: {e}")


if __name__ == "__main__":
    main()
