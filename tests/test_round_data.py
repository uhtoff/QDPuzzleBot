"""Pure tests for the M6 typed-group fields on TagGroupData — no DB."""
from bot.store.tag_group_data import TagGroupData


def test_typed_group_defaults():
    r = TagGroupData()
    assert r.status == "active"
    assert r.parent_group_id is None


def test_group_code_is_str():
    r = TagGroupData()
    assert isinstance(r.group_code, str)
    assert r.group_code == "0"


def test_import_dict_round_trips_typed_group_fields():
    r = TagGroupData.import_dict({
        "id": 3, "hunt_id": 10, "target_puzzle_id": 5, "group_code": "ABC123",
        "status": "solved", "parent_group_id": 9,
    })
    assert r.status == "solved"
    assert r.parent_group_id == 9
    assert r.group_code == "ABC123"


def test_import_dict_defaults_when_columns_absent():
    # A row read before the columns existed shouldn't blow up.
    r = TagGroupData.import_dict({"id": 1, "hunt_id": 10, "target_puzzle_id": 0})
    assert r.parent_group_id is None


def test_has_target_false_without_target_puzzle():
    # Default target_puzzle_id is 0 — a metaless/Group has no target.
    r = TagGroupData()
    assert r.has_target is False


def test_has_target_true_with_target_puzzle():
    r = TagGroupData(target_puzzle_id=5)
    assert r.has_target is True
