import discord
from discord.ext import commands
import asyncio
import json
import os
from datetime import datetime, timezone

from config.ids import (
    GUILD_ID,
    CARGO_STAFF,
    CARGO_STAFF_APROVADO,
    CANAL_PAINEL_INSCRICAO_STAFF,  # painel público
    CANAL_CARREGAR_FORMS,          # painel staff + onde aparece 1 formulário por vez
    CANAL_LOGS_FORMS,              # logs aprova/reprova/timeout

    CATEGORIA_INSCRICAO_STAFF,     # categoria onde cria as "abas" (canais) de inscrição
)

# =====================
# VISUAL
# =====================
COR_PADRAO = 0x7A1FA2
FOOTER_PADRAO = "Sistema de Inscrição • Akira Roleplay"
IMG_THUMB = "https://i.imgur.com/tF85i5l.png"
IMG_BANNER = "https://i.imgur.com/nxnvh7d.png"

# =====================
# CONFIG
# =====================
TIMEOUT_PERGUNTA = 600  # 10 minutos por pergunta
DB_PATH = "staff_forms_db.json"

# =====================
# PERGUNTAS (edite)
# =====================
FORM_QUESTIONS = [
    "Qual é o seu nome no RP?",
    "Qual é o seu ID?",
    "Qual sua idade?",
    "Qual seu horário disponível (dias/horas)?",
    "Você já foi staff antes? Se sim, onde e qual função?",
    "Por que você quer entrar para a staff?",
    "Conte um pouco sobre você / experiência / maturidade.",
]

# =====================
# Utils
# =====================
def utc_now():
    return datetime.now(timezone.utc)

def fmt_utc(dt: datetime):
    return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M:%S")

def load_db():
    if not os.path.exists(DB_PATH):
        return {"last_id": 0, "forms": {}}
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(db):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def is_staff(member: discord.Member, guild: discord.Guild) -> bool:
    role = guild.get_role(CARGO_STAFF)
    return bool(role and role in member.roles)

def make_panel_embed(title: str, desc: str) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=COR_PADRAO)
    if IMG_THUMB:
        e.set_thumbnail(url=IMG_THUMB)
    if IMG_BANNER:
        e.set_image(url=IMG_BANNER)
    e.set_footer(text="Akira Roleplay © All rights reserved")
    return e

def make_log_embed(title: str) -> discord.Embed:
    e = discord.Embed(title=title, color=COR_PADRAO)
    e.set_footer(text=FOOTER_PADRAO)
    return e

# =====================
# Embed do formulário (colapsado/expandido)
# =====================
def build_application_embed(form_obj: dict, expanded: bool) -> discord.Embed:
    status = form_obj["status"]
    user_id = form_obj["user_id"]
    fid = form_obj["id"]
    created_at = datetime.fromisoformat(form_obj["created_at"])

    title = "📝 Inscrição • PENDENTE" if status == "PENDENTE" else f"📌 Inscrição • {status}"
    e = discord.Embed(title=title, color=COR_PADRAO)

    e.add_field(name="👤 Candidato", value=f"<@{user_id}>\n`{form_obj.get('user_tag','')}`", inline=True)
    e.add_field(name="🆔 Form ID", value=f"`{fid}`", inline=True)
    e.add_field(name="🕒 Enviado (UTC)", value=fmt_utc(created_at), inline=True)

    answers = form_obj.get("answers", [])

    if not expanded:
        # resumo (primeiras 3)
        resumo = []
        for item in answers[:3]:
            resumo.append(f"**{item['q']}** — {item['a']}")
        if len(answers) > 3:
            resumo.append(f"... (+{len(answers)-3} respostas)")
        e.add_field(name="📄 Resumo", value="\n".join(resumo) if resumo else "—", inline=False)
        e.set_footer(text=f"{FOOTER_PADRAO} • FORM:{fid} • MODE:collapsed")
        return e

    # expandido: tudo
    lines = []
    for item in answers:
        lines.append(f"**{item['q']}**\n> {item['a']}")
    content = "\n\n".join(lines)
    if len(content) > 3900:
        content = content[:3900] + "\n\n...(cortado)"

    e.add_field(name="📚 Formulário Completo", value=content or "—", inline=False)
    e.set_footer(text=f"{FOOTER_PADRAO} • FORM:{fid} • MODE:expanded")
    return e

# =====================
# LOG: timeout
# =====================
async def log_timeout(guild: discord.Guild, user: discord.Member, channel: discord.TextChannel):
    log_ch = guild.get_channel(CANAL_LOGS_FORMS)
    if not log_ch:
        return
    e = make_log_embed("⏰ Inscrição cancelada por inatividade")
    e.add_field(name="👤 Usuário", value=f"{user.mention} (`{user}`)", inline=False)
    e.add_field(name="📌 Canal", value=f"`{channel.name}` ({channel.id})", inline=False)
    e.add_field(name="🕒 Data (UTC)", value=fmt_utc(utc_now()), inline=True)
    await log_ch.send(embed=e)

# =====================
# LOG: aprovado/reprovado
# =====================
async def log_decision(guild: discord.Guild, form: dict):
    log_ch = guild.get_channel(CANAL_LOGS_FORMS)
    if not log_ch:
        return

    status = form["status"]
    fid = form["id"]
    user_id = form["user_id"]
    reviewer_id = form.get("reviewer_id", 0)
    reason = form.get("review_reason", "")

    created = datetime.fromisoformat(form["created_at"])
    reviewed = datetime.fromisoformat(form["reviewed_at"])

    e = make_log_embed(f"📌 Formulário #{fid} • {status}")
    e.add_field(name="👤 Candidato", value=f"<@{user_id}> (`{form.get('user_tag','')}`)", inline=False)
    e.add_field(name="🕒 Enviado (UTC)", value=fmt_utc(created), inline=True)
    e.add_field(name="🧑‍⚖️ Revisado por", value=f"<@{reviewer_id}>" if reviewer_id else "—", inline=True)
    e.add_field(name="✅/❌ Decisão (UTC)", value=fmt_utc(reviewed), inline=True)
    e.add_field(name="📝 Motivo", value=f"> {reason or '—'}", inline=False)
    await log_ch.send(embed=e)

# =====================
# Modal motivo decisão (staff) + deletar a msg do formulário após decidir
# =====================
class ReviewReasonModal(discord.ui.Modal):
    def __init__(self, form_id: str, decision: str, review_channel_id: int, review_message_id: int):
        super().__init__(title=f"{decision} • Motivo")
        self.form_id = form_id
        self.decision = decision
        self.review_channel_id = review_channel_id
        self.review_message_id = review_message_id

        self.reason = discord.ui.TextInput(
            label="Informe o motivo",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=600
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild

        # Permissão
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user, guild):
            await interaction.response.send_message("❌ Apenas Staff pode fazer isso.", ephemeral=True, delete_after=10)
            return

        db = load_db()
        form = db["forms"].get(self.form_id)

        if not form:
            await interaction.response.send_message("❌ Formulário não encontrado.", ephemeral=True, delete_after=10)
            return

        if form["status"] != "PENDENTE":
            await interaction.response.send_message("⚠️ Esse formulário já foi revisado.", ephemeral=True, delete_after=10)
            return

        # =========================
        # SALVA DECISÃO
        # =========================
        form["status"] = "APROVADO" if self.decision == "Aprovar" else "REPROVADO"
        form["reviewer_id"] = interaction.user.id
        form["reviewed_at"] = utc_now().isoformat()
        form["review_reason"] = self.reason.value

        db["forms"][self.form_id] = form
        save_db(db)

        # =========================
        # ENVIA DM + ADICIONA CARGO
        # =========================
        try:
            # tenta pegar membro do servidor
            member = guild.get_member(form["user_id"])
            if member is None:
                try:
                    member = await guild.fetch_member(form["user_id"])
                except Exception:
                    member = None

            # objeto User (sempre funciona para DM)
            user_obj = member or await interaction.client.fetch_user(form["user_id"])

            if form["status"] == "APROVADO":
                embed_dm = discord.Embed(
                    title="🎉 Parabéns! Você foi aprovado!",
                    description=(
                        "Sua inscrição para a **Staff** foi aprovada!\n\n"
                        "✅ Parabéns pela conquista!\n"
                        "Você agora faz parte da equipe.\n"
                        "Fique atento às orientações da coordenação."
                    ),
                    color=0x2ecc71
                )

                # adiciona cargo automaticamente
                if member is not None:
                    cargo = guild.get_role(CARGO_STAFF_APROVADO)
                    if cargo is None:
                        raise RuntimeError("CARGO_STAFF_APROVADO não encontrado.")
                    await member.add_roles(cargo, reason="Inscrição staff aprovada")

            else:
                embed_dm = discord.Embed(
                    title="❌ Inscrição não aprovada",
                    description=(
                        "Sua inscrição para a **Staff** foi analisada,\n"
                        "porém não foi aprovada desta vez.\n\n"
                        "📝 Você pode tentar novamente futuramente.\n"
                        "Não desanime!"
                    ),
                    color=0xe74c3c
                )

            embed_dm.add_field(
                name="📝 Motivo",
                value=f"> {form.get('review_reason', '—')}",
                inline=False
            )
            embed_dm.set_footer(text="Akira Roleplay • Sistema de Inscrição")

            await user_obj.send(embed=embed_dm)

        except Exception as e:
            print(f"[STAFF_FORMS] Erro ao enviar DM/adicionar cargo: {repr(e)}")

            # loga erro no canal de logs
            try:
                log_ch = guild.get_channel(CANAL_LOGS_FORMS)
                if log_ch:
                    await log_ch.send(
                        f"⚠️ Não consegui enviar DM/adicionar cargo para <@{form['user_id']}>.\n"
                        f"Erro: `{repr(e)}`"
                    )
            except Exception:
                pass

        # =========================
        # ENVIA LOG
        # =========================
        await log_decision(guild, form)

        # =========================
        # REMOVE MENSAGEM DO FORMULÁRIO
        # =========================
        try:
            ch = guild.get_channel(self.review_channel_id)
            if isinstance(ch, discord.TextChannel):
                msg = ch.get_partial_message(self.review_message_id)
                await msg.delete()
        except Exception:
            pass

        await interaction.response.send_message(
            f"✅ Decisão registrada: **{form['status']}**.\n"
            "📌 O formulário foi removido do canal.",
            ephemeral=True,
            delete_after=12
        )
# =====================
# View do formulário (expandir/recolher + aprovar/reprovar)
# =====================
class ApplicationReviewView(discord.ui.View):
    def __init__(self, form_id: str):
        super().__init__(timeout=None)
        self.form_id = form_id

    @discord.ui.button(label="Expandir", style=discord.ButtonStyle.secondary, emoji="🔼", custom_id="form_expand")
    async def expand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user, interaction.guild):
            await interaction.response.send_message("❌ Apenas Staff.", ephemeral=True, delete_after=8)
            return

        db = load_db()
        form = db["forms"].get(self.form_id)
        if not form:
            await interaction.response.send_message("❌ Formulário não encontrado.", ephemeral=True, delete_after=8)
            return

        await interaction.response.edit_message(embed=build_application_embed(form, expanded=True), view=self)

    @discord.ui.button(label="Recolher", style=discord.ButtonStyle.danger, emoji="🔽", custom_id="form_collapse")
    async def collapse(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user, interaction.guild):
            await interaction.response.send_message("❌ Apenas Staff.", ephemeral=True, delete_after=8)
            return

        db = load_db()
        form = db["forms"].get(self.form_id)
        if not form:
            await interaction.response.send_message("❌ Formulário não encontrado.", ephemeral=True, delete_after=8)
            return

        await interaction.response.edit_message(embed=build_application_embed(form, expanded=False), view=self)

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.success, emoji="✅", custom_id="form_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user, interaction.guild):
            await interaction.response.send_message("❌ Apenas Staff pode aprovar.", ephemeral=True, delete_after=8)
            return

        # abre modal e passa ids para deletar a msg após decidir
        await interaction.response.send_modal(
            ReviewReasonModal(
                form_id=self.form_id,
                decision="Aprovar",
                review_channel_id=interaction.channel.id,
                review_message_id=interaction.message.id
            )
        )

    @discord.ui.button(label="Reprovar", style=discord.ButtonStyle.danger, emoji="❌", custom_id="form_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user, interaction.guild):
            await interaction.response.send_message("❌ Apenas Staff pode reprovar.", ephemeral=True, delete_after=8)
            return

        await interaction.response.send_modal(
            ReviewReasonModal(
                form_id=self.form_id,
                decision="Reprovar",
                review_channel_id=interaction.channel.id,
                review_message_id=interaction.message.id
            )
        )

# =====================
# Fluxo: canal tipo ticket + perguntas
# =====================
async def run_questionnaire(bot: commands.Bot, channel: discord.TextChannel, user: discord.Member):
    answers = []
    guild = channel.guild

    await channel.send(
        f"{user.mention} ✅ **Inscrição iniciada!**\n"
        f"⏳ Você tem **10 minutos por pergunta**. Se não responder, o canal será fechado automaticamente."
    )

    for idx, question in enumerate(FORM_QUESTIONS, start=1):
        await channel.send(f"**Pergunta {idx}/{len(FORM_QUESTIONS)}:** {question}")

        def check(m: discord.Message):
            return (
                m.channel.id == channel.id
                and m.author.id == user.id
                and (m.content and m.content.strip())
            )

        try:
            msg = await bot.wait_for("message", timeout=TIMEOUT_PERGUNTA, check=check)
        except asyncio.TimeoutError:
            await channel.send("⏰ **Tempo esgotado!** Inscrição cancelada por inatividade.")
            await log_timeout(guild, user, channel)
            await channel.delete()
            return

        answers.append({"q": question, "a": msg.content.strip()})

    # finaliza -> salva no DB como pendente
    db = load_db()
    db["last_id"] += 1
    form_id = str(db["last_id"])

    form_obj = {
        "id": int(form_id),
        "user_id": user.id,
        "user_tag": str(user),
        "created_at": utc_now().isoformat(),
        "status": "PENDENTE",
        "reviewer_id": 0,
        "reviewed_at": "",
        "review_reason": "",
        "answers": answers,
    }
    db["forms"][form_id] = form_obj
    save_db(db)

    await channel.send("✅ **Formulário enviado!** Aguarde a análise da staff.")
    await asyncio.sleep(2)

    # fecha a aba (canal) SEM deixar histórico lá
    await channel.delete()

# =====================
# Painel público: abrir inscrição
# =====================
class StaffApplyPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Abrir Inscrição", style=discord.ButtonStyle.primary, emoji="📝", custom_id="open_staff_apply")
    async def open_apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user

        categoria = guild.get_channel(CATEGORIA_INSCRICAO_STAFF)
        if not categoria or not isinstance(categoria, discord.CategoryChannel):
            await interaction.response.send_message("❌ Categoria de inscrição não encontrada.", ephemeral=True, delete_after=10)
            return

        staff_role = guild.get_role(CARGO_STAFF)
        if not staff_role:
            await interaction.response.send_message("❌ Cargo staff não encontrado (CARGO_STAFF).", ephemeral=True, delete_after=10)
            return

        # evita duplicado
        canal_nome = f"inscricao-{user.id}"
        for ch in categoria.channels:
            if isinstance(ch, discord.TextChannel) and ch.name == canal_nome:
                await interaction.response.send_message(
                    f"⚠️ Você já tem uma inscrição aberta: {ch.mention}",
                    ephemeral=True,
                    delete_after=12
                )
                return

        # cria canal privado
        ch = await guild.create_text_channel(
            name=canal_nome,
            category=categoria,
            topic=f"STAFFAPPLY|user={user.id}|created={utc_now().isoformat()}"
        )

        await ch.set_permissions(guild.default_role, view_channel=False)
        await ch.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True)
        await ch.set_permissions(staff_role, view_channel=True, send_messages=True, read_message_history=True)

        await interaction.response.send_message(
            f"✅ Inscrição criada! Vá para: {ch.mention}",
            ephemeral=True,
            delete_after=15
        )

        self.bot.loop.create_task(run_questionnaire(self.bot, ch, user))

# =====================
# Painel staff: carregar / pendentes
# =====================
class StaffReviewPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Carregar Formulário", style=discord.ButtonStyle.primary, emoji="📥", custom_id="load_next_form")
    async def load_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user, interaction.guild):
            await interaction.response.send_message("❌ Apenas Staff.", ephemeral=True, delete_after=8)
            return

        db = load_db()
        pendentes = [(k, f) for k, f in db["forms"].items() if f.get("status") == "PENDENTE"]

        if not pendentes:
            await interaction.response.send_message("✅ Nenhum formulário pendente.", ephemeral=True, delete_after=10)
            return

        # pega o mais antigo
        pendentes.sort(key=lambda x: x[1].get("created_at", ""))
        form_id, form = pendentes[0]

        # posta abaixo do painel (mensagem normal no canal)
        embed = build_application_embed(form, expanded=False)
        await interaction.response.send_message("✅ Formulário carregado abaixo.", ephemeral=True, delete_after=6)
        await interaction.channel.send(embed=embed, view=ApplicationReviewView(form_id))

    @discord.ui.button(label="Formulários Pendentes", style=discord.ButtonStyle.secondary, emoji="📂", custom_id="pending_count")
    async def pending(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user, interaction.guild):
            await interaction.response.send_message("❌ Apenas Staff.", ephemeral=True, delete_after=8)
            return

        db = load_db()
        pending = [f for f in db["forms"].values() if f.get("status") == "PENDENTE"]
        await interaction.response.send_message(f"📌 Pendentes agora: **{len(pending)}**", ephemeral=True, delete_after=10)

# =====================
# COG: auto painéis
# =====================
class StaffFormsSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.iniciado = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self.iniciado:
            return
        self.iniciado = True
        await self.init_panels()

    async def _reset_panel(self, channel: discord.TextChannel, embed: discord.Embed, view: discord.ui.View):
        async for msg in channel.history(limit=50):
            if msg.author == self.bot.user:
                await msg.delete()
        await channel.send(embed=embed, view=view)

    async def init_panels(self):
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            print("❌ Guild não encontrada (GUILD_ID).")
            return

        ch_apply = guild.get_channel(CANAL_PAINEL_INSCRICAO_STAFF)
        ch_review = guild.get_channel(CANAL_CARREGAR_FORMS)
        ch_logs = guild.get_channel(CANAL_LOGS_FORMS)

        if not ch_apply or not ch_review or not ch_logs:
            print("❌ staff_forms: canais não encontrados (IDs).")
            return

        # Painel público
        embed_apply = make_panel_embed(
            "📋 INSCRIÇÃO STAFF",
            "Clique abaixo para iniciar sua inscrição.\n\n"
            "⏳ Você terá **10 minutos por pergunta**.\n"
            "⚠️ Se ficar inativo, a aba será fechada automaticamente."
        )
        await self._reset_panel(ch_apply, embed_apply, StaffApplyPanelView(self.bot))

        # Painel staff (carregar / pendentes)
        embed_review = make_panel_embed(
            "🧾 CARREGAR FORMULÁRIOS",
            "Use o painel abaixo para carregar inscrições pendentes.\n\n"
            "📥 **Carregar Formulário**: mostra 1 inscrição abaixo com botões.\n"
            "📂 **Formulários Pendentes**: mostra quantos estão pendentes."
        )
        await self._reset_panel(ch_review, embed_review, StaffReviewPanelView())

        print("✅ Painéis de inscrição staff iniciados.")

async def setup(bot: commands.Bot):
    await bot.add_cog(StaffFormsSystem(bot))

    # Views persistentes
    bot.add_view(StaffApplyPanelView(bot))
    bot.add_view(StaffReviewPanelView())