# systems/punicoes.py
import asyncio
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import ids as IDS


UTC = timezone.utc


# -------------------------
# Util: parse duração "1d2h30m"
# -------------------------
DUR_RE = re.compile(r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.I)

def parse_duration(text: str) -> int:
    """
    Retorna duração em segundos.
    Aceita: 10m, 2h, 1d2h, 1d 3h 20m, etc.
    """
    text = (text or "").strip().lower()
    if text in ("0", "perma", "perm", "permanente", "permanent"):
        return 0  # 0 = permanente
    m = DUR_RE.fullmatch(text.replace(",", " ").replace(":", " "))
    if not m:
        raise ValueError("Formato inválido. Use tipo: 2h, 1d3h, 30m, perma")
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    total = d * 86400 + h * 3600 + mi * 60 + s
    if total <= 0:
        raise ValueError("Duração precisa ser > 0 (ou 'perma').")
    return total


def dt_to_str(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return discord.utils.format_dt(dt, style="F")


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


# -------------------------
# DB (SQLite)
# -------------------------
class PunishDB:
    def __init__(self, path: str = "data/punicoes.sqlite"):
        self.path = path
        self._init_done = False

    async def init(self):
        if self._init_done:
            return

        # garante pasta data/
        import os
        os.makedirs("data", exist_ok=True)

        def _init():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS punishments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_tag TEXT,
                    staff_id INTEGER NOT NULL,
                    staff_tag TEXT,
                    reason TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    duration_sec INTEGER NOT NULL,   -- 0 = permanente
                    ends_at INTEGER,                 -- null se perma
                    active INTEGER NOT NULL DEFAULT 1,
                    removed_at INTEGER,
                    removed_by INTEGER,
                    removed_by_tag TEXT,
                    removed_reason TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS appeals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    punishment_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    content TEXT,
                    attachments TEXT,
                    status TEXT NOT NULL DEFAULT 'open', -- open/accepted/denied
                    decided_at INTEGER,
                    decided_by INTEGER,
                    decided_by_tag TEXT,
                    decision_reason TEXT,
                    FOREIGN KEY(punishment_id) REFERENCES punishments(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id INTEGER PRIMARY KEY,
                    panel_message_id INTEGER,
                    status_message_id INTEGER
                )
            """)
            con.commit()
            con.close()

        await asyncio.to_thread(_init)
        self._init_done = True

    async def create_punishment(
        self,
        guild_id: int,
        user: discord.Member,
        staff: discord.Member,
        reason: str,
        duration_sec: int
    ) -> int:
        created = int(now_utc().timestamp())
        ends_at = None
        if duration_sec > 0:
            ends_at = int((now_utc() + timedelta(seconds=duration_sec)).timestamp())

        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                INSERT INTO punishments
                (guild_id, user_id, user_tag, staff_id, staff_tag, reason, created_at, duration_sec, ends_at, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                guild_id,
                user.id,
                str(user),
                staff.id,
                str(staff),
                reason,
                created,
                duration_sec,
                ends_at
            ))
            pid = cur.lastrowid
            con.commit()
            con.close()
            return pid

        return await asyncio.to_thread(_do)

    async def remove_punishment(self, guild_id: int, user_id: int, removed_by: discord.Member, removed_reason: str) -> int | None:
        ts = int(now_utc().timestamp())

        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            # pega a última ativa
            cur.execute("""
                SELECT id FROM punishments
                WHERE guild_id=? AND user_id=? AND active=1
                ORDER BY created_at DESC LIMIT 1
            """, (guild_id, user_id))
            row = cur.fetchone()
            if not row:
                con.close()
                return None

            pid = row[0]
            cur.execute("""
                UPDATE punishments
                SET active=0, removed_at=?, removed_by=?, removed_by_tag=?, removed_reason=?
                WHERE id=? AND guild_id=?
            """, (ts, removed_by.id, str(removed_by), removed_reason, pid, guild_id))
            con.commit()
            con.close()
            return pid

        return await asyncio.to_thread(_do)

    async def get_active_punishment(self, guild_id: int, user_id: int):
        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                SELECT id, user_id, user_tag, staff_id, staff_tag, reason, created_at, duration_sec, ends_at
                FROM punishments
                WHERE guild_id=? AND user_id=? AND active=1
                ORDER BY created_at DESC LIMIT 1
            """, (guild_id, user_id))
            row = cur.fetchone()
            con.close()
            return row
        return await asyncio.to_thread(_do)

    async def list_active(self, guild_id: int):
        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                SELECT id, user_id, user_tag, staff_id, staff_tag, reason, created_at, duration_sec, ends_at
                FROM punishments
                WHERE guild_id=? AND active=1
                ORDER BY created_at DESC
            """, (guild_id,))
            rows = cur.fetchall()
            con.close()
            return rows
        return await asyncio.to_thread(_do)

    async def expire_due(self, guild_id: int) -> list[int]:
        """Desativa punições vencidas (não-perma) e retorna IDs expirados."""
        ts = int(now_utc().timestamp())

        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                SELECT id FROM punishments
                WHERE guild_id=? AND active=1 AND duration_sec>0 AND ends_at IS NOT NULL AND ends_at <= ?
            """, (guild_id, ts))
            rows = [r[0] for r in cur.fetchall()]
            if rows:
                cur.execute(f"""
                    UPDATE punishments
                    SET active=0, removed_at=?, removed_by=?, removed_by_tag=?, removed_reason=?
                    WHERE guild_id=? AND id IN ({",".join("?" for _ in rows)})
                """, (ts, 0, "Sistema", "Expirou automaticamente", guild_id, *rows))
            con.commit()
            con.close()
            return rows

        return await asyncio.to_thread(_do)

    async def create_appeal(self, guild_id: int, punishment_id: int, user_id: int, content: str, attachments: list[str]) -> int:
        ts = int(now_utc().timestamp())
        att = "\n".join(attachments) if attachments else ""

        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                INSERT INTO appeals (guild_id, punishment_id, user_id, created_at, content, attachments, status)
                VALUES (?, ?, ?, ?, ?, ?, 'open')
            """, (guild_id, punishment_id, user_id, ts, content, att))
            aid = cur.lastrowid
            con.commit()
            con.close()
            return aid

        return await asyncio.to_thread(_do)

    async def upsert_settings(self, guild_id: int, panel_message_id: int | None = None, status_message_id: int | None = None):
        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("SELECT guild_id, panel_message_id, status_message_id FROM settings WHERE guild_id=?", (guild_id,))
            row = cur.fetchone()
            if row:
                pm = panel_message_id if panel_message_id is not None else row[1]
                sm = status_message_id if status_message_id is not None else row[2]
                cur.execute("""
                    UPDATE settings SET panel_message_id=?, status_message_id=? WHERE guild_id=?
                """, (pm, sm, guild_id))
            else:
                cur.execute("""
                    INSERT INTO settings (guild_id, panel_message_id, status_message_id) VALUES (?, ?, ?)
                """, (guild_id, panel_message_id, status_message_id))
            con.commit()
            con.close()
        await asyncio.to_thread(_do)

    async def get_settings(self, guild_id: int):
        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("SELECT panel_message_id, status_message_id FROM settings WHERE guild_id=?", (guild_id,))
            row = cur.fetchone()
            con.close()
            return row
        return await asyncio.to_thread(_do)


# -------------------------
# Permissões
# -------------------------
def has_role(member: discord.Member, role_id: int) -> bool:
    return any(r.id == role_id for r in member.roles)

def can_manage(member: discord.Member) -> bool:
    # gerência ou admin
    if member.guild_permissions.administrator:
        return True
    if IDS.PUNISH_MANAGER_ROLE_ID and has_role(member, IDS.PUNISH_MANAGER_ROLE_ID):
        return True
    return False

def is_staff(member: discord.Member) -> bool:
    if IDS.STAFF_ROLE_ID and has_role(member, IDS.STAFF_ROLE_ID):
        return True
    # fallback: se não setar STAFF_ROLE_ID, exige ao menos permissão moderadora
    return member.guild_permissions.manage_guild or member.guild_permissions.manage_roles


# -------------------------
# UI: Modals
# -------------------------
class ApplyPunishmentModal(discord.ui.Modal, title="Aplicar punição"):
    user_id = discord.ui.TextInput(label="ID do staff punido", placeholder="Ex: 123456789012345678", required=True)
    duration = discord.ui.TextInput(label="Duração (ex: 2h, 1d3h, 30m ou perma)", placeholder="2h", required=True)
    reason = discord.ui.TextInput(label="Motivo", style=discord.TextStyle.paragraph, required=True, max_length=800)

    def __init__(self, cog: "PunicoesCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)

        if not can_manage(interaction.user):
            return await interaction.response.send_message("Você não tem permissão pra aplicar punição.", ephemeral=True)

        try:
            uid = int(str(self.user_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("ID inválido.", ephemeral=True)

        member = interaction.guild.get_member(uid)
        if not member:
            return await interaction.response.send_message("Não achei esse membro no servidor.", ephemeral=True)

        if IDS.STAFF_ROLE_ID and not has_role(member, IDS.STAFF_ROLE_ID):
            return await interaction.response.send_message("Esse usuário não parece ser STAFF (cargo não encontrado).", ephemeral=True)

        # impede punir gerência/admin por acidente (ajuste se quiser)
        if can_manage(member) and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Não vou punir gerência/admin por aqui.", ephemeral=True)

        try:
            dur = parse_duration(self.duration.value)
        except Exception as e:
            return await interaction.response.send_message(f"Erro na duração: {e}", ephemeral=True)

        reason = str(self.reason.value).strip()
        pid = await self.cog.db.create_punishment(interaction.guild.id, member, interaction.user, reason, dur)

        # resposta
        await interaction.response.send_message(f"✅ Punição aplicada com sucesso. ID #{pid}", ephemeral=True)

        # logs + update tabela
        await self.cog.log_punishment_applied(interaction.guild, pid)
        await self.cog.refresh_status_board(interaction.guild)


class RemovePunishmentModal(discord.ui.Modal, title="Remover punição"):
    user_id = discord.ui.TextInput(label="ID do staff punido", placeholder="123...", required=True)
    reason = discord.ui.TextInput(label="Motivo da retirada", style=discord.TextStyle.paragraph, required=True, max_length=800)

    def __init__(self, cog: "PunicoesCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)

        if not can_manage(interaction.user):
            return await interaction.response.send_message("Você não tem permissão pra remover punição.", ephemeral=True)

        try:
            uid = int(str(self.user_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("ID inválido.", ephemeral=True)

        pid = await self.cog.db.remove_punishment(interaction.guild.id, uid, interaction.user, str(self.reason.value).strip())
        if not pid:
            return await interaction.response.send_message("Esse staff não tem punição ativa.", ephemeral=True)

        await interaction.response.send_message(f"✅ Punição removida. (Punição #{pid})", ephemeral=True)
        await self.cog.log_punishment_removed(interaction.guild, pid)
        await self.cog.refresh_status_board(interaction.guild)


class ConsultPunishmentModal(discord.ui.Modal, title="Consultar punição"):
    user_id = discord.ui.TextInput(label="ID do staff", placeholder="123...", required=True)

    def __init__(self, cog: "PunicoesCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)

        try:
            uid = int(str(self.user_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("ID inválido.", ephemeral=True)

        row = await self.cog.db.get_active_punishment(interaction.guild.id, uid)
        if not row:
            return await interaction.response.send_message("Sem punição ativa.", ephemeral=True)

        (pid, user_id, user_tag, staff_id, staff_tag, reason, created_at, duration_sec, ends_at) = row
        created_dt = datetime.fromtimestamp(created_at, tz=UTC)
        ends_dt = datetime.fromtimestamp(ends_at, tz=UTC) if ends_at else None

        embed = discord.Embed(title=f"Punição ativa #{pid}", color=discord.Color.orange())
        embed.add_field(name="Punido", value=f"<@{user_id}> (`{user_tag}`)\nID: `{user_id}`", inline=False)
        embed.add_field(name="Aplicada por", value=f"<@{staff_id}> (`{staff_tag}`)\nID: `{staff_id}`", inline=False)
        embed.add_field(name="Motivo", value=reason, inline=False)
        embed.add_field(name="Início", value=dt_to_str(created_dt), inline=True)
        if duration_sec == 0:
            embed.add_field(name="Duração", value="Permanente", inline=True)
            embed.add_field(name="Término", value="—", inline=True)
        else:
            embed.add_field(name="Duração", value=f"{duration_sec//60} min (~)", inline=True)
            embed.add_field(name="Término", value=dt_to_str(ends_dt), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------------
# UI: Painel
# -------------------------
class PunishPanelView(discord.ui.View):
    def __init__(self, cog: "PunicoesCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Aplicar punição", style=discord.ButtonStyle.danger, custom_id="punish:apply")
    async def apply_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)
        if not can_manage(interaction.user):
            return await interaction.response.send_message("Sem permissão.", ephemeral=True)
        await interaction.response.send_modal(ApplyPunishmentModal(self.cog))

    @discord.ui.button(label="Remover punição", style=discord.ButtonStyle.success, custom_id="punish:remove")
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)
        if not can_manage(interaction.user):
            return await interaction.response.send_message("Sem permissão.", ephemeral=True)
        await interaction.response.send_modal(RemovePunishmentModal(self.cog))

    @discord.ui.button(label="Consultar", style=discord.ButtonStyle.secondary, custom_id="punish:consult")
    async def consult_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)
        await interaction.response.send_modal(ConsultPunishmentModal(self.cog))


class AppealModal(discord.ui.Modal, title="Recorrer punição"):
    proof = discord.ui.TextInput(
        label="Explique e cole links (vídeos/prints) aqui",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1500
    )

    def __init__(self, cog: "PunicoesCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)

        # precisa ter punição ativa
        row = await self.cog.db.get_active_punishment(interaction.guild.id, interaction.user.id)
        if not row:
            return await interaction.response.send_message("Você não tem punição ativa para recorrer.", ephemeral=True)

        pid = row[0]
        aid = await self.cog.db.create_appeal(
            interaction.guild.id,
            punishment_id=pid,
            user_id=interaction.user.id,
            content=str(self.proof.value).strip(),
            attachments=[],
        )

        await interaction.response.send_message(f"✅ Recurso aberto! (Recurso #{aid} / Punição #{pid})", ephemeral=True)

        # manda um resumo no canal de recurso e logs
        await self.cog.post_appeal_summary(interaction.guild, interaction.user, pid, aid, str(self.proof.value).strip())


class AppealView(discord.ui.View):
    def __init__(self, cog: "PunicoesCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Abrir recurso", style=discord.ButtonStyle.primary, custom_id="punish:appeal")
    async def appeal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)
        await interaction.response.send_modal(AppealModal(self.cog))


# -------------------------
# Cog
# -------------------------
class PunicoesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = PunishDB()
        self.expire_task.start()

        # Views persistentes
        self.bot.add_view(PunishPanelView(self))
        self.bot.add_view(AppealView(self))

    async def cog_load(self):
        await self.db.init()

    def cog_unload(self):
        self.expire_task.cancel()

    # ----------- Helpers de canal -----------
    def ch(self, guild: discord.Guild, channel_id: int) -> discord.TextChannel | None:
        c = guild.get_channel(channel_id)
        return c if isinstance(c, discord.TextChannel) else None

    async def log_embed(self, guild: discord.Guild, embed: discord.Embed):
        log_ch = self.ch(guild, IDS.PUNISH_LOG_CHANNEL_ID)
        if log_ch:
            await log_ch.send(embed=embed)

    # ----------- Logs detalhados -----------
    async def log_punishment_applied(self, guild: discord.Guild, pid: int):
        # busca punição
        rows = await self.db.list_active(guild.id)
        row = next((r for r in rows if r[0] == pid), None)
        if not row:
            return

        (pid, user_id, user_tag, staff_id, staff_tag, reason, created_at, duration_sec, ends_at) = row
        created_dt = datetime.fromtimestamp(created_at, tz=UTC)
        ends_dt = datetime.fromtimestamp(ends_at, tz=UTC) if ends_at else None

        embed = discord.Embed(title=f"✅ Punição aplicada #{pid}", color=discord.Color.red())
        embed.add_field(name="Punido", value=f"<@{user_id}> (`{user_tag}`)\nID: `{user_id}`", inline=False)
        embed.add_field(name="Aplicada por", value=f"<@{staff_id}> (`{staff_tag}`)\nID: `{staff_id}`", inline=False)
        embed.add_field(name="Motivo", value=reason, inline=False)
        embed.add_field(name="Início", value=dt_to_str(created_dt), inline=True)
        if duration_sec == 0:
            embed.add_field(name="Duração", value="Permanente", inline=True)
            embed.add_field(name="Término", value="—", inline=True)
        else:
            embed.add_field(name="Duração", value=f"{duration_sec//60} min (~)", inline=True)
            embed.add_field(name="Término", value=dt_to_str(ends_dt), inline=True)

        await self.log_embed(guild, embed)

    async def log_punishment_removed(self, guild: discord.Guild, pid: int):
        # precisamos ler do banco diretamente (pode não estar ativa)
        def _do():
            con = sqlite3.connect(self.db.path)
            cur = con.cursor()
            cur.execute("""
                SELECT id, user_id, user_tag, staff_id, staff_tag, reason, created_at, duration_sec, ends_at,
                       removed_at, removed_by, removed_by_tag, removed_reason
                FROM punishments
                WHERE guild_id=? AND id=?
            """, (guild.id, pid))
            row = cur.fetchone()
            con.close()
            return row

        row = await asyncio.to_thread(_do)
        if not row:
            return

        (pid, user_id, user_tag, staff_id, staff_tag, reason, created_at, duration_sec, ends_at,
         removed_at, removed_by, removed_by_tag, removed_reason) = row

        created_dt = datetime.fromtimestamp(created_at, tz=UTC)
        ends_dt = datetime.fromtimestamp(ends_at, tz=UTC) if ends_at else None
        removed_dt = datetime.fromtimestamp(removed_at, tz=UTC) if removed_at else None

        embed = discord.Embed(title=f"✅ Punição removida #{pid}", color=discord.Color.green())
        embed.add_field(name="Punido", value=f"<@{user_id}> (`{user_tag}`)\nID: `{user_id}`", inline=False)
        embed.add_field(name="Aplicada por", value=f"<@{staff_id}> (`{staff_tag}`)\nID: `{staff_id}`", inline=False)
        embed.add_field(name="Motivo original", value=reason, inline=False)

        embed.add_field(name="Início", value=dt_to_str(created_dt), inline=True)
        if duration_sec == 0:
            embed.add_field(name="Duração", value="Permanente", inline=True)
            embed.add_field(name="Término previsto", value="—", inline=True)
        else:
            embed.add_field(name="Duração", value=f"{duration_sec//60} min (~)", inline=True)
            embed.add_field(name="Término previsto", value=dt_to_str(ends_dt), inline=True)

        embed.add_field(name="Removida em", value=dt_to_str(removed_dt), inline=True)
        embed.add_field(name="Removida por", value=f"<@{removed_by}> (`{removed_by_tag}`)\nID: `{removed_by}`", inline=True)
        embed.add_field(name="Motivo da retirada", value=removed_reason or "—", inline=False)

        await self.log_embed(guild, embed)

    # ----------- Canal de recurso -----------
    async def post_appeal_summary(self, guild: discord.Guild, user: discord.Member, pid: int, aid: int, content: str):
        appeal_ch = self.ch(guild, IDS.PUNISH_APPEAL_CHANNEL_ID)
        embed = discord.Embed(title=f"📨 Recurso aberto #{aid}", color=discord.Color.blurple())
        embed.add_field(name="Punido", value=f"{user.mention} (`{user}`)\nID: `{user.id}`", inline=False)
        embed.add_field(name="Punição", value=f"#{pid}", inline=True)
        embed.add_field(name="Texto/Provas", value=content[:1000], inline=False)
        embed.set_footer(text="Gerência pode analisar pelo canal e remover a punição no painel, se necessário.")

        if appeal_ch:
            await appeal_ch.send(embed=embed)

        # logs também
        await self.log_embed(guild, embed)

    # ----------- Status board -----------
    async def build_status_embed(self, guild: discord.Guild) -> discord.Embed:
        rows = await self.db.list_active(guild.id)

        embed = discord.Embed(
            title="📋 Tabela de punições ativas (STAFF)",
            color=discord.Color.orange(),
            description="Atualiza automaticamente quando alguém é punido/removido ou quando expira."
        )
        embed.set_footer(text="Bronks Games • Sistema de Punições")

        if not rows:
            embed.description = "✅ Nenhuma punição ativa no momento."
            return embed

        # mostra até 20 por embed (simples). Se quiser paginação depois, dá.
        lines = []
        for (pid, user_id, user_tag, staff_id, staff_tag, reason, created_at, duration_sec, ends_at) in rows[:20]:
            created_dt = datetime.fromtimestamp(created_at, tz=UTC)
            if duration_sec == 0:
                ends_txt = "Permanente"
            else:
                ends_dt = datetime.fromtimestamp(ends_at, tz=UTC) if ends_at else None
                ends_txt = discord.utils.format_dt(ends_dt, style="R") if ends_dt else "—"

            reason_short = (reason[:60] + "…") if len(reason) > 60 else reason
            lines.append(
                f"**#{pid}** • <@{user_id}> • por <@{staff_id}>\n"
                f"⏱️ {dt_to_str(created_dt)} • termina: **{ends_txt}**\n"
                f"📝 {reason_short}"
            )

        embed.add_field(name="Punidos", value="\n\n".join(lines), inline=False)

        if len(rows) > 20:
            embed.add_field(name="Observação", value=f"Mostrando 20 de {len(rows)} punições ativas.", inline=False)

        return embed

    async def refresh_status_board(self, guild: discord.Guild):
        status_ch = self.ch(guild, IDS.PUNISH_STATUS_CHANNEL_ID)
        if not status_ch:
            return

        settings = await self.db.get_settings(guild.id)
        status_msg_id = settings[1] if settings else None

        embed = await self.build_status_embed(guild)

        try:
            if status_msg_id:
                msg = await status_ch.fetch_message(status_msg_id)
                await msg.edit(embed=embed)
                return
        except Exception:
            pass

        # cria nova msg e salva
        msg = await status_ch.send(embed=embed)
        await self.db.upsert_settings(guild.id, status_message_id=msg.id)

    # ----------- Expiração automática -----------
    @tasks.loop(minutes=1)
    async def expire_task(self):
        await self.db.init()
        for guild in self.bot.guilds:
            expired_ids = await self.db.expire_due(guild.id)
            if expired_ids:
                # loga e atualiza tabela
                for pid in expired_ids:
                    # log como removida pelo sistema
                    await self.log_punishment_removed(guild, pid)
                await self.refresh_status_board(guild)

    @expire_task.before_loop
    async def before_expire(self):
        await self.bot.wait_until_ready()

    # -------------------------
    # Slash: setup (manda painel e botão de recurso)
    # -------------------------
    @app_commands.command(name="punicoes_setup", description="Cria/atualiza as mensagens do painel e do recurso.")
    async def punicoes_setup(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)
        if not can_manage(interaction.user):
            return await interaction.response.send_message("Sem permissão.", ephemeral=True)

        await self.db.init()

        reg_ch = self.ch(interaction.guild, IDS.PUNISH_REG_CHANNEL_ID)
        appeal_ch = self.ch(interaction.guild, IDS.PUNISH_APPEAL_CHANNEL_ID)

        if not reg_ch or not appeal_ch:
            return await interaction.response.send_message(
                "Configura os IDs dos canais em `config/ids.py` (registro e recorrer).",
                ephemeral=True
            )

        panel_embed = discord.Embed(
            title="🛡️ Painel de Punições da STAFF",
            description=(
                "Use os botões abaixo para **aplicar**, **remover** ou **consultar** punições.\n\n"
                "⚠️ Somente gerência/direção pode aplicar/remover."
            ),
            color=discord.Color.dark_gold()
        )
        panel_embed.set_footer(text="Bronks Games • Painel oficial")

        panel_msg = await reg_ch.send(embed=panel_embed, view=PunishPanelView(self))
        await self.db.upsert_settings(interaction.guild.id, panel_message_id=panel_msg.id)

        appeal_embed = discord.Embed(
            title="📨 Recorrer punição",
            description=(
                "Se você foi punido e quer recorrer, clique no botão abaixo e explique.\n"
                "Cole links (clips/prints) no texto do recurso."
            ),
            color=discord.Color.blurple()
        )
        await appeal_ch.send(embed=appeal_embed, view=AppealView(self))

        await self.refresh_status_board(interaction.guild)

        await interaction.response.send_message("✅ Setup feito: painel + recurso + status.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PunicoesCog(bot))
