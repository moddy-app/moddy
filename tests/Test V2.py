"""
Fichier de test pour les composants Discord V2
Teste toutes les fonctionnalités de la branche expérimentale
"""

import nextcord
from nextcord.ext import commands
import asyncio
from datetime import datetime

# Configuration
TOKEN = "TON_TOKEN_ICI"  # Remplace par ton token de test
TEST_GUILD_ID = 123456789  # Remplace par l'ID de ton serveur de test

# Création du bot
intents = nextcord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


class ComponentsV2Test(commands.Cog):
    """Tests des composants V2"""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="v2test")
    async def test_basic_v2(self, ctx):
        """Test basique des composants V2"""
        # Méthode 1 : Envoi direct avec flags
        try:
            await ctx.send(
                flags=nextcord.MessageFlags.components_v2,  # ou 32768
                components=[
                    {
                        "type": 10,  # Text Display
                        "content": "# Test Composants V2\n\nCeci est un **Text Display** avec du markdown!"
                    }
                ]
            )
        except Exception as e:
            await ctx.send(f"Erreur méthode 1: {e}")

    @commands.command(name="v2layout")
    async def test_layout_v2(self, ctx):
        """Test avec LayoutView"""

        class TestLayoutView(nextcord.ui.LayoutView):
            def __init__(self):
                super().__init__()

            @nextcord.ui.text_display(content="**Bonjour!** Ceci est un Text Display dans une LayoutView.")
            async def text_component(self):
                pass

            @nextcord.ui.container(accent_color=0x00FF00)
            async def green_container(self):
                """Container avec couleur verte"""
                pass

            @nextcord.ui.separator(spacing=nextcord.ui.SeparatorSpacing.Large, divider=True)
            async def divider(self):
                pass

        try:
            view = TestLayoutView()
            await ctx.send(view=view)
        except Exception as e:
            await ctx.send(f"Erreur LayoutView: {e}")

    @commands.command(name="v2complex")
    async def test_complex_v2(self, ctx):
        """Test complexe avec tous les composants"""

        components = [
            # Text Display principal
            {
                "type": 10,
                "content": "# Démonstration Complète V2"
            },
            # Container avec accent
            {
                "type": 12,  # Container
                "accent_color": 0x5865F2,  # Blurple Discord
                "components": [
                    {
                        "type": 10,
                        "content": "**Container** avec couleur d'accent\n\nCe texte est dans un container coloré."
                    }
                ]
            },
            # Séparateur
            {
                "type": 14,  # Separator
                "spacing": "large",
                "divider": True
            },
            # Section avec bouton
            {
                "type": 13,  # Section
                "components": [
                    {
                        "type": 10,
                        "content": "**Section** avec un bouton accessoire →"
                    }
                ],
                "accessory": {
                    "type": 2,  # Button
                    "style": 1,  # Primary
                    "label": "Cliquez-moi!",
                    "custom_id": "test_button"
                }
            }
        ]

        try:
            await ctx.send(
                flags=32768,  # IS_COMPONENTS_V2
                components=components
            )
        except Exception as e:
            await ctx.send(f"Erreur complexe: {e}")

    @commands.command(name="v2interactive")
    async def test_interactive_v2(self, ctx):
        """Test avec interactions"""

        class InteractiveView(nextcord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.click_count = 0

            @nextcord.ui.button(label="Compteur: 0", style=nextcord.ButtonStyle.primary)
            async def counter_button(self, interaction: nextcord.Interaction, button: nextcord.ui.Button):
                self.click_count += 1
                button.label = f"Compteur: {self.click_count}"

                # Mise à jour avec composants V2
                await interaction.response.edit_message(
                    flags=32768,
                    components=[
                        {
                            "type": 10,
                            "content": f"# Clics: {self.click_count}\n\nDernière interaction: {datetime.now().strftime('%H:%M:%S')}"
                        }
                    ],
                    view=self
                )

        try:
            view = InteractiveView()
            await ctx.send(
                flags=32768,
                components=[
                    {
                        "type": 10,
                        "content": "# Test Interactif\n\nCliquez sur le bouton!"
                    }
                ],
                view=view
            )
        except Exception as e:
            await ctx.send(f"Erreur interactive: {e}")

    @commands.command(name="v2media")
    async def test_media_v2(self, ctx):
        """Test avec médias (si supporté)"""

        components = [
            {
                "type": 10,
                "content": "# Galerie Média"
            },
            # Media Gallery (si supporté)
            {
                "type": 15,  # Media Gallery
                "items": [
                    {
                        "type": 11,  # Thumbnail
                        "url": "https://cdn.discordapp.com/embed/avatars/0.png"
                    }
                ]
            }
        ]

        try:
            await ctx.send(
                flags=32768,
                components=components
            )
        except Exception as e:
            await ctx.send(f"Erreur média: {e}")

    @commands.command(name="v2defer")
    async def test_defer_v2(self, ctx):
        """Test avec interaction différée"""

        # Simuler une interaction différée avec une commande slash
        await ctx.send("Cette commande teste les interactions différées avec une commande normale")

        # Test direct
        msg = await ctx.send("Traitement en cours...")
        await asyncio.sleep(2)

        try:
            await msg.edit(
                content=None,
                flags=32768,
                components=[
                    {
                        "type": 10,
                        "content": f"# Réponse Mise à Jour\n\nModifiée après **2 secondes**\n\nTimestamp: {datetime.now().strftime('%H:%M:%S')}"
                    }
                ]
            )
        except Exception as e:
            await msg.edit(content=f"Erreur defer: {e}")


# Événements
@bot.event
async def on_ready():
    print(f"Bot connecté : {bot.user}")
    print(f"Version nextcord.py : {nextcord.__version__}")
    print(f"Serveurs : {len(bot.guilds)}")

    # Vérifier si les composants V2 sont disponibles
    if hasattr(discord, 'LayoutView'):
        print("✅ LayoutView disponible")
    else:
        print("❌ LayoutView non disponible")

    if hasattr(nextcord.MessageFlags, 'components_v2'):
        print("✅ Flag components_v2 disponible")
    else:
        print("❌ Flag components_v2 non disponible")

    # Vérifier d'autres attributs V2
    if hasattr(nextcord.ui, 'text_display'):
        print("✅ text_display disponible")
    else:
        print("❌ text_display non disponible")


@bot.event
async def on_command_error(ctx, error):
    """Gestion des erreurs"""
    if isinstance(error, commands.CommandNotFound):
        return

    print(f"Erreur : {type(error).__name__}: {error}")
    await ctx.send(f"```py\n{type(error).__name__}: {error}\n```")


# Ajout du cog et lancement
async def main():
    async with bot:
        await bot.add_cog(ComponentsV2Test(bot))
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())