import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, Dict

# =========================
# Utils
# =========================
def is_url(s: str) -> bool:
    s = (s or "").strip()
    return s.startswith("http://") or s.startswith("https://")

def parse_hex_color(hex_str: str) -> Optional[int]:
    if not hex_str:
        return None
    t = hex_str.strip().lower().replace("#", "")
    if len(t) != 6:
        return None
    try:
        return int(t, 16)
    except:
        return None

def clamp(s: str, limit: int) -> str:
    s = s or ""
    return s[:limit]


# =========================
# Estado por usuário (em memória)
# =========================
class AnuncioState:
    def __init__(
        self,
        owner_id: int,
        channel_id: int,
        mode: str = "criar",
        target_message_id: Optional[int] = None
    ):
        self.owner_id = owner_id
        self.channel_id = channel_id
        self.panel_message_id: Optional[int] = None  # mensagem do painel (única)
        self.mode = mode  # criar/editar
        self.target_message_id = target_message_id  # msg do anúncio para editar

        # embed fields
        self.title = ""
        self.description = ""
        self.color_hex = "#8E44AD"
        self.image_url = ""
        self.thumb_url = ""
        self.author_name = ""
        self.author_icon = ""
        self.footer_text = ""
        self.footer_icon = ""

        # ✅ VÁRIOS BOTÕES (label + url)
        # [{"label": "TikTok", "url": "https://..."}, ...]
        self.buttons: list[dict] = []

    def build_embed(self, for_panel: bool = False) -> discord.Embed:
        """
        for_panel=True  -> embed de preview do editor (com instruções se vazio)
        for_panel=False -> embed FINAL do anúncio (sem debug, sem "modo")
        """
        color = parse_hex_color(self.color_hex) or 0x8E44AD

        title = clamp(self.title, 256).strip() if self.title else ""
        desc = clamp(self.description, 4000).strip() if self.description else ""

        if for_panel and not title and not desc:
            title = "🧩 Editor de Anúncio"
            desc = (
                "Use o menu abaixo para configurar seu anúncio.\n\n"
                "📝 **Título & Descrição**\n"
                "👤 **Autor**\n"
                "🎨 **Cores**\n"
                "🖼️ **Imagens**\n"
                "▶️ **Botões com link**\n"
                "🧾 **Footer**\n\n"
                "Quando estiver pronto, selecione **Enviar**."
            )

        # Discord não permite embed totalmente vazia:
        if not title and not desc:
            desc = "⠀"  # invisível

        embed = discord.Embed(
            title=title if title else None,
            description=desc,
            color=color
        )

        if is_url(self.image_url):
            embed.set_image(url=self.image_url.strip())
        if is_url(self.thumb_url):
            embed.set_thumbnail(url=self.thumb_url.strip())

        if self.author_name:
            embed.set_author(
                name=clamp(self.author_name, 256),
                icon_url=self.author_icon.strip() if is_url(self.author_icon) else None
            )

        if self.footer_text:
            embed.set_footer(
                text=clamp(self.footer_text, 2048),
                icon_url=self.footer_icon.strip() if is_url(self.footer_icon) else None
            )

        # ✅ IMPORTANTE: SEM "Modo" NO ANÚNCIO FINAL
        if for_panel:
            mode_txt = "`criar`" if self.mode == "criar" else f"`editar` (ID: `{self.target_message_id}`)"
            embed.add_field(name="Modo do editor", value=mode_txt, inline=False)

            # ✅ lista de botões (pra ajudar a remover por índice)
            if self.buttons:
                lista = "\n".join(
                    [f"{i+1}. {clamp((b.get('label') or ''), 60)}" for i, b in enumerate(self.buttons[:25])]
                )
            else:
                lista = "_Nenhum botão_"
            embed.add_field(name="Botões (máx. 25)", value=lista, inline=False)

        return embed

    def build_final_view(self) -> discord.ui.View:
        # ✅ monta todos os botões (máx. 25)
        view = discord.ui.View(timeout=None)
        count = 0
        for b in self.buttons:
            if count >= 25:
                break
            lbl = (b.get("label") or "").strip()
            url = (b.get("url") or "").strip()
            if lbl and is_url(url):
                view.add_item(discord.ui.Button(label=clamp(lbl, 80), url=url))
                count += 1
        return view


# =========================
# Painel (mensagem única)
# =========================
class Panel:
    @staticmethod
    async def create(interaction: discord.Interaction, state: AnuncioState, content: str):
        embed = state.build_embed(for_panel=True)
        view = EditorView(state)
        msg = await interaction.channel.send(content=content, embed=embed, view=view)
        state.panel_message_id = msg.id
        return msg

    @staticmethod
    async def update(interaction: discord.Interaction, state: AnuncioState, content: str):
        if interaction.user.id != state.owner_id:
            return await interaction.response.send_message("❌ Só quem abriu o editor pode mexer.", ephemeral=True)

        embed = state.build_embed(for_panel=True)
        view = EditorView(state)

        try:
            msg = await interaction.channel.fetch_message(state.panel_message_id)
            await msg.edit(content=content, embed=embed, view=view)
        except Exception:
            msg = await interaction.channel.send(content=content, embed=embed, view=view)
            state.panel_message_id = msg.id

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

    @staticmethod
    async def close(interaction: discord.Interaction, state: AnuncioState):
        try:
            msg = await interaction.channel.fetch_message(state.panel_message_id)
            await msg.delete()
        except:
            pass


# =========================
# Modals
# =========================
class TituloDescModal(discord.ui.Modal, title="Título & Descrição"):
    titulo = discord.ui.TextInput(label="Título", required=False, max_length=256)
    desc = discord.ui.TextInput(label="Descrição", required=False, style=discord.TextStyle.paragraph, max_length=4000)

    def __init__(self, state: AnuncioState):
        super().__init__()
        self.state = state
        self.titulo.default = state.title
        self.desc.default = state.description

    async def on_submit(self, interaction: discord.Interaction):
        self.state.title = str(self.titulo).strip()
        self.state.description = str(self.desc).strip()
        await Panel.update(interaction, self.state, "✅ Título/descrição atualizados.")

class AutorModal(discord.ui.Modal, title="Autor"):
    nome = discord.ui.TextInput(label="Nome do autor", required=False, max_length=256)
    icon = discord.ui.TextInput(label="Ícone do autor (URL)", required=False, max_length=400)

    def __init__(self, state: AnuncioState):
        super().__init__()
        self.state = state
        self.nome.default = state.author_name
        self.icon.default = state.author_icon

    async def on_submit(self, interaction: discord.Interaction):
        self.state.author_name = str(self.nome).strip()
        self.state.author_icon = str(self.icon).strip()
        await Panel.update(interaction, self.state, "✅ Autor atualizado.")

class CoresModal(discord.ui.Modal, title="Cores"):
    cor = discord.ui.TextInput(label="Cor HEX (#RRGGBB)", required=False, max_length=7, placeholder="#8E44AD")

    def __init__(self, state: AnuncioState):
        super().__init__()
        self.state = state
        self.cor.default = state.color_hex

    async def on_submit(self, interaction: discord.Interaction):
        val = str(self.cor).strip()
        if val and not parse_hex_color(val):
            return await interaction.response.send_message("❌ Cor inválida. Use #RRGGBB", ephemeral=True)
        if val:
            self.state.color_hex = val
        await Panel.update(interaction, self.state, "✅ Cor atualizada.")

class ImagensModal(discord.ui.Modal, title="Imagens"):
    thumb = discord.ui.TextInput(label="Thumbnail/Logo (URL)", required=False, max_length=400)
    imagem = discord.ui.TextInput(label="Imagem principal (URL)", required=False, max_length=400)

    def __init__(self, state: AnuncioState):
        super().__init__()
        self.state = state
        self.thumb.default = state.thumb_url
        self.imagem.default = state.image_url

    async def on_submit(self, interaction: discord.Interaction):
        self.state.thumb_url = str(self.thumb).strip()
        self.state.image_url = str(self.imagem).strip()
        await Panel.update(interaction, self.state, "✅ Imagens atualizadas.")

class FooterModal(discord.ui.Modal, title="Footer"):
    texto = discord.ui.TextInput(label="Texto do footer", required=False, max_length=2048)
    icon = discord.ui.TextInput(label="Ícone do footer (URL)", required=False, max_length=400)

    def __init__(self, state: AnuncioState):
        super().__init__()
        self.state = state
        self.texto.default = state.footer_text
        self.icon.default = state.footer_icon

    async def on_submit(self, interaction: discord.Interaction):
        self.state.footer_text = str(self.texto).strip()
        self.state.footer_icon = str(self.icon).strip()
        await Panel.update(interaction, self.state, "✅ Footer atualizado.")

# ✅ Modal único pra ADD/DEL
class BotoesModal(discord.ui.Modal, title="Gerenciar botões"):
    acao = discord.ui.TextInput(
        label="Ação (add ou del)",
        required=True,
        max_length=3,
        placeholder="add"
    )
    label = discord.ui.TextInput(
        label="Texto do botão (add)",
        required=False,
        max_length=80,
        placeholder="Ex: TikTok"
    )
    url = discord.ui.TextInput(
        label="URL do botão (add)",
        required=False,
        max_length=400,
        placeholder="https://seu_link"
    )
    indice = discord.ui.TextInput(
        label="Índice para remover (del) — ex: 1",
        required=False,
        max_length=2,
        placeholder="1"
    )

    def __init__(self, state: AnuncioState):
        super().__init__()
        self.state = state

    async def on_submit(self, interaction: discord.Interaction):
        action = str(self.acao).strip().lower()

        if action == "add":
            lbl = str(self.label).strip()
            url = str(self.url).strip()

            if not lbl or not url:
                return await interaction.response.send_message(
                    "❌ Para **add**, preencha **Texto** e **URL**.",
                    ephemeral=True
                )
            if not is_url(url):
                return await interaction.response.send_message(
                    "❌ URL inválida. Use http:// ou https://",
                    ephemeral=True
                )
            if len(self.state.buttons) >= 25:
                return await interaction.response.send_message(
                    "❌ Você já atingiu o limite de **25 botões**.",
                    ephemeral=True
                )

            self.state.buttons.append({"label": lbl, "url": url})
            return await Panel.update(interaction, self.state, f"✅ Botão adicionado. Total: **{len(self.state.buttons)}**")

        if action == "del":
            raw = str(self.indice).strip()
            if not raw.isdigit():
                return await interaction.response.send_message(
                    "❌ Para **del**, informe um **índice numérico** (ex: 1).",
                    ephemeral=True
                )
            idx = int(raw) - 1
            if idx < 0 or idx >= len(self.state.buttons):
                return await interaction.response.send_message(
                    f"❌ Índice inválido. Use 1 até {len(self.state.buttons)}.",
                    ephemeral=True
                )

            removed = self.state.buttons.pop(idx)
            return await Panel.update(
                interaction,
                self.state,
                f"✅ Botão removido: **{removed.get('label','')}**. Total: **{len(self.state.buttons)}**"
            )

        return await interaction.response.send_message("❌ Ação inválida. Use **add** ou **del**.", ephemeral=True)


class EditarIdModal(discord.ui.Modal, title="Ativar edição (cole o ID)"):
    msg_id = discord.ui.TextInput(label="ID da mensagem (no MESMO canal)", required=True, max_length=32)

    def __init__(self, state: AnuncioState):
        super().__init__()
        self.state = state

    async def on_submit(self, interaction: discord.Interaction):
        try:
            mid = int(str(self.msg_id).strip())
        except:
            return await interaction.response.send_message("❌ ID inválido.", ephemeral=True)

        try:
            msg = await interaction.channel.fetch_message(mid)
        except discord.NotFound:
            return await interaction.response.send_message("❌ Mensagem não encontrada neste canal.", ephemeral=True)
        except discord.Forbidden:
            return await interaction.response.send_message("❌ Sem permissão para ler histórico.", ephemeral=True)

        if msg.embeds:
            e = msg.embeds[0]
            self.state.title = e.title or ""
            self.state.description = e.description or ""
            try:
                col = getattr(e, "colour", None) or getattr(e, "color", None)
                if col and getattr(col, "value", None) is not None:
                    self.state.color_hex = f"#{col.value:06x}"
            except:
                pass
            try:
                if e.image and e.image.url:
                    self.state.image_url = e.image.url
            except:
                pass
            try:
                if e.thumbnail and e.thumbnail.url:
                    self.state.thumb_url = e.thumbnail.url
            except:
                pass
            try:
                if e.author:
                    self.state.author_name = e.author.name or ""
                    self.state.author_icon = e.author.icon_url or ""
            except:
                pass
            try:
                if e.footer:
                    self.state.footer_text = e.footer.text or ""
                    self.state.footer_icon = e.footer.icon_url or ""
            except:
                pass

        # ✅ botões link (carrega todos)
        self.state.buttons = []
        try:
            for row in msg.components:
                for comp in row.children:
                    label = getattr(comp, "label", None)
                    url = getattr(comp, "url", None)
                    if label and url and is_url(url):
                        self.state.buttons.append({"label": label, "url": url})
        except:
            pass
        self.state.buttons = self.state.buttons[:25]

        self.state.mode = "editar"
        self.state.target_message_id = mid
        await Panel.update(interaction, self.state, f"✅ Modo editar ativado. ID carregado: `{mid}`")


# =========================
# View do editor
# =========================
class EditorView(discord.ui.View):
    def __init__(self, state: AnuncioState):
        super().__init__(timeout=600)
        self.state = state

        # ✅ preview dos botões no painel (até 25)
        count = 0
        for b in state.buttons:
            if count >= 25:
                break
            lbl = (b.get("label") or "").strip()
            url = (b.get("url") or "").strip()
            if lbl and is_url(url):
                self.add_item(discord.ui.Button(label=clamp(lbl, 80), url=url))
                count += 1

    @discord.ui.select(
        placeholder="Selecione uma opção...",
        options=[
            discord.SelectOption(label="Título & Descrição", description="Altera título e descrição.", emoji="📝", value="td"),
            discord.SelectOption(label="Autor", description="Altera o autor.", emoji="👤", value="autor"),
            discord.SelectOption(label="Cores", description="Altera a cor.", emoji="🎨", value="cores"),
            discord.SelectOption(label="Imagens", description="Thumbnail e imagem.", emoji="🖼️", value="img"),
            discord.SelectOption(label="Botões com link", description="Adicionar/remover botões.", emoji="▶️", value="btn"),
            discord.SelectOption(label="Footer", description="Altera o footer.", emoji="🧾", value="footer"),
            discord.SelectOption(label="Ativar edição (ID)", description="Carrega uma mensagem pelo ID.", emoji="✏️", value="load"),
            discord.SelectOption(label="Enviar", description="Envia ou edita o anúncio.", emoji="📨", value="send"),
            discord.SelectOption(label="Fechar", description="Apaga o painel do editor.", emoji="🗑️", value="close"),
        ]
    )
    async def menu(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.state.owner_id:
            return await interaction.response.send_message("❌ Só quem abriu o editor pode mexer.", ephemeral=True)

        v = select.values[0]
        if v == "td":
            return await interaction.response.send_modal(TituloDescModal(self.state))
        if v == "autor":
            return await interaction.response.send_modal(AutorModal(self.state))
        if v == "cores":
            return await interaction.response.send_modal(CoresModal(self.state))
        if v == "img":
            return await interaction.response.send_modal(ImagensModal(self.state))
        if v == "btn":
            return await interaction.response.send_modal(BotoesModal(self.state))
        if v == "footer":
            return await interaction.response.send_modal(FooterModal(self.state))
        if v == "load":
            return await interaction.response.send_modal(EditarIdModal(self.state))

        if v == "send":
            await interaction.response.defer(ephemeral=True)
            return await self._send_final(interaction)

        if v == "close":
            await interaction.response.defer(ephemeral=True)
            await Panel.close(interaction, self.state)

    async def _send_final(self, interaction: discord.Interaction):
        final_embed = self.state.build_embed(for_panel=False)  # ✅ sem "modo"
        final_view = self.state.build_final_view()

        # criar
        if self.state.mode == "criar":
            msg = await interaction.channel.send(embed=final_embed, view=final_view)

            # ✅ após enviar, apaga o painel (para não ficar 2 mensagens)
            await Panel.close(interaction, self.state)

            # feedback só pra você (ephemeral)
            await interaction.followup.send(f"✅ Anúncio enviado! ID: `{msg.id}`", ephemeral=True)
            return

        # editar
        if self.state.mode == "editar" and self.state.target_message_id:
            try:
                target = await interaction.channel.fetch_message(self.state.target_message_id)
                await target.edit(embed=final_embed, view=final_view)

                # ✅ apaga o painel também
                await Panel.close(interaction, self.state)

                await interaction.followup.send(f"✅ Anúncio editado! ID: `{target.id}`", ephemeral=True)
            except discord.NotFound:
                await Panel.update(interaction, self.state, "❌ Não achei a mensagem para editar (ID inválido ou outro canal).")
            except discord.Forbidden:
                await Panel.update(interaction, self.state, "❌ Sem permissão para editar mensagens aqui.")
            return

        await Panel.update(interaction, self.state, "❌ Nada para enviar/editar.")


# =========================
# Cog
# =========================
class Anuncios(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: Dict[int, AnuncioState] = {}

    def is_staff(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.manage_messages or interaction.user.guild_permissions.manage_guild

    @app_commands.command(name="anuncio", description="Criar/editar anúncio com editor por opções")
    async def anuncio(self, interaction: discord.Interaction):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("❌ Você não tem permissão.", ephemeral=True)

        if not interaction.guild or not interaction.channel:
            return await interaction.response.send_message("Use dentro do servidor.", ephemeral=True)

        state = AnuncioState(owner_id=interaction.user.id, channel_id=interaction.channel.id, mode="criar")
        self.states[interaction.user.id] = state

        await interaction.response.send_message("✅ Editor aberto (o painel será apagado ao enviar).", ephemeral=True)
        await Panel.create(interaction, state, f"🧩 **Editor de anúncio** — {interaction.user.mention}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Anuncios(bot))