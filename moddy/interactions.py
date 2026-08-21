"""Safe, small interaction response primitives."""

from typing import Any

import discord


class InteractionResponse:
    """Reply to interactions exactly once, regardless of current state."""

    @staticmethod
    async def send(
        interaction: discord.Interaction,
        content: str | None = None,
        *,
        ephemeral: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Send an initial response or a follow-up when already acknowledged."""

        if interaction.response.is_done():
            return await interaction.followup.send(content=content, ephemeral=ephemeral, **kwargs)
        return await interaction.response.send_message(content=content, ephemeral=ephemeral, **kwargs)

    @staticmethod
    async def defer(
        interaction: discord.Interaction,
        *,
        ephemeral: bool = True,
        thinking: bool = True,
    ) -> None:
        """Acknowledge a potentially slow interaction if needed."""

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)

    @staticmethod
    async def edit_original(interaction: discord.Interaction, **kwargs: Any) -> Any:
        """Edit the response created by this interaction."""

        return await interaction.edit_original_response(**kwargs)


__all__ = ("InteractionResponse",)
