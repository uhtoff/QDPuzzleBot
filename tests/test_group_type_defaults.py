"""Per-type create-time defaults, keyed on the INPUT LABEL (M30).

Two edges create groups: the bang !round family (_round_impl in
bot/cogs/puzzles.py) and the web POST /hunts/<uid>/metas route (create_meta in
bot/web/actions.py). Both used to hand-derive the same two per-type facts --
the target puzzle's name, and the Runaround source_mode default -- and their
own comments admitted the duplication ("Mirror the bang path's meta naming",
"set source_mode like the bang path does").

The naming fact in particular CANNOT be keyed on the stored tag_group.type:
"Round" and "Metapuzzle" are both create-time labels for the same stored type
("Metapuzzle" -- see GROUP_TYPE_INPUT_ALIASES) but name their target puzzle
differently ("Alpha - meta" vs "Alpha"). So the seam
(meta_puzzle_name_for/default_source_mode_for in bot/core/puzzle_service.py)
is keyed on the create-time INPUT LABEL, not the stored type -- that is the
fact that was hand-synced between the two edges, and the fact these tests
guard.
"""
from unittest.mock import AsyncMock, MagicMock

from bot.core.puzzle_service import (
    GROUP_TYPE_INPUT_ALIASES,
    GROUP_TYPES,
    default_source_mode_for,
    meta_puzzle_name_for,
)
from tests.conftest import make_cog, make_ctx, make_round


def test_round_and_metapuzzle_share_a_stored_type_but_not_a_target_name():
    # The load-bearing case: an implementation keyed on the STORED type
    # (as the review's stated direction proposed) cannot distinguish these
    # two calls, because they share a stored type -- it would pass at most
    # one of the two assertions below.
    assert meta_puzzle_name_for("Round", "Alpha") == "Alpha - meta"
    assert meta_puzzle_name_for("Metapuzzle", "Alpha") == "Alpha"
    assert GROUP_TYPE_INPUT_ALIASES.get("Round", "Round") == "Metapuzzle"
    assert GROUP_TYPE_INPUT_ALIASES.get("Metapuzzle", "Metapuzzle") == "Metapuzzle"


def test_group_has_no_target_puzzle():
    assert meta_puzzle_name_for("Group", "Alpha") is False


def test_capstone_and_runaround_keep_the_group_name():
    assert meta_puzzle_name_for("Capstone", "Alpha") == "Alpha"
    assert meta_puzzle_name_for("Runaround", "Alpha") == "Alpha"


def test_runaround_is_the_only_type_that_overrides_source_mode():
    # Iterate GROUP_TYPES (not a hand-listed subset) so a new type added to
    # TYPE_DEFAULTS without an override is automatically covered here.
    for round_type in GROUP_TYPES:
        if round_type == "Runaround":
            assert default_source_mode_for(round_type) == "subtree"
        else:
            assert default_source_mode_for(round_type) is None


# --- Regression guard: the two edges must agree on every alias --------------
#
# _round_impl's command_map is a local variable (not importable), so this
# mirrors it here -- keep this dict in step with bot/cogs/puzzles.py's
# command_map. The check is behavioural, not a static dict compare: each
# alias is driven through the REAL _round_impl and the resulting target-puzzle
# name is compared against the web edge's helper for the equivalent input
# label, so a wiring bug in either edge (not just a stale copy of this table)
# would be caught.
_ALIAS_TO_INPUT_LABEL = {
    "r": "Round",
    "round": "Round",
    "mp": "Metapuzzle",
    "metapuzzle": "Metapuzzle",
    "capstone": "Capstone",
    "cs": "Capstone",
    "runaround": "Runaround",
    "ra": "Runaround",
    "group": "Group",
}


async def test_every_bang_alias_agrees_with_its_web_label():
    for alias, input_label in _ALIAS_TO_INPUT_LABEL.items():
        cog = make_cog()
        cog.bot = MagicMock()
        cog.service.create_tag_group = AsyncMock(return_value=(make_round(), MagicMock()))
        cog.service.set_source_mode = AsyncMock()
        await cog._round_impl(make_ctx(), "Alpha", alias)
        kwargs = cog.service.create_tag_group.call_args.kwargs

        expected_name = meta_puzzle_name_for(input_label, "Alpha")
        assert kwargs["meta_puzzle_name"] == expected_name, (
            f"bang alias {alias!r} disagrees with web label {input_label!r}"
        )

        expected_type = GROUP_TYPE_INPUT_ALIASES.get(input_label, input_label)
        assert kwargs["round_type"] == expected_type
