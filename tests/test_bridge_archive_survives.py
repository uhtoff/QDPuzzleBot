"""Deleting a *channel* must not purge its captured messages (that's the archive).
Only an explicit per-message delete purges (privacy). The bridge has no
on_guild_channel_delete listener, so channel deletion is inert for the messages table."""
import inspect


def test_bridge_has_no_channel_delete_listener():
    import bot.cogs.bridge as bridge
    src = inspect.getsource(bridge)
    assert "on_guild_channel_delete" not in src
    assert "on_raw_message_delete" in src  # per-message privacy purge still present
