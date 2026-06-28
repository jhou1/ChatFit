import os
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from dotenv import load_dotenv

# Load variables from .env
load_dotenv()

API_URL = "http://127.0.0.1:8000/chat"

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
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
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
            
            if not bot_reply:
                bot_reply = "Sorry, I processed that but didn't generate a response."
                
    except httpx.HTTPError as e:
        bot_reply = f"Sorry, I'm having trouble connecting to the backend right now. Error: {e}"
    except Exception as e:
        bot_reply = f"An unexpected error occurred: {e}"
        
    await update.message.reply_text(bot_reply)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN not found in environment variables.")
        print("Please add it to your .env file.")
        exit(1)
        
    print("Initializing Telegram Bot...")
    
    # Use the same proxy as the LLM model if running locally
    proxy_url = os.environ.get("TELEGRAM_PROXY", "socks5://127.0.0.1:8990")
    
    if proxy_url:
        print(f"Using proxy: {proxy_url}")
        # We also want to give Telegram's internal httpx client a longer timeout
        request = HTTPXRequest(proxy=proxy_url, connect_timeout=30.0, read_timeout=30.0)
        app = ApplicationBuilder().token(token).request(request).build()
    else:
        app = ApplicationBuilder().token(token).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Bot is polling for messages. Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == '__main__':
    main()
