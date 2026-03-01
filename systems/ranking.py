# systems/ranking.py
# ✅ Painel único + Ranking em "cartão gamer" (imagem) + Atualização automática (sem duplicar imagem)
# Requisitos: pillow (Pillow) no requirements.txt
# IDs: config/ids.py -> GUILD_ID, CANAL_PAINEL_RANK

import discord
from discord.ext import commands, tasks
import config.ids as ids

import os
import json
import time
from io import BytesIO
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont, ImageFilter

ARQ_RANK = "data/ranking.json"

# ========= CONFIG =========
TOP_N = 10
ATUALIZAR_A_CADA_MIN = 5

XP_POR_MSG = 10
COOLDOWN_XP_SEG = 30

# Visual
COR_EMBED = 0x8E44AD
ARQ_IMAGEM = "ranking.png"  # attachment filename


# =========================
# Storage
# =========================
def garantir_pasta():
    os.makedirs(os.path.dirname(ARQ_RANK), exist_ok=True)


def carregar():
    garantir_pasta()
    if not os.path.exists(ARQ_RANK):
        return {"xp": {}, "panel_message_id": None}
    try:
        with open(ARQ_RANK, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("xp", {})
        data.setdefault("panel_message_id", None)
        return data
    except:
        return {"xp": {}, "panel_message_id": None}


def salvar(data):
    garantir_pasta()
    with open(ARQ_RANK, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def medalha(pos: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(pos, f"#{pos}")


# =========================
# Gamer Card (Pillow)
# =========================
def _load_font(size: int) -> ImageFont.ImageFont:
    # fontes comuns em linux
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except:
            pass
    return ImageFont.load_default()


def _round_rect(draw: ImageDraw.ImageDraw, xy, r, fill=None, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    t = (text or "").strip()
    if draw.textlength(t, font=font) <= max_w:
        return t
    while t and draw.textlength(t + "…", font=font) > max_w:
        t = t[:-1]
    return (t + "…") if t else ""


def _gradient_bg(w: int, h: int) -> Image.Image:
    base = Image.new("RGB", (w, h), (18, 10, 36))
    top = Image.new("RGB", (w, h), (120, 55, 170))
    mask = Image.new("L", (w, h))
    md = ImageDraw.Draw(mask)
    for y in range(h):
        v = int(255 * (y / (h - 1)))
        md.line([(0, y), (w, y)], fill=v)
    bg = Image.composite(top, base, mask)

    # vinheta
    vign = Image.new("L", (w, h), 0)
    vd = ImageDraw.Draw(vign)
    vd.ellipse([-w * 0.2, -h * 0.25, w * 1.2, h * 1.25], fill=255)
    vign = vign.filter(ImageFilter.GaussianBlur(70))
    bg = Image.composite(bg, Image.new("RGB", (w, h), (8, 6, 16)), Image.eval(vign, lambda p: 255 - p))
    return bg


async def render_ranking_card(
    guild: discord.Guild,
    ranking: list[tuple[int, int]],
    top_n: int,
) -> BytesIO:
    W, H = 980, 380
    img = _gradient_bg(W, H).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # fontes
    f_title = _load_font(38)
    f_sub = _load_font(18)
    f_line = _load_font(20)
    f_small = _load_font(16)

    # painel "glass"
    panel = Image.new("RGBA", (W - 60, H - 60), (255, 255, 255, 18))
    img.alpha_composite(panel, (30, 30))
    _round_rect(draw, (30, 30, W - 30, H - 30), 26, outline=(255, 255, 255, 40), width=2)

    # header
    draw.text((60, 55), "TOP ATIVOS", font=f_title, fill=(255, 255, 255, 235))
    # Linha neon decorativa
    draw.line((60, 100, 430, 100), fill=(180, 90, 255, 220), width=4)
    srv = _fit_text(draw, guild.name.upper(), f_sub, 640)
    draw.text((62, 102), srv, font=f_sub, fill=(255, 255, 255, 190))

    # badge total
    total = len(ranking)
    badge_text = f"{total} membros ranqueados"
    bx, by = 60, 132
    bw = int(draw.textlength(badge_text, font=f_small)) + 24
    _round_rect(draw, (bx, by, bx + bw, by + 28), 14, fill=(0, 0, 0, 95))
    draw.text((bx + 12, by + 6), badge_text, font=f_small, fill=(255, 255, 255, 210))

    # ícone do servidor (círculo)
    try:
        if guild.icon:
            asset = guild.icon.with_size(128)
            raw = await asset.read()
            icon = Image.open(BytesIO(raw)).convert("RGBA").resize((110, 110))

            mask = Image.new("L", (110, 110), 0)
            md = ImageDraw.Draw(mask)
            md.ellipse((0, 0, 110, 110), fill=255)
            icon.putalpha(mask)

            img.alpha_composite(icon, (W - 170, 70))
            _round_rect(draw, (W - 176, 64, W - 54, 186), 30, outline=(255, 255, 255, 40), width=2)
    except:
        pass

    # lista top
    start_y = 175
    line_h = 30
    max_bar = 360
    left_x = 60

    max_xp = ranking[0][1] if ranking else 1

    for i, (uid, xp) in enumerate(ranking[:top_n], start=1):
        m = guild.get_member(uid)
        name = m.display_name if m else f"Usuário {uid}"
        name = _fit_text(draw, name, f_line, 240)

        y = start_y + (i - 1) * line_h

        # tag
        tag = medalha(i)
        draw.text((left_x, y), tag, font=f_line, fill=(255, 255, 255, 230))
        draw.text((left_x + 55, y), name, font=f_line, fill=(255, 255, 255, 230))

        # barras
        frac = 0 if max_xp <= 0 else (xp / max_xp)
        bar_w = max(2, int(max_bar * frac)) if xp > 0 else 0

        _round_rect(draw, (left_x + 320, y + 6, left_x + 320 + max_bar, y + 22), 9, fill=(0, 0, 0, 95))
        if bar_w > 0:
            _round_rect(draw, (left_x + 320, y + 6, left_x + 320 + bar_w, y + 22), 9, fill=(180, 90, 255, 185))

        # xp
        draw.text((left_x + 320 + max_bar + 18, y), fmt_int(xp), font=f_line, fill=(255, 255, 255, 210))

    # rodapé
    now_txt = datetime.now().strftime("%d/%m • %H:%M")
    draw.text((60, H - 70), f"Atualiza a cada {ATUALIZAR_A_CADA_MIN} min • {now_txt}",
              font=f_small, fill=(255, 255, 255, 170))

    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


# =========================
# Cog
# =========================
class RankingSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = carregar()
        self.last_gain: dict[int, float] = {}
        self.atualizar_painel_loop.start()

    def cog_unload(self):
        self.atualizar_painel_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        await self.garantir_painel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        now = time.time()
        last = self.last_gain.get(message.author.id, 0)
        if now - last < COOLDOWN_XP_SEG:
            return
        self.last_gain[message.author.id] = now

        uid = str(message.author.id)
        self.data["xp"][uid] = int(self.data["xp"].get(uid, 0)) + XP_POR_MSG
        salvar(self.data)

    @tasks.loop(minutes=ATUALIZAR_A_CADA_MIN)
    async def atualizar_painel_loop(self):
        await self.garantir_painel()
        await self.atualizar_painel()

    @atualizar_painel_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    def _get_guild_channel(self):
        guild_id = int(ids.GUILD_ID)
        canal_id = int(ids.CANAL_PAINEL_RANK)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            print(f"❌ Não achei a guild. Verifique GUILD_ID: {guild_id}")
            return None, None

        canal = guild.get_channel(canal_id)
        if not canal:
            print(f"❌ Não achei o canal. Verifique CANAL_PAINEL_RANK: {canal_id}")
            return guild, None

        return guild, canal

    def _get_sorted_ranking(self) -> list[tuple[int, int]]:
        xp_map = self.data.get("xp", {})
        items = [(int(uid), int(xp)) for uid, xp in xp_map.items()]
        items.sort(key=lambda x: x[1], reverse=True)
        return items

    def montar_embed(self, guild: discord.Guild) -> discord.Embed:
        ranking = self._get_sorted_ranking()
        total = len(ranking)

        desc = (
            f"📌 **Servidor:** {guild.name}\n"
            f"👥 **Membros ranqueados:** `{total}`\n"
            f"🕒 **Atualiza:** a cada `{ATUALIZAR_A_CADA_MIN}` minutos\n\n"
            f"💡 *Ganhe XP conversando. Anti-spam: +{XP_POR_MSG} XP a cada {COOLDOWN_XP_SEG}s.*"
        )

        e = discord.Embed(
            title="🏆 RANK TOP ATIVOS",
            description=desc,
            color=COR_EMBED
        )
        # ✅ imagem vem do attachment
        e.set_thumbnail(url="https://i.imgur.com/tF85i5l.png")
        e.set_image(url=f"attachment://{ARQ_IMAGEM}")
        e.set_footer(text="Painel automático • Não apague esta mensagem")
        return e

    async def garantir_painel(self):
        guild, canal = self._get_guild_channel()
        if not guild or not canal:
            return

        msg_id = self.data.get("panel_message_id")
        if msg_id:
            try:
                await canal.fetch_message(int(msg_id))
                return
            except discord.NotFound:
                pass
            except discord.Forbidden:
                print("❌ Falta permissão: Read Message History no canal do painel.")
                return
            except Exception as e:
                print("⚠️ Erro ao buscar mensagem do painel:", e)
                return

        ranking = self._get_sorted_ranking()
        card = await render_ranking_card(guild, ranking, TOP_N)
        file = discord.File(fp=card, filename=ARQ_IMAGEM)

        embed = self.montar_embed(guild)

        # ✅ cria com attachment correto
        msg = await canal.send(embed=embed, file=file)

        self.data["panel_message_id"] = msg.id
        salvar(self.data)

    async def atualizar_painel(self):
        guild, canal = self._get_guild_channel()
        if not guild or not canal:
            return

        msg_id = self.data.get("panel_message_id")
        if not msg_id:
            return

        try:
            msg = await canal.fetch_message(int(msg_id))
        except discord.NotFound:
            # Se apagaram, não recria automaticamente (pra não spammar)
            print("⚠️ Painel apagado manualmente. Use comando para recriar.")
            self.data["panel_message_id"] = None
            salvar(self.data)
            return
        except discord.Forbidden:
            print("❌ Falta permissão: Read Message History no canal do painel.")
            return
        except Exception as e:
            print("⚠️ Erro ao buscar msg do painel:", e)
            return

        ranking = self._get_sorted_ranking()
        card = await render_ranking_card(guild, ranking, TOP_N)
        file = discord.File(fp=card, filename=ARQ_IMAGEM)

        embed = self.montar_embed(guild)

        # ✅ substitui o attachment sem mandar "segunda imagem" no canal
        try:
            await msg.edit(embed=embed, attachments=[file])
        except TypeError:
            # Se a build não suportar attachments=[file], recria a mensagem (SEM spammar)
            try:
                await msg.delete()
            except:
                pass
            msg2 = await canal.send(embed=embed, file=file)
            self.data["panel_message_id"] = msg2.id
            salvar(self.data)

    # ✅ comando admin pra recriar painel quando quiser
    @commands.command(name="recriar_rank")
    @commands.has_permissions(administrator=True)
    async def recriar_rank(self, ctx: commands.Context):
        self.data["panel_message_id"] = None
        salvar(self.data)
        await self.garantir_painel()
        await ctx.reply("✅ Painel de ranking recriado.", mention_author=False)

    # (Opcional) comando pra ver o cartão na hora
    @commands.command(name="top")
    async def cmd_top(self, ctx: commands.Context):
        if not ctx.guild:
            return
        ranking = self._get_sorted_ranking()
        card = await render_ranking_card(ctx.guild, ranking, TOP_N)
        await ctx.reply(file=discord.File(card, filename=ARQ_IMAGEM), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(RankingSystem(bot))