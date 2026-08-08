"""Ops hardening (O2/O3/O5, docs/reviews/2026-08-07-comprehensive-review.md).

Covers the three things that had no coverage at all: the SIGTERM handler, the
rotating log handler, and the config template matching the keys the code reads.

Locally-named helpers -- the parallel runner shards by file.
"""
import asyncio
import json
import logging
import logging.handlers
import signal
from unittest.mock import AsyncMock, MagicMock

import pytest

import bot as bot_pkg


def _ops_fake_bot():
    fake = MagicMock()
    fake.close = AsyncMock()
    fake._web_shutdown = asyncio.Event()
    return fake


def _ops_register(fake, monkeypatch):
    """Install the handlers against a stubbed add_signal_handler, returning
    {signal name: callback} as actually registered on the running loop."""
    registered = {}
    loop = asyncio.get_running_loop()

    def _fake_add(sig, cb, *args):
        registered[sig] = lambda: cb(*args)

    monkeypatch.setattr(loop, "add_signal_handler", _fake_add)
    bot_pkg.install_shutdown_handlers(fake)
    return registered


@pytest.mark.asyncio
async def test_ops_registers_both_termination_signals(monkeypatch):
    registered = _ops_register(_ops_fake_bot(), monkeypatch)
    # Both, not just SIGINT: start.sh and systemd send SIGTERM.
    assert set(registered) == {signal.SIGTERM, signal.SIGINT}


@pytest.mark.asyncio
async def test_ops_sigterm_closes_bot_and_stops_dashboard(monkeypatch):
    fake = _ops_fake_bot()
    registered = _ops_register(fake, monkeypatch)

    registered[signal.SIGTERM]()
    await asyncio.sleep(0)  # let the scheduled close() task run

    fake.close.assert_awaited_once()
    assert fake._web_shutdown.is_set(), "hypercorn is never told to stop serving"


@pytest.mark.asyncio
async def test_ops_sigint_closes_bot_too(monkeypatch):
    fake = _ops_fake_bot()
    registered = _ops_register(fake, monkeypatch)

    registered[signal.SIGINT]()
    await asyncio.sleep(0)

    fake.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_ops_second_signal_exits_without_closing_twice(monkeypatch):
    fake = _ops_fake_bot()
    registered = _ops_register(fake, monkeypatch)
    exits = []
    monkeypatch.setattr(bot_pkg.os, "_exit", lambda code: exits.append(code))

    registered[signal.SIGTERM]()
    await asyncio.sleep(0)
    registered[signal.SIGTERM]()
    await asyncio.sleep(0)

    assert exits == [1], "a second SIGTERM must give up rather than wait for SIGKILL"
    fake.close.assert_awaited_once()


def test_ops_log_handler_rotates(tmp_path, monkeypatch):
    """A plain FileHandler grows unbounded -- nothing on the host rotates it."""
    monkeypatch.chdir(tmp_path)  # keep discord.log out of the checkout
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        bot_pkg.setup_logger()
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 1
        handler = added[0]
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes > 0, "maxBytes=0 disables rotation entirely"
        assert handler.backupCount > 0, "backupCount=0 discards history on roll"
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_ops_config_template_covers_every_key_the_code_reads():
    """config.json.dist is the only doc for these; a missing key means a
    silently-defaulted feature (e.g. image evidence with no archive_media_dir)."""
    with open("config.json.dist") as fh:
        dist = json.load(fh)

    for key in ("max_channels_per_category", "debug_host", "debug_port"):
        assert key in dist, f"config.json.dist omits {key}"
    for key in (
        "frontend_dist",
        "bridge_enabled",
        "archive_media_dir",
        "attachment_max_bytes",
    ):
        assert key in dist["web"], f"config.json.dist omits web.{key}"
