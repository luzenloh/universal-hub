import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from groq import Groq
import memory
from prompts import SYSTEM_PROMPT, SUMMARY_PROMPT

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I'm an AI assistant.\n\n"
        "Ask me anything — I remember our conversation during the session.\n\n"
        "Type /help to see available commands."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Commands:*\n\n"
        "/start — welcome message\n"
        "/help — this help\n"
        "/clear — clear conversation history\n"
        "/summary — brief summary of our conversation\n\n"
        "Just send a message and I'll answer any question!",
        parse_mode="Markdown",
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memory.clear_history(update.effective_user.id)
    await update.message.reply_text("🗑 History cleared. Starting fresh!")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history = memory.get_history(user_id)

    if not history:
        await update.message.reply_text("No conversation history yet. Start chatting first!")
        return

    history_text = "\n".join(
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
        for m in history
    )

    response = groq_client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": SUMMARY_PROMPT.format(history=history_text)}],
        max_tokens=300,
    )

    summary = response.choices[0].message.content
    await update.message.reply_text(f"📝 *Conversation summary:*\n\n{summary}", parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    memory.add_message(user_id, "user", user_text)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + memory.get_history(user_id)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    response = groq_client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=1000,
    )

    reply = response.choices[0].message.content
    memory.add_message(user_id, "assistant", reply)

    await update.message.reply_text(reply, parse_mode="Markdown")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
