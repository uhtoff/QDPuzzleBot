"""Ops hardening (O2/O3/O5, docs/reviews/2026-08-07-comprehensive-review.md).

Covers the three things that had no coverage at all: the SIGTERM handler, the
rotating log handler, and the config template matching the keys the code reads.

Locally-named helpers -- the parallel runner shards by file.
"""
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import time
from types import SimpleNamespace
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


def test_ops_log_handler_rotates_daily_and_keeps_30(tmp_path, monkeypatch):
    """The privacy page promises daily rotation and deletion after 30 days."""
    monkeypatch.chdir(tmp_path)  # keep discord.log out of the checkout
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        bot_pkg.setup_logger()
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 1
        handler = added[0]
        assert isinstance(handler, logging.handlers.TimedRotatingFileHandler)
        assert handler.when == "MIDNIGHT"
        assert handler.backupCount == 30, "backupCount=0 keeps logs forever"
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


@pytest.mark.asyncio
async def test_ops_dashboard_access_log_goes_to_a_logger_not_stdout(monkeypatch):
    """Hypercorn's accesslog='-' writes to stdout (bot.log, cleared only on restart)."""
    import bot.web as web

    captured = {}

    async def fake_serve(app, cfg, shutdown_trigger=None):
        captured["cfg"] = cfg
        captured["trigger"] = shutdown_trigger

    monkeypatch.setattr(web, "serve", fake_serve)
    monkeypatch.setattr(web, "create_app", lambda bot: object())
    monkeypatch.setattr(web.config, "web_enabled", True, raising=False)
    monkeypatch.setattr(web.config, "web_session_secret", "x" * 64, raising=False)
    monkeypatch.setattr(web, "_valid_session_secret", lambda s: True)
    monkeypatch.setattr(web.config, "web_host", "127.0.0.1", raising=False)
    monkeypatch.setattr(web.config, "web_port", 8123, raising=False)

    task = await web.start_web(SimpleNamespace())
    await task

    cfg = captured["cfg"]
    assert isinstance(cfg.accesslog, logging.Logger)
    assert isinstance(cfg.errorlog, logging.Logger)
    assert cfg.bind == ["127.0.0.1:8123"]
    assert captured["trigger"] is not None, "without it hypercorn grabs SIGTERM"


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


def test_ops_config_exposes_the_debug_endpoint_run_py_reads():
    """run.py reads config.debug_host/debug_port at import when debug is true.

    Nothing else exercises that path -- run.py has no test -- so without this a
    dropped attribute in config.py stays green here and AttributeErrors at boot,
    which is the exact class of failure O5 removed.
    """
    from bot.utils.config import Config

    cfg = Config("config.json.dist")  # the template, so the defaults are asserted too
    assert cfg.debug_host == "127.0.0.1"
    assert cfg.debug_port == 29781


def _touch_aged(path, days_old, now):
    path.write_text("x")
    t = now - days_old * 86400
    os.utime(path, (t, t))


def test_ops_prune_old_logs_deletes_by_age_only(tmp_path):
    now = time.time()
    old_timed = tmp_path / "discord.log.2026-01-01"
    old_legacy = tmp_path / "discord.log.3"
    new_timed = tmp_path / "discord.log.2026-11-06"
    new_legacy = tmp_path / "discord.log.1"
    live = tmp_path / "discord.log"
    other = tmp_path / "other.log.2020"
    other2 = tmp_path / "notes.txt"
    for f, d in [(old_timed, 31), (old_legacy, 45), (new_timed, 29), (new_legacy, 1),
                 (live, 400), (other, 400), (other2, 400)]:
        _touch_aged(f, d, now)

    deleted = bot_pkg.prune_old_logs(tmp_path, now=now)

    assert sorted(p.name for p in deleted) == ["discord.log.2026-01-01", "discord.log.3"]
    assert not old_timed.exists() and not old_legacy.exists()
    assert new_timed.exists() and new_legacy.exists()
    assert live.exists(), "the live log is never pruned, however old its mtime"
    assert other.exists() and other2.exists()


def test_ops_handler_prunes_at_setup_and_on_rollover(tmp_path):
    now = time.time()
    stale = tmp_path / "discord.log.4"
    _touch_aged(stale, 40, now)
    handler = bot_pkg.AgePrunedTimedRotatingFileHandler(
        str(tmp_path / "discord.log"), when="midnight", backupCount=30, encoding="utf-8"
    )
    try:
        assert not stale.exists(), "setup must prune"
        stale2 = tmp_path / "discord.log.2026-01-02"
        _touch_aged(stale2, 40, now)
        handler.doRollover()
        assert not stale2.exists(), "rollover must prune"
    finally:
        handler.close()
