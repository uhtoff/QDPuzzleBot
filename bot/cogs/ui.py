"""Reusable discord.ui components for the Puzzles cog (M10)."""
import discord


class ConfirmView(discord.ui.View):
    """A Confirm/Cancel gate for irreversible actions.

    `value` is None until resolved: True on Confirm, False on Cancel, and stays
    None on timeout. Only the invoking user (author_id) may click; others get an
    ephemeral notice. Set `view.message` to the prompt message so the buttons can
    be disabled on timeout.
    """

    def __init__(self, author_id, *, timeout=30.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value = None
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This confirmation isn't for you.", ephemeral=True
            )
            return False
        return True

    def _disable_all(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Confirm delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button=None):
        self.value = True
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button=None):
        self.value = False
        self._disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        self._disable_all()
        if self.message is not None:
            await self.message.edit(view=self)
