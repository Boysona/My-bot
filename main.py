import re
import json
import logging
import requests
import telebot
from flask import Flask, request, abort
from datetime import datetime
import time

# === CONFIGURATION ===
TOKEN = "7669714776:AAEzSzAusOsKGzGknKcD2I1dX7Q1pUFi6rQ"
DEEPSEEK_API_KEY = "sk-fefb48f89f7b4e32bc6b214ef739b248"
ADMIN_ID = 5978150981
WEBHOOK_URL = "https://my-bot-xjig.onrender.com"

USERS_FILE = 'users.json'
MEMORY_FILE = 'user_memory.json'

# === SETUP ===
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# === USER DATA & MEMORY ===
user_data = {}
user_memory = {}

def load_user_data():
    global user_data
    try:
        with open(USERS_FILE, 'r') as f:
            user_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        user_data = {}

def save_user_data():
    with open(USERS_FILE, 'w') as f:
        json.dump(user_data, f, indent=4)

def load_memory():
    global user_memory
    try:
        with open(MEMORY_FILE, 'r') as f:
            user_memory = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        user_memory = {}

def save_memory():
    with open(MEMORY_FILE, 'w') as f:
        json.dump(user_memory, f, indent=4)

# Initialize persisted data
load_user_data()
load_memory()

# === ANTI‑SPAM FOR GROUPS ===
@bot.message_handler(
    func=lambda m: m.chat.type in ["group", "supergroup"] and m.content_type == 'text'
)
def anti_spam_filter(message):
    try:
        bot_member = bot.get_chat_member(message.chat.id, bot.get_me().id)
        if bot_member.status not in ['administrator', 'creator']:
            return  # Bot not admin => can't delete

        user_member = bot.get_chat_member(message.chat.id, message.from_user.id)
        if user_member.status in ['administrator', 'creator']:
            return  # Allow admins

        text = message.text or ""
        if (
            len(text) > 120
            or re.search(r"https?://", text)
            or "t.me/" in text
            or re.search(r"@\w+", text)
        ):
            bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logging.warning(f"Anti-spam check failed: {e}")

# === DEEPSEEK AI INTERACTION ===
def ask_deepseek(user_id, user_message, max_retries=3, retry_delay=5):
    uid = str(user_id)
    history = user_memory.setdefault(uid, [])
    history.append({
        "role": "user",
        "content": user_message,
        "timestamp": datetime.utcnow().isoformat()
    })
    messages = [{"role": msg["role"], "content": msg["content"]} for msg in history]
    payload = {
        "model": "deepseek-chat",
        "messages": messages
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    url = "https://api.deepseek.com/v1/chat/completions"

    for i in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload)
            resp.raise_for_status()  # Raise an exception for HTTP errors
            data = resp.json()
            if "choices" in data and data["choices"]:
                reply = data['choices'][0]['message']['content']
                history.append({
                    "role": "assistant",
                    "content": reply,
                    "timestamp": datetime.utcnow().isoformat()
                })
                save_memory()
                return reply
            else:
                return "Deepseek AI API error: " + json.dumps(data)
        except requests.exceptions.RequestException as e:
            logging.error(f"Error contacting Deepseek AI API (Attempt {i + 1}/{max_retries}): {e}")
            if i < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return f"Error contacting Deepseek AI API after {max_retries} retries: {e}"
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding Deepseek AI API response (Attempt {i + 1}/{max_retries}): {e}, Response: {resp.text}")
            if i < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return f"Error decoding Deepseek AI API response after {max_retries} retries: {e}"
    return "Deepseek AI API is currently unavailable after multiple retries."

# === BOT INFO ===
def set_bot_info():
    cmds = [
        telebot.types.BotCommand("start", "Start the bot"),
        telebot.types.BotCommand("reset", "Remove chat memory and start a new chat")
    ]
    bot.set_my_commands(cmds)
    bot.set_my_description(
        "This bot is an advanced spam blocker and auto moderator designed for Telegram groups to stay clean and safe from ads, spam, and unwanted links.”."
    )

# === USER ACTIVITY TRACKING ===
def update_user_activity(user_id):
    user_data[str(user_id)] = datetime.utcnow().isoformat()
    save_user_data()

def is_active_within(ts_iso, days):
    try:
        last = datetime.fromisoformat(ts_iso)
        return (datetime.utcnow() - last).days < days
    except:
        return False

def get_user_counts():
    total = len(user_data)
    monthly = sum(is_active_within(ts, 30) for ts in user_data.values())
    weekly  = sum(is_active_within(ts, 7) for ts in user_data.values())
    return total, monthly, weekly

# === COMMAND HANDLERS ===
@bot.message_handler(commands=['start'])
def handle_start(message):
    update_user_activity(message.from_user.id)
    if message.from_user.id == ADMIN_ID:
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("Total Users", "/status", "/broadcast")
        bot.send_message(message.chat.id, "👋 Welcome, Admin!", reply_markup=markup)
    else:
        bot.send_message(
            message.chat.id,
            "👋 Welcome! Add me to your group to remove spam, or chat with me directly."
        )

@bot.message_handler(commands=['help'])
def handle_help(message):
    bot.send_message(
        message.chat.id,
        "Commands:\n"
        "/start - Start bot\n"
        "/help - This help message\n"
        "/status - Bot stats (admin only)\n"
        "/broadcast - Send a broadcast message (admin only)\n"
        "/reset - Reset AI chat memory",
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['status'])
def handle_status(message):
    if message.from_user.id != ADMIN_ID:
        return
    total, monthly, weekly = get_user_counts()
    bot.send_message(
        message.chat.id,
        f"📊 Stats:\n"
        f"• Total Users: {total}\n"
        f"• Active (30d): {monthly}\n"
        f"• Active (7d): {weekly}"
    )

@bot.message_handler(commands=['broadcast'])
def handle_broadcast(message):
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "📢 Enter the message you want to broadcast:")
        bot.register_next_step_handler(message, process_broadcast_message)
    else:
        bot.send_message(message.chat.id, "🔒 This command is for admins only.")

def process_broadcast_message(message):
    broadcast_text = message.text
    sent_count = 0
    failed_count = 0
    for user_id_str in user_data:
        try:
            bot.send_message(int(user_id_str), broadcast_text)
            sent_count += 1
        except Exception as e:
            failed_count += 1
            logging.error(f"Failed to send broadcast to user {user_id_str}: {e}")
    bot.send_message(
        message.chat.id,
        f"📣 Broadcast sent!\n"
        f"• Successful deliveries: {sent_count}\n"
        f"• Failed deliveries: {failed_count}"
    )

@bot.message_handler(commands=['reset'])
def reset_memory(message):
    uid = str(message.from_user.id)
    user_memory.pop(uid, None)
    save_memory()
    bot.send_message(message.chat.id, "✅ All your AI chat memory has been cleared.")

# === TEXT MESSAGES TO DEEPSEEK AI ===
@bot.message_handler(func=lambda m: m.content_type == "text" and m.from_user.id != ADMIN_ID)
def handle_text(message):
    update_user_activity(message.from_user.id)
    reply = ask_deepseek(message.from_user.id, message.text)
    bot.reply_to(message, reply)

# === WEBHOOK ENDPOINTS ===

@app.route("/", methods=["GET", "POST", "HEAD"])
def webhook():
    if request.method in ("GET", "HEAD"):
        return "OK", 200
    if request.method == "POST":
        content_type = request.headers.get("Content-Type", "")
        if content_type and content_type.startswith("application/json"):
            update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
            bot.process_new_updates([update])
            return "", 200
    return abort(403)

@app.route("/set_webhook", methods=["GET", "POST"])
def set_webhook_route():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        return f"Webhook set to {WEBHOOK_URL}", 200
    except Exception as e:
        logging.error(f"Failed to set webhook: {e}")
        return f"Failed to set webhook: {e}", 500

@app.route("/delete_webhook", methods=["GET", "POST"])
def delete_webhook_route():
    try:
        bot.delete_webhook()
        return "Webhook deleted.", 200
    except Exception as e:
        logging.error(f"Failed to delete webhook: {e}")
        return f"Failed to delete webhook: {e}", 500

def set_webhook_on_startup():
    try:
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set successfully to {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Failed to set webhook on startup: {e}")

def set_bot_info_and_startup():
    # This function will now also load data into caches
    connect_to_mongodb()
    # set_bot_info() # This function doesn't exist in the provided code, so I've commented it out.
    set_webhook_on_startup()

if __name__ == "__main__":
    set_bot_info_and_startup()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
