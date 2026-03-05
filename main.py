import platform
import os
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

print("SYSTEM:", platform.system(), flush=True)
print("ARCH (host):", platform.machine(), flush=True)

logging.basicConfig(level=logging.INFO)

# =====================
# CARREGA VARIÁVEIS
# =====================
load_dotenv()  # ok manter pra rodar local
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise RuntimeError("❌ TOKEN não encontrado. Configure a variável TOKEN (Railway Variables ou .env local).")

# =====================
# INTENTS
# =====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# =====================
# BOT
# =====================
bot = commands.Bot(
    command_prefix=commands.when_mentioned_or(),  # sem prefixo, só mencionando o bot
    intents=intents
)

# =====================
# EVENTOS DE DIAGNÓSTICO (gateway)
# =====================
@bot.event
async def on_connect():
    print("✅ Conectou ao Gateway (on_connect)")

@bot.event
async def on_disconnect():
    print("⚠️ Desconectou do Gateway (on_disconnect)")

@bot.event
async def on_resumed():
    print("🔁 Sessão retomada (on_resumed)")

# =====================
# SETUP AUTOMÁTICO
# =====================
@bot.event
async def setup_hook():
    extensoes = [
        "systems.tickets",
        "systems.ranking",
        "systems.staff_forms",
        "systems.anuncios",
        "systems.welcome_logs",
        "systems.indicacoes",
        "systems.liberar_id",
        "systems.punicoes",
    ]

    for ext in extensoes:
        try:
            await bot.load_extension(ext)
            print(f"✅ Extensão carregada: {ext}")
        except Exception as e:
            print(f"❌ Erro ao carregar {ext}: {type(e).__name__}: {e}")

    # sync slash (se usar)
    try:
        synced = await bot.tree.sync()
        print(f"✅ Slash commands sincronizados: {len(synced)}")
    except Exception as e:
        print(f"⚠️ Não foi possível sincronizar slash commands: {type(e).__name__}: {e}")

# =====================
# BOT READY
# =====================
@bot.event
async def on_ready():
    print(f"🤖 Bot online como {bot.user} (ID: {bot.user.id})")

# =====================
# START
# =====================
bot.run(TOKEN)
