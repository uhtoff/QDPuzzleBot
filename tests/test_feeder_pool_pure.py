"""Unit tests for the pure feeder-pool helpers. No DB, no Discord, no Sheets."""
from dataclasses import dataclass

from bot.core.feeder_pool import claim_state, provided_item_ids, rival_names


@dataclass
class Claim:
    """Duck-typed stand-in — the module never imports the real dataclass."""
    item_id: int
    target_id: int
    confirmed: bool = False


MINI_1, MINI_2 = 101, 102
ALBATROSS, MARZIPAN = 12, 13


def test_free_when_no_other_target_claims_it():
    claims = [Claim(MARZIPAN, MINI_1)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "free"


def test_free_when_only_the_viewer_claims_it():
    """My own unconfirmed claim does not make the item look contested to me."""
    claims = [Claim(ALBATROSS, MINI_2)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "free"


def test_claimed_when_another_target_has_an_unconfirmed_claim():
    claims = [Claim(ALBATROSS, MINI_1), Claim(MARZIPAN, MINI_2)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "claimed"
    # ...and the other item, viewed the other way, is NOT claimed — a swapped
    # item/target argument would make both of these agree.
    assert claim_state(MARZIPAN, MINI_1, claims) == "claimed"
    assert claim_state(MARZIPAN, MINI_2, claims) == "free"


def test_taken_when_another_target_has_a_confirmed_claim():
    claims = [Claim(ALBATROSS, MINI_1, confirmed=True)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "taken"


def test_mine_when_the_viewer_holds_the_confirmed_claim():
    claims = [Claim(ALBATROSS, MINI_2, confirmed=True)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "mine"


def test_mine_wins_over_a_rival_unconfirmed_claim():
    claims = [Claim(ALBATROSS, MINI_2, confirmed=True), Claim(ALBATROSS, MINI_1)]
    assert claim_state(ALBATROSS, MINI_2, claims) == "mine"
    assert claim_state(ALBATROSS, MINI_1, claims) == "taken"


NAMES = {MINI_1: "mini1", MINI_2: "mini2", 103: "mini3"}


def test_rival_names_lists_other_claimants_only():
    claims = [Claim(ALBATROSS, MINI_1), Claim(ALBATROSS, MINI_2)]
    assert rival_names(ALBATROSS, MINI_2, claims, NAMES) == "mini1"


def test_rival_names_is_empty_when_nobody_else_claims_it():
    claims = [Claim(ALBATROSS, MINI_2)]
    assert rival_names(ALBATROSS, MINI_2, claims, NAMES) == ""


def test_rival_names_are_sorted_not_insertion_ordered():
    """Order must be stable across syncs, or every reconcile rewrites the row.

    Two rivals inserted in the 'wrong' order — a function returning insertion
    order would disagree with one returning sorted order.
    """
    claims = [Claim(ALBATROSS, 103), Claim(ALBATROSS, MINI_1)]
    assert rival_names(ALBATROSS, MINI_2, claims, NAMES) == "mini1, mini3"


def test_rival_names_skips_targets_with_no_known_name():
    claims = [Claim(ALBATROSS, MINI_1), Claim(ALBATROSS, 999)]
    assert rival_names(ALBATROSS, MINI_2, claims, NAMES) == "mini1"


MINIMETA = 100


def test_a_claim_to_the_minimeta_yields_a_provided_row():
    claims = [Claim(MARZIPAN, MINIMETA)]
    assert provided_item_ids(MINIMETA, claims) == [MARZIPAN]


def test_it_renders_while_unconfirmed():
    """The confirming event for a main-meta claim is solving the main meta, which
    is the last thing that happens — so waiting for confirmation would mean the row
    never appeared while it was useful."""
    claims = [Claim(MARZIPAN, MINIMETA, confirmed=False)]
    assert provided_item_ids(MINIMETA, claims) == [MARZIPAN]


def test_a_confirmed_claim_elsewhere_supersedes_it():
    claims = [Claim(MARZIPAN, MINIMETA), Claim(MARZIPAN, MINI_1, confirmed=True)]
    assert provided_item_ids(MINIMETA, claims) == []


def test_an_unconfirmed_rival_does_not_supersede_it():
    claims = [Claim(MARZIPAN, MINIMETA), Claim(MARZIPAN, MINI_1, confirmed=False)]
    assert provided_item_ids(MINIMETA, claims) == [MARZIPAN]


def test_superseding_is_per_item_not_wholesale():
    """Two items, one superseded and one not — a rule that dropped everything (or
    nothing) as soon as any confirmed claim existed would pass with only one item."""
    claims = [
        Claim(ALBATROSS, MINIMETA), Claim(ALBATROSS, MINI_1, confirmed=True),
        Claim(MARZIPAN, MINIMETA), Claim(MARZIPAN, MINI_2, confirmed=False),
    ]
    assert provided_item_ids(MINIMETA, claims) == [MARZIPAN]


def test_claims_that_are_not_the_minimetas_are_ignored():
    claims = [Claim(ALBATROSS, MINI_1), Claim(MARZIPAN, MINI_2)]
    assert provided_item_ids(MINIMETA, claims) == []
