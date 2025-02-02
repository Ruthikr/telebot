import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import groq
import time

# Load environment variables
load_dotenv()

# Secure API key handling
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Set this to your server's URL
PORT = int(os.getenv("PORT", 8443))  # Default to 8443 if not set

# Configure logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Database file
DB_FILE = "chat_history.db"

# Rate limiting per user (3-second cooldown)
user_last_message_time = {}

# Initialize Groq client
client = groq.Client(api_key=GROQ_API_KEY)


def init_db():
    """Initialize the SQLite database with WAL mode for better concurrency."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")  # Enable Write-Ahead Logging
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME
            )
            """
        )
        conn.commit()


def clean_old_messages():
    """Remove messages older than 1 hour."""
    with sqlite3.connect(DB_FILE) as conn:
        one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S.%f")
        conn.execute("DELETE FROM messages WHERE timestamp < ?", (one_hour_ago,))
        conn.commit()


def add_message_to_history(chat_id, role, content):
    """Store messages in the database."""
    with sqlite3.connect(DB_FILE) as conn:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, timestamp),
        )
        # Keep only the last 10 messages per chat
        conn.execute(
            """
            DELETE FROM messages
            WHERE id NOT IN (
                SELECT id FROM messages WHERE chat_id = ?
                ORDER BY timestamp DESC LIMIT 10
            )
            """,
            (chat_id,),
        )
        conn.commit()


def get_chat_messages(chat_id):
    """Retrieve recent messages for a chat."""
    with sqlite3.connect(DB_FILE) as conn:
        clean_old_messages()
        cursor = conn.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp ASC LIMIT 10", (chat_id,)
        )
        return [{"role": role, "content": content} for role, content in cursor.fetchall()]


async def chat_with_groq(chat_id: int, message: str) -> str:
    """Send user messages to Groq API asynchronously."""
    try:
        # Add user message to history
        add_message_to_history(chat_id, "user", message)

        # Get recent messages
        messages = get_chat_messages(chat_id)

        # Call Groq API asynchronously
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="llama3-8b-8192",
                messages=messages,
                temperature=0.7,
                max_tokens=1024,
            )
        )

        # Extract response text
        response_text = response.choices[0].message.content.strip()

        # Store response in history
        add_message_to_history(chat_id, "assistant", response_text)

        return response_text

    except Exception as e:
        logger.error(f"Error in chat_with_groq: {e}")
        return "Sorry, I encountered an error. Please try again."


async def start(update: Update, context: CallbackContext) -> None:
    """Handle the /start command."""
    await update.message.reply_text("Hi! I'm your AI assistant. How can I help you today?")


async def handle_message(update: Update, context: CallbackContext) -> None:
    """Process incoming messages with rate limiting."""
    chat_id = update.message.chat_id

    # Rate limiting (1 request every 3 seconds)
    global user_last_message_time
    now = time.time()
    if chat_id in user_last_message_time and now - user_last_message_time[chat_id] < 3:
        await update.message.reply_text("You're sending messages too quickly. Please wait a moment.")
        return
    user_last_message_time[chat_id] = now

    try:
        # Show typing indicator
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # Get response from Groq
        response = await chat_with_groq(chat_id, update.message.text)

        # Send response
        await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text("Sorry, something went wrong. Please try again.")


def main():
    """Start the bot with webhook support."""
    try:
        # Initialize database
        init_db()

        # Create application
        app = Application.builder().token(TELEGRAM_TOKEN).build()

        # Add command/message handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Webhook setup
        logger.info("Starting bot with webhook...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
        )

    except Exception as e:
        logger.error(f"Error in main: {e}")


if __name__ == "__main__":
    main()
