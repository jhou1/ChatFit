import os
import httpx
import mistune
import telegram.error
from telegram import CallbackQuery, Update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from dotenv import load_dotenv

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
API_RESUME_URL = os.environ.get("API_RESUME_URL", f"http://127.0.0.1:{api_port}/resume")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command."""
    welcome_text = (
        "Hello! I am ChatFit, your personal fitness and diet assistant.\n"
        "Tell me about your workouts, what you ate, or ask for analysis on your progress!"
    )
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for all standard text messages."""
    user_id = str(update.effective_user.id)
    user_message = update.message.text

    # Send a typing action to let the user know the bot is thinking
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    except telegram.error.NetworkError as ne:
        print(f"Network error while sending typing action: {ne}")

    try:
        # Increase timeout because agent chains can take a while to complete.
        # Explicitly set proxy to None for the local API call so it doesn't get routed through the SOCKS5 proxy.
        async with httpx.AsyncClient(timeout=120.0, proxy=None) as client:
            response = await client.post(
                API_URL,
                json={"user_id": user_id, "message": user_message}
            )
            response.raise_for_status()
            data = response.json()
            bot_reply = data.get("response")

            if bot_reply == "[SYSTEM_APPROVAL]":
                pending_tools = data.get("pending_tools", [])
                tools_text = "\n".join([f"- {tool_call.get('name')}" for tool_call in pending_tools])
                prompt_text = f"[Approval Requested]: I'll execute the following write operation: \n{tools_text}"

                keyboard = [
                    [
                        InlineKeyboardButton("✅ Approve", callback_data="approve_yes"),
                        InlineKeyboardButton("❌ Reject", callback_data="approve_no")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(prompt_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
                return

            if not bot_reply:
                bot_reply = "Sorry, I processed that but didn't generate a response."

    except httpx.HTTPError as e:
        bot_reply = f"Sorry, I'm having trouble connecting to the backend right now. Error: {e}"
    except Exception as e:
        bot_reply = f"An unexpected error occurred: {e}"

    try:
        html_reply = markdown_to_tg_html(bot_reply).strip()
        await update.message.reply_text(html_reply, parse_mode=ParseMode.HTML)
    except telegram.error.BadRequest:
        # Fallback to plain text if Telegram rejects the HTML
        try:
            await update.message.reply_text(bot_reply)
        except telegram.error.NetworkError as ne:
            print(f"Network error during fallback reply: {ne}")
    except telegram.error.NetworkError as ne:
        print(f"Network error while sending reply to Telegram: {ne}")

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not found in environment variables.")
        print("Please add it to your .env file.")
        exit(1)

    print("Initializing Telegram Bot...")

    proxy_url = os.environ.get("TELEGRAM_PROXY", None)

    if proxy_url:
        print(f"Using proxy: {proxy_url}")
        # We also want to give Telegram's internal httpx client a longer timeout
        request = HTTPXRequest(proxy=proxy_url, connect_timeout=30.0, read_timeout=30.0)
        app = ApplicationBuilder().token(token).request(request).build()
    else:
        app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear_context))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(CallbackQueryHandler(handle_approval_callback))

    print("Bot is polling for messages. Press Ctrl+C to stop.")
    app.run_polling()

async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /clear command."""
    user_id = str(update.effective_user.id)

    try:
        async with httpx.AsyncClient(timeout=30.0, proxy=None) as client:
            response = await client.post(
                API_CLEAR_URL,
                json={"user_id": user_id, "message": "/clear"}
            )
            response.raise_for_status()
            data = response.json()
            reply = data.get("response", "Context cleared.")
            await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"Failed to clear context: {e}")

async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle InlineKeyboardButton"""
    query = update.callback_query

    await query.answer()
    user_id = str(query.from_user.id)
    approved = query.data == "approve_yes"

    status_text = "Approved, executing..." if approved else "Rejected."
    await query.edit_message_text(f"{query.message.text}\n\n{status_text}", parse_mode=ParseMode.HTML)

    try:
        async with httpx.AsyncClient(timeout=120.0, proxy=None) as client:
            response = await client.post(
                API_RESUME_URL,
                json={"user_id": user_id, "approved": approved}
            )
            response.raise_for_status()
            data = response.json()

            bot_reply = data.get("response", "Operation complete.")

            html_reply = markdown_to_tg_html(bot_reply).strip()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=html_reply,
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"Resume operation error: {e}."
        )

if __name__ == '__main__':
    main()

