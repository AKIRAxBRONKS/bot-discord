import discord
from discord.ext import commands
from config.ids import *
import io
from datetime import datetime, timezone
from urllib.parse import quote, unquote
import asyncio

# =====================
# VISUAL DO PAINEL
# =====================
PAINEL_COR = 0x7A1FA2
PAINEL_TITULO = "ATENDIMENTO"
PAINEL_DESCRICAO = (
    "Escolha o tipo de ticket abaixo para abrir.\n\n"
    "📕 **LEIA ANTES DE ABRIR** 📕\n"
    "Não abra um ticket sem necessidade.\n"
    "Não marque excessivamente a equipe."
)

PAINEL_THUMBNAIL = "https://i.imgur.com/tF85i5l.png"
PAINEL_IMAGEM = "https://i.imgur.com/nxnvh7d.png"

# =====================
# VISUAL DO LOG
# =====================
LOG_COR = 0x7A1FA2
LOG_FOOTER = "Sistema de Tickets • Akira Roleplay"

TOPIC_PREFIX = "TICKETMETA|"

# =====================
# TEMPOS (auto delete)
# =====================
TTL_REDIRECIONAR = 180   # msg ephemeral "ticket aberto / ir"
TTL_LINK_FINAL = 25      # msg ephemeral com o link

# =========================
# Helpers (topic metadata)
# =========================
def _safe_encode(value: str, max_len: int = 400) -> str:
    value = (value or "").strip().replace("\n", " ")
    if len(value) > max_len:
        value = value[:max_len] + "..."
    return quote(value, safe="")

def _safe_decode(value: str) -> str:
    try:
        return unquote(value or "")
    except Exception:
        return value or ""

def build_topic(user_id: int, tipo: str, motivo_abertura: str, assumido_por: int | None = None) -> str:
    assignee = assumido_por if assumido_por else 0
    return (
        f"{TOPIC_PREFIX}"
        f"user={user_id}|"
        f"tipo={_safe_encode(tipo, 60)}|"
        f"assignee={assignee}|"
        f"open={_safe_encode(motivo_abertura, 450)}"
    )

def parse_topic(topic: str | None) -> dict:
    if not topic or not topic.startswith(TOPIC_PREFIX):
        return {}
    raw = topic[len(TOPIC_PREFIX):]
    parts = raw.split("|")
    data = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            data[k] = v
    if "tipo" in data:
        data["tipo"] = _safe_decode(data["tipo"])
    if "open" in data:
        data["open"] = _safe_decode(data["open"])
    return data

async def get_display_name(bot: commands.Bot, guild: discord.Guild, user_id: int) -> str:
    if not user_id:
        return "desconhecido"

    m = guild.get_member(user_id)
    if m:
        return m.display_name

    try:
        m = await guild.fetch_member(user_id)
        return m.display_name
    except Exception:
        pass

    try:
        u = await bot.fetch_user(user_id)
        return u.name
    except Exception:
        return str(user_id)

def fmt_user_mention(user_id: int) -> str:
    return f"<@{user_id}>" if user_id else "`desconhecido`"

# =========================
# Transcript TXT
# =========================
async def build_transcript(channel: discord.TextChannel, header_info: str) -> str:
    lines = []
    lines.append(header_info)
    lines.append(f"Canal: #{channel.name} ({channel.id})")
    lines.append(f"Gerado em (UTC): {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M:%S')}")
    lines.append("-" * 72)

    async for msg in channel.history(limit=None, oldest_first=True):
        ts = msg.created_at.replace(tzinfo=timezone.utc).strftime("%d/%m/%Y %H:%M")
        author = f"{msg.author} ({msg.author.id})"
        content = msg.content or ""

        attach_info = ""
        if msg.attachments:
            attach_urls = " | ".join(a.url for a in msg.attachments)
            attach_info = f" [ANEXOS: {attach_urls}]"

        if not content and not attach_info:
            if msg.embeds:
                content = "[EMBED]"
            else:
                content = "[SEM TEXTO]"

        lines.append(f"[{ts}] {author}: {content}{attach_info}")

    lines.append("-" * 72)
    return "\n".join(lines)

# =======================================================
# VIEW: "IR PARA O TICKET" (captura clique e apaga msg)
# =======================================================
class GoToTicketView(discord.ui.View):
    def __init__(self, canal: discord.TextChannel):
        super().__init__(timeout=180)
        self.add_item(
            discord.ui.Button(
                label="Ir para o Ticket",
                style=discord.ButtonStyle.link,
                url=canal.jump_url,
                emoji="➡️"
            )
        )
    async def go_to_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        # apaga a mensagem ephemeral do botão (quando possível)
        try:
            await interaction.response.defer(ephemeral=True)
            await interaction.delete_original_response()
        except Exception:
            pass

        # manda link e some rápido
        await interaction.followup.send(
            f"✅ Aqui está seu ticket: {self.canal.jump_url}",
            ephemeral=True,
            delete_after=TTL_LINK_FINAL
        )

# =========================
# MODAL: MOTIVO DE ABERTURA
# =========================
class OpenReasonModal(discord.ui.Modal, title="Motivo da Abertura do Ticket"):
    motivo = discord.ui.TextInput(
        label="Descreva o motivo",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=600
    )

    def __init__(self, tipo: str):
        super().__init__()
        self.tipo = tipo

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        categoria = guild.get_channel(CATEGORIA_TICKETS)
        if not categoria:
            await interaction.response.send_message("❌ Categoria de tickets não encontrada.", ephemeral=True, delete_after=15)
            return

        cargo_staff = guild.get_role(CARGO_STAFF)
        if not cargo_staff:
            await interaction.response.send_message("❌ Cargo da Staff não encontrado (verifique CARGO_STAFF).", ephemeral=True, delete_after=15)
            return

        # evita ticket duplicado (procura pelo topic)
        for ch in categoria.channels:
            if isinstance(ch, discord.TextChannel):
                meta = parse_topic(ch.topic)
                if meta and int(meta.get("user", "0") or 0) == interaction.user.id:
                    # aqui não dá pra "clicar e apagar" com botão URL; então manda link e auto-some
                    await interaction.response.send_message(
                        f"⚠️ Você já possui um ticket aberto: {ch.jump_url}",
                        ephemeral=True,
                        delete_after=TTL_REDIRECIONAR
                    )
                    return

        # cria canal
        channel_name = f"ticket-{interaction.user.id}"
        canal = await guild.create_text_channel(
            name=channel_name,
            category=categoria,
            topic=build_topic(interaction.user.id, self.tipo, self.motivo.value, assumido_por=None)
        )

        # permissões
        await canal.set_permissions(guild.default_role, view_channel=False)
        await canal.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True)
        await canal.set_permissions(cargo_staff, view_channel=True, send_messages=True, read_message_history=True)

        # mensagem dentro do ticket
        embed = discord.Embed(
            title=f"🎟️ Ticket - {self.tipo}",
            description=(
                f"👤 **Solicitante:** {interaction.user.mention}\n"
                f"📝 **Motivo da abertura:**\n> {self.motivo.value}\n\n"
                "✅ **Staff:** clique em **Assumir Ticket** antes de fechar.\n"
                "🔒 Ao fechar, será obrigatório informar o motivo."
            ),
            color=PAINEL_COR
        )

        await canal.send(
            content=f"{interaction.user.mention} | {cargo_staff.mention}",
            embed=embed,
            view=TicketControlView()
        )

        # ÚNICA mensagem pós-modal: redirecionamento
        await interaction.response.send_message(
            "✅ Ticket criado! Clique para ir até ele.",
            ephemeral=True,
            view=GoToTicketView(canal),
            delete_after=30  # a mensagem some sozinha
        )
# =========================
# VIEW: PAINEL COM TIPOS (SEM MENSAGEM EXTRA)
# =========================
class TicketTypeSelectPanel(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="📌 Selecione o tipo de ticket...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Denúncia", value="Denúncia", emoji="🚨", description="Reportar algo para a Staff"),
                discord.SelectOption(label="VIP", value="VIP", emoji="💎", description="Assuntos relacionados a VIP"),
                discord.SelectOption(label="Suporte", value="Suporte", emoji="🛠️", description="Problemas / Ajuda / Dúvidas"),
                discord.SelectOption(label="Sugestões", value="Sugestões", emoji="💡", description="Enviar ideias e sugestões"),
            ],
            custom_id="ticket_panel_select"
        )

    async def callback(self, interaction: discord.Interaction):
        tipo = self.values[0]

        # (opcional) tenta "limpar" a seleção visual, reeditando a mensagem do painel
        # Isso não apaga nada, só volta o placeholder depois de escolher.
        try:
            await interaction.message.edit(view=TicketPanelView())
        except Exception:
            pass

        # abre direto o modal do motivo
        await interaction.response.send_modal(OpenReasonModal(tipo))


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelectPanel())
# =========================
# CONTROLES DO TICKET (Assumir -> Fechar)
# =========================
class CloseModal(discord.ui.Modal, title="Fechamento do Ticket"):
    motivo = discord.ui.TextInput(
        label="Motivo do fechamento",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=600
    )

    def __init__(self, canal: discord.TextChannel, bot: commands.Bot):
        super().__init__()
        self.canal = canal
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        meta = parse_topic(self.canal.topic)

        requester_id = int(meta.get("user", "0") or 0)
        tipo = meta.get("tipo", "N/A")
        open_reason = meta.get("open", "N/A")
        assignee_id = int(meta.get("assignee", "0") or 0)

        requester_name = await get_display_name(self.bot, guild, requester_id)
        assignee_name = await get_display_name(self.bot, guild, assignee_id) if assignee_id else "ninguém"
        closer_name = interaction.user.display_name

        header = (
            f"TICKET: {tipo}\n"
            f"ABERTO POR: {requester_name} ({requester_id})\n"
            f"ASSUMIDO POR: {assignee_name} ({assignee_id})\n"
            f"FECHADO POR: {closer_name} ({interaction.user.id})\n"
            f"MOTIVO ABERTURA: {open_reason}\n"
            f"MOTIVO FECHAMENTO: {self.motivo.value}\n"
        )

        transcript_text = await build_transcript(self.canal, header)

        safe_name = self.canal.name.replace("/", "-").replace(" ", "-")
        transcript_file = discord.File(
            fp=io.BytesIO(transcript_text.encode("utf-8")),
            filename=f"transcript-{safe_name}.txt"
        )

        log_channel = guild.get_channel(CANAL_LOG_TICKETS)
        closed_unix = int(datetime.now(timezone.utc).timestamp())

        embed = discord.Embed(
            title=f"🎟️ Ticket de {tipo} criado por {requester_name} ➜ # {self.canal.name}",
            color=LOG_COR
        )

        embed.add_field(
            name="📕 HISTÓRICO DO TICKET",
            value=(
                f"🎫 **Ticket:** `{self.canal.name}`\n"
                f"👤 **Aberto por:** {fmt_user_mention(requester_id)}\n"
                f"🧑‍💻 **Assumido por:** {fmt_user_mention(assignee_id) if assignee_id else '`ninguém`'}\n"
                f"🔒 **Fechado por:** {interaction.user.mention}\n\n"
                f"🧾 **Motivo da abertura:**\n> {open_reason}\n\n"
                f"📝 **Motivo do fechamento:**\n> {self.motivo.value}"
            ),
            inline=False
        )

        embed.set_footer(text=f"{LOG_FOOTER} • ⏰ <t:{closed_unix}:f>")

        if log_channel:
            await log_channel.send(embed=embed)
            await log_channel.send(
                content=f"```[{datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}] {interaction.client.user.name}:```",
                file=transcript_file
            )

        await interaction.response.send_message("✅ Ticket fechado e salvo no log.", ephemeral=True, delete_after=12)
        await self.canal.delete()

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Assumir Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🧑‍💻",
        custom_id="assumir_ticket"
    )
    async def assumir(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_staff = interaction.guild.get_role(CARGO_STAFF)
        if not role_staff or role_staff not in interaction.user.roles:
            await interaction.response.send_message("❌ Apenas Staff pode assumir.", ephemeral=True, delete_after=10)
            return

        meta = parse_topic(interaction.channel.topic)
        if not meta:
            await interaction.response.send_message("❌ Este canal não parece ser um ticket válido.", ephemeral=True, delete_after=10)
            return

        current_assignee = int(meta.get("assignee", "0") or 0)
        if current_assignee != 0:
            await interaction.response.send_message("⚠️ Este ticket já foi assumido.", ephemeral=True, delete_after=10)
            return

        requester_id = int(meta.get("user", "0") or 0)
        tipo = meta.get("tipo", "N/A")
        open_reason = meta.get("open", "")

        await interaction.channel.edit(
            topic=build_topic(requester_id, tipo, open_reason, assumido_por=interaction.user.id)
        )

        await interaction.response.send_message("✅ Você assumiu este ticket.", ephemeral=True, delete_after=10)

    @discord.ui.button(
        label="Fechar Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="fechar_ticket"
    )
    async def fechar(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_staff = interaction.guild.get_role(CARGO_STAFF)
        if not role_staff or role_staff not in interaction.user.roles:
            await interaction.response.send_message("❌ Apenas Staff pode fechar.", ephemeral=True, delete_after=10)
            return

        meta = parse_topic(interaction.channel.topic)
        if not meta:
            await interaction.response.send_message("❌ Este canal não parece ser um ticket válido.", ephemeral=True, delete_after=10)
            return

        assignee_id = int(meta.get("assignee", "0") or 0)
        if assignee_id == 0:
            await interaction.response.send_message("⚠️ Antes, clique em **Assumir Ticket**.", ephemeral=True, delete_after=12)
            return

        if interaction.user.id != assignee_id:
            await interaction.response.send_message("❌ Apenas quem assumiu pode fechar.", ephemeral=True, delete_after=12)
            return

        await interaction.response.send_modal(CloseModal(interaction.channel, interaction.client))

# =========================
# SISTEMA (painel automático / reset ao restart)
# =========================
class TicketSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.iniciado = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.iniciado:
            return
        self.iniciado = True
        await self.iniciar_painel()

    async def iniciar_painel(self):
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            print("❌ Guild não encontrada (verifique GUILD_ID).")
            return

        canal = guild.get_channel(CANAL_PAINEL_TICKET)
        if not canal:
            print("❌ Canal do painel não encontrado (verifique CANAL_PAINEL_TICKET).")
            return

        # apaga painéis antigos do bot
        async for msg in canal.history(limit=50):
            if msg.author == self.bot.user:
                await msg.delete()

        embed = discord.Embed(
            title=PAINEL_TITULO,
            description=PAINEL_DESCRICAO,
            color=PAINEL_COR
        )

        if isinstance(PAINEL_THUMBNAIL, str) and PAINEL_THUMBNAIL.startswith("http"):
            embed.set_thumbnail(url=PAINEL_THUMBNAIL)
        if isinstance(PAINEL_IMAGEM, str) and PAINEL_IMAGEM.startswith("http"):
            embed.set_image(url=PAINEL_IMAGEM)

        embed.set_footer(text="AKIRABOTS © All rights reserved.")

        # envia o painel com botões de tipo (sem mensagem extra)
        await canal.send(embed=embed, view=TicketPanelView())
        print("✅ Painel de ticket iniciado")

async def setup(bot):
    await bot.add_cog(TicketSystem(bot))

    # Views persistentes
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())