"""Typed view of the Quart app used by the dashboard blueprints.

create_app() staples the live bot onto the app instance; this subclass declares
that attribute so handlers can reach it through bot_app() with real types.
Blueprints import Quart freely — only bot/web/__init__.py must stay lazy.
"""
from typing import cast

from discord.ext import commands
from quart import Quart, current_app


class BotQuart(Quart):
    bot: commands.AutoShardedBot


def bot_app() -> BotQuart:
    return cast(BotQuart, current_app)
