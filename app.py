import asyncio
import nest_asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import groq
import logging
import sqlite3
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_TOKEN = "7860243128:AAFA71x-CJSioQt9c4nxvDJ9Ntr9h4o0kRk"
GROQ_API_KEY = "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"
DB_FILE = "chat_history.db"
# Add this line
nest_asyncio.apply()  # Add this line
# Initialize database
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME
        )
        """)
        conn.commit()

def clean_old_messages():
    """Remove messages older than 1 hour"""
    with sqlite3.connect(DB_FILE) as conn:
        one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S.%f')
        conn.execute("DELETE FROM messages WHERE timestamp < ?", (one_hour_ago,))
        conn.commit()

def add_message_to_history(chat_id, role, content):
    """Add a message to the chat history"""
    with sqlite3.connect(DB_FILE) as conn:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')  # High precision timestamp
        conn.execute(
            "INSERT INTO messages (chat_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (chat_id, role, content, timestamp)
        )

        # Keep only last 10 messages per chat
        conn.execute("""
            DELETE FROM messages
            WHERE id NOT IN (
                SELECT id
                FROM messages
                WHERE chat_id = ?
                ORDER BY timestamp DESC
                LIMIT 10
            )
        """, (chat_id,))

        conn.commit()

def get_chat_messages(chat_id):
    """Get recent messages for the chat"""
    with sqlite3.connect(DB_FILE) as conn:
        clean_old_messages()
        cursor = conn.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY timestamp ASC LIMIT 10",
            (chat_id,)
        )
        return [{'role': role, 'content': content} for role, content in cursor.fetchall()]

async def chat_with_groq(chat_id: int, message: str) -> str:
    """Handle chat with Groq"""
    try:
        # Add user message to history
        add_message_to_history(chat_id, 'user', message)

        # Get recent messages
        messages = get_chat_messages(chat_id)

        # Call Groq API
        response = client.chat.completions.create(
            model="llama3-8b-8192",
            messages=messages,
            temperature=0.7,
            max_tokens=1024
        )

        # Get and store response
        response_text = response.choices[0].message.content.strip()
        add_message_to_history(chat_id, 'assistant', response_text)

        return response_text
    except Exception as e:
        logger.error(f"Error in chat_with_groq: {e}")
        return "Sorry, I encountered an error. Please try again."

async def start(update: Update, context: CallbackContext) -> None:
    """Handle /start command"""
    await update.message.reply_text(
        "Hi! I'm your AI assistant. I'll remember our conversation even if I restart. How can I help you today?"
    )

async def handle_message(update: Update, context: CallbackContext) -> None:
    """Handle incoming messages"""
    try:
        # Show typing indicator
        await context.bot.send_chat_action(chat_id=update.message.chat_id, action="typing")

        # Get response
        response = await chat_with_groq(
            update.message.chat_id,
            update.message.text
        )

        # Send response
        await update.message.reply_text(response)

    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text("Sorry, something went wrong. Please try again.")

def main():
    """Start the bot"""
    try:
        # Initialize database
        init_db()

        # Create application
        app = Application.builder().token(TELEGRAM_TOKEN).build()

        # Add handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        # Start bot
        logger.info("Starting bot...")
        app.run_polling(poll_interval=1.0)

    except Exception as e:
        logger.error(f"Error in main: {e}")

# Initialize Groq client
client = groq.Client(api_key=GROQ_API_KEY)

if __name__ == "__main__":
    main()
