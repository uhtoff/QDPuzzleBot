# Verbatim category names — design

Date: 2026-07-01
Status: approved (design), pending implementation plan

## Goal

Discord **categories** accept their name essentially verbatim (case, spaces,
punctuation, emoji; force-uppercased only for display), whereas **text
channels** are slugified by Discord. Today both go through the same `clean_name`
slug function, so a round called `Monster Mash!` becomes a category literally
named `monster-mash` (shown as `MONSTER-MASH`). We want categories to keep a
lightly-cleaned verbatim name (`Monster Mash!` → `MONSTER MASH!`), while
channels stay slugified.

## Context

- Categories and channels are resolved by stored `category_id` / `channel_id`;
  the name is cosmetic and name-based matching is only a reconcile/fallback
  path. So **no data migration is needed** — existing categories pick up their
  verbatim name on the next group rename.
- The group's human name already lives in `tag_group.name` (the source of
  truth). The category name is purely display.
- `clean_name` currently lives in `bot/core/discord_channels.py` and models
  Discord's text-channel normalisation (lowercase, spaces→hyphens, strip ASCII
  punctuation except `-`/`_`, collapse hyphen runs, trim, cap 100; non-ASCII
  such as emoji / em-en dashes / `£` passes through).

## Decisions

- **Cleaning level for categories:** lightly cleaned — collapse internal
  whitespace to single spaces, trim, cap at Discord's 100-char limit; preserve
  case, punctuation, emoji.
- **Scope:** all category names — the primary round category *and* the
  split-round solved / overflow categories (`… — solved`, `… (2)`).
- **Shape:** two separate functions (not a flag/enum) — each Discord object type
  gets its own single-purpose naming function.

## Naming functions (`bot/core/discord_channels.py`)

- Rename `clean_name` → **`clean_channel_name`**. Behaviour unchanged (Discord
  text-channel slug).
- Add **`clean_category_name(name)`**: `re.sub(r"\s+", " ", name).strip()` then
  cap at 100 and re-strip. Preserves case, punctuation, emoji.

No shared private helper unless it clearly earns its place (YAGNI).

## Routing category sites to `clean_category_name`

- `bot/core/puzzle_service.py`
  - `:1733` new category name.
  - `:1736` hunt-name collision suffix (`… — <hunt name>`) — the appended hunt
    name uses `clean_category_name`.
  - `:2106` brand-new category name.
  - Audit each site during implementation: if it also creates a **text
    channel** from the same name variable, that channel must use
    `clean_channel_name`.
- `bot/core/category_layout.py`
  - `:83` `base_name` fallback (`clean_channel_name(tag_group.name)` →
    `clean_category_name(tag_group.name)`).
  - `:278` `group_rename_plan`: split the single `new_base` into
    - `new_cat_base = clean_category_name(new_name)` — used for the primary
      category plan entry and every `overflow_category_name(new_cat_base, …)` /
      `solved_category_name` entry;
    - `new_chan_name = clean_channel_name(new_name)` — used **only** for the
      metaless group's text-channel plan entry (`:302` / `:307`).
  - `:300` text-channel *match* stays `clean_channel_name(old_base)`:
    `clean_channel_name` of a verbatim category name still equals the channel's
    slug, so matching keeps working.
- `solved_category_name` / `overflow_category_name` are unchanged — they
  decorate whatever base they receive, so `Monster Mash! — solved` falls out
  naturally.

## What stays slugified

All text channels (Discord forces it): the metaless group text channel and the
`———solved———` divider sentinel (a fixed string, not derived from a name).

## Tests

- Mechanical: rename `clean_name` → `clean_channel_name` in
  `tests/test_discord_channels.py`, `tests/test_rename_group.py`.
- Behaviour: update `test_rename_group` category assertions to expect verbatim
  names (`"New Round"`, `"New Round — solved"`) while the metaless text channel
  stays `"new-group"`.
- Add `clean_category_name` unit tests: keeps case/punctuation/emoji; collapses
  internal whitespace; trims; caps at 100.

## Commit structure

Keep the refactor and the behaviour change separate:

- **Commit A — mechanical rename** `clean_name` → `clean_channel_name` (19 refs
  across 5 source files + 2 test files, the cog re-export in
  `bot/cogs/puzzles.py`, and the `mysqldb` import). Tests stay green; no
  behaviour change.
- **Commit B — feature**: add `clean_category_name`, route the category sites,
  split `new_base` in `group_rename_plan`, and update the behaviour tests.

## Edge cases / risks

- **Empty / whitespace-only name** → `""`, which Discord rejects. Group create /
  rename already reject empty names upstream, so this is guarded; no new
  validation added, just noted.
- **Already-split groups:** `split_group`'s by-*name* fallback for the solved
  category could miss an old slugified solved category and clone a duplicate.
  The DAO `get_for_group` fast-path returns early for already-split groups, so
  this only bites on an interrupted resume. Low risk; flagged, not
  re-architected.

## Out of scope (YAGNI)

- No data migration script (lookup is by id).
- No change to the collision-suffix *logic* — only its source function.
- No change to the forum layout path (already out of active scope).
- No shared private helper unless it earns its place.

## Acceptance criteria

- A round named `Monster Mash!` produces a category displayed as
  `MONSTER MASH!` and a text channel `monster-mash`.
- A split round shows `Monster Mash!` and `Monster Mash! — solved` categories.
- Renaming a group updates all its category names verbatim and its metaless
  text channel slugified, matched by id.
- `clean_channel_name` behaviour is identical to today's `clean_name`.
- Full test suite green.
