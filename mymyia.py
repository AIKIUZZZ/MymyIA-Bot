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
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_KEY = os.environ.get("GROQ_KEY")
PORT = int(os.environ.get("PORT", "8080"))

# Se eliminó el sys.exit(1) para evitar fallos de construcción en Railway
if not TELEGRAM_TOKEN or not GROQ_KEY:
    print("⚠️ ADVERTENCIA: Faltan secretos (TELEGRAM_TOKEN o GROQ_KEY). El bot no funcionará correctamente hasta configurarlos.")

bot = telebot.TeleBot(TELEGRAM_TOKEN if TELEGRAM_TOKEN else "DUMMY", threaded=False)
BOT_USERNAME = ""

def init_bot_username():
    global BOT_USERNAME
    if not TELEGRAM_TOKEN:
        return
    try:
        BOT_USERNAME = (bot.get_me().username or "").lower()
    except Exception as e:
        print(f"get_me error: {e}", flush=True)

# 2. PERSONALIDAD
SYSTEM_PROMPT = (
    "Eres MymyIA, una asistente genial creada por AIKIU. "
    "Eres divertida, usas emojis y recuerdas todo sobre el usuario. "
)
MAX_MENSAJES = 20
MENSAJE_DEMORA = "⚠️ Estoy experimentando una pequeña demora, por favor intenta de nuevo en un momento"

# 3. BASE DE DATOS (WAL + thread-local)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mymyia.db")
_db_local = threading.local()

def get_db():
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
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
    if not GROQ_KEY:
        return prompt
    instr = (
        "You are an expert prompt engineer for AI image generation. "
        "Rewrite the user's idea as ONE detailed English paragraph (60-90 words) "
        "with subject, style, composition, lighting, colors, mood and quality. "
        "Output ONLY the paragraph."
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "system", "content": instr},
                               {"role": "user", "content": prompt}]},
            timeout=20,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"mejorar_prompt error: {e}", flush=True)
    return prompt

def hablar_con_ia(user_id, texto):
    if not GROQ_KEY:
        return "❌ Error: GROQ_KEY no configurada."
    guardar(user_id, "user", texto)
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT}] + cargar(user_id)
    payload = {"model": "llama-3.3-70b-versatile", "messages": mensajes}
    headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}

    for _ in range(2):
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload, headers=headers, timeout=25,
            )
            if r.status_code == 200:
                resp = r.json()["choices"][0]["message"]["content"]
                guardar(user_id, "assistant", resp)
                return resp
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5)
                continue
            print(f"Groq {r.status_code}: {r.text[:200]}", flush=True)
            return MENSAJE_DEMORA
        except requests.Timeout:
            print("Groq timeout", flush=True)
        except Exception as e:
            print(f"Groq error: {e}", flush=True)
            break
    return MENSAJE_DEMORA

# 5. COMANDOS
@bot.message_handler(commands=["start", "help"])
def ayuda(message):
    texto = (
        "Hola, soy MymyIA. Esto es lo que puedo hacer:\n\n"
        "/img [descripcion] - genero una imagen a partir de tu texto\n"
        "/reset - borro nuestro historial y empezamos de cero\n"
        "/help - muestro la lista de comandos\n\n"
        "También puedes escribirme cualquier cosa y te responderé recordando los últimos mensajes."
    )
    bot.reply_to(message, texto)

@bot.message_handler(commands=["reset"])
def reset(message):
    borrar(message.from_user.id)
    bot.reply_to(message, "✨ Memoria limpia. ¡Hola de nuevo!")

@bot.message_handler(commands=["img"])
def imagen(message):
    prompt = message.text.partition(" ")[2].strip()
    if not prompt:
        return bot.reply_to(message, "Uso: /img [descripcion]")
    aviso = bot.reply_to(message, "🎨 diseñando tu imagen en alta definición...")
    try:
        mejorado = mejorar_prompt(prompt)
        seed = random.randint(1, 1_000_000_000)
        url = (f"https://image.pollinations.ai/prompt/{quote(mejorado)}"
               f"?model=flux&width=1024&height=1024&seed={seed}&nologo=true")
        r = requests.get(url, timeout=180)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            bot.send_photo(message.chat.id, r.content, caption=prompt)
        else:
            bot.reply_to(message, MENSAJE_DEMORA)
    except Exception as e:
        print(f"/img error: {e}", flush=True)
        bot.reply_to(message, MENSAJE_DEMORA)
    finally:
        try:
            bot.delete_message(message.chat.id, aviso.message_id)
        except Exception:
            pass

def es_para_mi(message):
    texto = message.text or ""
    if message.chat.type == "private":
        return True, texto
    if BOT_USERNAME and f"@{BOT_USERNAME}" in texto.lower():
        idx = texto.lower().find(f"@{BOT_USERNAME}")
        limpio = (texto[:idx] + texto[idx + len(BOT_USERNAME) + 1:]).strip()
        return True, limpio or texto
    return False, texto

@bot.message_handler(func=lambda m: True)
def chat(message):
    para_mi, texto = es_para_mi(message)
    if not para_mi or not texto:
        return
    try:
        bot.send_chat_action(message.chat.id, "typing")
        bot.reply_to(message, hablar_con_ia(message.from_user.id, texto))
    except Exception as e:
        print(f"chat error: {e}", flush=True)
        try:
            bot.reply_to(message, MENSAJE_DEMORA)
        except Exception:
            pass

# 6. SERVIDOR WEB
app = Flask(__name__)

@app.route("/")
def home():
    return "MymyIA Online 🚀"

@app.route("/healthz")
def healthz():
    return jsonify(status="ok", service="mymyia"), 200

@app.route("/ping")
def ping():
    return "pong"

def run_flask():
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)

def auto_keep_alive():
    while True:
        time.sleep(240)
        try:
            requests.get(f"http://127.0.0.1:{PORT}/ping", timeout=5)
        except Exception:
            pass

def run_bot_loop():
    if not TELEGRAM_TOKEN:
        print("Bot loop no iniciado: falta TELEGRAM_TOKEN")
        return
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20)
        except Exception as e:
            print(f"⚠️ Polling error: {e}. Reintentando...", flush=True)
            time.sleep(3)

def start_bot_async():
    init_bot_username()
    run_bot_loop()

if __name__ == "__main__":
    init_db()
    threading.Thread(target=start_bot_async, daemon=True).start()
    threading.Thread(target=auto_keep_alive, daemon=True).start()
    print(f"🚀 MymyIA ONLINE (web :{PORT})", flush=True)
    run_flask()
        
