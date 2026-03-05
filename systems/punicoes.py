# systems/punicoes.py
import asyncio
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from config import ids as IDS

UTC = timezone.utc

# ====== VISUAL (SEU MODELO) ======
THUMB_URL = "https://i.imgur.com/tF85i5l.png"
IMAGE_URL = "https://i.imgur.com/nxnvh7d.png"

ADV_DURATION_DAYS = 30
ADV_DURATION_SEC = ADV_DURATION_DAYS * 24 * 60 * 60

# remove prefixos tipo: [ADM] Fulano | 22  /  (ADM) Fulano | 22
NICK_PREFIX_RE = re.compile(r"^\s*[\[\(\{][^\]\)\}]{1,16}[\]\)\}]\s*", re.UNICODE)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def dt_to_str(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return discord.utils.format_dt(dt, style="F")


def clean_staff_prefix(nick: str) -> str:
    nick = (nick or "").strip()
    if not nick:
        return nick
    return NICK_PREFIX_RE.sub("", nick, count=1).strip()


def has_role(member: discord.Member, role_id: int) -> bool:
    return any(r.id == role_id for r in member.roles)


def can_manage(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if getattr(IDS, "PUNISH_MANAGER_ROLE_ID", 0) and has_role(member, IDS.PUNISH_MANAGER_ROLE_ID):
        return True
    return False


def staff_roles_to_remove(guild: discord.Guild) -> list[discord.Role]:
    ids_list = getattr(IDS, "STAFF_ROLES_TO_REMOVE", []) or []
    roles = []
    for rid in ids_list:
        role = guild.get_role(int(rid))
        if role:
            roles.append(role)

    # fallback: se não configurar lista, remove pelo menos STAFF_ROLE_ID
    if not roles and getattr(IDS, "STAFF_ROLE_ID", 0):
        r = guild.get_role(int(IDS.STAFF_ROLE_ID))
        if r:
            roles = [r]
    return roles


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
                    proof TEXT,
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
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id INTEGER PRIMARY KEY,
                    panel_message_id INTEGER,
                    appeal_message_id INTEGER,
                    status_message_id INTEGER
                )
            """)

            # migração (caso DB antiga)
            try:
                cur.execute("ALTER TABLE punishments ADD COLUMN proof TEXT")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE settings ADD COLUMN appeal_message_id INTEGER")
            except Exception:
                pass

            con.commit()
            con.close()

        await asyncio.to_thread(_init)
        self._init_done = True

    async def create_adv(self, guild_id: int, user: discord.Member, staff: discord.Member, reason: str, proof: str) -> int:
        created = int(now_utc().timestamp())
        duration_sec = ADV_DURATION_SEC
        ends_at = int((now_utc() + timedelta(seconds=duration_sec)).timestamp())

        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                INSERT INTO punishments
                (guild_id, user_id, user_tag, staff_id, staff_tag, reason, proof, created_at, duration_sec, ends_at, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                guild_id,
                user.id,
                str(user),
                staff.id,
                str(staff),
                reason,
                proof,
                created,
                duration_sec,
                ends_at
            ))
            pid = cur.lastrowid
            con.commit()
            con.close()
            return pid

        return await asyncio.to_thread(_do)

    async def remove_latest_active(self, guild_id: int, user_id: int, removed_by: discord.Member, removed_reason: str) -> int | None:
        ts = int(now_utc().timestamp())

        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
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

    async def count_active_user(self, guild_id: int, user_id: int) -> int:
        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                SELECT COUNT(*) FROM punishments
                WHERE guild_id=? AND user_id=? AND active=1
            """, (guild_id, user_id))
            n = int(cur.fetchone()[0])
            con.close()
            return n
        return await asyncio.to_thread(_do)

    async def list_active(self, guild_id: int):
        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                SELECT id, user_id, user_tag, staff_id, staff_tag, reason, proof, created_at, duration_sec, ends_at
                FROM punishments
                WHERE guild_id=? AND active=1
                ORDER BY created_at DESC
            """, (guild_id,))
            rows = cur.fetchall()
            con.close()
            return rows
        return await asyncio.to_thread(_do)

    async def get_settings(self, guild_id: int):
        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("SELECT panel_message_id, appeal_message_id, status_message_id FROM settings WHERE guild_id=?", (guild_id,))
            row = cur.fetchone()
            con.close()
            return row
        return await asyncio.to_thread(_do)

    async def upsert_settings(self, guild_id: int, panel_message_id=None, appeal_message_id=None, status_message_id=None):
        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("SELECT guild_id, panel_message_id, appeal_message_id, status_message_id FROM settings WHERE guild_id=?", (guild_id,))
            row = cur.fetchone()

            if row:
                pm = panel_message_id if panel_message_id is not None else row[1]
                am = appeal_message_id if appeal_message_id is not None else row[2]
                sm = status_message_id if status_message_id is not None else row[3]
                cur.execute("""
                    UPDATE settings SET panel_message_id=?, appeal_message_id=?, status_message_id=? WHERE guild_id=?
                """, (pm, am, sm, guild_id))
            else:
                cur.execute("""
                    INSERT INTO settings (guild_id, panel_message_id, appeal_message_id, status_message_id)
                    VALUES (?, ?, ?, ?)
                """, (guild_id, panel_message_id, appeal_message_id, status_message_id))

            con.commit()
            con.close()
        await asyncio.to_thread(_do)

    async def expire_due(self, guild_id: int) -> list[int]:
        ts = int(now_utc().timestamp())

        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                SELECT id FROM punishments
                WHERE guild_id=? AND active=1 AND ends_at IS NOT NULL AND ends_at <= ?
            """, (guild_id, ts))
            rows = [r[0] for r in cur.fetchall()]
            if rows:
                cur.execute(f"""
                    UPDATE punishments
                    SET active=0, removed_at=?, removed_by=?, removed_by_tag=?, removed_reason=?
                    WHERE guild_id=? AND id IN ({",".join("?" for _ in rows)})
                """, (ts, 0, "Sistema", "ADV expirou automaticamente (30 dias)", guild_id, *rows))
            con.commit()
            con.close()
            return rows

        return await asyncio.to_thread(_do)

    async def read_punishment(self, guild_id: int, pid: int):
        def _do():
            con = sqlite3.connect(self.path)
            cur = con.cursor()
            cur.execute("""
                SELECT id, user_id, user_tag, staff_id, staff_tag, reason, proof, created_at, duration_sec, ends_at,
                       removed_at, removed_by, removed_by_tag, removed_reason, active
                FROM punishments
                WHERE guild_id=? AND id=?
            """, (guild_id, pid))
            row = cur.fetchone()
            con.close()
            return row
        return await asyncio.to_thread(_do)


# -------------------------
# UI: Modals
# -------------------------
class ApplyAdvModal(discord.ui.Modal, title="Registrar ADV (30 dias)"):
    user_id = discord.ui.TextInput(label="ID do staff punido", placeholder="Ex: 123456789012345678", required=True)
    reason = discord.ui.TextInput(label="Motivo (obrigatório)", style=discord.TextStyle.paragraph, required=True, max_length=800)
    proof = discord.ui.TextInput(
        label="Provas (links / descrição curta)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    def __init__(self, cog: "PunicoesCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)

        if not can_manage(interaction.user):
            return await interaction.response.send_message("Você não tem permissão pra registrar ADV.", ephemeral=True)

        try:
            uid = int(str(self.user_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("ID inválido.", ephemeral=True)

        member = interaction.guild.get_member(uid)
        if not member:
            return await interaction.response.send_message("Não achei esse membro no servidor.", ephemeral=True)

        # conta quantos ADV ativos ele já tem
        current = await self.cog.db.count_active_user(interaction.guild.id, member.id)
        if current >= 3:
            return await interaction.response.send_message("Esse staff já está com 3 ADV ativos.", ephemeral=True)

        pid = await self.cog.db.create_adv(
            interaction.guild.id,
            user=member,
            staff=interaction.user,
            reason=str(self.reason.value).strip(),
            proof=str(self.proof.value).strip()
        )

        new_count = current + 1

        await interaction.response.send_message(
            f"✅ ADV registrado (#{pid}). Agora: **{new_count}/3**. Duração: **{ADV_DURATION_DAYS} dias**.",
            ephemeral=True
        )

        await self.cog.log_adv_applied(interaction.guild, pid, new_count)

        # se bateu 3: punição automática (remove cargos + limpa nick)
        if new_count >= 3:
            await self.cog.apply_three_adv_penalty(interaction.guild, member, interaction.user, pid)

        await self.cog.refresh_status_board(interaction.guild)


class RemoveAdvModal(discord.ui.Modal, title="Remover ADV (último ativo)"):
    user_id = discord.ui.TextInput(label="ID do staff punido", placeholder="123...", required=True)
    reason = discord.ui.TextInput(label="Motivo da retirada", style=discord.TextStyle.paragraph, required=True, max_length=800)

    def __init__(self, cog: "PunicoesCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)

        if not can_manage(interaction.user):
            return await interaction.response.send_message("Você não tem permissão pra remover ADV.", ephemeral=True)

        try:
            uid = int(str(self.user_id.value).strip())
        except ValueError:
            return await interaction.response.send_message("ID inválido.", ephemeral=True)

        pid = await self.cog.db.remove_latest_active(
            interaction.guild.id,
            uid,
            interaction.user,
            str(self.reason.value).strip()
        )
        if not pid:
            return await interaction.response.send_message("Esse staff não tem ADV ativo.", ephemeral=True)

        left = await self.cog.db.count_active_user(interaction.guild.id, uid)

        await interaction.response.send_message(f"✅ ADV removido. (Registro #{pid}) — Agora: **{left}/3**", ephemeral=True)
        await self.cog.log_adv_removed(interaction.guild, pid)
        await self.cog.refresh_status_board(interaction.guild)


class AppealModal(discord.ui.Modal, title="Recorrer punição (ADV)"):
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

        # precisa ter ao menos 1 ADV ativo
        count = await self.cog.db.count_active_user(interaction.guild.id, interaction.user.id)
        if count <= 0:
            return await interaction.response.send_message("Você não tem ADV ativo para recorrer.", ephemeral=True)

        # manda para o canal de recurso + logs
        await interaction.response.send_message("✅ Recurso enviado para análise.", ephemeral=True)
        await self.cog.post_appeal_summary(interaction.guild, interaction.user, str(self.proof.value).strip())


# -------------------------
# UI: Views (Painéis)
# -------------------------
class PunishPanelView(discord.ui.View):
    def __init__(self, cog: "PunicoesCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Registrar ADV (30d)", style=discord.ButtonStyle.danger, custom_id="punish:apply_adv")
    async def apply_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)
        if not can_manage(interaction.user):
            return await interaction.response.send_message("Sem permissão.", ephemeral=True)
        await interaction.response.send_modal(ApplyAdvModal(self.cog))

    @discord.ui.button(label="Remover ADV", style=discord.ButtonStyle.success, custom_id="punish:remove_adv")
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Só funciona no servidor.", ephemeral=True)
        if not can_manage(interaction.user):
            return await interaction.response.send_message("Sem permissão.", ephemeral=True)
        await interaction.response.send_modal(RemoveAdvModal(self.cog))


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

        # Views persistentes (pra botões funcionarem após restart)
        self.bot.add_view(PunishPanelView(self))
        self.bot.add_view(AppealView(self))

    async def cog_load(self):
        await self.db.init()
        self.bot.loop.create_task(self._auto_setup_all_guilds())

    def cog_unload(self):
        self.expire_task.cancel()

    # ----------- Helpers de canal -----------
    def ch(self, guild: discord.Guild, channel_id: int) -> discord.TextChannel | None:
        c = guild.get_channel(channel_id)
        return c if isinstance(c, discord.TextChannel) else None

    async def _auto_setup_all_guilds(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                await self.ensure_panels(guild)
            except Exception as e:
                print(f"⚠️ [Punicoes] Falha ao garantir painéis no guild {guild.id}: {type(e).__name__}: {e}")

    async def ensure_panels(self, guild: discord.Guild):
        """
        Garante automaticamente:
        1) Painel Registro
        2) Painel Recorrer
        3) Tabela de punidos
        Sem comando. Sem spam: edita se existir, cria se não existir.
        """
        await self.db.init()

        reg_ch = self.ch(guild, IDS.PUNISH_REG_CHANNEL_ID)
        appeal_ch = self.ch(guild, IDS.PUNISH_APPEAL_CHANNEL_ID)
        status_ch = self.ch(guild, IDS.PUNISH_STATUS_CHANNEL_ID)

        if not reg_ch or not appeal_ch or not status_ch:
            print(f"⚠️ [Punicoes] IDs de canal inválidos no guild {guild.id}. Verifique config/ids.py")
            return

        settings = await self.db.get_settings(guild.id)
        panel_msg_id = settings[0] if settings else None
        appeal_msg_id = settings[1] if settings else None

        # ---- Painel Registro (VISUAL IGUAL SEU MODELO) ----
        panel_embed = discord.Embed(
            title="🛡️ REGISTRO DE PUNIÇÕES",
            description=(
                "Use o painel abaixo para **registrar** ou **retirar** ADV de staff.\n\n"
                f"📌 Cada ADV dura **{ADV_DURATION_DAYS} dias** e sai automaticamente.\n"
                "⚠️ Ao chegar em **3 ADV**, o staff perde os cargos de staff automaticamente."
            ),
            color=discord.Color.dark_magenta()
        )
        panel_embed.set_thumbnail(url=THUMB_URL)
        panel_embed.set_image(url=IMAGE_URL)
        panel_embed.set_footer(text="Akira Roleplay © All rights reserved")

        panel_msg = None
        if panel_msg_id:
            try:
                panel_msg = await reg_ch.fetch_message(panel_msg_id)
                await panel_msg.edit(embed=panel_embed, view=PunishPanelView(self))
            except Exception:
                panel_msg = None

        if not panel_msg:
            panel_msg = await reg_ch.send(embed=panel_embed, view=PunishPanelView(self))
            await self.db.upsert_settings(guild.id, panel_message_id=panel_msg.id)

        # ---- Painel Recorrer (VISUAL) ----
        appeal_embed = discord.Embed(
            title="📨 RECORRER À PUNIÇÃO",
            description=(
                "Se você recebeu ADV e quer recorrer, clique no botão abaixo.\n"
                "Envie provas (links/prints) e explique direitinho."
            ),
            color=discord.Color.dark_magenta()
        )
        appeal_embed.set_thumbnail(url=THUMB_URL)
        appeal_embed.set_image(url=IMAGE_URL)
        appeal_embed.set_footer(text="Akira Roleplay © All rights reserved")

        appeal_msg = None
        if appeal_msg_id:
            try:
                appeal_msg = await appeal_ch.fetch_message(appeal_msg_id)
                await appeal_msg.edit(embed=appeal_embed, view=AppealView(self))
            except Exception:
                appeal_msg = None

        if not appeal_msg:
            appeal_msg = await appeal_ch.send(embed=appeal_embed, view=AppealView(self))
            await self.db.upsert_settings(guild.id, appeal_message_id=appeal_msg.id)

        # ---- Tabela / Status ----
        await self.refresh_status_board(guild)

        print(f"✅ [Punicoes] Painéis garantidos no guild {guild.id}")

    # ----------- Logs -----------
    async def log_embed(self, guild: discord.Guild, embed: discord.Embed):
        log_ch = self.ch(guild, IDS.PUNISH_LOG_CHANNEL_ID)
        if log_ch:
            await log_ch.send(embed=embed)

    async def log_adv_applied(self, guild: discord.Guild, pid: int, adv_count_after: int):
        row = await self.db.read_punishment(guild.id, pid)
        if not row:
            return

        (pid, user_id, user_tag, staff_id, staff_tag, reason, proof, created_at, duration_sec, ends_at,
         removed_at, removed_by, removed_by_tag, removed_reason, active) = row

        created_dt = datetime.fromtimestamp(created_at, tz=UTC)
        ends_dt = datetime.fromtimestamp(ends_at, tz=UTC) if ends_at else None

        embed = discord.Embed(title=f"✅ ADV registrado #{pid}", color=discord.Color.red())
        embed.add_field(name="Punido", value=f"<@{user_id}> (`{user_tag}`)\nID: `{user_id}`", inline=False)
        embed.add_field(name="Responsável", value=f"<@{staff_id}> (`{staff_tag}`)\nID: `{staff_id}`", inline=False)
        embed.add_field(name="ADV (após registro)", value=f"**{adv_count_after}/3**", inline=True)
        embed.add_field(name="Início", value=dt_to_str(created_dt), inline=True)
        embed.add_field(name="Término", value=dt_to_str(ends_dt), inline=True)
        embed.add_field(name="Motivo", value=reason, inline=False)
        embed.add_field(name="Provas", value=proof or "—", inline=False)

        await self.log_embed(guild, embed)

    async def log_adv_removed(self, guild: discord.Guild, pid: int):
        row = await self.db.read_punishment(guild.id, pid)
        if not row:
            return

        (pid, user_id, user_tag, staff_id, staff_tag, reason, proof, created_at, duration_sec, ends_at,
         removed_at, removed_by, removed_by_tag, removed_reason, active) = row

        created_dt = datetime.fromtimestamp(created_at, tz=UTC)
        ends_dt = datetime.fromtimestamp(ends_at, tz=UTC) if ends_at else None
        removed_dt = datetime.fromtimestamp(removed_at, tz=UTC) if removed_at else None

        embed = discord.Embed(title=f"✅ ADV removido #{pid}", color=discord.Color.green())
        embed.add_field(name="Punido", value=f"<@{user_id}> (`{user_tag}`)\nID: `{user_id}`", inline=False)
        embed.add_field(name="Registrado por", value=f"<@{staff_id}> (`{staff_tag}`)\nID: `{staff_id}`", inline=False)
        embed.add_field(name="Motivo original", value=reason, inline=False)
        embed.add_field(name="Provas originais", value=proof or "—", inline=False)

        embed.add_field(name="Início", value=dt_to_str(created_dt), inline=True)
        embed.add_field(name="Término previsto", value=dt_to_str(ends_dt), inline=True)

        embed.add_field(name="Removido em", value=dt_to_str(removed_dt), inline=True)
        embed.add_field(name="Removido por", value=f"<@{removed_by}> (`{removed_by_tag}`)\nID: `{removed_by}`", inline=False)
        embed.add_field(name="Motivo da retirada", value=removed_reason or "—", inline=False)

        await self.log_embed(guild, embed)

    async def apply_three_adv_penalty(self, guild: discord.Guild, member: discord.Member, by: discord.Member, last_pid: int):
        """
        Ao chegar em 3 ADV:
        - remove cargos de staff configurados
        - limpa prefixo do nick tipo [ADM]
        """
        roles = staff_roles_to_remove(guild)

        # remove roles
        removed_roles = []
        try:
            if roles:
                await member.remove_roles(*roles, reason="3 ADV ativos (punição automática)")
                removed_roles = [r.name for r in roles]
        except discord.Forbidden:
            removed_roles = ["(SEM PERMISSÃO PARA REMOVER CARGOS)"]
        except Exception:
            removed_roles = ["(ERRO AO REMOVER CARGOS)"]

        # limpa nick
        old_nick = member.nick or member.name
        new_nick = clean_staff_prefix(old_nick)

        nick_result = "não alterado"
        try:
            if new_nick and new_nick != old_nick:
                await member.edit(nick=new_nick, reason="3 ADV ativos (limpeza de tag staff)")
                nick_result = f"`{old_nick}` → `{new_nick}`"
            else:
                nick_result = f"`{old_nick}`"
        except discord.Forbidden:
            nick_result = "(SEM PERMISSÃO PARA MUDAR NICK)"
        except Exception:
            nick_result = "(ERRO AO MUDAR NICK)"

        embed = discord.Embed(title="🚫 3 ADV atingidos — ação automática", color=discord.Color.dark_red())
        embed.add_field(name="Staff", value=f"{member.mention} (`{member}`)\nID: `{member.id}`", inline=False)
        embed.add_field(name="Responsável (último registro)", value=f"{by.mention} (`{by}`)\nID: `{by.id}`", inline=False)
        embed.add_field(name="Registro que bateu 3", value=f"#{last_pid}", inline=True)
        embed.add_field(name="Cargos removidos", value="\n".join(removed_roles) if removed_roles else "—", inline=False)
        embed.add_field(name="Nick", value=nick_result, inline=False)
        embed.set_footer(text="Sistema automático • 3 ADV")

        await self.log_embed(guild, embed)

    # ----------- Canal de recurso -----------
    async def post_appeal_summary(self, guild: discord.Guild, user: discord.Member, content: str):
        appeal_ch = self.ch(guild, IDS.PUNISH_APPEAL_CHANNEL_ID)
        embed = discord.Embed(title="📨 Recurso enviado", color=discord.Color.blurple())
        embed.add_field(name="Staff", value=f"{user.mention} (`{user}`)\nID: `{user.id}`", inline=False)
        embed.add_field(name="Texto/Provas", value=content[:1000], inline=False)
        embed.set_footer(text="Gerência deve avaliar no canal de recurso.")

        if appeal_ch:
            await appeal_ch.send(embed=embed)

        await self.log_embed(guild, embed)

    # ----------- Status board -----------
    async def build_status_embed(self, guild: discord.Guild) -> discord.Embed:
        rows = await self.db.list_active(guild.id)

        embed = discord.Embed(
            title="📋 TABELA DE PUNIDOS (ADV ATIVOS)",
            description="Atualiza automaticamente quando registra/remove ou quando ADV expira (30 dias).",
            color=discord.Color.dark_magenta()
        )
        embed.set_thumbnail(url=THUMB_URL)
        embed.set_image(url=IMAGE_URL)
        embed.set_footer(text="Akira Roleplay © All rights reserved")

        if not rows:
            embed.description = "✅ Nenhum staff com ADV ativo no momento."
            return embed

        # conta por usuário: quantos ADV ativos ele tem
        counts = {}
        for r in rows:
            user_id = r[1]
            counts[user_id] = counts.get(user_id, 0) + 1

        # monta linhas únicas por usuário (não lista 100 registros repetidos)
        lines = []
        seen = set()
        for (pid, user_id, user_tag, staff_id, staff_tag, reason, proof, created_at, duration_sec, ends_at) in rows:
            if user_id in seen:
                continue
            seen.add(user_id)

            adv_count = counts.get(user_id, 1)
            ends_dt = datetime.fromtimestamp(ends_at, tz=UTC) if ends_at else None
            ends_txt = discord.utils.format_dt(ends_dt, style="R") if ends_dt else "—"
            reason_short = (reason[:60] + "…") if len(reason) > 60 else reason

            lines.append(
                f"**{adv_count}/3** • <@{user_id}>  (último: **#{pid}**)\n"
                f"⏳ expira: **{ends_txt}**\n"
                f"📝 {reason_short}"
            )

        embed.add_field(name="Status", value="\n\n".join(lines[:20]), inline=False)
        if len(lines) > 20:
            embed.add_field(name="Observação", value=f"Mostrando 20 de {len(lines)} staff com ADV ativo.", inline=False)

        return embed

    async def refresh_status_board(self, guild: discord.Guild):
        status_ch = self.ch(guild, IDS.PUNISH_STATUS_CHANNEL_ID)
        if not status_ch:
            return

        settings = await self.db.get_settings(guild.id)
        status_msg_id = settings[2] if settings else None

        embed = await self.build_status_embed(guild)

        try:
            if status_msg_id:
                msg = await status_ch.fetch_message(status_msg_id)
                await msg.edit(embed=embed)
                return
        except Exception:
            pass

        msg = await status_ch.send(embed=embed)
        await self.db.upsert_settings(guild.id, status_message_id=msg.id)

    # ----------- Expiração automática -----------
    @tasks.loop(minutes=1)
    async def expire_task(self):
        await self.db.init()
        for guild in self.bot.guilds:
            expired_ids = await self.db.expire_due(guild.id)
            if expired_ids:
                # só atualiza tabela e loga expiração
                for pid in expired_ids:
                    row = await self.db.read_punishment(guild.id, pid)
                    if row:
                        embed = discord.Embed(title="⌛ ADV expirou automaticamente", color=discord.Color.gold())
                        embed.add_field(name="Registro", value=f"#{pid}", inline=True)
                        embed.add_field(name="Staff", value=f"<@{row[1]}> (`{row[2]}`)\nID: `{row[1]}`", inline=False)
                        embed.add_field(name="Motivo original", value=row[5], inline=False)
                        embed.set_footer(text=f"Expiração automática • {ADV_DURATION_DAYS} dias")
                        await self.log_embed(guild, embed)

                await self.refresh_status_board(guild)

    @expire_task.before_loop
    async def before_expire(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(PunicoesCog(bot))
