"""
Système centralisé pour les embeds épurés
Style moderne avec couleurs élégantes
"""

import nextcord as discord
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timezone


class ModdyColors:
    """Palette de couleurs modernes et élégantes"""

    # Couleurs principales
    PRIMARY = 0x5865F2  # Blurple Discord moderne
    SUCCESS = 0x23A55A  # Vert moderne Discord
    WARNING = 0xF0B232  # Jaune doré élégant
    ERROR = 0xF23F43  # Rouge moderne Discord
    INFO = 0x5865F2  # Bleu info

    # Couleurs secondaires
    DARK = 0x1E1F22  # Fond sombre Discord
    LIGHT = 0x313338  # Gris clair Discord
    ACCENT = 0x7289DA  # Bleu accent
    PURPLE = 0x9B59B6  # Violet élégant
    TEAL = 0x11806A  # Turquoise moderne
    PINK = 0xE91E63  # Rose moderne

    # Gradients (utiliser la première couleur)
    GRADIENT_BLUE = 0x3498DB
    GRADIENT_GREEN = 0x2ECC71
    GRADIENT_ORANGE = 0xE67E22
    GRADIENT_RED = 0xE74C3C


class ModdyEmbed:
    """Classe pour créer des embeds standardisés épurés"""

    @staticmethod
    def create(
            title: Optional[str] = None,
            description: Optional[str] = None,
            color: Optional[int] = None,
            fields: Optional[List[tuple]] = None,
            footer: Optional[str] = None,
            author: Optional[dict] = None,
            thumbnail: Optional[str] = None,
            image: Optional[str] = None,
            timestamp: bool = False
    ) -> discord.Embed:
        """
        Crée un embed épuré avec style moderne

        Args:
            title: Titre de l'embed
            description: Description principale
            color: Couleur (utilise ModdyColors)
            fields: Liste de tuples (name, value, inline)
            footer: Texte du footer
            author: Dict avec name, icon_url
            thumbnail: URL de la miniature
            image: URL de l'image
            timestamp: Si True, ajoute un timestamp
        """
        # Couleur par défaut subtile
        if color is None:
            color = ModdyColors.LIGHT

        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )

        if fields:
            for field in fields:
                name = field[0]
                value = field[1]
                inline = field[2] if len(field) > 2 else False
                embed.add_field(name=name, value=value, inline=inline)

        if footer:
            embed.set_footer(text=footer)

        if author:
            embed.set_author(
                name=author.get('name', ''),
                icon_url=author.get('icon_url', None)
            )

        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        if image:
            embed.set_image(url=image)

        if timestamp:
            # Utilise un datetime timezone-aware pour éviter les avertissements
            embed.timestamp = datetime.now(timezone.utc)

        return embed

    @staticmethod
    def minimal(description: str, color: int = ModdyColors.LIGHT) -> discord.Embed:
        """Crée un embed minimal avec juste une description"""
        return discord.Embed(description=description, color=color)

    @staticmethod
    def field_block(title: str, fields: Dict[str, Any], color: int = ModdyColors.PRIMARY) -> discord.Embed:
        """Crée un embed avec des champs organisés"""
        embed = discord.Embed(title=title, color=color)

        for name, value in fields.items():
            # Formater automatiquement les valeurs
            if isinstance(value, (int, float)):
                formatted_value = f"`{value}`"
            elif isinstance(value, bool):
                formatted_value = "✓" if value else "✗"
            else:
                formatted_value = str(value)
                # Mettre les valeurs dynamiques entre backticks
                if not formatted_value.startswith("`"):
                    formatted_value = f"`{formatted_value}`"

            embed.add_field(name=name, value=formatted_value, inline=True)

        return embed


class ModdyResponse:
    """Templates de réponses standardisées avec style moderne"""

    @staticmethod
    def success(title: str, description: str, footer: Optional[str] = None) -> discord.Embed:
        """Message de succès avec couleur verte moderne"""
        embed = ModdyEmbed.create(
            title=title,
            description=description,
            color=ModdyColors.SUCCESS,
            footer=footer
        )
        return embed

    @staticmethod
    def error(title: str, description: str, footer: Optional[str] = None) -> discord.Embed:
        """Message d'erreur avec couleur rouge moderne"""
        embed = ModdyEmbed.create(
            title=title,
            description=description,
            color=ModdyColors.ERROR,
            footer=footer
        )
        return embed

    @staticmethod
    def warning(title: str, description: str, footer: Optional[str] = None) -> discord.Embed:
        """Message d'avertissement avec couleur jaune dorée"""
        embed = ModdyEmbed.create(
            title=title,
            description=description,
            color=ModdyColors.WARNING,
            footer=footer
        )
        return embed

    @staticmethod
    def info(title: str, description: str = None, fields: List[tuple] = None) -> discord.Embed:
        """Message d'information avec couleur bleue"""
        return ModdyEmbed.create(
            title=title,
            description=description,
            color=ModdyColors.INFO,
            fields=fields
        )

    @staticmethod
    def loading(message: str = "Chargement en cours...") -> discord.Embed:
        """Message de chargement épuré"""
        return ModdyEmbed.minimal(
            description=message,
            color=ModdyColors.LIGHT
        )

    @staticmethod
    def confirm(title: str, description: str, footer: str = None) -> discord.Embed:
        """Message de confirmation avec couleur subtile"""
        return ModdyEmbed.create(
            title=title,
            description=description,
            color=ModdyColors.ACCENT,
            footer=footer
        )


def format_diagnostic_embed(data: dict) -> discord.Embed:
    """Formate un embed de diagnostic avec style moderne"""

    embed = discord.Embed(
        title="Diagnostic Système",
        color=ModdyColors.PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )

    # Section Discord API
    api_status = "En ligne" if data['api_latency'] < 200 else "Dégradé"
    embed.add_field(
        name="Discord API",
        value=f"**{api_status}**\n"
              f"Latence: `{data['api_latency']}ms`\n"
              f"Gateway: `v{data['discord_version']}`",
        inline=True
    )

    # Section Bot
    msg_status = "Optimal" if data['message_latency'] < 100 else "Normal" if data['message_latency'] < 200 else "Lent"
    embed.add_field(
        name="Bot",
        value=f"**{msg_status}**\n"
              f"Réponse: `{data['message_latency']}ms`\n"
              f"Uptime: `{data['uptime']}`",
        inline=True
    )

    # Section Base de données
    embed.add_field(
        name="Base de données",
        value=f"**{data['db_status']}**\n"
              f"Latence: {data['db_latency']}\n"
              f"Type: PostgreSQL",
        inline=True
    )

    # Ligne vide pour la mise en page
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # Section Performance
    embed.add_field(
        name="Performance",
        value=f"CPU: `{data['cpu_percent']}%`\n"
              f"RAM: `{data['memory_usage']:.1f} MB`\n"
              f"Threads: `{data['threads']}`",
        inline=True
    )

    # Section Statistiques
    embed.add_field(
        name="Statistiques",
        value=f"Serveurs: `{data['guilds']}`\n"
              f"Utilisateurs: `{data['users']}`\n"
              f"Commandes: `{data['commands']}`",
        inline=True
    )

    # Section Système
    embed.add_field(
        name="Système",
        value=f"OS: `{data['os']}`\n"
              f"Python: `{data['python_version']}`\n"
              f"Node: `{data['node']}`",
        inline=True
    )

    if 'author' in data:
        embed.set_footer(
            text=f"Demandé par {data['author']}",
            icon_url=data.get('author_icon')
        )

    return embed


def format_commands_embed(commands_by_cog: dict) -> discord.Embed:
    """Formate un embed pour la liste des commandes"""
    embed = discord.Embed(
        title="Commandes Disponibles",
        description="Liste complète des commandes du bot",
        color=ModdyColors.PRIMARY
    )

    for cog_name, commands_list in commands_by_cog.items():
        if commands_list:
            # Limiter à 1024 caractères par field
            value = "\n".join(commands_list)[:1024]
            embed.add_field(
                name=f"**{cog_name}**",
                value=value,
                inline=True
            )

    return embed


# Fonction helper pour créer rapidement des embeds
def quick_embed(
        content: str,
        color: int = ModdyColors.PRIMARY,
        title: str = None,
        footer: str = None
) -> discord.Embed:
    """Crée rapidement un embed simple"""
    return ModdyEmbed.create(
        title=title,
        description=content,
        color=color,
        footer=footer
    )


# Export des couleurs pour usage direct
COLORS = {
    "primary": ModdyColors.PRIMARY,
    "success": ModdyColors.SUCCESS,
    "error": ModdyColors.ERROR,
    "warning": ModdyColors.WARNING,
    "info": ModdyColors.INFO,
    "dark": ModdyColors.DARK,
    "light": ModdyColors.LIGHT
}