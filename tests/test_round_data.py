"""Pure tests for the M6 typed-group fields on RoundData — no DB."""
from bot.store.round_data import RoundData


def test_typed_group_defaults():
    r = RoundData()
    assert r.kind == ""
    assert r.status == "active"
    assert r.parent_group_id is None


def test_meta_code_is_str():
    r = RoundData()
    assert isinstance(r.meta_code, str)
    assert r.meta_code == "0"


def test_import_dict_round_trips_typed_group_fields():
    r = RoundData.import_dict({
        "id": 3, "hunt_id": 10, "meta_id": 5, "meta_code": "ABC123",
        "kind": "meta", "status": "solved", "parent_group_id": 9,
    })
    assert r.kind == "meta"
    assert r.status == "solved"
    assert r.parent_group_id == 9
    assert r.meta_code == "ABC123"


def test_import_dict_defaults_when_columns_absent():
    # A row read before the columns existed shouldn't blow up.
    r = RoundData.import_dict({"id": 1, "hunt_id": 10, "meta_id": 0})
    assert r.parent_group_id is None
