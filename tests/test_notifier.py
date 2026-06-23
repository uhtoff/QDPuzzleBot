"""M3.5: Notifier interface + NullNotifier no-op contract.

Method arg shapes pinned against the relocated callers:
  welcome      <- _web_send_welcome(guild, hunt, hunt_round, puzzle, channel)  (:700)
  refresh_info <- web_refresh_info(guild, hunt, puzzle)                        (:708)
  puzzle_solved<- web_post_solved_embed(guild, hunt, puzzle)                   (:893)
"""
import pytest

from bot.core.notifier import Notifier, NullNotifier, DiscordNotifier   # <-- new → right reason


@pytest.mark.asyncio
async def test_null_notifier_methods_are_noops():
    n = NullNotifier()
    # Each interface method awaits cleanly and returns None.
    assert await n.welcome(guild=None, hunt=None, hunt_round=None, puzzle=None, channel=None) is None
    assert await n.refresh_info(guild=None, hunt=None, puzzle=None) is None
    assert await n.puzzle_solved(guild=None, hunt=None, puzzle=None) is None


def test_null_notifier_implements_interface():
    assert issubclass(NullNotifier, Notifier)


def test_discord_notifier_implements_interface():
    assert issubclass(DiscordNotifier, Notifier)
