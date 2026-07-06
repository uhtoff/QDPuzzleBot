"""Pure selection logic for extracting a channel-home meta into its own category.

No Discord or DB access: it takes already-loaded model objects and returns the
subset of puzzles to move, so it is unit-testable without mocks. See
docs/superpowers/specs/2026-07-06-extract-target-to-category-design.md.
"""
from bot.store.tag_group_data import EDGE_FEEDS, EDGE_INFORMS

_MEMBERSHIP_EDGES = frozenset({EDGE_FEEDS, EDGE_INFORMS})


def select_extract_puzzles(meta, target_puzzle, member_puzzles, target_puzzle_ids):
    """Puzzles to move when extracting *meta* into its own category.

    Returns *target_puzzle* first, then every exclusive plain feeder of *meta*:
    a member that feeds/informs *meta*, feeds/informs no other group, and is not
    itself any group's target (sub-metas are boundaries — left in place).

    - meta: the TagGroupData being extracted.
    - target_puzzle: meta's target PuzzleData (placed first).
    - member_puzzles: PuzzleData tagged to meta (e.g.
      PuzzleJsonDb.get_all_from_tag_group(meta.id)); each carries .tags/.tag_edges.
    - target_puzzle_ids: set of puzzle ids that are some group's target in this
      hunt, so sub-metas are recognised without a per-puzzle DB lookup.
    """
    result = [target_puzzle]
    for p in member_puzzles:
        if p.id == target_puzzle.id:
            continue
        if p.id in target_puzzle_ids:
            continue  # a target/sub-meta — leave it (and its own feeders) behind
        if not (p.edges_for(meta.id) & _MEMBERSHIP_EDGES):
            continue  # not actually a feeder of meta
        if _feeds_another_group(p, meta.id):
            continue  # shared feeder — can't pull it out of the other meta
        result.append(p)
    return result


def _feeds_another_group(puzzle, meta_id):
    """True if *puzzle* feeds/informs any group other than *meta_id*."""
    for gid in puzzle.tags:
        if gid == meta_id:
            continue
        if puzzle.edges_for(gid) & _MEMBERSHIP_EDGES:
            return True
    return False
