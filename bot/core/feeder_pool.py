"""Pure helpers for the feeder pool (meta matching puzzles).

No Discord, Sheets or DB imports — everything here is derivation over plain data,
so it is cheap to test exhaustively. Claims are duck-typed: any object with
`.item_id`, `.target_id` and `.confirmed` will do, which keeps
`bot/store/__init__.py` (and its MySQL connection) out of the unit tests.

See docs/superpowers/specs/2026-08-12-feeder-pool-design.md.
"""


def claim_state(item_id, viewing_target_id, claims):
    """The state of `item_id` as seen by `viewing_target_id`.

    'free'    — no OTHER target claims it (the viewer's own unconfirmed claim
                does not make it contested from the viewer's point of view)
    'claimed' — at least one other target holds an unconfirmed claim
    'taken'   — another target holds a CONFIRMED claim, so it is settled elsewhere
    'mine'    — the viewer holds the confirmed claim
    """
    mine_confirmed = False
    rival_confirmed = False
    rivals = 0
    for claim in claims:
        if claim.item_id != item_id:
            continue
        if claim.target_id == viewing_target_id:
            mine_confirmed = mine_confirmed or bool(claim.confirmed)
        else:
            rivals += 1
            rival_confirmed = rival_confirmed or bool(claim.confirmed)

    if mine_confirmed:
        return "mine"
    if rival_confirmed:
        return "taken"
    if rivals:
        return "claimed"
    return "free"


def rival_names(item_id, viewing_target_id, claims, names):
    """Comma-joined names of the OTHER targets claiming `item_id`.

    `names` maps target id to display name; a target with no name is skipped
    (it has been deleted, or is not part of this pool's rendering). Sorted, not
    insertion-ordered: an unstable order would rewrite the sheet row on every
    reconcile even when nothing changed.
    """
    rivals = sorted(
        names[claim.target_id]
        for claim in claims
        if claim.item_id == item_id
        and claim.target_id != viewing_target_id
        and claim.target_id in names
    )
    return ", ".join(rivals)


def provided_item_ids(minimeta_id, claims):
    """Item ids to render as `provided:` rows on the minimeta's contribution table.

    A claim held by the minimeta ITSELF yields a row — rendered on the claim, not on
    confirmation, because the confirming event for a main-meta claim would be solving
    the main meta, i.e. the end of the puzzle.

    Unless another target holds a CONFIRMED claim on the same item: solving that mini
    proved the feeder belongs there, so the main-meta guess is superseded and the row
    drops out. Nothing is deleted — the claim survives, rendering as 'taken', and
    unsolving that mini restores the row.

    Returned in claim order (item_id ascending, as the DAO fetches them).
    """
    confirmed_elsewhere = {
        claim.item_id for claim in claims
        if claim.confirmed and claim.target_id != minimeta_id
    }
    return [
        claim.item_id for claim in claims
        if claim.target_id == minimeta_id and claim.item_id not in confirmed_elsewhere
    ]


def diff_ticks(pool_item_ids, current_item_ids, ticked_item_ids, confirmed_item_ids):
    """What to change for ONE target after reading its tick column off a sheet.

    Returns (to_add, to_remove), both sorted item-id lists.

    Two rules the sheet forces:
      * A tick on an item that is no longer in the pool is ignored. reconcile_table
        blanks pruned rows instead of deleting them, so a tick outlives its item.
      * A confirmed claim is never removed by an untick — it is bot-owned, and the
        next render puts the tick back. Unsolving the mini is how it is undone.
    """
    pool = set(pool_item_ids)
    ticked = {item_id for item_id in ticked_item_ids if item_id in pool}
    current = set(current_item_ids)
    confirmed = set(confirmed_item_ids)

    to_add = sorted(ticked - current)
    to_remove = sorted(current - ticked - confirmed)
    return to_add, to_remove
