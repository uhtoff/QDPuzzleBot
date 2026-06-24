"""Phase 5a: PuzzleService.update_hunt_settings — whitelisted per-hunt field edits.

Service-level tests for the new committing settings method that the web admin pane and the
rewired !set_start / !set_login bang commands both flow through. Helpers are uniquely named
and local to this file (the parallel gate shards by file).
"""
import datetime
from unittest.mock import MagicMock, patch

import pytz

from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData


def _settings_hunt():
    h = HuntData()
    h.id = 10
    h.url = "https://old.example/"
    h.start_timestamp = 0
    h.start_time = None
    h.username = ""
    h.password = ""
    h.url_sep = "-"
    h.puzzle_prefix = "puzzle"
    h.name = "H"
    return h


def _settings_service():
    bot = MagicMock()
    bot.get_cog.return_value = None
    return PuzzleService(bot)


def test_update_url_commits_new_value():
    svc, hunt = _settings_service(), _settings_hunt()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, url="https://new.example/")
    assert hunt.url == "https://new.example/"
    hdb.commit.assert_called_once_with(hunt)


def test_update_start_timestamp_mirrors_start_time():
    svc, hunt = _settings_service(), _settings_hunt()
    ts = 1_700_000_000
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, start_timestamp=ts)
    assert hunt.start_timestamp == ts
    assert hunt.start_time == datetime.datetime.fromtimestamp(ts, tz=pytz.UTC)
    hdb.commit.assert_called_once_with(hunt)


def test_update_login_sets_both_fields():
    svc, hunt = _settings_service(), _settings_hunt()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, username="team", password="secret")
    assert (hunt.username, hunt.password) == ("team", "secret")
    hdb.commit.assert_called_once_with(hunt)


def test_update_url_building_keys():
    svc, hunt = _settings_service(), _settings_hunt()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, url_sep="_", puzzle_prefix="puzzles")
    assert (hunt.url_sep, hunt.puzzle_prefix) == ("_", "puzzles")
    hdb.commit.assert_called_once_with(hunt)


def test_unprovided_fields_left_untouched():
    svc, hunt = _settings_service(), _settings_hunt()
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        svc.update_hunt_settings(hunt, url="https://new.example/")
    assert hunt.username == "" and hunt.password == ""
    assert hunt.start_timestamp == 0 and hunt.start_time is None
    assert hunt.url_sep == "-" and hunt.puzzle_prefix == "puzzle"
