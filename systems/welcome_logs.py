# systems/welcome_logs.py

import discord
from discord.ext import commands
from config import ids  # 👈 IMPORTAÇÃO CORRETA

class WelcomeLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _safe_channel(self, guild: discord.Guild, channel_id: int):
        return guild.get_channel(channel_id)

    def _member_common_fields(self, member: discord.Member):
        user = member

        created = discord.utils.format_dt(user.created_at, style="F") if user.created_at else "N/D"
        joined = discord.utils.format_dt(member.joined_at, style="F") if member.joined_at else "N/D"

        boosting = (
            discord.utils.format_dt(member.premium_since, style="F")
            if member.premium_since else "Não"
        )

        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        roles_txt = ", ".join(roles) if roles else "Nenhum"

        return [
            ("👤 Usuário", f"{user.mention}\n`{user}`", True),
            ("🆔 ID", f"`{user.id}`", True),
            ("📅 Conta criada em", created, True),
            ("📥 Entrou no servidor em", joined, True),
            ("🚀 Boosting desde", boosting, True),
            ("🎭 Cargos", roles_txt, False),
            ("🤖 É bot?", "Sim" if user.bot else "Não", True),
        ]

    # =========================
    # ENTRADA
    # =========================
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        ch_bemvindos = self._safe_channel(guild, ids.BEM_VINDOS_CHANNEL_ID)
        ch_entrada = self._safe_channel(guild, ids.LOG_ENTRADA_CHANNEL_ID)

        # BOAS-VINDAS
        if ch_bemvindos:
            embed = discord.Embed(
                title="🎉 Bem-vindo(a) à Cidade!",
                description=(
                    f"👋 Olá {member.mention}, seja muito bem-vindo(a)!\n\n"
                    "💬 Explore nossos canais e divirta-se!\n"
                    f"📌 Leia as regras antes de começar sua jornada:\n{ids.REGRAS_LINK}\n\n"
                    "**Esperamos que curta sua estadia!**"
                ),
                color=discord.Color.purple()
            )

            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_image(url="https://i.imgur.com/lRkaN6h.gif")
            embed.set_footer(text=f"{guild.name}")

            await ch_bemvindos.send(embed=embed)

        # LOG ENTRADA
        if ch_entrada:
            embed = discord.Embed(
                title="✅ Membro Entrou",
                color=discord.Color.green()
            )

            embed.set_thumbnail(url=member.display_avatar.url)

            for name, value, inline in self._member_common_fields(member):
                embed.add_field(name=name, value=value, inline=inline)

            embed.set_footer(text=f"Total de membros: {guild.member_count}")
            await ch_entrada.send(embed=embed)

    # =========================
    # SAÍDA
    # =========================
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        ch_saida = self._safe_channel(guild, ids.LOG_SAIDA_CHANNEL_ID)

        if ch_saida:
            embed = discord.Embed(
                title="🚪 Membro Saiu",
                color=discord.Color.red()
            )

            embed.set_thumbnail(url=member.display_avatar.url)

            for name, value, inline in self._member_common_fields(member):
                embed.add_field(name=name, value=value, inline=inline)

            embed.set_footer(text=f"Total de membros: {guild.member_count}")
            await ch_saida.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeLogs(bot))