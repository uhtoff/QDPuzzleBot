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


KEY_PREFIX = "f:"
BOT_COLUMNS = ["key", "item", "Len"]
NOTE_COLUMN = "note"
MAIN_COLUMN = "main"
# What a departed target's tick column is retitled to. NOT "": a native Table
# rejects columnProperties carrying an empty columnName ("Table header row cell
# must have a value"), and Sheets silently renames an emptied header cell to
# "Column N" anyway — both probed live 2026-08-12 (scripts/diagnose_feeder_matrix.py,
# Q5c and Q6a). It carries no "#<id>", so target_id_from_label returns None for it
# and parse_ticks ignores the column, which is the whole point.
REMOVED_COLUMN = "(removed)"


def feeder_length(text):
    """Letter count excluding spaces, punctuation kept — '' for empty text.

    Deliberately the same rule as bot/core/unassigned.py's unassigned_row, because
    both columns answer the same solver question and disagreeing would be worse than
    either choice.
    """
    if not text:
        return ""
    return len(text.replace(" ", ""))


def target_column_label(name, target_id):
    """A tick column's header: human-readable, but carrying its target's id.

    The id is what makes the column identifiable. Matching on the bare name is
    unsound three ways: renaming a mini leaves a stale header, so its column stops
    matching and its ticks are silently read as some solver's own column; two minis
    may share a name (create_mini enforces no uniqueness), collapsing them onto one
    target; and deleting a mini would shift any position-based scheme.
    """
    return f"{name} #{target_id}"


def target_id_from_label(label):
    """The target id encoded in a tick column's header, or None if there is none.

    None means the column is not ours — a solver is free to add their own.
    """
    _, _, tail = str(label).rpartition("#")
    try:
        return int(tail.strip())
    except ValueError:
        return None


def matrix_header(minimeta, minis):
    """The matrix tab's header row: bot columns, note, then one column per target.

    The Minimeta's own column comes first — how "the main meta uses this feeder
    directly" is expressed. Minis follow in the order given (creation order), so a new
    mini appends at the far right and nothing already on the sheet moves.

    Every tick column carries its target's id (see target_column_label), so the
    mapping survives a rename and cannot confuse two minis that share a name.
    """
    return (
        BOT_COLUMNS
        + [NOTE_COLUMN, target_column_label(MAIN_COLUMN, minimeta.id)]
        + [target_column_label(m.name, m.id) for m in minis]
    )


def stable_matrix_header(existing, minimeta, minis):
    """The header row to WRITE, keeping every surviving target's column WHERE IT IS.

    Returns (header, blanked_indices) — the row to write, and the indices whose
    data cells the caller must clear.

    matrix_header derives a column's position from list position, so deleting a
    mini re-packs every label to its left while the tick data underneath does not
    move: the next read attributes the deleted mini's ticks to its right-hand
    neighbour, and a confirmed tick is written over the wrong column. A position
    must therefore be a property of the SHEET, not of the current mini list —
    `existing` (the header row as it currently reads back) is that record.

    The rules:
      * a surviving target keeps its current index, with its label refreshed so a
        rename is picked up (the id in the label is what identifies it, never the
        name — see target_column_label);
      * a target that is gone has its label replaced with REMOVED_COLUMN and its
        index returned for the caller to clear. Its ticks are meaningless and must
        not be inherited; parse_ticks already ignores a column with no id, so the
        retired column is harmless;
      * a second column carrying an id already placed is a duplicate left behind by
        the re-packing bug, and is retired the same way;
      * a column with NO id is a solver's own — left completely alone;
      * a genuinely new target is APPENDED to the far right, never dropped into a
        retired column: that column only stops being dangerous once its clearing
        write has actually landed, and a spare column is much cheaper than
        inheriting someone else's ticks.

    `existing` that carries no recognisable column at all (a blank tab, or a header
    row a solver deleted, leaving a DATA row in row 1) is not a record of anything —
    the canonical header is written instead, which also repairs it.
    """
    desired = matrix_header(minimeta, minis)
    first_tick = len(BOT_COLUMNS) + 1
    existing = [str(cell) for cell in (existing or [])]
    wanted = {target_id_from_label(label): label for label in desired[first_tick:]}
    if not any(target_id_from_label(label) in wanted
               for label in existing[first_tick:]):
        return desired, []

    header = desired[:first_tick] + existing[first_tick:]
    blanked = []
    for idx in range(first_tick, len(header)):
        target_id = target_id_from_label(header[idx])
        if target_id is None:
            continue
        label = wanted.pop(target_id, None)
        if label is None:
            header[idx] = REMOVED_COLUMN
            blanked.append(idx)
        else:
            header[idx] = label
    header.extend(wanted.values())
    return header, blanked


def matrix_rows(items):
    """Desired rows for reconcile_table — the BOT-OWNED columns only.

    note and the tick cells are the solver's. Including them here would hand them to
    reconcile_table's write range and overwrite a solver's work on every render.
    """
    return [[f"{KEY_PREFIX}{i.id}", i.text, feeder_length(i.text)] for i in items]


def parse_ticks(header, grid, known_target_ids):
    """{target_id: [item_id, ...]} read off the matrix.

    `header` is the sheet's header row and `grid` the data rows beneath it. A tick
    column is identified by the target id in its label, not by its name — so a renamed
    mini keeps its column, and two minis sharing a name stay distinct.

    `known_target_ids` is the set of ids that still exist; a column whose id is absent
    is stale (its mini was deleted) or was never ours, and is ignored. Columns a solver
    adds carry no id and are ignored too.

    Values arrive as FORMATTED_VALUE strings, so a ticked box is the string 'TRUE';
    the API also right-trims rows, so a row can be shorter than the header. Rows whose
    key does not parse are skipped: reconcile_table blanks pruned rows rather than
    deleting them, so a blanked row outlives its feeder.
    """
    known = set(known_target_ids)
    columns = []
    for idx, label in enumerate(header):
        target_id = target_id_from_label(label)
        if target_id is not None and target_id in known:
            columns.append((idx, target_id))
    ticks = {target_id: [] for _, target_id in columns}
    for row in grid:
        if not row or not str(row[0]).startswith(KEY_PREFIX):
            continue
        try:
            item_id = int(str(row[0])[len(KEY_PREFIX):])
        except ValueError:
            continue
        for idx, target_id in columns:
            cell = row[idx] if idx < len(row) else ""
            if str(cell).strip().upper() == "TRUE":
                ticks[target_id].append(item_id)
    return ticks
