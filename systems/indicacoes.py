# systems/indicacoes.py
# ✅ Painel único no canal de marcação + limpa tudo ao registrar
# ✅ Ranking único em "cartão gamer" (imagem) + atualiza só quando houver nova indicação
# Requisitos: pillow (Pillow) no requirements.txt
# IDs: config/ids.py -> GUILD_ID, CANAL_INDICACOES, CANAL_PAINEL_INDICACOES

import discord
from discord.ext import commands
import config.ids as ids

import os
import json
from io import BytesIO
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont, ImageFilter

ARQ_IND = "data/indicacoes.json"

# ========= CONFIG =========
TOP_N = 10

# Visual
COR_EMBED = 0x8E44AD
ARQ_IMAGEM = "indicacoes.png"  # attachment filename
THUMB_URL = "https://i.imgur.com/tF85i5l.png"  # sua logo pequena (thumbnail)
IMG_PAINEL_URL = "https://i.imgur.com/tF85i5l.png"  # imagem grande do painel (opcional)


# =========================
# Storage
# =========================
def garantir_pasta():
    os.makedirs(os.path.dirname(ARQ_IND), exist_ok=True)


def carregar():
    garantir_pasta()
    if not os.path.exists(ARQ_IND):
        return {
            "counts": {},                 # { indicador_id(str): int }
            "who_indicated_me": {},       # { membro_id(str): indicador_id(str) } (anti-duplicado)
            "panel_rank_message_id": None,
            "panel_info_message_id": None,
        }
    try:
        with open(ARQ_IND, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("counts", {})
        data.setdefault("who_indicated_me", {})
        data.setdefault("panel_rank_message_id", None)
        data.setdefault("panel_info_message_id", None)
        return data
    except:
        return {
            "counts": {},
            "who_indicated_me": {},
            "panel_rank_message_id": None,
            "panel_info_message_id": None,
        }


def salvar(data):
    garantir_pasta()
    with open(ARQ_IND, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def medalha(pos: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(pos, f"#{pos}")


# =========================
# Gamer Card (Pillow) - igual ao seu estilo
# =========================
def _load_font(size: int) -> ImageFont.ImageFont:
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

    vign = Image.new("L", (w, h), 0)
    vd = ImageDraw.Draw(vign)
    vd.ellipse([-w * 0.2, -h * 0.25, w * 1.2, h * 1.25], fill=255)
    vign = vign.filter(ImageFilter.GaussianBlur(70))
    bg = Image.composite(bg, Image.new("RGB", (w, h), (8, 6, 16)), Image.eval(vign, lambda p: 255 - p))
    return bg


async def render_indicacoes_card(
    guild: discord.Guild,
    ranking: list[tuple[int, int]],
    top_n: int,
) -> BytesIO:
    W, H = 980, 380
    img = _gradient_bg(W, H).convert("RGBA")
    draw = ImageDraw.Draw(img)

    f_title = _load_font(38)
    f_sub = _load_font(18)
    f_line = _load_font(20)
    f_small = _load_font(16)

    panel = Image.new("RGBA", (W - 60, H - 60), (255, 255, 255, 18))
    img.alpha_composite(panel, (30, 30))
    _round_rect(draw, (30, 30, W - 30, H - 30), 26, outline=(255, 255, 255, 40), width=2)

    draw.text((60, 55), "RANK DE INDICAÇÕES", font=f_title, fill=(255, 255, 255, 235))
    draw.line((60, 100, 520, 100), fill=(255, 210, 80, 230), width=4)
    srv = _fit_text(draw, guild.name.upper(), f_sub, 640)
    draw.text((62, 102), srv, font=f_sub, fill=(255, 255, 255, 190))

    total = len(ranking)
    badge_text = f"{total} participantes"
    bx, by = 60, 132
    bw = int(draw.textlength(badge_text, font=f_small)) + 24
    _round_rect(draw, (bx, by, bx + bw, by + 28), 14, fill=(0, 0, 0, 95))
    draw.text((bx + 12, by + 6), badge_text, font=f_small, fill=(255, 255, 255, 210))

    # ícone do servidor
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

    start_y = 175
    line_h = 30
    max_bar = 360
    left_x = 60

    max_val = ranking[0][1] if ranking else 1

    for i, (uid, val) in enumerate(ranking[:top_n], start=1):
        m = guild.get_member(uid)
        name = m.display_name if m else f"Usuário {uid}"
        name = _fit_text(draw, name, f_line, 240)
        y = start_y + (i - 1) * line_h

        tag = medalha(i)
        draw.text((left_x, y), tag, font=f_line, fill=(255, 255, 255, 230))
        draw.text((left_x + 55, y), name, font=f_line, fill=(255, 255, 255, 230))

        frac = 0 if max_val <= 0 else (val / max_val)
        bar_w = max(2, int(max_bar * frac)) if val > 0 else 0

        _round_rect(draw, (left_x + 320, y + 6, left_x + 320 + max_bar, y + 22), 9, fill=(0, 0, 0, 95))
        if bar_w > 0:
            _round_rect(draw, (left_x + 320, y + 6, left_x + 320 + bar_w, y + 22), 9, fill=(255, 210, 80, 200))

        draw.text((left_x + 320 + max_bar + 18, y), fmt_int(val), font=f_line, fill=(255, 255, 255, 210))

    now_txt = datetime.now().strftime("%d/%m • %H:%M")
    draw.text((60, H - 70), f"Atualizado em {now_txt}",
              font=f_small, fill=(255, 255, 255, 170))

    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out


# =========================
# Cog
# =========================
class IndicacoesSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = carregar()

    # ---------- helpers ----------
    def _guild(self) -> discord.Guild | None:
        return self.bot.get_guild(int(ids.GUILD_ID))

    def _chan(self, guild: discord.Guild, cid: int):
        return guild.get_channel(int(cid))

    def _sorted_ranking(self) -> list[tuple[int, int]]:
        mp = self.data.get("counts", {})
        items = [(int(uid), int(v)) for uid, v in mp.items()]
        items.sort(key=lambda x: x[1], reverse=True)
        return items

    async def _fetch_or_find_panel(self, channel: discord.TextChannel, stored_id: int | None, title_match: str):
        """Tenta buscar a msg salva; se não existir, procura no histórico por embed com título."""
        # 1) tenta buscar pelo ID salvo
        if stored_id:
            try:
                return await channel.fetch_message(int(stored_id))
            except:
                pass

        # 2) procura no histórico (evita duplicar caso json tenha sido apagado)
        async for msg in channel.history(limit=50):
            if msg.author.id != self.bot.user.id:
                continue
            if msg.embeds:
                emb = msg.embeds[0]
                if emb.title and title_match.lower() in emb.title.lower():
                    return msg
        return None

    async def _limpar_canal_deixando(self, channel: discord.TextChannel, keep_message_id: int):
        """
        Apaga tudo no canal e deixa só o painel.
        Requer permissão Manage Messages.
        Tenta purge (rápido) e depois tenta deletar o que sobrar.
        """
        def check(m: discord.Message):
            return m.id != keep_message_id

        try:
            # purge apaga só mensagens "recentes" (regra do Discord: <14 dias)
            await channel.purge(limit=200, check=check, bulk=True)
        except:
            pass

        # tenta remover qualquer coisa que ainda esteja lá (inclui mensagens fora do bulk)
        try:
            async for m in channel.history(limit=200):
                if m.id == keep_message_id:
                    continue
                try:
                    await m.delete()
                except:
                    pass
        except:
            pass

    # ---------- embeds ----------
    def montar_embed_info(self, guild: discord.Guild) -> discord.Embed:
        e = discord.Embed(
            title="📣 SISTEMA DE INDICAÇÕES",
            description=(
                "💬 Para registrar uma indicação, basta **marcar (@)** quem te chamou para o servidor.\n\n"
                "✅ **Exemplo:** `@Fulano`\n"
                "⚠️ Você só pode registrar **1 vez**.\n\n"
                "🏆 Cada indicação conta pontos no **ranking oficial de indicações**.\n"
                "🥇 Suba no topo e seja reconhecido pela sua ajuda!"
            ),
            color=COR_EMBED
        )
        e.set_thumbnail(url=THUMB_URL)
        if IMG_PAINEL_URL:
            e.set_image(url=IMG_PAINEL_URL)
        e.set_footer(text="Sistema de Indicações Automático")
        return e

    def montar_embed_rank(self, guild: discord.Guild) -> discord.Embed:
        ranking = self._sorted_ranking()
        total = len(ranking)

        desc = (
            f"📌 **Servidor:** {guild.name}\n"
            f"👥 **Participantes:** `{total}`\n\n"
            f"💡 *Indicações valem pontos. Registre marcando (@) quem te chamou no canal de indicações.*"
        )

        e = discord.Embed(
            title="🏆 RANK DE INDICAÇÕES",
            description=desc,
            color=COR_EMBED
        )
        e.set_thumbnail(url=THUMB_URL)
        e.set_image(url=f"attachment://{ARQ_IMAGEM}")
        e.set_footer(text="Painel automático • Não apague esta mensagem")
        return e

    # ---------- painéis ----------
    async def garantir_painel_info(self):
        guild = self._guild()
        if not guild:
            return
        canal = self._chan(guild, ids.CANAL_INDICACOES)
        if not canal:
            print("❌ Não achei CANAL_INDICACOES")
            return

        msg = await self._fetch_or_find_panel(
            canal,
            self.data.get("panel_info_message_id"),
            "SISTEMA DE INDICAÇÕES"
        )

        # Se não existe, cria
        if not msg:
            embed = self.montar_embed_info(guild)
            msg = await canal.send(embed=embed)

        # salva id
        self.data["panel_info_message_id"] = msg.id
        salvar(self.data)

        # remove duplicados e lixo
        await self._limpar_canal_deixando(canal, msg.id)

    async def garantir_painel_rank(self):
        guild = self._guild()
        if not guild:
            return
        canal = self._chan(guild, ids.CANAL_PAINEL_INDICACOES)
        if not canal:
            print("❌ Não achei CANAL_PAINEL_INDICACOES")
            return

        msg = await self._fetch_or_find_panel(
            canal,
            self.data.get("panel_rank_message_id"),
            "RANK DE INDICAÇÕES"
        )

        # Se não existe, cria
        if not msg:
            ranking = self._sorted_ranking()
            card = await render_indicacoes_card(guild, ranking, TOP_N)
            file = discord.File(fp=card, filename=ARQ_IMAGEM)
            embed = self.montar_embed_rank(guild)
            msg = await canal.send(embed=embed, file=file)

        # salva id
        self.data["panel_rank_message_id"] = msg.id
        salvar(self.data)

        # remove duplicados e lixo
        await self._limpar_canal_deixando(canal, msg.id)

    async def atualizar_ranking(self):
        guild = self._guild()
        if not guild:
            return
        canal = self._chan(guild, ids.CANAL_PAINEL_INDICACOES)
        if not canal:
            return

        msg_id = self.data.get("panel_rank_message_id")
        if not msg_id:
            await self.garantir_painel_rank()
            msg_id = self.data.get("panel_rank_message_id")
            if not msg_id:
                return

        try:
            msg = await canal.fetch_message(int(msg_id))
        except:
            # se sumiu, recria (1 vez) e limpa
            self.data["panel_rank_message_id"] = None
            salvar(self.data)
            await self.garantir_painel_rank()
            return

        ranking = self._sorted_ranking()
        card = await render_indicacoes_card(guild, ranking, TOP_N)
        file = discord.File(fp=card, filename=ARQ_IMAGEM)
        embed = self.montar_embed_rank(guild)

        # troca imagem/attachment sem duplicar no canal
        try:
            await msg.edit(embed=embed, attachments=[file])
        except TypeError:
            # fallback: recria 1 msg e limpa
            try:
                await msg.delete()
            except:
                pass
            msg2 = await canal.send(embed=embed, file=file)
            self.data["panel_rank_message_id"] = msg2.id
            salvar(self.data)

        # garante que não ficou duplicado
        await self._limpar_canal_deixando(canal, int(self.data["panel_rank_message_id"]))

    # ---------- eventos ----------
    @commands.Cog.listener()
    async def on_ready(self):
        # garante 1 painel em cada canal e limpa os canais
        await self.garantir_painel_info()
        await self.garantir_painel_rank()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if message.channel.id != int(ids.CANAL_INDICACOES):
            return

        # garante painel existe (e pega id)
        await self.garantir_painel_info()
        painel_id = int(self.data.get("panel_info_message_id") or 0)

        # não mexe no painel
        if message.id == painel_id:
            return

        # se não tiver exatamente 1 menção -> apaga (pra manter limpo)
        if len(message.mentions) != 1:
            try:
                await message.delete()
            except:
                pass
            # mantém só o painel no canal
            await self._limpar_canal_deixando(message.channel, painel_id)
            return

        indicado_por: discord.Member = message.mentions[0]

        # regras: não pode marcar bot nem a si mesmo
        invalido = indicado_por.bot or (indicado_por.id == message.author.id)

        # já registrou uma vez?
        author_id = str(message.author.id)
        ja_registrou = author_id in self.data.get("who_indicated_me", {})

        # sempre apaga a msg do usuário
        try:
            await message.delete()
        except:
            pass

        # limpa o canal deixando só painel
        await self._limpar_canal_deixando(message.channel, painel_id)

        if invalido or ja_registrou:
            # opcional: DM pro usuário (não suja canal)
            try:
                if invalido:
                    await message.author.send("❌ Indicação inválida: você não pode marcar a si mesmo ou um bot.")
                elif ja_registrou:
                    await message.author.send("⚠️ Você já registrou sua indicação uma vez e não pode alterar.")
            except:
                pass
            return

        # registra
        inviter_id = str(indicado_por.id)
        self.data["who_indicated_me"][author_id] = inviter_id
        self.data["counts"][inviter_id] = int(self.data["counts"].get(inviter_id, 0)) + 1
        salvar(self.data)

        # DM opcional de sucesso
        try:
            await message.author.send(f"✅ Indicação registrada! Você marcou {indicado_por} como quem te chamou.")
        except:
            pass

        # atualiza ranking (somente agora)
        await self.garantir_painel_rank()
        await self.atualizar_ranking()

    # ---------- comandos admin ----------
    @commands.command(name="recriar_indicacoes")
    @commands.has_permissions(administrator=True)
    async def recriar_indicacoes(self, ctx: commands.Context):
        self.data["panel_info_message_id"] = None
        self.data["panel_rank_message_id"] = None
        salvar(self.data)
        await self.garantir_painel_info()
        await self.garantir_painel_rank()
        await ctx.reply("✅ Painéis de indicações recriados e canais limpos.", mention_author=False)

    @commands.command(name="reset_indicacoes")
    @commands.has_permissions(administrator=True)
    async def reset_indicacoes(self, ctx: commands.Context):
        self.data["counts"] = {}
        self.data["who_indicated_me"] = {}
        salvar(self.data)
        await self.atualizar_ranking()
        await ctx.reply("✅ Indicações zeradas e ranking atualizado.", mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(IndicacoesSystem(bot))