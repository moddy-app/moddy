"""
Commande de gestion utilisateur pour développeurs
Panel complet avec boutons pour gérer les utilisateurs
"""

import nextcord as discord
from nextcord.ext import commands
from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from typing import Optional, Dict, Any
import io

sys.path.append(str(Path(__file__).parent.parent))
from utils.embeds import ModdyEmbed, ModdyResponse, ModdyColors
from config import COLORS


class UserManagement(commands.Cog):
    """Gestion complète des utilisateurs"""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Vérifie que l'utilisateur est développeur"""
        return self.bot.is_developer(ctx.author.id)

    @commands.command(name="user", aliases=["u", "manage"])
    async def user_management(self, ctx, user: discord.User = None):
        """Panel de gestion d'un utilisateur"""

        if not user:
            embed = discord.Embed(
                title="<:manageuser:1398729745293774919> User Management",
                description=(
                    "**Usage:** `user @user` or `user [ID]`\n\n"
                    "Displays a complete panel to manage the user:\n"
                    "• View and modify attributes\n"
                    "• Check stored data\n"
                    "• Manage permissions\n"
                    "• View history"
                ),
                color=COLORS["info"]
            )
            await ctx.send(embed=embed)
            return

        # Vérifie la BDD
        if not self.bot.db:
            await ctx.send("<:undone:1398729502028333218> Database not connected")
            return

        # Récupère les données utilisateur
        try:
            user_data = await self.bot.db.get_user(user.id)
        except Exception as e:
            embed = ModdyResponse.error(
                "Database Error",
                f"Unable to retrieve data: {str(e)}"
            )
            await ctx.send(embed=embed)
            return

        # Crée l'embed principal
        embed = UserManagement._create_user_embed(self.bot, user, user_data, ctx)

        # Crée la vue avec les boutons
        view = UserManagementView(self.bot, user, user_data, ctx.author)

        # Envoie le message
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

        # Log l'action
        if log_cog := self.bot.get_cog("LoggingSystem"):
            await log_cog.log_command(ctx, "user", {"target": str(user), "id": user.id})

    @staticmethod
    def _create_user_embed(bot, user: discord.User, user_data: Dict[str, Any], ctx: commands.Context) -> discord.Embed:
        """Crée l'embed principal avec les infos utilisateur"""

        # Récupère les infos Discord
        created_at = int(user.created_at.timestamp())

        # Badges
        badges = []
        if user_data['attributes'].get('DEVELOPER'):
            badges.append("<:dev:1398729645557285066>")
        if user_data['attributes'].get('PREMIUM'):
            badges.append("<:premium:1401602724801548381>")
        if user_data['attributes'].get('BETA'):
            badges.append("<:idea:1398729314597343313>")
        if user_data['attributes'].get('BLACKLISTED'):
            badges.append("<:blacklist:1401596866478477363>")
        if user_data['attributes'].get('TRACK'):
            badges.append("<:track:1401596933222695002>")

        badges_str = " ".join(badges) if badges else "None"

        # Compte les serveurs mutuels
        mutual_guilds = []
        for guild in bot.guilds:
            if guild.get_member(user.id):
                mutual_guilds.append(guild)

        embed = discord.Embed(
            title=f"<:manageuser:1398729745293774919> Managing {user}",
            color=COLORS["primary"]
        )

        # Avatar
        embed.set_thumbnail(url=user.display_avatar.url)

        # Informations principales
        embed.add_field(
            name="<:user:1398729712204779571> Information",
            value=(
                f"**ID:** `{user.id}`\n"
                f"**Mention:** {user.mention}\n"
                f"**Created:** <t:{created_at}:R>\n"
                f"**Bot:** {'Yes' if user.bot else 'No'}"
            ),
            inline=True
        )

        embed.add_field(
            name="<:settings:1398729549323440208> Status",
            value=(
                f"**Badges:** {badges_str}\n"
                f"**Mutual servers:** `{len(mutual_guilds)}`\n"
                f"**Attributes:** `{len(user_data['attributes'])}`\n"
                f"**Stored data:** {'Yes' if user_data['data'] else 'No'}"
            ),
            inline=True
        )

        # Attributs principaux
        if user_data['attributes']:
            attrs_preview = []
            for attr, value in list(user_data['attributes'].items())[:3]:
                if isinstance(value, bool):
                    val_str = "<:done:1398729525277229066>" if value else "<:undone:1398729502028333218>"
                else:
                    val_str = str(value)
                attrs_preview.append(f"`{attr}`: {val_str}")

            if len(user_data['attributes']) > 3:
                attrs_preview.append(f"*+{len(user_data['attributes']) - 3} more...*")

            embed.add_field(
                name="<:label:1398729473649676440> Attributes",
                value="\n".join(attrs_preview),
                inline=False
            )

        # Footer avec timestamp
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url
        )
        embed.timestamp = datetime.now(timezone.utc)

        return embed


class UserManagementView(discord.ui.View):
    """Vue avec les boutons de gestion"""

    def __init__(self, bot, user: discord.User, user_data: Dict[str, Any], author: discord.User):
        super().__init__(timeout=600)  # 10 minutes au lieu de 5
        self.bot = bot
        self.user = user
        self.user_data = user_data
        self.author = author
        self.current_page = "main"
        self.message = None

    async def on_timeout(self):
        """Appelé quand la vue expire"""
        try:
            for item in self.children:
                item.disabled = True

            if self.message:
                await self.message.edit(view=self)
        except:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Seul l'auteur peut utiliser les boutons ET doit être développeur"""
        # Vérifie d'abord que c'est un développeur
        if not self.bot.is_developer(interaction.user.id):
            await interaction.response.send_message(
                "<:undone:1398729502028333218> This action is reserved for developers.",
                ephemeral=True
            )
            return False

        # Vérifie ensuite que c'est l'auteur de la commande
        if interaction.user != self.author:
            await interaction.response.send_message(
                "<:undone:1398729502028333218> Only the command author can use these buttons.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Attributes", emoji="<:label:1398729473649676440>", style=discord.ButtonStyle.primary)
    async def show_attributes(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Affiche et gère les attributs"""

        embed = discord.Embed(
            title=f"<:label:1398729473649676440> {self.user}'s Attributes",
            color=COLORS["info"]
        )

        if self.user_data['attributes']:
            for attr, value in self.user_data['attributes'].items():
                if isinstance(value, bool):
                    val_str = "<:done:1398729525277229066> Enabled" if value else "<:undone:1398729502028333218> Disabled"
                else:
                    val_str = f"`{value}`"

                embed.add_field(
                    name=attr,
                    value=val_str,
                    inline=True
                )
        else:
            embed.description = "No attributes defined for this user."

        view = AttributeActionView(self.bot, self.user, self.user_data, self.author, self)
        view.message = interaction.message

        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Data", emoji="<:data_object:1401600908323852318>", style=discord.ButtonStyle.primary)
    async def show_data(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Affiche la data stockée"""

        embed = discord.Embed(
            title=f"<:data_object:1401600908323852318> {self.user}'s Data",
            color=COLORS["info"]
        )

        if self.user_data['data']:
            data_str = json.dumps(self.user_data['data'], indent=2, ensure_ascii=False)

            if len(data_str) > 1000:
                data_str = data_str[:997] + "..."

            embed.description = f"```json\n{data_str}\n```"

            embed.add_field(
                name="<:settings:1398729549323440208> Information",
                value=(
                    f"**Size:** `{len(json.dumps(self.user_data['data']))}` bytes\n"
                    f"**Main keys:** `{len(self.user_data['data'])}`"
                ),
                inline=False
            )
        else:
            embed.description = "No data stored for this user."

        view = DataManagementView(self.bot, self.user, self.user_data, self.author, self)

        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Actions", emoji="<:settings:1398729549323440208>", style=discord.ButtonStyle.secondary)
    async def show_actions(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Affiche les actions disponibles"""

        embed = discord.Embed(
            title=f"<:settings:1398729549323440208> Actions for {self.user}",
            description="Choose an action to perform:",
            color=COLORS["warning"]
        )

        view = UserActionsView(self.bot, self.user, self.user_data, self.author, self)
        view.message = interaction.message

        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="History", emoji="<:history:1401600464587456512>", style=discord.ButtonStyle.secondary)
    async def show_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Affiche l'historique des changements"""

        embed = discord.Embed(
            title=f"<:history:1401600464587456512> {self.user}'s History",
            color=COLORS["info"]
        )

        try:
            async with self.bot.db.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT * FROM attribute_changes
                    WHERE entity_type = 'user' AND entity_id = $1
                    ORDER BY changed_at DESC
                    LIMIT 10
                """, self.user.id)

            if rows:
                for row in rows:
                    changed_by = self.bot.get_user(row['changed_by']) or f"ID: {row['changed_by']}"
                    timestamp = int(row['changed_at'].timestamp())

                    value_text = (
                        f"**Attribute:** `{row['attribute_name']}`\n"
                        f"**Before:** `{row['old_value'] or 'Not defined'}`\n"
                        f"**After:** `{row['new_value'] or 'Removed'}`\n"
                        f"**By:** {changed_by}\n"
                        f"**Reason:** {row['reason'] or 'None'}"
                    )

                    embed.add_field(
                        name=f"<t:{timestamp}:R>",
                        value=value_text,
                        inline=False
                    )
            else:
                embed.description = "No history found for this user."

        except Exception as e:
            embed.description = f"<:undone:1398729502028333218> Error: {str(e)}"

        view = BackButtonView(self, interaction.message)

        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Refresh", emoji="<:sync:1398729150885269546>", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Rafraîchit les données"""

        await interaction.response.defer()

        try:
            self.user_data = await self.bot.db.get_user(self.user.id)

            class FakeContext:
                def __init__(self, author):
                    self.author = author

            fake_ctx = FakeContext(self.author)

            embed = UserManagement._create_user_embed(self.bot, self.user, self.user_data, fake_ctx)

            new_view = UserManagementView(self.bot, self.user, self.user_data, self.author)
            new_view.message = self.message

            await interaction.edit_original_response(embed=embed, view=new_view)

        except Exception as e:
            await interaction.followup.send(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ferme le panel"""
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()


class AttributeActionView(discord.ui.View):
    """Vue pour gérer les attributs"""

    def __init__(self, bot, user: discord.User, user_data: Dict[str, Any], author: discord.User, parent_view):
        super().__init__(timeout=600)
        self.bot = bot
        self.user = user
        self.user_data = user_data
        self.author = author
        self.parent_view = parent_view
        self.message = None

    async def on_timeout(self):
        """Désactive les boutons au timeout"""
        try:
            for item in self.children:
                item.disabled = True
            if self.message:
                await self.message.edit(view=self)
        except:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie que c'est un développeur ET l'auteur"""
        if not self.bot.is_developer(interaction.user.id):
            await interaction.response.send_message(
                "<:undone:1398729502028333218> This action is reserved for developers.",
                ephemeral=True
            )
            return False

        if interaction.user != self.author:
            await interaction.response.send_message(
                "<:undone:1398729502028333218> Only the command author can use these buttons.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Add", emoji="<:add:1401608434230493254>", style=discord.ButtonStyle.success)
    async def add_attribute(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Modal pour ajouter un attribut"""
        modal = AddAttributeModal(self.bot, self.user, self.author, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Modify", emoji="<:edit:1401600709824086169>", style=discord.ButtonStyle.primary)
    async def modify_attribute(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Sélecteur pour modifier un attribut"""
        if not self.user_data['attributes']:
            await interaction.response.send_message(
                "<:undone:1398729502028333218> No attributes to modify",
                ephemeral=True
            )
            return

        view = ModifyAttributeView(self.bot, self.user, self.user_data, self.author)

        await interaction.response.send_message(
            "Select the attribute to modify:",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="Remove", emoji="<:undone:1398729502028333218>", style=discord.ButtonStyle.danger)
    async def remove_attribute(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Sélecteur pour supprimer un attribut"""
        if not self.user_data['attributes']:
            await interaction.response.send_message(
                "<:undone:1398729502028333218> No attributes to remove",
                ephemeral=True
            )
            return

        view = RemoveAttributeView(self.bot, self.user, self.user_data, self.author)

        await interaction.response.send_message(
            "Select the attribute to remove:",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="Back", emoji="<:back:1401600847733067806>", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Retour au menu principal"""
        self.parent_view.user_data = await self.bot.db.get_user(self.user.id)

        class FakeContext:
            def __init__(self, author):
                self.author = author

        fake_ctx = FakeContext(self.author)

        embed = UserManagement._create_user_embed(self.bot, self.user, self.parent_view.user_data, fake_ctx)

        await interaction.response.edit_message(embed=embed, view=self.parent_view)

    async def refresh_parent_data(self):
        """Rafraîchit les données du parent view"""
        self.parent_view.user_data = await self.bot.db.get_user(self.user.id)
        self.user_data = self.parent_view.user_data


class UserActionsView(discord.ui.View):
    """Vue avec les actions utilisateur"""

    def __init__(self, bot, user: discord.User, user_data: Dict[str, Any], author: discord.User, parent_view):
        super().__init__(timeout=600)
        self.bot = bot
        self.user = user
        self.user_data = user_data
        self.author = author
        self.parent_view = parent_view
        self.message = None

        # Ajoute les boutons dynamiquement avec les bonnes couleurs
        self._add_dynamic_buttons()

    def _add_dynamic_buttons(self):
        """Ajoute les boutons avec les bonnes couleurs selon l'état"""
        # Bouton Premium
        has_premium = self.user_data['attributes'].get('PREMIUM', False)
        premium_btn = discord.ui.Button(
            label="Premium",
            emoji="<:premium:1401602724801548381>",
            style=discord.ButtonStyle.success if has_premium else discord.ButtonStyle.danger,
            row=0
        )
        premium_btn.callback = self.toggle_premium
        self.add_item(premium_btn)

        # Bouton Blacklist
        is_blacklisted = self.user_data['attributes'].get('BLACKLISTED', False)
        blacklist_btn = discord.ui.Button(
            label="Blacklist",
            emoji="<:blacklist:1401596866478477363>",
            style=discord.ButtonStyle.success if is_blacklisted else discord.ButtonStyle.danger,
            row=0
        )
        blacklist_btn.callback = self.toggle_blacklist
        self.add_item(blacklist_btn)

        # Bouton Track
        is_tracked = self.user_data['attributes'].get('TRACK', False)
        track_btn = discord.ui.Button(
            label="Track",
            emoji="<:track:1401596933222695002>",
            style=discord.ButtonStyle.success if is_tracked else discord.ButtonStyle.danger,
            row=0
        )
        track_btn.callback = self.toggle_track
        self.add_item(track_btn)

        # Autres boutons
        reset_btn = discord.ui.Button(
            label="Reset",
            emoji="<:sync:1398729150885269546>",
            style=discord.ButtonStyle.danger,
            row=1
        )
        reset_btn.callback = self.reset_user
        self.add_item(reset_btn)

        export_btn = discord.ui.Button(
            label="Export",
            emoji="<:download:1401600503867248730>",
            style=discord.ButtonStyle.secondary,
            row=1
        )
        export_btn.callback = self.export_data
        self.add_item(export_btn)

        back_btn = discord.ui.Button(
            label="Back",
            emoji="<:back:1401600847733067806>",
            style=discord.ButtonStyle.secondary,
            row=1
        )
        back_btn.callback = self.back
        self.add_item(back_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie que c'est un développeur ET l'auteur"""
        if not self.bot.is_developer(interaction.user.id):
            await interaction.response.send_message(
                "<:undone:1398729502028333218> This action is reserved for developers.",
                ephemeral=True
            )
            return False
        return interaction.user == self.author

    async def toggle_premium(self, interaction: discord.Interaction):
        """Active/désactive le premium"""
        has_premium = self.user_data['attributes'].get('PREMIUM', False)
        new_value = not has_premium

        try:
            await self.bot.db.set_attribute(
                'user', self.user.id, 'PREMIUM', new_value,
                self.author.id, f"{'Removal' if has_premium else 'Addition'} via panel by {self.author}"
            )

            # Rafraîchit les données
            self.user_data = await self.bot.db.get_user(self.user.id)
            self.parent_view.user_data = self.user_data

            # Recrée la vue avec les bonnes couleurs
            new_view = UserActionsView(self.bot, self.user, self.user_data, self.author, self.parent_view)
            new_view.message = self.message

            # Recrée l'embed
            embed = discord.Embed(
                title=f"<:settings:1398729549323440208> Actions for {self.user}",
                description=f"<:done:1398729525277229066> Premium {'enabled' if new_value else 'disabled'}!",
                color=COLORS["success"]
            )

            await interaction.response.edit_message(embed=embed, view=new_view)

        except Exception as e:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )

    async def toggle_blacklist(self, interaction: discord.Interaction):
        """Active/désactive la blacklist"""
        is_blacklisted = self.user_data['attributes'].get('BLACKLISTED', False)

        if not is_blacklisted:
            # Demande confirmation pour blacklist
            view = ConfirmView()
            embed = discord.Embed(
                title="Confirmation required",
                description=f"Are you sure you want to blacklist {self.user.mention}?\n"
                            "They will no longer be able to use the bot.",
                color=COLORS["error"]
            )

            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            await view.wait()

            if not view.value:
                return

        try:
            new_value = not is_blacklisted
            await self.bot.db.set_attribute(
                'user', self.user.id, 'BLACKLISTED', new_value,
                self.author.id, f"{'Removal' if is_blacklisted else 'Addition'} blacklist via panel"
            )

            # Rafraîchit les données
            self.user_data = await self.bot.db.get_user(self.user.id)
            self.parent_view.user_data = self.user_data

            # Recrée la vue
            new_view = UserActionsView(self.bot, self.user, self.user_data, self.author, self.parent_view)
            new_view.message = self.message

            # Recrée l'embed
            embed = discord.Embed(
                title=f"<:settings:1398729549323440208> Actions for {self.user}",
                description=f"<:done:1398729525277229066> {'Blacklist enabled' if new_value else 'Blacklist removed'}!",
                color=COLORS["success"]
            )

            if is_blacklisted:
                await interaction.response.edit_message(embed=embed, view=new_view)
            else:
                await interaction.edit_original_response(embed=embed, view=new_view)

        except Exception as e:
            if is_blacklisted:
                await interaction.response.send_message(
                    f"<:undone:1398729502028333218> Error: {str(e)}",
                    ephemeral=True
                )
            else:
                await interaction.edit_original_response(
                    content=f"<:undone:1398729502028333218> Error: {str(e)}",
                    embed=None,
                    view=None
                )

    async def toggle_track(self, interaction: discord.Interaction):
        """Active/désactive le tracking"""
        is_tracked = self.user_data['attributes'].get('TRACK', False)
        new_value = not is_tracked

        try:
            await self.bot.db.set_attribute(
                'user', self.user.id, 'TRACK', new_value,
                self.author.id, f"{'Stop' if is_tracked else 'Start'} tracking via panel"
            )

            # Rafraîchit les données
            self.user_data = await self.bot.db.get_user(self.user.id)
            self.parent_view.user_data = self.user_data

            # Recrée la vue
            new_view = UserActionsView(self.bot, self.user, self.user_data, self.author, self.parent_view)
            new_view.message = self.message

            # Recrée l'embed
            embed = discord.Embed(
                title=f"<:settings:1398729549323440208> Actions for {self.user}",
                description=f"<:done:1398729525277229066> Tracking {'enabled' if new_value else 'disabled'}!",
                color=COLORS["success"]
            )

            await interaction.response.edit_message(embed=embed, view=new_view)

        except Exception as e:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )

    async def reset_user(self, interaction: discord.Interaction):
        """Réinitialise toutes les données de l'utilisateur"""
        modal = ResetConfirmModal(self.bot, self.user, self.author)
        await interaction.response.send_modal(modal)

    async def export_data(self, interaction: discord.Interaction):
        """Exporte toutes les données de l'utilisateur"""
        await interaction.response.defer(ephemeral=True)

        try:
            export_data = {
                "user": {
                    "id": self.user.id,
                    "username": str(self.user),
                    "created_at": self.user.created_at.isoformat()
                },
                "database": {
                    "attributes": self.user_data['attributes'],
                    "data": self.user_data['data'],
                    "created_at": self.user_data.get('created_at', 'N/A'),
                    "updated_at": self.user_data.get('updated_at', 'N/A')
                },
                "export_info": {
                    "exported_by": str(self.author),
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "bot_version": "Moddy v1.0"
                }
            }

            json_str = json.dumps(export_data, indent=2, ensure_ascii=False)

            file = discord.File(
                io.StringIO(json_str),
                filename=f"user_{self.user.id}_export.json"
            )

            await interaction.followup.send(
                f"<:done:1398729525277229066> Complete export of {self.user.mention}",
                file=file,
                ephemeral=True
            )

        except Exception as e:
            await interaction.followup.send(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )

    async def back(self, interaction: discord.Interaction):
        """Retour au menu principal"""
        self.parent_view.user_data = await self.bot.db.get_user(self.user.id)

        class FakeContext:
            def __init__(self, author):
                self.author = author

        fake_ctx = FakeContext(self.author)

        embed = UserManagement._create_user_embed(self.bot, self.user, self.parent_view.user_data, fake_ctx)

        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class DataManagementView(discord.ui.View):
    """Vue pour gérer la data utilisateur"""

    def __init__(self, bot, user: discord.User, user_data: Dict[str, Any], author: discord.User, parent_view):
        super().__init__(timeout=600)
        self.bot = bot
        self.user = user
        self.user_data = user_data
        self.author = author
        self.parent_view = parent_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie que c'est un développeur ET l'auteur"""
        if not self.bot.is_developer(interaction.user.id):
            await interaction.response.send_message(
                "<:undone:1398729502028333218> This action is reserved for developers.",
                ephemeral=True
            )
            return False
        return interaction.user == self.author

    @discord.ui.button(label="Edit JSON", emoji="<:edit:1401600709824086169>", style=discord.ButtonStyle.primary)
    async def edit_json(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ouvre un modal pour éditer le JSON complet"""
        modal = EditDataModal(self.bot, self.user, self.user_data, self.author)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Add key", emoji="<:add:1401608434230493254>", style=discord.ButtonStyle.success)
    async def add_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Ajoute une nouvelle clé à la data"""
        modal = AddDataKeyModal(self.bot, self.user, self.author)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Remove key", emoji="<:undone:1398729502028333218>", style=discord.ButtonStyle.danger)
    async def remove_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Supprime une clé de la data"""
        if not self.user_data['data']:
            await interaction.response.send_message(
                "<:undone:1398729502028333218> No data to remove",
                ephemeral=True
            )
            return

        view = RemoveDataKeyView(self.bot, self.user, self.user_data, self.author)
        await interaction.response.send_message(
            "Select the key to remove:",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(label="Reset", emoji="<:sync:1398729150885269546>", style=discord.ButtonStyle.danger)
    async def reset_data(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Réinitialise toute la data"""
        view = ConfirmView()
        embed = discord.Embed(
            title="Confirmation required",
            description=f"Are you sure you want to reset all data for {self.user.mention}?",
            color=COLORS["error"]
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        await view.wait()

        if view.value:
            try:
                async with self.bot.db.pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE users 
                        SET data = '{}'::jsonb, updated_at = NOW()
                        WHERE user_id = $1
                    """, self.user.id)

                await interaction.edit_original_response(
                    content=f"<:done:1398729525277229066> Data reset for {self.user.mention}",
                    embed=None,
                    view=None
                )
            except Exception as e:
                await interaction.edit_original_response(
                    content=f"<:undone:1398729502028333218> Error: {str(e)}",
                    embed=None,
                    view=None
                )

    @discord.ui.button(label="Back", emoji="<:back:1401600847733067806>", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Retour au menu principal"""
        self.parent_view.user_data = await self.bot.db.get_user(self.user.id)

        class FakeContext:
            def __init__(self, author):
                self.author = author

        fake_ctx = FakeContext(self.author)

        embed = UserManagement._create_user_embed(self.bot, self.user, self.parent_view.user_data, fake_ctx)

        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class EditDataModal(discord.ui.Modal, title="Edit JSON data"):
    """Modal pour éditer le JSON complet"""

    def __init__(self, bot, user: discord.User, user_data: Dict[str, Any], author: discord.User):
        super().__init__()
        self.bot = bot
        self.user = user
        self.user_data = user_data
        self.author = author

        # Prépare le JSON actuel
        current_json = json.dumps(user_data['data'], indent=2, ensure_ascii=False)

        # Limite à 1024 caractères pour le champ Discord
        if len(current_json) > 1024:
            current_json = current_json[:1021] + "..."

        self.json_input = discord.ui.TextInput(
            label="JSON Data",
            style=discord.TextStyle.paragraph,
            placeholder='{"key": "value"}',
            default=current_json,
            max_length=4000,
            required=True
        )
        self.add_item(self.json_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse le JSON
            new_data = json.loads(self.json_input.value)

            # Met à jour dans la BDD
            async with self.bot.db.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE users 
                    SET data = $1::jsonb, updated_at = NOW()
                    WHERE user_id = $2
                """, json.dumps(new_data), self.user.id)

            embed = ModdyResponse.success(
                "Data modified",
                f"<:done:1398729525277229066> {self.user.mention}'s data has been updated"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except json.JSONDecodeError as e:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Invalid JSON: {str(e)}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )


class AddDataKeyModal(discord.ui.Modal, title="Add a key to data"):
    """Modal pour ajouter une nouvelle clé"""

    def __init__(self, bot, user: discord.User, author: discord.User):
        super().__init__()
        self.bot = bot
        self.user = user
        self.author = author

    key_path = discord.ui.TextInput(
        label="Key path",
        placeholder="Ex: preferences.theme or simply theme",
        max_length=100,
        required=True
    )

    value_input = discord.ui.TextInput(
        label="Value (JSON)",
        style=discord.TextStyle.paragraph,
        placeholder='Examples:\n"text"\n123\ntrue\n{"key": "value"}\n["item1", "item2"]',
        max_length=1000,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse la valeur
            if self.value_input.value.lower() == "true":
                value = True
            elif self.value_input.value.lower() == "false":
                value = False
            elif self.value_input.value.lower() == "null":
                value = None
            else:
                try:
                    value = json.loads(self.value_input.value)
                except:
                    # Si ce n'est pas du JSON valide, traite comme string
                    value = self.value_input.value

            # Met à jour dans la BDD
            await self.bot.db.update_user_data(self.user.id, self.key_path.value, value)

            embed = ModdyResponse.success(
                "Key added",
                f"<:done:1398729525277229066> `{self.key_path.value}` = `{value}`"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )


class RemoveDataKeyView(discord.ui.View):
    """Vue pour sélectionner une clé à supprimer"""

    def __init__(self, bot, user: discord.User, user_data: Dict[str, Any], author: discord.User):
        super().__init__(timeout=60)
        self.bot = bot
        self.user = user
        self.user_data = user_data
        self.author = author

        # Crée les options depuis les clés de premier niveau
        options = []
        for key in user_data['data'].keys():
            value_preview = str(user_data['data'][key])
            if len(value_preview) > 50:
                value_preview = value_preview[:47] + "..."

            options.append(
                discord.SelectOption(
                    label=key,
                    value=key,
                    description=value_preview,
                    emoji="<:undone:1398729502028333218>"
                )
            )

        self.select = discord.ui.Select(
            placeholder="Choose a key to remove",
            options=options[:25]
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        """Quand une clé est sélectionnée"""
        selected = self.select.values[0]

        try:
            # Récupère la data actuelle
            user_data = await self.bot.db.get_user(self.user.id)
            current_data = user_data['data']

            # Supprime la clé
            if selected in current_data:
                del current_data[selected]

            # Met à jour dans la BDD
            async with self.bot.db.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE users 
                    SET data = $1::jsonb, updated_at = NOW()
                    WHERE user_id = $2
                """, json.dumps(current_data), self.user.id)

            embed = ModdyResponse.success(
                "Key removed",
                f"<:done:1398729525277229066> The key `{selected}` has been removed"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )


class BackButtonView(discord.ui.View):
    """Vue simple avec juste un bouton retour"""

    def __init__(self, parent_view, message):
        super().__init__(timeout=600)
        self.parent_view = parent_view
        self.message = message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie que c'est un développeur"""
        if not self.parent_view.bot.is_developer(interaction.user.id):
            await interaction.response.send_message(
                "<:undone:1398729502028333218> This action is reserved for developers.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Back", emoji="<:back:1401600847733067806>", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Retour au menu principal"""
        self.parent_view.user_data = await self.parent_view.bot.db.get_user(self.parent_view.user.id)

        class FakeContext:
            def __init__(self, author):
                self.author = author

        fake_ctx = FakeContext(self.parent_view.author)

        embed = UserManagement._create_user_embed(
            self.parent_view.bot,
            self.parent_view.user,
            self.parent_view.user_data,
            fake_ctx
        )

        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class AddAttributeModal(discord.ui.Modal, title="Add an attribute"):
    """Modal pour ajouter un attribut"""

    def __init__(self, bot, user: discord.User, author: discord.User, parent_view=None):
        super().__init__()
        self.bot = bot
        self.user = user
        self.author = author
        self.parent_view = parent_view

    attribute_name = discord.ui.TextInput(
        label="Attribute name",
        placeholder="Ex: BETA, PREMIUM, LANG...",
        max_length=50,
        required=True
    )

    attribute_value = discord.ui.TextInput(
        label="Value",
        placeholder="true, false, FR, EN... (leave empty for true)",
        max_length=100,
        required=False
    )

    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why this attribute is being added",
        max_length=200,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        value = self.attribute_value.value or "true"
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif value.isdigit():
            value = int(value)

        try:
            await self.bot.db.set_attribute(
                'user', self.user.id,
                self.attribute_name.value.upper(),
                value,
                self.author.id,
                self.reason.value or "Added via panel"
            )

            # Rafraîchit les données du parent si disponible
            if self.parent_view and hasattr(self.parent_view, 'refresh_parent_data'):
                await self.parent_view.refresh_parent_data()

            embed = ModdyResponse.success(
                "Attribute added",
                f"<:done:1398729525277229066> `{self.attribute_name.value.upper()}` = `{value}`"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )


class ModifyAttributeView(discord.ui.View):
    """Vue pour sélectionner un attribut à modifier"""

    def __init__(self, bot, user: discord.User, user_data: Dict[str, Any], author: discord.User):
        super().__init__(timeout=60)
        self.bot = bot
        self.user = user
        self.user_data = user_data
        self.author = author

        options = []
        for attr, value in user_data['attributes'].items():
            options.append(
                discord.SelectOption(
                    label=attr,
                    value=attr,
                    description=f"Current value: {value}"
                )
            )

        self.select = discord.ui.Select(
            placeholder="Choose an attribute",
            options=options[:25]
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        """Quand un attribut est sélectionné"""
        selected = self.select.values[0]
        current_value = self.user_data['attributes'][selected]

        modal = ModifyAttributeModal(self.bot, self.user, self.author, selected, current_value)
        await interaction.response.send_modal(modal)


class RemoveAttributeView(discord.ui.View):
    """Vue pour sélectionner un attribut à supprimer"""

    def __init__(self, bot, user: discord.User, user_data: Dict[str, Any], author: discord.User):
        super().__init__(timeout=60)
        self.bot = bot
        self.user = user
        self.user_data = user_data
        self.author = author

        options = []
        for attr, value in user_data['attributes'].items():
            options.append(
                discord.SelectOption(
                    label=attr,
                    value=attr,
                    description=f"Value: {value}",
                    emoji="<:undone:1398729502028333218>"
                )
            )

        self.select = discord.ui.Select(
            placeholder="Choose an attribute to remove",
            options=options[:25]
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

    async def select_callback(self, interaction: discord.Interaction):
        """Quand un attribut est sélectionné"""
        selected = self.select.values[0]

        try:
            await self.bot.db.set_attribute(
                'user', self.user.id, selected, None,
                self.author.id, f"Removed via panel"
            )

            embed = ModdyResponse.success(
                "Attribute removed",
                f"<:done:1398729525277229066> The attribute `{selected}` has been removed"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )


class ModifyAttributeModal(discord.ui.Modal, title="Modify an attribute"):
    """Modal pour modifier un attribut"""

    def __init__(self, bot, user: discord.User, author: discord.User, attr_name: str, current_value):
        super().__init__()
        self.bot = bot
        self.user = user
        self.author = author
        self.attr_name = attr_name

        self.value_input = discord.ui.TextInput(
            label=f"New value for {attr_name}",
            placeholder=f"Current value: {current_value}",
            default=str(current_value),
            max_length=100,
            required=True
        )
        self.add_item(self.value_input)

        self.reason = discord.ui.TextInput(
            label="Reason for modification",
            placeholder="Optional",
            max_length=200,
            required=False
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        value = self.value_input.value
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        elif value.isdigit():
            value = int(value)

        try:
            await self.bot.db.set_attribute(
                'user', self.user.id, self.attr_name, value,
                self.author.id, self.reason.value or "Modified via panel"
            )

            embed = ModdyResponse.success(
                "Attribute modified",
                f"<:done:1398729525277229066> `{self.attr_name}` = `{value}`"
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )


class ConfirmView(discord.ui.View):
    """Vue de confirmation simple"""

    def __init__(self):
        super().__init__(timeout=30)
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = True
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.value = False
        self.stop()


class ResetConfirmModal(discord.ui.Modal, title="Reset user"):
    """Modal de confirmation pour reset"""

    def __init__(self, bot, user: discord.User, author: discord.User):
        super().__init__()
        self.bot = bot
        self.user = user
        self.author = author

    confirm_text = discord.ui.TextInput(
        label="Type the username to confirm",
        placeholder="Exact username",
        max_length=100,
        required=True
    )

    reason = discord.ui.TextInput(
        label="Reason for reset",
        placeholder="Why reset this user?",
        max_length=200,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm_text.value != self.user.name:
            await interaction.response.send_message(
                f"<:undone:1398729502028333218> Incorrect confirmation. You must type exactly: `{self.user.name}`",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            user_data = await self.bot.db.get_user(self.user.id)

            for attr in list(user_data['attributes'].keys()):
                await self.bot.db.set_attribute(
                    'user', self.user.id, attr, None,
                    self.author.id, f"Complete reset: {self.reason.value}"
                )

            async with self.bot.db.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE users
                    SET data = '{}'::jsonb, updated_at = NOW()
                    WHERE user_id = $1
                """, self.user.id)

            embed = ModdyResponse.success(
                "User reset",
                f"<:done:1398729525277229066> All data for {self.user.mention} has been deleted."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(
                f"<:undone:1398729502028333218> Error: {str(e)}",
                ephemeral=True
            )


def setup(bot):
    bot.add_cog(UserManagement(bot))
