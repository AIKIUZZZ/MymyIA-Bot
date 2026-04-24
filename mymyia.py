import os
import random
import sqlite3
import sys
import threading
import time
from urllib.parse import quote
import telebot
import requests
from flask import Flask, jsonify

# 1. CREDENCIALES
# El .strip() es vital para evitar el error "Token must not contain spaces"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
GROQ_KEY = os.environ.get("GROQ_KEY", "").strip()
# Railway asigna el puerto dinámicamente; lo capturamos aquí
PORT = int(os.environ.get("PORT", 8080))

if not TELEGRAM_TOKEN or not GROQ_KEY:
    print("⚠️ ADVERTENCIA: Faltan secretos. El bot esperará configuración.", flush=True)

bot = telebot.TeleBot(TELEGRAM_TOKEN if TELEGRAM_TOKEN else "DUMMY", threaded=False)
BOT_USERNAME = ""

def init_bot_username():
    global BOT_USERNAME
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "DUMMY":
        return
    try:
        BOT_USERNAME = (bot.get_me().username or "").lower()
    except Exception as e:
        print(f"get_me error: {e}", flush=True)

# 2. PERSONALIDAD
SYSTEM_PROMPT = (
    "Eres MymyIA, una asistente genial creada por AIKIU. "
    "Eres divertida, usas emojis y recuerdas todo sobre el usuario."
)
MAX_MENSAJES = 20
MENSAJE_DEMORA = "⚠️ Estoy experimentando una pequeña demora, por favor intenta de nuevo en un momento"

# 3. BASE DE DATOS
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mymyia.db")
_db_local = threading.local()

def get_db():
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _db_local.conn = conn
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mensajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON mensajes(user_id, id DESC)")
    conn.commit()

def guardar(user_id, role, content):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO mensajes (user_id, role, content, ts) VALUES (?, ?, ?, ?)",
            (user_id, role, content, time.time()),
        )
        conn.commit()
    except Exception as e:
        print(f"DB guardar error: {e}", flush=True)

def cargar(user_id):
    try:
        conn = get_db()
        cur = conn.execute(
            "SELECT role, content FROM mensajes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, MAX_MENSAJES),
        )
        return [{"role": r, "content": c} for r, c in reversed(cur.fetchall())]
    except Exception as e:
        print(f"DB cargar error: {e}", flush=True)
        return []

def borrar(user_id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM mensajes WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception as e:
        print(f"DB borrar error: {e}", flush=True)

# 4. INTELIGENCIA
def mejorar_prompt(prompt):
    if not GROQ_KEY: return prompt
    instr = "Rewrite the user's idea as ONE detailed English paragraph (60-90 words) for AI image generation. Output ONLY the paragraph."
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": instr}, {"role": "user", "content": prompt}]},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except: pass
    return prompt

def hablar_con_ia(user_id, texto):
    if not GROQ_KEY: return "❌ Configura GROQ_KEY en Railway."
    guardar(user_id, "user", texto)
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT}] + cargar(user_id)
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={"model": "llama-3.3-70b-versatile", "messages": mensajes},
            headers=headers, timeout=25
        )
        if r.status_code == 200:
            resp = r.json()["choices"][0]["message"]["content"]
            guardar(user_id, "assistant", resp)
            return resp
    except Exception as e:
        print(f"IA Error: {e}", flush=True)
    return MENSAJE_DEMORA

# 5. COMANDOS TELEGRAM
@bot.message_handler(commands=["start", "help"])
def ayuda(message):
    bot.reply_to(message, "🌟 ¡Hola! Soy MymyIA.\n\n/img [texto] - Crea imágenes\n/reset - Limpia memoria\nEnvíame cualquier mensaje para charlar.")

@bot.message_handler(commands=["reset"])
def reset(message):
    borrar(message.from_user.id)
    bot.reply_to(message, "✨ Memoria limpia.")

@bot.message_handler(commands=["img"])
def imagen(message):
    prompt = message.text.partition(" ")[2].strip()
    if not prompt: return bot.reply_to(message, "Uso: /img gato espacial")
    aviso = bot.reply_to(message, "🎨 Dibujando...")
    try:
        mejorado = mejorar_prompt(prompt)
        seed = random.randint(1, 10**9)
        url = f"https://image.pollinations.ai/prompt/{quote(mejorado)}?model=flux&width=1024&height=1024&seed={seed}&nologo=true"
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            bot.send_photo(message.chat.id, r.content, caption=f"✨ {prompt}")
        else:
            bot.reply_to(message, "❌ Error al generar imagen.")
    except:
        bot.reply_to(message, MENSAJE_DEMORA)
    finally:
        bot.delete_message(message.chat.id, aviso.message_id)

@bot.message_handler(func=lambda m: True)
def chat(message):
    if message.chat.type != "private" and f"@{BOT_USERNAME}" not in (message.text or "").lower():
        return
    texto = (message.text or "").replace(f"@{BOT_USERNAME}", "").strip()
    if not texto: return
    bot.send_chat_action(message.chat.id, "typing")
    bot.reply_to(message, hablar_con_ia(message.from_user.id, texto))

# 6. SERVIDOR WEB Y MANTENIMIENTO
app = Flask(__name__)

@app.route("/")
def home(): return "MymyIA Online 🚀"

@app.route("/healthz")
def healthz(): return jsonify(status="ok"), 200

def run_flask():
    # Es fundamental usar la variable PORT de Railway
    app.run(host="0.0.0.0", port=PORT)

def run_bot_loop():
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "DUMMY": return
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20, skip_pending=True)
        except Exception as e:
            print(f"Bot error: {e}", flush=True)
            time.sleep(5)

if __name__ == "__main__":
    init_db()
    # Iniciar bot en hilo separado
    threading.Thread(target=run_bot_loop, daemon=True).start()
    print(f"🚀 Servidor en puerto {PORT}", flush=True)
    # Flask debe correr en el hilo principal
    run_flask()
    
