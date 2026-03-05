import platform, struct, os

print("SYSTEM:", platform.system(), flush=True)
print("ARCH (host):", platform.machine(), flush=True)

# =====================
# DIAGNÓSTICO CLOUDFLARED (opcional, mas ajuda)
# =====================
path = "./cloudflared"
if not os.path.exists(path):
    print("cloudflared: NÃO ENCONTRADO", flush=True)
else:
    with open(path, "rb") as f:
        head = f.read(64)

    if head[:4] != b"\x7fELF":
        print("cloudflared: NÃO É ELF (arquivo errado/corrompido). Primeiros bytes:", head[:8], flush=True)
    else:
        ei_class = head[4]  # 1=32bit, 2=64bit
        ei_data  = head[5]  # 1=little, 2=big
        endian = "<" if ei_data == 1 else ">"
        e_machine = struct.unpack(endian + "H", head[18:20])[0]

        mach_map = {
            62:  "x86_64 (amd64)",
            183: "aarch64 (arm64)",
            40:  "arm (32-bit)",
        }
        print(
            f"cloudflared: ELF ok | class={ei_class} data={ei_data} "
            f"e_machine={e_machine} => {mach_map.get(e_machine, 'desconhecido')}",
            flush=True
        )

import subprocess
import discord
from discord.ext import commands
from dotenv import load_dotenv

# =====================
# CARREGA VARIÁVEIS
# =====================
load_dotenv()  # ok manter pra rodar local
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise RuntimeError("❌ TOKEN não encontrado. Configure a variável TOKEN (Railway Variables ou .env local).")

# =====================
# CLOUDFLARED (TUNNEL)
# =====================
# Expor sua API local (http://127.0.0.1:35555) pra um link público trycloudflare
CLOUDFLARED_BIN = "./cloudflared"
CLOUDFLARED_URL = "http://127.0.0.1:35555"

_cloudflared_proc = None


def start_cloudflared():
    """
    Inicia o cloudflared e imprime o link trycloudflare no console.
    Não trava o bot se falhar.
    """
    global _cloudflared_proc

    # se já está rodando, não inicia outro
    if _cloudflared_proc and _cloudflared_proc.poll() is None:
        print("ℹ️ Cloudflared já está rodando.")
        return

    if not os.path.exists(CLOUDFLARED_BIN):
        print(f"⚠️ Cloudflared não encontrado em {CLOUDFLARED_BIN}.")
        print("   👉 Suba o arquivo correto (ex: cloudflared-linux-arm64) e renomeie para 'cloudflared'.")
        return

    try:
        # tenta dar permissão (se o host permitir)
        try:
            os.chmod(CLOUDFLARED_BIN, 0o755)
        except Exception:
            pass

        print("🌐 Iniciando Cloudflared Tunnel...")

        _cloudflared_proc = subprocess.Popen(
            [CLOUDFLARED_BIN, "tunnel", "--no-autoupdate", "--url", CLOUDFLARED_URL],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # ✅ Agora lê até encontrar o LINK (ele aparece algumas linhas depois)
        found = False
        for line in _cloudflared_proc.stdout:
            line = (line or "").strip()
            if line:
                print(f"[cloudflared] {line}")

            # o link geralmente vem como https://xxxx.trycloudflare.com
            if "https://" in line and "trycloudflare.com" in line:
                print("🔥 LINK DO TUNNEL ACIMA 🔥 (copie e coloque no BOT_CHECK_URL do server.lua)")
                found = True
                break

        if not found:
            print("⚠️ Cloudflared iniciou, mas não consegui capturar o link no log (verifique as linhas acima).")

        print("✅ Cloudflared iniciado (background).")

    except Exception as e:
        print(f"❌ Erro ao iniciar cloudflared: {type(e).__name__}: {e}")
        _cloudflared_proc = None


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
