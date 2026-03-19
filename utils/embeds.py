"""
Centralized system for clean embeds.
Modern style with elegant colors.
"""

import discord
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, timezone

from utils.emojis import DONE, UNDONE, LOADING, SETTINGS, COMMANDS


class ModdyColors:
    """Modern and elegant color palette."""

    # Primary colors
    PRIMARY = 0x5865F2  # Modern Discord Blurple
    SUCCESS = 0x23A55A  # Modern Discord Green
    WARNING = 0xF0B232  # Elegant golden yellow
    ERROR = 0xF23F43  # Modern Discord Red
    INFO = 0x5865F2  # Info blue

    # Secondary colors
    DARK = 0x1E1F22  # Discord dark background
    LIGHT = 0x313338  # Discord light grey
    ACCENT = 0x7289DA  # Accent blue
    PURPLE = 0x9B59B6  # Elegant purple
    TEAL = 0x11806A  # Modern teal
    PINK = 0xE91E63  # Modern pink

    # Gradients (use the first color)
    GRADIENT_BLUE = 0x3498DB
    GRADIENT_GREEN = 0x2ECC71
    GRADIENT_ORANGE = 0xE67E22
    GRADIENT_RED = 0xE74C3C


class ModdyEmbed:
    """Class to create standardized and clean embeds."""

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
        Creates a clean embed with a modern style.

        Args:
            title: The title of the embed.
            description: The main description.
            color: The color (uses ModdyColors).
            fields: A list of tuples (name, value, inline).
            footer: The footer text.
            author: A dict with name, icon_url.
            thumbnail: The URL of the thumbnail.
            image: The URL of the image.
            timestamp: If True, adds a timestamp.
        """
        # Subtle default color
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
            # Use a timezone-aware datetime to avoid warnings
            embed.timestamp = datetime.now(timezone.utc)

        return embed

    @staticmethod
    def minimal(description: str, color: int = ModdyColors.LIGHT) -> discord.Embed:
        """Creates a minimal embed with just a description."""
        return discord.Embed(description=description, color=color)

    @staticmethod
    def field_block(title: str, fields: Dict[str, Any], color: int = ModdyColors.PRIMARY) -> discord.Embed:
        """Creates an embed with organized fields."""
        embed = discord.Embed(title=title, color=color)

        for name, value in fields.items():
            # Automatically format values
            if isinstance(value, (int, float)):
                formatted_value = f"`{value}`"
            elif isinstance(value, bool):
                formatted_value = DONE if value else UNDONE
            else:
                formatted_value = str(value)
                # Put dynamic values in backticks
                if not formatted_value.startswith("`"):
                    formatted_value = f"`{formatted_value}`"

            embed.add_field(name=name, value=formatted_value, inline=True)

        return embed


class ModdyResponse:
    """Standardized response templates with a modern style."""

    @staticmethod
    def success(title: str, description: str, footer: Optional[str] = None) -> discord.Embed:
        """Success message with a modern green color."""
        embed = ModdyEmbed.create(
            title=f"{DONE} {title}",
            description=description,
            color=ModdyColors.SUCCESS,
            footer=footer
        )
        return embed

    @staticmethod
    def error(title: str, description: str, footer: Optional[str] = None) -> discord.Embed:
        """Error message with a modern red color."""
        embed = ModdyEmbed.create(
            title=f"{UNDONE} {title}",
            description=description,
            color=ModdyColors.ERROR,
            footer=footer
        )
        return embed

    @staticmethod
    def warning(title: str, description: str, footer: Optional[str] = None) -> discord.Embed:
        """Warning message with a golden yellow color."""
        embed = ModdyEmbed.create(
            title=title,
            description=description,
            color=ModdyColors.WARNING,
            footer=footer
        )
        return embed

    @staticmethod
    def info(title: str, description: str = None, fields: List[tuple] = None) -> discord.Embed:
        """Information message with a blue color."""
        return ModdyEmbed.create(
            title=title,
            description=description,
            color=ModdyColors.INFO,
            fields=fields
        )

    @staticmethod
    def loading(message: str = "Loading...") -> discord.Embed:
        """Clean loading message."""
        return ModdyEmbed.minimal(
            description=f"{LOADING} {message}",
            color=ModdyColors.LIGHT
        )

    @staticmethod
    def confirm(title: str, description: str, footer: str = None) -> discord.Embed:
        """Confirmation message with a subtle color."""
        return ModdyEmbed.create(
            title=title,
            description=description,
            color=ModdyColors.ACCENT,
            footer=footer
        )


def format_diagnostic_embed(data: dict) -> discord.Embed:
    """Formats a diagnostic embed with a modern style."""

    embed = discord.Embed(
        title=f"{SETTINGS} System Diagnostic",
        color=ModdyColors.PRIMARY,
        timestamp=datetime.now(timezone.utc)
    )

    # Discord API Section
    api_status = "Online" if data['api_latency'] < 200 else "Degraded"
    embed.add_field(
        name="Discord API",
        value=f"**{api_status}**\n"
              f"Latency: `{data['api_latency']}ms`\n"
              f"Gateway: `v{data['discord_version']}`",
        inline=True
    )

    # Bot Section
    msg_status = "Optimal" if data['message_latency'] < 100 else "Normal" if data['message_latency'] < 200 else "Slow"
    embed.add_field(
        name="Bot",
        value=f"**{msg_status}**\n"
              f"Response: `{data['message_latency']}ms`\n"
              f"Uptime: `{data['uptime']}`",
        inline=True
    )

    # Database Section
    embed.add_field(
        name="Database",
        value=f"**{data['db_status']}**\n"
              f"Latency: {data['db_latency']}\n"
              f"Type: PostgreSQL",
        inline=True
    )

    # Empty line for layout
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # Performance Section
    embed.add_field(
        name="Performance",
        value=f"CPU: `{data['cpu_percent']}%`\n"
              f"RAM: `{data['memory_usage']:.1f} MB`\n"
              f"Threads: `{data['threads']}`",
        inline=True
    )

    # Statistics Section
    embed.add_field(
        name="Statistics",
        value=f"Servers: `{data['guilds']}`\n"
              f"Users: `{data['users']}`\n"
              f"Commands: `{data['commands']}`",
        inline=True
    )

    # System Section
    embed.add_field(
        name="System",
        value=f"OS: `{data['os']}`\n"
              f"Python: `{data['python_version']}`\n"
              f"Node: `{data['node']}`",
        inline=True
    )

    if 'author' in data:
        embed.set_footer(
            text=f"Requested by {data['author']}",
            icon_url=data.get('author_icon')
        )

    return embed


def format_commands_embed(commands_by_cog: dict) -> discord.Embed:
    """Formats an embed for the command list."""
    embed = discord.Embed(
        title=f"{COMMANDS} Available Commands",
        description="Complete list of the bot's commands.",
        color=ModdyColors.PRIMARY
    )

    for cog_name, commands_list in commands_by_cog.items():
        if commands_list:
            # Limit to 1024 characters per field
            value = "\n".join(commands_list)[:1024]
            embed.add_field(
                name=f"**{cog_name}**",
                value=value,
                inline=True
            )

    return embed


# Helper function to quickly create simple embeds
def quick_embed(
        content: str,
        color: int = ModdyColors.PRIMARY,
        title: str = None,
        footer: str = None
) -> discord.Embed:
    """Quickly creates a simple embed."""
    return ModdyEmbed.create(
        title=title,
        description=content,
        color=color,
        footer=footer
    )


# Export colors for direct use
COLORS = {
    "primary": ModdyColors.PRIMARY,
    "success": ModdyColors.SUCCESS,
    "error": ModdyColors.ERROR,
    "warning": ModdyColors.WARNING,
    "info": ModdyColors.INFO,
    "dark": ModdyColors.DARK,
    "light": ModdyColors.LIGHT
}