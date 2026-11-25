"""PuzzleJsonDb.check_duplicates_in_hunt compares puzzle NAMES only. A name that
merely cleans to another puzzle's channel name is for the channel-name clash
prompt (Accept/Cancel, -N channel), not a refusal."""
from unittest.mock import patch

from bot.store import PuzzleJsonDb


def test_duplicate_check_matches_on_name_and_hunt_only():
    with patch.object(PuzzleJsonDb, "_fetch_one", return_value=None) as fetch:
        assert PuzzleJsonDb.check_duplicates_in_hunt("General!!", 7) is False
    sql, params = fetch.call_args.args
    assert "channel_name" not in sql
    assert tuple(params) == ("General!!", 7)


def test_duplicate_check_excludes_the_puzzle_being_renamed():
    with patch.object(PuzzleJsonDb, "_fetch_one", return_value={"1": 1}) as fetch:
        assert PuzzleJsonDb.check_duplicates_in_hunt("General", 7, exclude_puzzle_id=3) is True
    sql, params = fetch.call_args.args
    assert "puzzles.id != %s" in sql
    assert tuple(params) == ("General", 7, 3)
