"""RollupData — one tracked aggregation target (M5).

A rollup is the bookkeeping record for a meta round's roll-up sheet: where it
writes (tab_page_id / metadata_id), how it aggregates (source_mode /
edge_type_filter — inert at their defaults until M6/M7), and the success/failure
of the last refresh (last_ok / last_error / updated_at) which the dashboard
surfaces as a "sheet stale" badge.

Identity is per tag_group_id (one rollup per meta tag_group) OR, since M8, per
target_puzzle_id (one rollup per minimeta whose mini-puzzles render onto its own
sheet — the puzzle-keyed path). Exactly one of tag_group_id / target_puzzle_id is set.
See docs/superpowers/specs/2026-06-22-m5-rollups-design.md.
"""
from dataclasses import dataclass, fields
from dataclasses_json import dataclass_json
import datetime
import logging
from typing import List, Optional, TypeVar, Type

logger = logging.getLogger(__name__)


@dataclass_json
@dataclass
class RollupData:
    id: int = 0
    hunt_id: int = 0
    tag_group_id: Optional[int] = None        # NULL for puzzle-keyed (minimeta) roll-ups
    target_puzzle_id: Optional[int] = None  # M8: minimeta target (round_id is NULL)
    tab_page_id: Optional[int] = None
    metadata_id: Optional[int] = None
    source_mode: str = "round"            # inert until M7 (round/subtree/explicit)
    edge_type_filter: Optional[str] = "feeds"
    last_ok: int = 1
    last_error: Optional[str] = None
    updated_at: Optional[datetime.datetime] = None

    T = TypeVar("T", bound="RollupData")

    @classmethod
    def import_dict(cls: Type[T], rollup_data: dict):
        values = {}
        for field in fields(cls):
            attr = field.name
            if attr in ("updated_at",) or attr.endswith("_time"):
                db_date = rollup_data.get(attr, None)
                values[attr] = (
                    db_date.replace(tzinfo=datetime.timezone.utc)
                    if db_date is not None else None
                )
            else:
                values[attr] = rollup_data.get(attr, None)
        return cls(**values)


class _RollupJsonDb:
    def commit(self, rollup_data):
        pass

    def delete(self, rollup_id):
        pass

    def get_by_attr(self, **kwargs) -> RollupData:
        pass

    def get_all_from_hunt(self, hunt_id) -> List[RollupData]:
        pass
