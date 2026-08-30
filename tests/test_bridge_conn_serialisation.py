"""The bridge's dedicated MySQL connection must never be used by two threads at once.

`_lookup_puzzle_channel` runs via `asyncio.to_thread` straight off the `on_message`
listener (one asyncio Task per invocation, default multi-worker executor), and the
60s `_refresh_loop` fires `_load_puzzle_channels` on a worker thread too. A
mysql-connector connection shared across threads previously caused a SIGABRT
heap-corruption crash (see bot/store/__init__.py), so both helpers must serialise.
"""
import asyncio
import threading
import time
from unittest.mock import patch


class _FakeCursor:
    def __init__(self, probe):
        self._probe = probe

    def execute(self, *args, **kwargs):
        time.sleep(0.02)

    def fetchall(self):
        return [{"channel_id": 100}, {"channel_id": 101}]

    def fetchone(self):
        return None

    def close(self):
        self._probe.release()


class _ConcurrencyProbe:
    """Stand-in for `bridge_channels_conn` that records overlapping cursor use."""

    def __init__(self, hold=0.05):
        self._guard = threading.Lock()  # protects the probe's own counters only
        self._hold = hold
        self.active = 0
        self.max_active = 0
        self.calls = 0

    def dict_cursor(self):
        with self._guard:
            self.active += 1
            self.calls += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self._hold)  # widen the window a racing thread could enter
        return _FakeCursor(self)

    def release(self):
        with self._guard:
            self.active -= 1


async def test_concurrent_lookups_never_share_the_connection():
    from bot.cogs.bridge import MessageBridge
    probe = _ConcurrencyProbe()
    with patch("bot.store.bridge_channels_conn", probe):
        await asyncio.gather(*[
            asyncio.to_thread(MessageBridge._lookup_puzzle_channel, cid)
            for cid in (1, 2, 3, 4)
        ])
    assert probe.calls == 4, "every lookup should have opened a cursor"
    assert probe.max_active == 1, f"lookups overlapped on the shared connection (max {probe.max_active})"


async def test_refresh_and_lookup_never_share_the_connection():
    """The refresh loop and the lazy gate must contend on the *same* lock."""
    from bot.cogs.bridge import MessageBridge
    probe = _ConcurrencyProbe()
    with patch("bot.store.bridge_channels_conn", probe):
        await asyncio.gather(
            asyncio.to_thread(MessageBridge._load_puzzle_channels),
            asyncio.to_thread(MessageBridge._lookup_puzzle_channel, 7),
            asyncio.to_thread(MessageBridge._load_puzzle_channels),
            asyncio.to_thread(MessageBridge._lookup_puzzle_channel, 8),
        )
    assert probe.calls == 4
    assert probe.max_active == 1, f"refresh and lookup overlapped (max {probe.max_active})"


async def test_lookup_still_returns_its_result_and_load_still_maps_rows():
    """Serialising must not change what the helpers return."""
    from bot.cogs.bridge import MessageBridge
    probe = _ConcurrencyProbe(hold=0)
    with patch("bot.store.bridge_channels_conn", probe):
        assert await asyncio.to_thread(MessageBridge._lookup_puzzle_channel, 1) is False
        assert await asyncio.to_thread(MessageBridge._load_puzzle_channels) == {100, 101}
