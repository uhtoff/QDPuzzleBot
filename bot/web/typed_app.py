"""Typed view of the Quart app used by the dashboard blueprints.

create_app() staples the live bot onto the app instance; this subclass declares
that attribute so handlers can reach it through bot_app() with real types.
Blueprints import Quart freely — only bot/web/__init__.py must stay lazy.
"""
from typing import TYPE_CHECKING, Optional, cast

from discord.ext import commands
from quart import Quart, current_app

if TYPE_CHECKING:
    from bot.cogs.puzzles import Puzzles
    from bot.cogs.puzzles_gsheet import GoogleSheets


class BotQuart(Quart):
    bot: commands.AutoShardedBot


def bot_app() -> BotQuart:
    return cast(BotQuart, current_app)


def puzzles_cog() -> "Optional[Puzzles]":
    return cast("Optional[Puzzles]", bot_app().bot.get_cog("Puzzles"))


def gsheet_cog() -> "Optional[GoogleSheets]":
    return cast("Optional[GoogleSheets]", bot_app().bot.get_cog("GoogleSheets"))
