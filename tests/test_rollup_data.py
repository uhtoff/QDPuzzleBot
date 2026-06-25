"""Pure tests for the RollupData dataclass (M5) — no DB."""
import datetime

from bot.store.rollup_data import RollupData


def test_defaults_match_current_meta_behaviour():
    r = RollupData()
    assert r.id == 0
    assert r.source_mode == "round"
    assert r.edge_type_filter == "feeds-meta"
    assert r.last_ok == 1
    assert r.last_error is None
    assert r.tab_page_id is None
    assert r.metadata_id is None
    assert r.updated_at is None


def test_import_dict_localizes_updated_at_to_utc():
    naive = datetime.datetime(2026, 6, 22, 12, 0, 0)
    r = RollupData.import_dict(
        {"id": 7, "hunt_id": 2, "tag_group_id": 3, "last_ok": 0,
         "last_error": "boom", "updated_at": naive}
    )
    assert r.id == 7
    assert r.tag_group_id == 3
    assert r.last_ok == 0
    assert r.last_error == "boom"
    assert r.updated_at.tzinfo == datetime.timezone.utc


def test_import_dict_handles_null_updated_at():
    r = RollupData.import_dict({"id": 1, "hunt_id": 2, "tag_group_id": 3, "updated_at": None})
    assert r.updated_at is None
