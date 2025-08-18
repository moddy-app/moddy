"""
Système de vérification de blacklist corrigé
Intercepte et BLOQUE toutes les interactions des utilisateurs blacklistés
"""

import discord
from discord.ext import commands
from typing import Union

from config import COLORS


class BlacklistButton(discord.ui.View):
    """Vue avec le bouton de demande d'unblacklist"""

    def __init__(self):
        super().__init__()
        # Ajoute le bouton avec un lien
        self.add_item(discord.ui.Button(
            label="Unblacklist request",
            url="https://moddy.app/unbl_request",
            style=discord.ButtonStyle.link
        ))


class BlacklistCheck(commands.Cog):
    """Vérifie le statut blacklist avant toute interaction"""

    def __init__(self, bot):
        self.bot = bot
        # Pas de cache pour le blacklist - on veut des vérifications en temps réel

    async def is_blacklisted(self, user_id: int) -> bool:
        """Vérifie si un utilisateur est blacklisté (sans cache pour temps réel)"""
        # Vérifie directement la DB à chaque fois
        if self.bot.db:
            try:
                is_bl = await self.bot.db.has_attribute('user', user_id, 'BLACKLISTED')
                return is_bl
            except:
                return False
        return False

    async def send_blacklist_message(self, interaction: discord.Interaction):
        """Envoie TOUJOURS le message de blacklist en ephemeral
        (Fonction maintenant obsolète mais gardée pour compatibilité)"""
        embed = discord.Embed(
            description=(
                "<:blacklist:1401596864784777363> You have been blacklisted from using Moddy.\n"
                "<:blacklist:1401596864784777363> Vous avez été blacklisté de Moddy."
            ),
            color=COLORS["error"]
        )

        embed.set_footer(text=f"ID: {interaction.user.id}")

        view = BlacklistButton()

        try:
            if interaction.response.is_done():
                # Si la réponse est déjà faite, utilise followup en ephemeral
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            else:
                # Sinon, envoie une nouvelle réponse en ephemeral
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except discord.HTTPException:
            # En cas d'erreur HTTP, ne fait rien (évite le spam)
            pass
        except Exception:
            # Pour toute autre erreur, ne fait rien
            pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Intercepte TOUTES les interactions Discord"""
        # Ignore les interactions du bot lui-même
        if interaction.user.bot:
            return

        # Vérifie seulement pour les commandes et composants
        if interaction.type not in [
            discord.InteractionType.application_command,
            discord.InteractionType.component,
            discord.InteractionType.modal_submit
        ]:
            return

        # Vérifie si l'utilisateur est blacklisté
        if await self.is_blacklisted(interaction.user.id):
            # IMPORTANT: Vérifie si c'est un développeur ET une commande dev
            # Les développeurs blacklistés PEUVENT utiliser les commandes dev
            if self.bot.is_developer(interaction.user.id):
                if hasattr(interaction, 'command') and interaction.command:
                    # Si c'est une commande slash, vérifie le module
                    command_name = interaction.command.name

                    # Récupère la commande depuis le bot
                    command_obj = self.bot.tree.get_command(command_name)

                    # Si la commande appartient à un cog dev, laisse passer
                    if command_obj and hasattr(command_obj, 'module'):
                        cog = self.bot.get_cog(command_obj.module)
                        if cog and hasattr(cog, '__class__'):
                            cog_file = cog.__class__.__module__
                            # Si c'est un cog dans staff/ ou dev/, laisse passer pour les devs
                            if 'staff.' in cog_file or 'dev.' in cog_file:
                                # Log mais laisse passer
                                if log_cog := self.bot.get_cog("LoggingSystem"):
                                    await log_cog.log_info(
                                        title="Dev Blacklisté - Commande dev autorisée",
                                        description=(
                                            f"**Utilisateur:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                                            f"**Commande:** {command_name}\n"
                                            f"**Module:** {cog_file}"
                                        )
                                    )
                                return  # Laisse passer la commande

            # Log l'interaction bloquée (pour tous les cas)
            if log_cog := self.bot.get_cog("LoggingSystem"):
                await log_cog.log_critical(
                    title="Interaction Blacklistée",
                    description=(
                        f"**Utilisateur:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                        f"**Type:** {interaction.type.name}\n"
                        f"**Commande:** {getattr(interaction.command, 'name', 'N/A')}\n"
                        f"**Serveur:** {interaction.guild.name if interaction.guild else 'DM'}"
                    ),
                    ping_dev=False
                )

            # IMPORTANT: Répond immédiatement avec le message de blacklist
            # Cela empêche la commande de s'exécuter car l'interaction est déjà répondue
            embed = discord.Embed(
                description=(
                    "<:blacklist:1401596864784777363> You have been blacklisted from using Moddy.\n"
                    "<:blacklist:1401596864784777363> Vous avez été blacklisté de Moddy."
                ),
                color=COLORS["error"]
            )
            embed.set_footer(text=f"ID: {interaction.user.id}")
            view = BlacklistButton()

            try:
                # Répond immédiatement pour bloquer la commande
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            except discord.errors.InteractionResponded:
                # Si déjà répondu, utilise followup
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            except Exception:
                pass

            # Arrête complètement le traitement ici
            return

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Vérifie aussi pour les commandes texte (préfixe)"""
        # Ignore les bots
        if message.author.bot:
            return

        # Vérifie si c'est potentiellement une commande
        if message.content:
            # Récupère les préfixes possibles
            prefixes = await self.bot.get_prefix(message)

            # Vérifie si le message commence par un préfixe
            for prefix in prefixes:
                if message.content.startswith(prefix):
                    # C'est potentiellement une commande
                    command_content = message.content[len(prefix):].split()[0] if message.content[
                                                                                  len(prefix):].split() else ""

                    # Vérifie si l'utilisateur est blacklisté
                    if await self.is_blacklisted(message.author.id):
                        # IMPORTANT: Vérifie si c'est un dev qui utilise une commande dev
                        # Les développeurs blacklistés PEUVENT utiliser les commandes dev
                        if self.bot.is_developer(message.author.id):
                            # Récupère la commande
                            command = self.bot.get_command(command_content)

                            # Si c'est une commande dev, laisse passer
                            if command and hasattr(command, 'cog'):
                                cog = command.cog
                                if cog and hasattr(cog, '__class__'):
                                    cog_file = cog.__class__.__module__
                                    # Si c'est un cog dans staff/ ou dev/, laisse passer pour les devs
                                    if 'staff.' in cog_file or 'dev.' in cog_file:
                                        # Log mais laisse passer
                                        if log_cog := self.bot.get_cog("LoggingSystem"):
                                            await log_cog.log_info(
                                                title="Dev Blacklisté - Commande préfixe dev autorisée",
                                                description=(
                                                    f"**Utilisateur:** {message.author.mention} (`{message.author.id}`)\n"
                                                    f"**Commande:** {command_content}\n"
                                                    f"**Module:** {cog_file}"
                                                )
                                            )
                                        return  # Laisse passer la commande

                        # Crée l'embed de blacklist
                        embed = discord.Embed(
                            description=(
                                "<:blacklist:1401596864784777363> You have been blacklisted from using Moddy.\n"
                                "<:blacklist:1401596864784777363> Vous avez été blacklisté de Moddy."
                            ),
                            color=COLORS["error"]
                        )

                        embed.set_footer(text=f"ID: {message.author.id}")

                        view = BlacklistButton()

                        # Essaie de répondre en mode "incognito" (sans mention)
                        try:
                            # Envoie le message sans mentionner l'auteur
                            await message.reply(embed=embed, view=view, mention_author=False, delete_after=10)
                        except discord.Forbidden:
                            # Si on ne peut pas répondre, essaie d'envoyer dans le canal
                            try:
                                await message.channel.send(embed=embed, view=view, delete_after=10)
                            except:
                                pass

                        # IMPORTANT: Supprime le message de commande si possible
                        try:
                            await message.delete()
                        except:
                            pass

                        # Empêche le traitement de la commande en levant une exception
                        # Cela va stopper la propagation vers process_commands
                        return True  # Indique que le message a été intercepté

    @commands.command(name="testbl")
    async def test_blacklist(self, ctx):
        """Teste le message de blacklist (commande dev)"""
        # Les devs peuvent utiliser les commandes dev même si blacklistés
        if not self.bot.is_developer(ctx.author.id):
            return

        # Simule l'envoi du message
        embed = discord.Embed(
            description=(
                "<:blacklist:1401596864784777363> You have been blacklisted from using Moddy.\n"
                "<:blacklist:1401596864784777363> Vous avez été blacklisté de Moddy."
            ),
            color=COLORS["error"]
        )

        embed.set_footer(text=f"ID: {ctx.author.id}")

        view = BlacklistButton()

        await ctx.send("**[TEST MODE]** Voici ce que verrait un utilisateur blacklisté:", embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(BlacklistCheck(bot))