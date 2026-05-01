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

# 1. CONFIGURACIÓN E INYECCIÓN DE VARIABLES
# Intentamos obtener las variables de entorno
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
GROQ_KEY = os.environ.get("GROQ_KEY", "").strip()
PORT = int(os.environ.get("PORT", 8080))

# --- VALIDACIÓN CRÍTICA (Fast-Fail) ---
# Si esto falla, el bot no se intenta iniciar, evitando errores de la librería telebot
if not TELEGRAM_TOKEN or ":" not in TELEGRAM_TOKEN:
    print(f"❌ ERROR CRÍTICO: TELEGRAM_TOKEN no configurado o inválido.", flush=True)
    print(f"DEBUG: Valor recibido (longitud {len(TELEGRAM_TOKEN)}): '{TELEGRAM_TOKEN}'", flush=True)
    # Salimos del proceso para que Railway sepa que la configuración es incorrecta
    sys.exit(1)

print("✅ Token validado correctamente. Iniciando MymyIA...", flush=True)

# 2. INICIALIZACIÓN
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)
BOT_USERNAME = ""

def init_bot_username():
    global BOT_USERNAME
    try:
        me = bot.get_me()
        BOT_USERNAME = (me.username or "").lower()
        print(f"🤖 Bot conectado como: @{BOT_USERNAME}", flush=True)
    except Exception as e:
        print(f"❌ Error al conectar con Telegram: {e}", flush=True)

# 3. BASE DE DATOS
DB_PATH = "/tmp/mymyia.db"
_db_local = threading.local()

def get_db():
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
        _db_local.conn = conn
    return conn

def init_db():
    conn = get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS mensajes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, ts REAL)")
    conn.commit()

def guardar(user_id, role, content):
    try:
        get_db().execute("INSERT INTO mensajes (user_id, role, content, ts) VALUES (?, ?, ?, ?)", (user_id, role, content, time.time()))
        get_db().commit()
    except Exception as e: print(f"DB Error guardar: {e}")

def cargar(user_id):
    cur = get_db().execute("SELECT role, content FROM mensajes WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user_id,))
    return [{"role": r, "content": c} for r, c in reversed(cur.fetchall())]

def borrar(user_id):
    get_db().execute("DELETE FROM mensajes WHERE user_id = ?", (user_id,))
    get_db().commit()

# 4. LÓGICA DE IA
def hablar_con_ia(user_id, texto):
    if not GROQ_KEY: return "❌ GROQ_KEY no configurado en Railway."
    guardar(user_id, "user", texto)
    mensajes = [{"role": "system", "content": "Eres MymyIA, una asistente genial."}] + cargar(user_id)
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            json={"model": "llama-3.3-70b-versatile", "messages": mensajes},
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}, timeout=25)
        if r.status_code == 200:
            resp = r.json()["choices"][0]["message"]["content"]
            guardar(user_id, "assistant", resp)
            return resp
    except Exception as e: print(f"IA Error: {e}")
    return "⚠️ Estoy experimentando problemas, intenta luego."

# 5. HANDLERS
@bot.message_handler(commands=["start", "help"])
def ayuda(m): bot.reply_to(m, "🌟 ¡Hola! Soy MymyIA.\n/img [texto] - Crear imágenes\n/reset - Limpiar memoria")

@bot.message_handler(commands=["reset"])
def reset(m): borrar(m.from_user.id); bot.reply_to(m, "✨ Memoria limpia.")

@bot.message_handler(commands=["img"])
def imagen(m):
    prompt = m.text.partition(" ")[2].strip()
    if not prompt: return bot.reply_to(m, "Uso: /img [descripción]")
    url = f"https://image.pollinations.ai/prompt/{quote(prompt)}?model=flux&nologo=true"
    bot.send_photo(m.chat.id, url, caption=f"✨ {prompt}")

@bot.message_handler(func=lambda m: True)
def chat(m):
    if m.chat.type != "private" and f"@{BOT_USERNAME}" not in (m.text or "").lower(): return
    bot.reply_to(m, hablar_con_ia(m.from_user.id, m.text.replace(f"@{BOT_USERNAME}", "").strip()))

# 6. SERVIDOR WEB Y BUCLE
app = Flask(__name__)
@app.route("/")
def home(): return "MymyIA Online 🚀"

def run_bot_loop():
    init_bot_username()
    bot.infinity_polling(timeout=20, long_polling_timeout=20, skip_pending=True)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)
            
