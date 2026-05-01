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
# FORZAMOS la lectura y limpieza de espacios
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
GROQ_KEY = os.environ.get("GROQ_KEY", "").strip()
PORT = int(os.environ.get("PORT", 8080))

# --- DIAGNÓSTICO ESTRICTO ---
print(f"DEBUG INICIO: Token detectado como '{TELEGRAM_TOKEN[:5]}...' (Longitud: {len(TELEGRAM_TOKEN)})", flush=True)

if not TELEGRAM_TOKEN or ":" not in TELEGRAM_TOKEN:
    print(f"❌ ERROR CRÍTICO: El token no es válido o está vacío. Token recibido: '{TELEGRAM_TOKEN}'", flush=True)
    # No paramos el proceso aquí para que el bot no se quede en "Crashed", 
    # pero el bot no se iniciará hasta que la variable esté bien.
    pass 
else:
    print("✅ Token validado correctamente. Iniciando bot...", flush=True)

# Inicializamos el bot solo si el token parece real
bot = telebot.TeleBot(TELEGRAM_TOKEN if ":" in TELEGRAM_TOKEN else "INVALID_TOKEN", threaded=False)
BOT_USERNAME = ""

def init_bot_username():
    global BOT_USERNAME
    if ":" not in TELEGRAM_TOKEN: return
    try:
        me = bot.get_me()
        BOT_USERNAME = (me.username or "").lower()
        print(f"🤖 Bot conectado como: @{BOT_USERNAME}", flush=True)
    except Exception as e:
        print(f"❌ Error al conectar con Telegram (¿Token mal escrito?): {e}", flush=True)

# 2. BASE DE DATOS
DB_PATH = "/tmp/mymyia.db"
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
    conn.execute("CREATE TABLE IF NOT EXISTS mensajes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, ts REAL NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON mensajes(user_id, id DESC)")
    conn.commit()

# (Las funciones guardar, cargar, borrar, mejorar_prompt y hablar_con_ia se mantienen igual)
def guardar(user_id, role, content):
    try:
        conn = get_db()
        conn.execute("INSERT INTO mensajes (user_id, role, content, ts) VALUES (?, ?, ?, ?)", (user_id, role, content, time.time()))
        conn.commit()
    except Exception as e: print(f"DB guardar error: {e}", flush=True)

def cargar(user_id):
    try:
        conn = get_db()
        cur = conn.execute("SELECT role, content FROM mensajes WHERE user_id = ? ORDER BY id DESC LIMIT 20", (user_id,))
        return [{"role": r, "content": c} for r, c in reversed(cur.fetchall())]
    except Exception as e: return []

def borrar(user_id):
    try:
        conn = get_db()
        conn.execute("DELETE FROM mensajes WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception as e: print(f"DB borrar error: {e}", flush=True)

def mejorar_prompt(prompt):
    if not GROQ_KEY: return prompt
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": "Rewrite to detailed image prompt"}, {"role": "user", "content": prompt}]}, timeout=15)
        if r.status_code == 200: return r.json()["choices"][0]["message"]["content"].strip()
    except: pass
    return prompt

def hablar_con_ia(user_id, texto):
    if not GROQ_KEY: return "❌ GROQ_KEY no configurado."
    guardar(user_id, "user", texto)
    mensajes = [{"role": "system", "content": "Eres MymyIA."}] + cargar(user_id)
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            json={"model": "llama-3.3-70b-versatile", "messages": mensajes},
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}, timeout=25)
        if r.status_code == 200:
            resp = r.json()["choices"][0]["message"]["content"]
            guardar(user_id, "assistant", resp)
            return resp
    except Exception as e: print(f"IA Error: {e}", flush=True)
    return "⚠️ Demora, intenta luego."

# 5. COMANDOS TELEGRAM
@bot.message_handler(commands=["start", "help"])
def ayuda(message): bot.reply_to(message, "🌟 MymyIA Online.")

@bot.message_handler(commands=["reset"])
def reset(message): 
    borrar(message.from_user.id)
    bot.reply_to(message, "✨ Memoria limpia.")

@bot.message_handler(commands=["img"])
def imagen(message):
    prompt = message.text.partition(" ")[2].strip()
    if not prompt: return bot.reply_to(message, "Uso: /img [texto]")
    try:
        url = f"https://image.pollinations.ai/prompt/{quote(mejorar_prompt(prompt))}?model=flux&nologo=true"
        bot.send_photo(message.chat.id, url, caption=f"✨ {prompt}")
    except: bot.reply_to(message, "❌ Error.")

@bot.message_handler(func=lambda m: True)
def chat(message):
    if message.chat.type != "private" and f"@{BOT_USERNAME}" not in (message.text or "").lower(): return
    bot.reply_to(message, hablar_con_ia(message.from_user.id, message.text.replace(f"@{BOT_USERNAME}", "").strip()))

# 6. SERVIDOR WEB
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
    
