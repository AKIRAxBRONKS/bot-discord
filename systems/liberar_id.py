# systems/liberar_id.py
# ✅ Painel + Botão + Modal (ID + Nome)
# ✅ Muda nick: "ID | Nome"
# ✅ Dá cargo (CARGO_LIBERADO_ID)
# ✅ API HTTP pro FiveM checar whitelist: GET /wl/check?discord_id=123&secret=...
#
# Requisitos: aiohttp
# IDs em config/ids.py:
# GUILD_ID, CANAL_LIBERAR_ID, CARGO_LIBERADO_ID, FIVEM_SHARED_SECRET,
# (opcional) PAINEL_THUMB_URL, PAINEL_IMG_URL

from typing import Optional
import asyncio

import discord
from discord.ext import commands
import config.ids as ids

from aiohttp import web


# ✅ no mesmo PC: pode usar 127.0.0.1 ou 0.0.0.0
API_HOST = "0.0.0.0"
API_PORT = 35555

PANEL_TITLE = "🔐 LIBERAÇÃO DE ID"
BUTTON_CUSTOM_ID = "btn_liberar_id_v1"
MODAL_CUSTOM_ID = "modal_liberar_id_v1"


def _safe_int(x, default=0):
    try:
        return int(x)
    except:
        return default


class LiberarIdModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Liberar ID", custom_id=MODAL_CUSTOM_ID)

        self.campo_id = discord.ui.TextInput(
            label="Seu Passaporte/ID (apenas números)",
            placeholder="Ex: 1001",
            required=True,
            min_length=1,
            max_length=12,
        )

        self.campo_nome = discord.ui.TextInput(
            label="Seu Nome (como quer no Discord)",
            placeholder="Ex: João Silva",
            required=True,
            min_length=2,
            max_length=24,
        )

        self.add_item(self.campo_id)
        self.add_item(self.campo_nome)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        if not guild or not isinstance(member, discord.Member):
            return await interaction.response.send_message("❌ Use isso dentro do servidor.", ephemeral=True)

        role_id = _safe_int(getattr(ids, "CARGO_LIBERADO_ID", 0))
        if not role_id:
            return await interaction.response.send_message("❌ CARGO_LIBERADO_ID não configurado.", ephemeral=True)

        role = guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("❌ Cargo não encontrado. Verifique o ID.", ephemeral=True)

        # não pode repetir
        if role in member.roles:
            return await interaction.response.send_message("⚠️ Você já está liberado.", ephemeral=True)

        raw_id = (self.campo_id.value or "").strip()
        raw_nome = (self.campo_nome.value or "").strip()

        if not raw_id.isdigit():
            return await interaction.response.send_message("❌ O ID precisa ter apenas números.", ephemeral=True)

        novo_apelido = f"{raw_id} | {raw_nome}"

        # muda nick
        try:
            await member.edit(nick=novo_apelido, reason="Liberação de ID (WL)")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ Sem permissão para alterar seu apelido.\n"
                "Dê ao bot **Gerenciar Apelidos** e deixe o cargo dele acima do seu.",
                ephemeral=True
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"⚠️ Não consegui alterar seu apelido: `{type(e).__name__}`",
                ephemeral=True
            )

        # dá cargo
        try:
            await member.add_roles(role, reason="Liberação de ID (WL)")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ Sem permissão para dar cargo.\n"
                "Dê ao bot **Gerenciar Cargos** e deixe o cargo dele acima do cargo liberado.",
                ephemeral=True
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"⚠️ Não consegui dar o cargo: `{type(e).__name__}`",
                ephemeral=True
            )

        await interaction.response.send_message(
            f"✅ Liberado!\n📛 Nick: **{novo_apelido}**\n🏷️ Cargo: **{role.name}**",
            ephemeral=True
        )


class LiberarIdView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Liberar ID",
        style=discord.ButtonStyle.success,
        custom_id=BUTTON_CUSTOM_ID,
        emoji="✅"
    )
    async def liberar_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LiberarIdModal())


class LiberarIdSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panel_message_id: Optional[int] = None

        # ✅ API state
        self._api_started = False
        self.app = web.Application()
        self.app.router.add_get("/wl/check", self.http_check)
        self.runner = web.AppRunner(self.app)
        self.site: Optional[web.TCPSite] = None

        # ✅ view persistente
        self.bot.add_view(LiberarIdView())

    async def start_api(self):
        """Sobe a API uma única vez (garantido)."""
        if self._api_started:
            return
        self._api_started = True

        try:
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, API_HOST, API_PORT)
            await self.site.start()
            print(f"✅ [WL] API online: http://127.0.0.1:{API_PORT}/wl/check")
        except OSError as e:
            # porta ocupada / sem permissão
            print(f"❌ [WL] Não consegui abrir a API na porta {API_PORT}: {e}")
        except Exception as e:
            print(f"❌ [WL] Erro ao iniciar API: {type(e).__name__}: {e}")

    async def cog_unload(self):
        try:
            await self.runner.cleanup()
        except:
            pass

    def _build_embed(self, guild: discord.Guild) -> discord.Embed:
        thumb = getattr(ids, "PAINEL_THUMB_URL", None)
        img = getattr(ids, "PAINEL_IMG_URL", None)

        e = discord.Embed(
            title=PANEL_TITLE,
            description=(
                "Clique no botão **Liberar ID** para abrir o formulário.\n\n"
                "🧾 Preencha:\n"
                "• **Seu Passaporte/ID** (somente números)\n"
                "• **Seu Nome**\n\n"
                "✅ O sistema vai:\n"
                "• Ajustar seu nick para `ID | Nome`\n"
                "• Aplicar o cargo de **liberado**\n"
            ),
            color=0x8E44AD
        )
        if thumb:
            e.set_thumbnail(url=thumb)
        if img:
            e.set_image(url=img)
        e.set_footer(text="Painel automático • Não apague esta mensagem")
        return e

    async def _find_panel(self, channel: discord.TextChannel):
        if self.panel_message_id:
            try:
                return await channel.fetch_message(self.panel_message_id)
            except:
                pass

        async for msg in channel.history(limit=50):
            if msg.author.id != self.bot.user.id:
                continue
            if msg.embeds and msg.embeds[0].title == PANEL_TITLE:
                return msg
        return None

    async def _clean_channel_keep(self, channel: discord.TextChannel, keep_id: int):
        def check(m: discord.Message):
            return m.id != keep_id

        try:
            await channel.purge(limit=200, check=check, bulk=True)
        except:
            pass

        try:
            async for m in channel.history(limit=200):
                if m.id == keep_id:
                    continue
                try:
                    await m.delete()
                except:
                    pass
        except:
            pass

    async def ensure_panel(self):
        guild = self.bot.get_guild(_safe_int(getattr(ids, "GUILD_ID", 0)))
        if not guild:
            print("❌ [WL] Não achei a guild (GUILD_ID).")
            return

        channel = guild.get_channel(_safe_int(getattr(ids, "CANAL_LIBERAR_ID", 0)))
        if not channel or not isinstance(channel, discord.TextChannel):
            print("❌ [WL] CANAL_LIBERAR_ID inválido ou canal não encontrado.")
            return

        msg = await self._find_panel(channel)
        embed = self._build_embed(guild)
        view = LiberarIdView()

        if not msg:
            msg = await channel.send(embed=embed, view=view)
        else:
            try:
                await msg.edit(embed=embed, view=view)
            except:
                pass

        self.panel_message_id = msg.id
        await self._clean_channel_keep(channel, msg.id)
        print("✅ [WL] Painel garantido no canal.")

    @commands.Cog.listener()
    async def on_ready(self):
        # ✅ garante API e painel sem travar o ready
        asyncio.create_task(self.start_api())
        asyncio.create_task(self.ensure_panel())

    # ========= API pro FiveM =========
    async def http_check(self, request: web.Request):
        secret = request.query.get("secret", "")
        if secret != getattr(ids, "FIVEM_SHARED_SECRET", ""):
            return web.json_response({"ok": False, "error": "bad_secret"}, status=403)

        discord_id = request.query.get("discord_id", "").strip()
        if not discord_id.isdigit():
            return web.json_response({"ok": False, "error": "bad_discord_id"}, status=400)

        guild = self.bot.get_guild(_safe_int(getattr(ids, "GUILD_ID", 0)))
        if not guild:
            return web.json_response({"ok": False, "error": "guild_not_found"}, status=500)

        member = guild.get_member(int(discord_id))
        if not member:
            return web.json_response({"ok": True, "whitelisted": False, "reason": "not_in_guild"}, status=200)

        role = guild.get_role(_safe_int(getattr(ids, "CARGO_LIBERADO_ID", 0)))
        if not role:
            return web.json_response({"ok": False, "error": "role_not_found"}, status=500)

        whitelisted = role in member.roles
        return web.json_response({"ok": True, "whitelisted": whitelisted}, status=200)


async def setup(bot: commands.Bot):
    await bot.add_cog(LiberarIdSystem(bot))