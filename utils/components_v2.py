"""
Components V2 Helper for Discord.py
Utilities for creating structured messages using Discord's Components V2
"""

import discord
from discord.ui import LayoutView, Container, TextDisplay, Separator, Section, Thumbnail
from discord import SeparatorSpacing
from typing import List, Optional, Dict

from utils.emojis import EMOJIS


def create_simple_message(
    title: str,
    description: str,
    fields: Optional[List[Dict[str, str]]] = None,
    color: Optional[int] = None,
    footer: Optional[str] = None
) -> LayoutView:
    """
    Create a simple message using Components V2

    Args:
        title: Message title
        description: Message description
        fields: List of dictionaries with 'name' and 'value' keys
        color: Not used in V2, kept for compatibility
        footer: Optional footer text

    Returns:
        LayoutView ready to send
    """
    view = LayoutView()
    container = Container()

    # Add title and description
    header = f"**{title}**\n{description}"
    container.add_item(TextDisplay(header))

    # Add fields if present
    if fields:
        # Add separator before fields
        container.add_item(Separator(spacing=SeparatorSpacing.small))

        for field in fields:
            field_text = f"**{field['name']}**\n{field['value']}"
            container.add_item(TextDisplay(field_text))

    # Add footer if present
    if footer:
        container.add_item(Separator(spacing=SeparatorSpacing.small))
        container.add_item(TextDisplay(f"*{footer}*"))

    view.add_item(container)
    return view


def create_error_message(title: str, description: str, fields: Optional[List[Dict[str, str]]] = None) -> LayoutView:
    """
    Create an error message using Components V2

    Args:
        title: Error title
        description: Error description
        fields: Optional list of fields

    Returns:
        LayoutView ready to send
    """
    view = LayoutView()
    container = Container()

    error_text = f"{EMOJIS['error']} **{title}**\n{description}"
    container.add_item(TextDisplay(error_text))

    if fields:
        container.add_item(Separator(spacing=SeparatorSpacing.small))
        for field in fields:
            field_text = f"**{field['name']}**\n{field['value']}"
            container.add_item(TextDisplay(field_text))

    view.add_item(container)
    return view


def create_success_message(title: str, description: str, fields: Optional[List[Dict[str, str]]] = None, footer: Optional[str] = None) -> LayoutView:
    """
    Create a success message using Components V2

    Args:
        title: Success title
        description: Success description
        fields: Optional list of fields
        footer: Optional footer text

    Returns:
        LayoutView ready to send
    """
    view = LayoutView()
    container = Container()

    success_text = f"{EMOJIS['done']} **{title}**\n{description}"
    container.add_item(TextDisplay(success_text))

    if fields:
        container.add_item(Separator(spacing=SeparatorSpacing.small))
        for field in fields:
            field_text = f"**{field['name']}**\n{field['value']}"
            container.add_item(TextDisplay(field_text))

    if footer:
        container.add_item(Separator(spacing=SeparatorSpacing.small))
        container.add_item(TextDisplay(f"*{footer}*"))

    view.add_item(container)
    return view


def create_info_message(title: str, description: str, fields: Optional[List[Dict[str, str]]] = None, footer: Optional[str] = None) -> LayoutView:
    """
    Create an info message using Components V2

    Args:
        title: Info title
        description: Info description
        fields: Optional list of fields
        footer: Optional footer text

    Returns:
        LayoutView ready to send
    """
    view = LayoutView()
    container = Container()

    info_text = f"{EMOJIS['info']} **{title}**\n{description}"
    container.add_item(TextDisplay(info_text))

    if fields:
        container.add_item(Separator(spacing=SeparatorSpacing.small))
        for field in fields:
            field_text = f"**{field['name']}**\n{field['value']}"
            container.add_item(TextDisplay(field_text))

    if footer:
        container.add_item(Separator(spacing=SeparatorSpacing.small))
        container.add_item(TextDisplay(f"*{footer}*"))

    view.add_item(container)
    return view


def create_warning_message(title: str, description: str, fields: Optional[List[Dict[str, str]]] = None) -> LayoutView:
    """
    Create a warning message using Components V2

    Args:
        title: Warning title
        description: Warning description
        fields: Optional list of fields

    Returns:
        LayoutView ready to send
    """
    view = LayoutView()
    container = Container()

    warning_text = f"⚠️ **{title}**\n{description}"
    container.add_item(TextDisplay(warning_text))

    if fields:
        container.add_item(Separator(spacing=SeparatorSpacing.small))
        for field in fields:
            field_text = f"**{field['name']}**\n{field['value']}"
            container.add_item(TextDisplay(field_text))

    view.add_item(container)
    return view


def create_staff_info_message(
    title: str,
    user_name: str,
    user_id: int,
    fields: List[Dict[str, str]],
    footer: Optional[str] = None
) -> LayoutView:
    """
    Create a staff information message using Components V2

    Args:
        title: Message title
        user_name: User's display name
        user_id: User's ID
        fields: List of information fields
        footer: Optional footer text

    Returns:
        LayoutView ready to send
    """
    view = LayoutView()
    container = Container()

    # Header with user info
    header = f"**{title}**\n*{user_name}* (`{user_id}`)"
    container.add_item(TextDisplay(header))

    # Add separator
    container.add_item(Separator(spacing=SeparatorSpacing.small))

    # Add fields
    for field in fields:
        field_text = f"**{field['name']}**\n{field['value']}"
        container.add_item(TextDisplay(field_text))

    # Add footer if present
    if footer:
        container.add_item(Separator(spacing=SeparatorSpacing.small))
        container.add_item(TextDisplay(f"*{footer}*"))

    view.add_item(container)
    return view


def create_section_with_thumbnail(
    title: str,
    thumbnail_url: str,
    description: Optional[str] = None,
) -> Section:
    """
    Create a ui.Section with a Thumbnail accessory (image on the right).

    Intended to be added directly to a Container via container.add_item().

    Args:
        title: Primary text (supports markdown)
        thumbnail_url: URL of the image to show as thumbnail
        description: Optional secondary text below the title

    Returns:
        Section with Thumbnail accessory
    """
    items = [TextDisplay(title)]
    if description:
        items.append(TextDisplay(description))
    return Section(*items, accessory=Thumbnail(media=thumbnail_url))


def create_suspension_message(locale: str = "en-US", *, guild: bool = False) -> LayoutView:
    """
    Build the "no access to Moddy" panel shown to a suspended subject.

    A suspension is the heaviest global sanction (a ``ban`` sanction on a
    ``global`` case — see ``utils/global_sanctions.py``). It can target the
    user themselves or the server they are acting in, hence ``guild``.

    Args:
        locale: Discord locale of the viewer
        guild: True when the *server* is suspended, not the user

    Returns:
        LayoutView with the suspension notice and an appeal button
    """
    from utils.i18n import t

    suffix = "guild" if guild else "user"

    view = LayoutView()
    container = Container(accent_colour=discord.Colour(0xED4245))
    container.add_item(TextDisplay(
        f"### {EMOJIS['undone']} {t(f'global_sanctions.suspended.{suffix}.title', locale=locale)}\n"
        f"{t(f'global_sanctions.suspended.{suffix}.description', locale=locale)}\n"
        f"-# {t('global_sanctions.suspended.appeal_hint', locale=locale)}"
    ))
    view.add_item(container)

    button_row = discord.ui.ActionRow()
    button_row.add_item(discord.ui.Button(
        label=t("global_sanctions.suspended.appeal_button", locale=locale),
        url="https://moddy.app/unbl_request",
        style=discord.ButtonStyle.link
    ))
    view.add_item(button_row)

    return view


def create_limited_message(locale: str = "en-US", *, guild: bool = False) -> LayoutView:
    """
    Build the "reduced service" panel shown to a limited subject.

    A limitation is a ``restrict`` sanction on a ``global`` case: premium is
    off, no **new** module can be configured, and the automod AI stops running.

    Args:
        locale: Discord locale of the viewer
        guild: True when the *server* is limited, not the user
    """
    from utils.i18n import t

    suffix = "guild" if guild else "user"

    view = LayoutView()
    container = Container(accent_colour=discord.Colour(0xF28500))
    container.add_item(TextDisplay(
        f"### {EMOJIS['warning']} {t(f'global_sanctions.limited.{suffix}.title', locale=locale)}\n"
        f"{t(f'global_sanctions.limited.{suffix}.description', locale=locale)}\n"
        f"-# {t('global_sanctions.limited.appeal_hint', locale=locale)}"
    ))
    view.add_item(container)

    button_row = discord.ui.ActionRow()
    button_row.add_item(discord.ui.Button(
        label=t("global_sanctions.limited.appeal_button", locale=locale),
        url="https://moddy.app/unbl_request",
        style=discord.ButtonStyle.link
    ))
    view.add_item(button_row)

    return view
