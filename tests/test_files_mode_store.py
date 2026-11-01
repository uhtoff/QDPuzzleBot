"""Migration 051 fields: rows without the new keys import with the defaults;
rows with them round-trip."""
from bot.store.hunt_data import HuntData
from bot.store.puzzle_data import AdditionalSheetData, PuzzleData


def test_hunt_without_the_new_keys_gets_defaults():
    hunt = HuntData.import_dict({"id": 3, "name": "Big Hunt"})
    assert (hunt.sheet_mode, hunt.drive_folder_id, hunt.solved_folder_id) == ("tabs", "", "")


def test_hunt_new_keys_round_trip():
    hunt = HuntData.import_dict({
        "id": 3, "sheet_mode": "files", "drive_folder_id": "FOLD", "solved_folder_id": "SOLV",
    })
    assert (hunt.sheet_mode, hunt.drive_folder_id, hunt.solved_folder_id) == ("files", "FOLD", "SOLV")


def test_puzzle_google_sheet_id_default_and_round_trip():
    assert PuzzleData.import_dict({"id": 1, "name": "p"}).google_sheet_id == ""
    assert PuzzleData.import_dict({"id": 1, "google_sheet_id": "FILE"}).google_sheet_id == "FILE"


def test_additional_sheet_has_google_sheet_id_default_empty():
    assert AdditionalSheetData().google_sheet_id == ""
