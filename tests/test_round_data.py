"""Pure tests for the M6 typed-group fields on TagGroupData — no DB."""
from bot.store.tag_group_data import TagGroupData


def test_typed_group_defaults():
    r = TagGroupData()
    assert r.status == "active"


def test_group_code_defaults_to_unminted():
    """Migration 042 made the column UNIQUE, so the "no code yet" value had to
    stop being the shared '0' sentinel — many rows may be NULL under a UNIQUE
    index, but only one could ever hold '0'. The creation paths mint before the
    first commit, so None here means "not minted yet", not "codeless group"."""
    r = TagGroupData()
    assert r.group_code is None


def test_import_dict_round_trips_typed_group_fields():
    r = TagGroupData.import_dict({
        "id": 3, "hunt_id": 10, "target_puzzle_id": 5, "group_code": "ABC123",
        "status": "solved",
    })
    assert r.status == "solved"
    assert r.group_code == "ABC123"


def test_import_dict_defaults_when_columns_absent():
    # A row missing some columns shouldn't blow up; absent fields come back None.
    r = TagGroupData.import_dict({"id": 1, "hunt_id": 10, "target_puzzle_id": 0})
    assert r.id == 1
    assert r.type is None


def test_has_target_false_without_target_puzzle():
    # Default target_puzzle_id is 0 — a metaless/Group has no target.
    r = TagGroupData()
    assert r.has_target is False


def test_has_target_true_with_target_puzzle():
    r = TagGroupData(target_puzzle_id=5)
    assert r.has_target is True
