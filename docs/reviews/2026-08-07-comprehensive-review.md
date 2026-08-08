# Comprehensive codebase review — 2026-08-07

Review-only. No code was modified. Branch `main` @ `3251336`.

Method: ten parallel review workstreams (web security, core service layer, Discord
cogs, DB/store/migrations, media/canvas, Google Sheets, React frontend, test quality,
dead-code/DRY, docs/config/ops). Headline findings were re-verified against source and,
where noted, at runtime in the project venv.

## Status — what has since been fixed

Updated 2026-08-07, after the review. Everything below landed on `staging` with a
failing test written first (each verified red against the old code), and the full gates
green (1658 tests, basedpyright 0 errors, ruff clean).

| Finding | Status | Commit |
|---|---|---|
| H1 SVG served inline (stored XSS) | fixed | `b4c6280` |
| H2 placeholder session secret passes the guard | fixed | `48c7cab` |
| H3 no pixel-dimension cap | fixed (`MAX_PIXELS` 300 MP) | `eaea8a8` |
| M1 `"Solved"` vs `"solved"` compares | fixed | `43eaa5d` |
| M2 `ChannelType.text != "text"` no-op | fixed | `4fc4921` |
| X1 `retile_canvas` self-race | fixed (guard in `schedule_tiling`) | `a8f8527` |
| X2 hunt delete leaks media files | fixed | `8852586` |
| X3 uncapped region-crop executor | fixed (shares the tiling semaphore) | `a8f8527` |
| X4 `media_gc.sweep` blocks the loop | fixed | `b64476c` |
| X5 crop rejected by the upload byte cap | fixed | `bbdf80a` |
| O1 `scripts/` accidentally gitignored | fixed | `96120d8` |
| O4 Pillow undeclared | fixed | `eebb7ff` |
| TT3 always-true assertion | fixed | `595656a` |
| M4 teardown doesn't refresh other fed groups | fixed | `d5071bc` |
| M11 `!remove_minimeta` ungated/unconfirmed | fixed | `fbaf5e4` |
| TT1 destructive routes untested with gates engaged | fixed (mutation-proven) | `0242043` |
| TT2 CSRF guard never exercised at request level | fixed (mutation-proven) | `0242043` |
| T3 `hunt_category.hunt_id` type mismatch, no FK | fixed (migration 038) | `f3df818` |
| T4 import-time DB connections | fixed (lazy connect) | `417b97e` |
| T5 N+1 hydration on the event loop | fixed (mutation-proven) | `cc0d91f` |
| O2 no graceful shutdown, no log rotation | fixed (mutation-proven) | `e758232` |
| O3 `config.json.dist` omits keys the code reads | fixed | `5121a09` |
| O5 `run.py` debug path crashes prod | fixed | `478c841` |
| Dependency bump + Pipfile pinning | shipped to prod | `9e24542` |
| Web-hardening Lows (CSRF binding, WS Origin, int casts, error echo, cache bound) | fixed | `31e8f70` |
| Frontend Lows (WS backoff, safeHref, undo-after-erase, zoom purity, 401 dead-end, unused dep) | fixed | `f7ddf29` |
| Dead / obsolete code group | removed | `d7f8086` |
| DRY: solution-update embed + DeleteRoundModal | fixed | `5e34c7f` |
| Docs drift (absorb_category, pytest flags, clone URL, Manage-Channels, LADDER_SPOT_DATA_DIR) | fixed | this tranche |
| No audit on markup update/delete | fixed | `54f6f6d` |
| `info_bot` LadderSpot title + DM `shard_id` crash | fixed | `54f6f6d` |
| Missing UNIQUE on `hunts.uid` | fixed (migration 040 + re-mint retry; prod verified duplicate-free) | `54f6f6d` |
| Missing UNIQUE on `tag_group.group_code` | **blocked** — needs the `'0'` sentinel moved to NULL first | — |
| Status vocabulary drift (+ case drift, `"Partially Solved"`, `priority='Normal'`) | fixed (migration 041) | this tranche |
| DRY: the drifted unsolve embed pair | fixed (`build_unsolved_embed`) | this tranche |

**M10 (`change-type` tier drift) — resolved, levelled at organiser** (`81cda53`). The
puzzle-keyed conversions moved from `require_admin` to `require_organiser`, matching the
group-keyed `change-type` route; the admin gate was never a boundary anyway, since
organiser ⊇ admin role names meant an organiser could reach the same conversion via the
group. `require_admin` now guards exactly the hunt-lifecycle routes. Note the *bang*
convert commands remain admin-only, so the two surfaces differ deliberately.

**H5 (the cross-task deadlock) — resolved**, with its confirm-under-lock aggravator
(`0173802`, `cd05c68`); see the finding for what changed.

**M6 (reconcile category scope) — fixed** (`806e35e`). The relink search and the orphan
scan now build the hunt's category id set from both join tables plus `hunt.category_id`,
not each round's primary `category_id` alone. Widening the orphan scan brings the hunt's
own channel into range, so it is excluded explicitly — orphans are what `apply-orphans`
deletes.

**M7 (`change-type` 500 on a saved mutation) — fixed** (`51bb15c`), both halves. The
route gained the `except HttpError` its puzzle-keyed twin already had; and `bulk_re_edge`
no longer renders mid-conversion, so a Sheets failure can no longer abort `convert_to_type`
before the Group teardown and `set_source_mode`. The conversion renders once, at the end,
after every durable mutation is committed.

**M8 (authorisation keyed on role names) — fixed**, in five phases
(`7233246`, `78fe9c0`, `0aa4287`, `0f8a3d0`, `6910111`). A guild now pins each tier to
Discord role **ids** stored on `guilds.admin_role_ids` / `organiser_role_ids`
(migration 039), resolved by the single seam `bot/core/role_gate.py` that both the web
routes and the bang commands ask. Empty means NOT CONFIGURED and the tier falls back to
matching names, so nothing changed for an existing guild until it runs
`!set_admin_roles` / `!set_organiser_roles`; `!show_auth_roles` reports which state a
guild is in. A guild that has pinned ids never falls back to names, even if every stored
id is junk. `constants.organiser_gate_active` and `auth.resolve_role_names` were removed
rather than left beside their replacements.

**M9 (organiser-tier destructive bypass) — reviewed, deliberately left as-is**
(decision, 2026-08-08). In a guild with no Organiser-named role — and, since M8, none
pinned by id — `require_organiser` still returns None for any member, and that gate fronts
`delete_puzzle`, `delete_tag_group` (including `keep_puzzles=false`, which deletes every
member puzzle, tab and channel), `delete_mini`, `media_sweep`,
`reconcile?mode=apply-orphans` and `delete_canvas`. The bang side keeps the matching
asymmetry: `!delete_round`/`!delete_puzzle` are effectively open (with a confirm) while
`!delete_hunt` stays admin-gated. This is accepted as a convenience for small single-team
guilds, where every member is trusted with deletion; a guild that wants it shut can now
pin an organiser role by id, which activates the gate. Revisit if a multi-team hunt ever
runs in a guild without pinned roles.

**H4 / T1 / M5 / T2 (the atomicity class) — shipped 2026-08-08**, as the 12-task series in
`docs/superpowers/plans/2026-08-08-atomicity.md` (`ba0df74..f472a83`, 17 commits). No
transactions were introduced: each multi-statement store op was made un-half-appliable by
statement *shape* — reordered, collapsed to one statement, or replaced by an FK cascade —
and the bang edge's per-puzzle lock was widened to cover the read as well as the write.
Migration 037 went with it. H5 was a later, separate finding from the same area.

**The ops tranche (O2/O3/O5) — shipped 2026-08-08** (`478c841`, `5121a09`, `e758232`).
SIGTERM/SIGINT now stop the dashboard and close the gateway cleanly (a second signal
exits at once, rather than waiting out start.sh's SIGKILL); `discord.log` rotates at
10 MB x 5; `config.json.dist` carries every key the code reads; and `run.py`'s debug path
guards its dev-only import and takes its endpoint from config instead of a hardcoded LAN
address. New coverage in `tests/test_ops_shutdown_logging.py`.

**T4 / T5 (the remaining DB items) — shipped 2026-08-08** (`417b97e`, `cc0d91f`).
`MySQLReconnector` now connects on first use rather than in `__init__`, so MySQL being
down at boot no longer raises out of `import bot.store` into a supervisor restart-loop —
and ancillary scripts and test imports stop needing a live database. Puzzle list paths go
through a new `construct_puzzles`, fixing hydration at 4 queries per call instead of 4 per
row. New coverage in `tests/test_store_lazy_connect.py` and
`tests/test_puzzle_batch_hydration.py`.

**Still open**, named individually rather than by series:

* **Rate limiting** on the member-level channel/sheet/disk-creating routes — deferred from
  the web-hardening tranche as larger than the surrounding Lows (needs a limiter and a
  storage decision).
* **Keyboard reachability** for the pointer-only interactions (colour swatches,
  click-to-edit cells, sortable headers) — deferred from the frontend tranche as a real
  a11y pass rather than a Low.
* **UNIQUE on `tag_group.group_code`** — held back from migration 040. The column default
  is `'0'`, as is `TagGroupData.group_code`, and that is an in-band sentinel for "no code
  yet" which many rows legitimately share (`resolve_group` only treats 6-character codes as
  real). A UNIQUE index makes `'0'` single-occupancy — it breaks eight live-DB tests and the
  next codeless insert in production. Indexing it requires first moving the sentinel from
  `'0'` to NULL (NULLs are exempt from UNIQUE): a data + model change touching the column
  default, the dataclass default, and the f-string read sites that would start printing
  "None" instead of "0". `hunts.uid` did not have this problem — it is already NULLable.
* **`statusTone` tints `"partially solved"` green**, i.e. as fully solved: the rule at
  `statusTone.ts:20` matches the substring `"solved"` and only carves out `"unsolved"`.
  Found during the status-vocabulary tranche and deliberately NOT changed there — whether a
  partial solve should read as solved, in-flight, or its own tone is a UI call, not drift.
* **`backfill_hunt_uids.py`** — spent once every DB row has a non-null uid, which needs a
  live DB to confirm. (It is not broken: the `dict_cursor` call this review reported is not
  in the file.)

The naming items remain evidence-only pending sign-off; `round_id` is the agreed first
rename when that starts.

## Verification commands run

| Check | Command | Result |
|---|---|---|
| Tests | `pipenv run python -m pytest tests/ -q` | **1645 passed**, 28 warnings, 11.4s |
| Lint | `pipenv run ruff check` | **All checks passed** |
| Types | `pipenv run basedpyright bot/` | **0 errors, 0 warnings** |
| Frontend types | `npx tsc --noEmit` | **exit 0** |

The 28 warnings are all one thing: `datetime.utcnow()` deprecation (Python 3.12+) at
`bot/store/mysqldb.py:708,839`, `bot/core/media_gc.py:42` and two test sites. Mechanical
to fix; note the media_gc column is naive-UTC, so a naive `utcnow()` is *correct* there
today — a careless swap to `now()` would introduce a 24h-window bug.

## Overall assessment

Healthy and disciplined for its size (~21k Python, ~10k TS, 150 test files, 37
migrations). Gate checks are green, the service-layer/lock architecture is real and
mostly sound, path traversal is systematically defended, SQL is parameterised
throughout, and the OAuth flow is correct. The problems cluster in three places:

1. **The read-modify-write story is not actually atomic** — `autocommit=True` makes every
   `commit()` a no-op, the bang-command edge reads its snapshot *before* taking the
   per-puzzle lock and writes the whole row *after* releasing it, and two background
   `create_task` link paths commit unlocked. These compound into silent lost updates.
2. **A few string/enum comparisons are quietly wrong**, turning whole code paths into
   no-ops that tests don't catch because the mocks use the string the code expects.
3. **The web layer's newest surfaces lag its own hardening** — the attachment route
   serves SVG inline (stored XSS) that the media route explicitly forbids; there is no
   pixel-dimension cap anywhere; auth keys on low-entropy Discord role *names*.

Nothing here is an unauthenticated remote-code-execution class defect. The High items are
a realistic XSS, an availability/DoS vector, and two silent-data-corruption races.

---

## High

### H1. Stored XSS: SVG attachments served inline from the dashboard origin
**Confirmed (code path) — found independently by two workstreams.**
`bot/web/api.py:186-193`. `content_type` is Discord's claim, stored verbatim by the
bridge (`bot/cogs/bridge.py`), with no validation on ingest (`message_media.download_attachment`).
`is_image = content_type.startswith("image/")` is satisfied by `image/svg+xml`, so the
file is served **inline, same-origin, with its declared type**. `X-Content-Type-Options:
nosniff` does not help — SVG is the declared type and is a scriptable document. This
directly contradicts the rule stated two files away (`bot/core/media_store.py:16-17`:
"SVG is deliberately absent … serving one from our own origin would be stored XSS"),
which the upload path enforces and `tests/test_media_web.py` asserts. `csrf_token` is
`httponly=False`, so injected script can read it and reach every mutating route.
Trigger needs a click-through to `/api/attachments/<id>` (or a shared link), not just the
in-`<img>` render. **Fix:** apply `media_store.ALLOWED_IMAGE_TYPES` on this route too
(non-allowlisted → `as_attachment=True` + `application/octet-stream`); add a
`Content-Security-Policy` on both byte-serving routes.

### H2. Shipped placeholder session secret passes the weak-secret guard
**Confirmed.** `config.json.dist:19` ships `"session_secret":
"change-me-to-a-long-random-string"` — 33 chars, absent from the blocklist in
`_valid_session_secret` (`bot/web/__init__.py:33-37`), so the start-up guard that is
supposed to refuse a weak secret passes the single value most likely to be copied
verbatim. A known secret → forgeable Quart session cookies → arbitrary
`session["user"]["id"]`, including admin impersonation. **Fix:** reject any secret equal
to the dist value (or add it to the blocklist).

### H3. No pixel-dimension cap anywhere — image decompression bomb → OOM
**High confidence.** `validate_image_bytes` (`bot/core/media_store.py:81-89`) caps only
*compressed* bytes (25 MB). `MAX_DIRECT_PIXELS` (`image_tiler.py:73`) is a
"should-we-tile" threshold, not a limit — exceeding it *schedules* the expensive pyramid
build. A ~1 MB PNG of a flat 40000×40000 canvas passes every check; `build_pyramid` then
peaks tri-digit-MB per image (the module's own note cites ~360 MB for 207 MP) and the
concurrency semaphore of 2 bounds count, not per-image footprint. Two uploads OOM a
CX23-class box. **Fix:** probe dimensions (header-only) before accepting; reject above a
hard `MAX_PIXELS` in the `validate_image_bytes` seam.

### H4. Lost updates at the bang-command edge: snapshot read before the lock, full row committed after it releases
**High confidence.** `Puzzles.cog_before_invoke` (`bot/cogs/puzzles.py:197-282`) loads
puzzle/tag_group/hunt into `self.environment` **before** `with_puzzle_mutex` acquires the
per-puzzle lock (the decorator wraps only the callback), and `cog_after_invoke:286`
commits the whole row **after** the decorator has released it. The generic `commit()`
writes every field by name, so a stale snapshot clobbers a concurrent bang/web write
(e.g. `!note` + `!status` racing: the note vanishes). The M4 per-puzzle lock serialises
*execution* but neither the read nor the write. Also: after-invoke runs in discord.py's
`finally`, so a command that mutates then raises still persists the partial mutation.
**Fix:** read (or re-read) and commit inside the lock, or drop the blanket after-invoke
commit (services already commit). See also T1 (no real transactions) and M-race items.

### H5. Cross-task deadlock: convert vs. delete/archive acquire overlapping locks in opposite orders
**RESOLVED** (`0173802`, aggravator `cd05c68`). `bulk_re_edge` now takes no lock of its
own and every convert edge acquires target + members in one sorted
`puzzles_lock(convert_lock_ids(...))`, the order the delete/archive edges already used —
with no nested acquisition the cycle cannot be constructed. Regression cover in
`tests/test_lock_ordering.py`; the aggravator (confirm-under-lock) is fixed too.
One correction to the finding below: the bang convert commands do **not** enter the
cycle via `with_puzzle_mutex` — they carry no mutex decorator and took no puzzle lock at
all, which was a separate lost-update hole. They now take the same set.

**High confidence (structural).** `bulk_re_edge` acquires every member lock in sorted
order *internally* (by design); its convert callers hold the **target's** lock first
(`bot/web/actions.py:1341,1692`; bang via `with_puzzle_mutex`) → order `T, then
sorted(members)`. `delete_round`/`delete_hunt`/`archive_hunt` take
`puzzles_lock(member_ids)` which **includes the target** (its `target` self-edge row) in
globally sorted order. When any member id sorts below the target id (the feeders-first
creation order the domain explicitly supports), convert holds `T` waiting on member `a`
while delete holds `a` waiting on `T` — asyncio locks never time out, so both tasks and
everything touching those puzzles wedge until restart. The `puzzles_lock` docstring's
"can't join a cycle" argument is invalidated by `bulk_re_edge`-under-a-held-target-lock.
**Fix:** acquire target+members as one sorted set at a single site. Aggravator worth
fixing independently: bang delete/archive hold `puzzles_lock` **across** the human
`_confirm_destructive` click (`puzzles.py:3830,3918,4016`), blocking all solves/retags in
the round/hunt for the confirm window — confirm first, then lock.

---

## Medium

### Correctness / logic

- **M1. Solve-status case mismatch — three paths treat solved feeders as unsolved.**
  *Confirmed.* `solve()` writes `puzzle.status = "Solved"` (`puzzle_service.py:1240`) but
  `extract_target_to_category` (`:2149-2150`) and `consolidate_to` (`:2265,2274`) compare
  against lowercase `"solved"`, so the comparisons are always False (and miss
  `"backsolved"` entirely). Effect: solved feeders placed above the solve divider on
  extract; solved channels counted toward overflow and moved to the unsolved side on
  consolidate. Every other path uses the `puzzle.solved` boolean — **fix:** use it here.

- **M2. Orphan-channel scan is a production no-op.** *Confirmed at runtime.*
  `_collect_orphan_channels` (`puzzle_service.py:588`) does `getattr(channel, "type",
  "text") != "text"` — but `discord.ChannelType.text == 'text'` is `False`, so every real
  channel is skipped; `!reconcile` never reports and `delete_orphans` never deletes orphan
  channels in prod. Tests pass because mocks set `type="text"` as a string. **Fix:**
  compare against `discord.ChannelType.text`.

- **M3. `safe_rename`'s rate-limit deferral is dead code; renames now block inline —
  including under `layout_lock`.** *High confidence.* `max_ratelimit_timeout` was removed
  (dc4805e), so discord.py never raises `discord.RateLimited`; the whole
  `except RateLimited`/`_deferred_rename` machinery can't engage and `channel.edit(name=)`
  blocks the coroutine for up to ~10 min. `extract_target_to_category` calls it inside
  `category_layout.layout_lock` (`:2182`), so a rename storm can wedge all
  category-structure ops. **Fix:** delete the dead machinery and move glyph renames
  outside the lock / behind `create_task`, or restore the setting understanding its
  pre-emption bug.

- **M4. `_teardown_group_target` deletes a target without refreshing other groups it fed**
  (`:1160-1182`) — the metameta case leaves stale contribution rows, unlike `delete_round`
  which collects `other_fed_group_ids` and refreshes. **Fix:** snapshot `target.tags`
  before delete, refresh those groups.

- **M5. Background evidence auto-link commits a puzzle unlocked** (`:3041-3056`, launched
  as bare `create_task` from `actions.py:2209+`) — same lost-update class as H4/T1; the
  bidirectional tag-sync in `commit()` can silently revert an informs edge a concurrent
  locked command wrote. **Fix:** wrap the link/unlink bodies in `puzzle_lock(source_id)`.

- **M6. Reconcile relink/orphan scope misses the general + split/overflow categories**
  (`:554-556`) — the id set is built from each round's primary `category_id` only, so
  relink can't find a channel already in an overflow/solved/general category and step 3
  **creates a duplicate** (at guild root, outside gating overwrites, for channel-home
  groups). **Fix:** build the set from both join tables + `hunt.category_id`.

- **M7. Group-keyed `change-type` returns 500 on a mutation it already saved.** *High
  confidence.* `refresh_target` re-raises the **raw** `HttpError`, not `SheetRefreshError`;
  the puzzle-keyed handler catches `HttpError` (`actions.py:1372`) but `change_group_type`
  (`:1697-1702`) does not, so a Sheets hiccup after commit surfaces as 500 and skips the
  `@pushes_board` push. A mid-`bulk_re_edge` Sheets failure also aborts `convert_to_type`
  before teardown/`set_source_mode`, leaving a half-converted group that "Rebuild" won't
  finish. **Fix:** wrap refresh failures in `SheetRefreshError`, or add `HttpError`
  handling + failure-isolated teardown.

### Web authorisation

- **M8. Authorisation keyed on low-entropy Discord role *names*.** *Confirmed.*
  `ADMIN_ROLES = ('Moderator', 'mod', 'admin')` (`constants.py:10`) matched by exact string
  against `member.roles` names. Anyone with Discord's *Manage Roles* permission can create
  a role named `mod`/`admin`, self-assign, and gain full dashboard admin (delete_hunt,
  set_hunt_role, patch settings, create_hunt); such names may also pre-exist for unrelated
  reasons. **Fix:** key on role *ids* stored per guild, names as bootstrap only.

- **M9. Organiser-tier destructive bypass is under-scoped.** *Confirmed (deliberate, but
  the gated set has grown).* In a guild with no Organiser-named role, `require_organiser`
  returns None for **any** member (`auth.py:228-239`, tested) — and that gate now fronts
  `delete_puzzle`, `delete_tag_group` (incl. `keep_puzzles=false`, which deletes every
  member puzzle/tab/channel), `delete_mini`, `media_sweep`, `reconcile?mode=apply-orphans`,
  `delete_canvas`. In an un-role'd guild these are open to any team member. The bang side
  has the same asymmetry: `!delete_round`/`!delete_puzzle` effectively open (with confirm)
  while `!delete_hunt` stays admin-gated. **Fix:** a "destructive" tier that is never
  bypassed, or make the bypass opt-in per guild.

- **M10. `change-type` tier drift** — the group-keyed route is `require_organiser`
  (`actions.py:1662`) while the equivalent puzzle-keyed conversions are `require_admin`
  (`:709,1277,1337`); since organiser ⊇ admin-role-names, an organiser performs the
  admin-gated conversion by addressing the group. **Fix:** one tier for "retype a group".

- **M11. `!remove_minimeta` is destructive, ungated, unconfirmed** (`puzzles.py:1540`) —
  deletes every mini (threads + tabs) + rollup with no role check and no ConfirmView, while
  its single-item sibling `!delete_mini` requires both. **Fix:** gate + confirm to match.

### Data / persistence / DB

- **T1. No real transactions: `autocommit=True` makes `commit()` a no-op; multi-statement
  ops are non-atomic.** *Confirmed.* `bot/store/__init__.py:65,77`. Every `self.mydb.commit()`
  commits nothing; each statement autocommits individually. Half-applyable ops:
  `bulk_update_edge_type` (DELETE-then-reINSERT of a group's edges — a failure between
  **destroys membership**), `PuzzleJsonDb.commit`'s base-row + tag-diff, `delete_message`'s
  attachments-then-message. `set_positions` even carries a docstring
  (`mysqldb.py:1102-1103`) claiming an atomicity guarantee the code does not provide.
  **Fix:** either make the reconnector genuinely transactional around multi-statement ops,
  or delete the no-op `commit()`s and the false atomicity claims and lean on the FK
  cascades (022/033/035) already in place.

- **T2. Generic id-addressed UPDATE silently no-ops on a missing row** (`mysqldb.py:52-59`,
  no `rowcount` check) — a stale object whose row was cascade-deleted (e.g. held across a
  `delete_round`, or deleted from the web mid bang-command) silently drops the write.
  Compounds H4/M5. **Fix:** log/raise on `rowcount == 0`.

- **T3. Migration 026 repeats the exact bug migration 022 fixed.** *Confirmed.*
  `026_hunt_category.sql:7` declares `hunt_id BIGINT(20) UNSIGNED` with **no FK**, but
  `hunts.id` is `int(11)` — 022 diagnosed and fixed this same copy-paste for
  `tag_group_category`. So hunt deletion doesn't cascade `hunt_category`, and the app-side
  cleanup sits inside `if guild is not None:` (`puzzle_service.py:3472`), so a guild-less
  (web/API) hunt delete orphans the rows and a recycled category can be mis-adopted.
  **Fix:** a migration mirroring 022 (`MODIFY hunt_id int(11) NOT NULL` + FK ON DELETE
  CASCADE).

- **T4. Import-time DB connections → production crash-loop risk — fixed** (`417b97e`) (`bot/store/__init__.py`
  opens three `MySQLReconnector`s at import; no startup retry/backoff — only *established*
  connections self-heal). MySQL down at boot → import raises after ~15s and the supervisor
  restart-loops a recovering DB; also forces every ancillary script/test to have a live DB.
  **Fix:** lazy-connect on first `cursor()` (the `_ensure` machinery already supports it) —
  fixes bot boot, scripts, and the test-import constraint at once.

- **T5. N+1 hydration on the event loop — fixed** (`cc0d91f`) — `construct_puzzle` runs 4 queries/row
  (`mysqldb.py:345-351`); the list paths loop it, so a 100-puzzle hunt is ~400 sequential
  round-trips on the gateway thread. The dashboard path already shows the batched-join
  alternative. **Fix:** batch-hydrate the list paths.

### Media / canvas

- **X1. `retile_canvas` races itself** (`actions.py:2464`, not gated, accepts `pending`) —
  two schedules both pass the check; `build_pyramid` does `shutil.rmtree(dest)` then
  `dzsave`, so builder A rmtree's out from under builder B and the two `update_tiles` land
  in arbitrary order (row can read `ready` over a half-written pyramid). **Fix:** in-process
  guard keyed on `media.id`; build into a temp dir and atomically rename.

- **X2. Hunt deletion leaks every media file permanently** — `HuntJsonDb.delete`
  cascade-drops the `media` rows (030), but nothing removes files/pyramids, and `media_gc`
  reclaims *from rows*, so once the rows are gone the bytes are unreachable. *Confirmed:*
  `delete_hunt` (`puzzle_service.py:3488-3494`) has no filesystem cleanup. **Fix:** rmtree
  `uploads/<hunt_id>/` and `tiles/<hunt_id>/` in `delete_hunt` (both are hunt-keyed).

- **X3. Region crop runs uncapped on the shared executor** (`actions.py:2363`,
  `crop_bytes` = full decode of up to 207 MP) — the exact hazard `MAX_CONCURRENT_TILING`
  bounds for tiling; this path took no semaphore. **Fix:** share the tiling semaphore.

- **X4. `media_gc.sweep` blocks the event loop** on `shutil.rmtree`-per-row
  (`actions.py:1832`, `puzzles.py:1027`) — tens of thousands of unlinks on the gateway
  thread; the rest of the subsystem got the executor treatment. **Fix:**
  `await asyncio.to_thread(media_gc.sweep, hunt_id)`.

- **X5. Region crop silently fails when the crop exceeds the *upload* size cap**
  (`_crop_region_media` re-validates server-generated PNG against
  `web_attachment_max_bytes`; a large `locates` region re-encodes past 25 MB → `MediaRejected`
  swallowed → region created with `media_id=None`). A hostile-input control applied to
  trusted local bytes. **Fix:** internal save path that skips the size cap (keep the sniff).

### Ops / infra

- **O1. `scripts/` is accidentally gitignored — four real source files are uncommitted.**
  *Confirmed* (`git status --ignored` shows `!! scripts/`). `.gitignore:31` (`scripts/`,
  Python-packaging boilerplate) makes `diagnose_addtable.py`, `diagnose_generate.py`,
  `drop_empty_hunts.py`, `elements.py` untracked — the same "source file goes missing"
  failure the .gitignore itself warns about for `frontend/src/lib`. **Fix:** `!scripts/*.py`
  (keep the DB dump ignored).

- **O2. No graceful shutdown; no log rotation — fixed** (`e758232`). No `SIGTERM` handler anywhere, so
  `start.sh`'s SIGTERM→10s→SIGKILL aborts in-flight archive/channel/sheet work mid-op and
  evaporates in-process locks (reconcile heals it, but deploys during a live hunt are a real
  window). `discord.log` is a plain unbounded `FileHandler` on the root logger
  (`bot/__init__.py:252`). **Fix:** minimal `add_signal_handler → bot.close()`;
  `RotatingFileHandler` for discord.log.

- **O3. `config.json.dist` omits keys the code reads — fixed** (`5121a09`) — `max_channels_per_category`,
  `web.frontend_dist`, `web.bridge_enabled`, `web.archive_media_dir` (required for image
  evidence even with the bridge off), `web.attachment_max_bytes`. **Fix:** add them
  (commented/defaulted) to the template.

- **O4. Pillow is an undeclared direct dependency** — `bot/core/ocr.py:10` imports it, but
  `Pipfile` only gets it transitively via pytesseract; a loosened pytesseract pin stops
  `pipenv sync` installing it. **Fix:** add `pillow` to `[packages]` and re-lock.

- **O5. `run.py` debug path crashes prod — fixed** (`478c841`) — `"debug": true` imports `pydevd_pycharm` (a
  *dev* dep, absent after `deploy.sh`'s `pipenv sync`) and dials a hardcoded
  `192.168.44.2:29781` (`run.py:5-7`). **Fix:** guard the import / drop the hardcoded IP.

### Tests

- **TT1. The most destructive web routes are never tested with their auth gate in place.**
  *Confirmed.* `test_delete_hunt_web.py:30` patches `require_admin` to a no-op and asserts
  zero 403s; delete_round/delete_puzzle/convert/edge/layout tests do the same. Dropping the
  `require_admin(...)` call from the delete-hunt route leaves the suite green. The denial-test
  pattern exists (delete_mini, canvas, media, evidence have it) — it's just missing on the
  highest-blast-radius routes. **Fix:** one gate-unpatched denial test per destructive route.

- **TT2. CSRF guard + app-factory wiring never exercised** — `test_csrf.py` tests only the
  pure helpers; no test calls `create_app`, so the `@before_request` guard, cookie flags,
  and weak-secret refusal are inspection-only. Removing the guard passes the suite.

- **TT3. Always-true assertion.** `tests/test_evidence_commands.py:47`
  `assert cog.service.refresh_evidence.called or True` — named `test_fires_refresh_evidence`,
  cannot fail. **Fix:** assert `refresh_evidence_safe.assert_awaited_once()` or delete.

- **TT4. Zero-coverage high-risk modules:** `migrate.py`, `bot/web/data.py:get_hunt_puzzles`
  (the main dashboard read model — only source-string-sniffed by
  `test_minimeta_service.py:484`), `sheet_tables.py`, OAuth `login`/`callback` state
  validation, and the `bulk_update_edge_type` SQL (asserted only as a mocked call).

---

## Low (grouped)

- **Docs drift:** `!absorb_category` missing from the "canonical" `docs/commands.md`
  (found twice); README documents non-existent pytest `--compact`/`--parallel` flags
  (`README.md:824`); README clone URL points at `sloop-puzzles/puzzlehunt-discordbot` while
  the remote is `uhtoff/QDPuzzleBot`; `backfill_glyphs`/`extract`/`consolidate` also require
  Discord Manage-Channels, undocumented; stale `$LADDER_SPOT_DATA_DIR` JSON-era claim.
- **Web hardening (defence-in-depth):** no Origin check on the activity WebSocket (CSRF
  guard doesn't cover websockets in Quart); CSRF token unbound to the session and
  domain-fixable from a sibling host; unguarded `int()` casts on client JSON → 500 not 400
  (`api.py:558`, `actions.py:2399,2799`); internal exception strings echoed to clients
  (~9 sites); no rate limit on member-level channel/sheet/disk-creating routes; unbounded
  `_not_member_cache`.
- **Frontend:** activity WebSocket reconnects forever on a 1008 auth-close (no
  `event.code` check, no backoff); mid-session 401 dead-ends in a raw "auth_required" toast
  because `["me"]` is never invalidated; markup **undo after erase** pops an already-deleted
  id → 404 toast, undo consumed (eraser never prunes `mine`, `MarkupCanvas.tsx:242`);
  impure `setPan`-inside-`setZoom` updater double-applies in StrictMode; `@tanstack/react-table`
  is an unused dependency; several primary interactions (colour swatches, click-to-edit
  cells, sortable headers) are pointer-only / not keyboard-reachable.
- **Untrusted embed URL** rendered as an anchor with no scheme allowlist and no `rel`
  (`ChatViewer.tsx:95`) — a `javascript:` href would execute if one ever got through.
- **Dead / broken odds and ends:** `dbtest.py` imports removed `RoundData`/`RoundJsonDb`
  (ImportError on run); `backfill_hunt_uids.py` calls `dict_cursor` on a raw connection
  (AttributeError); `memes.sh` runs the bot as root from a hardcoded path (delete);
  `run-bot.sh`/`process.id` orphaned; `get_lowest_code_in_hunt` (callerless, and its
  `ORDER BY … DESC` contradicts its name), `get_all(hunt_id=…)` ignores its param;
  `update_metapuzzle`/`_update_all_metas` kept alive only by tests.
- **Status vocabulary drift:** `"needs submission"` in `STATUSES` but omitted from slash
  choices/docstring/docs; `unsolve` writes `"Unsolved"`, not in `STATUSES`.
- **Missing UNIQUE constraints** on `hunts.uid` and `tag_group.group_code` — uniqueness is
  check-then-insert (`generate_uid`), a TOCTOU race between a bang + web create can mint a
  duplicate and make one hunt unreachable.
- **No audit on markup update/delete** (only `create_mark` audits) — any member can
  silently erase another's ink with no trail.
- **`info_bot`** embed still titled "LadderSpot" (template leftover); `ctx.guild.shard_id`
  crashes in DMs.

## Dead / obsolete code (safe to remove)

Verified callerless (AST reference scan run twice — with and without comments/strings —
plus dynamic-dispatch, decorator and re-export checks):

- **`_install_ratelimit_probe()`** (`bot/__init__.py:22-126`) — three unconditional
  monkey-patches wrapping *every* HTTP request the bot makes, self-labelled "TEMPORARY /
  remove once the scope is known". The mystery it chased was resolved 2026-06-23 (dc4805e);
  its own comment still blames `max_ratelimit_timeout`, which no longer exists in the file.
  Highest-value deletion here.
- **Dead cog methods:** `web_refresh_meta` (`puzzles.py:1041`), `_web_send_welcome`
  (`:1050`), `clean_channel_name` wrapper (`:406`), `get_solved_channel` (`:4086`),
  `update_metapuzzle`/`_update_all_metas` (test-only).
- **Dead service/store:** `is_fed`/`is_informed` (`puzzle_service.py:171,183`),
  `delete_category_with_channels` (`discord_channels.py:123`), `get_lowest_code_in_hunt` +
  `check_duplicate_group_code` (`mysqldb.py:171,178`), `MySQLChannelActivityDb.get_for_guild`,
  `get_guild_activity` (`data.py:744`), the legacy JSON-storage ABCs `_PuzzleJsonDb`/
  `_HuntJsonDb`/`_TagGroupJsonDb`/`_RollupJsonDb`/`_GuildSettingsDb` (nothing subclasses
  them — all DAOs subclass `_MySQLBaseDb`), the `HuntSettings` dataclass, and
  `dir_path`/`DATA_DIR`/`LADDER_SPOT_DATA_DIR` (stored, never read).
- **Dead utils / config:** `docs_url`/`drive_folder_url`/`extract_id_from_url`
  (`urls.py`), `_chunk` (`chunking.py`), commented-out `ping` (`utility.py:21`),
  `Config.store()` (dead **and** hazardous — would truncate config.json to 3 keys),
  config keys `owner_email`/`puzzle_addons_path`/`puzzle_cell_link`/`DB_DSN`.
- **Spent one-offs / stale scripts:** `memes.sh` (runs the bot as root from a dead path),
  `run-bot.sh`, `process.id`, `dbtest.py` (broken import), `backfill_hunt_uids.py` (broken
  `dict_cursor` call; also spent once every DB has non-null uids — verify then archive),
  `scripts/diagnose_*.py` and `drop_empty_hunts.py` (spent), `scripts/sql/001_dashboard.sql`
  (byte-dup of `migrations/001`).

## DRY / refactoring opportunities

- The remaining live `web_*` twins duplicate **embed wording**, not logic: the
  partial/change/add/unsolved titles+descriptions are copied verbatim between
  `web_post_solution_update_embed`/`web_post_unsolved` (`puzzles.py:1098,1135`) and the bang
  `_partial_impl`/`_change_or_add_solution_impl`/`!unsolve` paths. The #79 fix
  (`build_solved_embed`) already did this for "solved" — the same shared-builder treatment
  finishes it. (`web_refresh_info` is a thin best-effort adapter — an acceptable seam, not
  duplication.)
- The two round-delete confirmation modals in the SPA (`BoardGroup.tsx:381`,
  `DragBoard.tsx:942`) are near-identical and their warning copy has already drifted
  ("Group…" vs "Round…") — extract a shared `DeleteRoundModal`.

## Naming (stale — evidence only; each needs your sign-off before any rename)

Per the house "naming must track meaning" rule these are reported as evidence, not verdicts;
none touched. Blast radius counted, not estimated:

- **`round_id` now means a tag_group** — survives in `SheetRefreshError.failed_round_ids`
  (`errors.py`), `rollup_lock(round_id)` (`locks.py:94`), and — crossing the wire — the API
  payload key `round_id` (`actions.py:2798` ↔ `frontend/src/api.ts:1022`). DB columns were
  already renamed by migration 011; these are the code/wire survivors.
- **`meta_code` vs canonical `group_code`** — 45 occurrences; renaming changes slash-command
  argument names (user-visible).
- **`*JsonDb` names are false** — every MySQL DAO carries the JSON-file-era name; ~100 files
  including tests and the patch-target names pinned in AGENTS.md.
- **`additional_sheets.puzzle_name`** holds a tab title, not a puzzle name (reaches a DB
  column; the web layer already re-labels it `"name"` in payloads).

## Cross-cutting themes

1. **Atomicity of read-modify-write** (H4, M5, T1, T2) — one root cause with several faces.
   Fixing the lock/commit boundary and the autocommit no-op together closes the whole class.
2. **String/enum comparisons the tests can't catch** (M1, M2) — the mocks feed the string
   the code expects, so unfalsifiable tests pass while prod paths no-op. Worth a grep sweep
   for other `== "text"` / `== "solved"`-style comparisons.
3. **The web layer's newest surfaces lag its own hardening** (H1, H3, M8, X-series) — the
   patterns to copy already exist in-repo (`media_store` allowlist, the tiling semaphore,
   the executor offload); several routes just didn't get them.

## Coverage / limitations

Inspected: all of `bot/`, `frontend/src`, all 37 migrations, the test suite structure +
deep reads of the safety-critical files, docs, config, and deploy scripts. Not done: no DB
was connected (live-schema-vs-migration drift, DB default charset, and pool-revival
behaviour are unverified); no runtime reproduction of the deadlock (H5) or the pixel-bomb
memory figures (H3) — both are structural/measured-from-code; Discord's exact
`content_type` for a `.svg` upload (H1's trigger) was not tested against the live API;
`test_contribution_table_requests.py` (1,462 lines) and the gsheet/archive/bridge test
families were not read in full. The bang↔web permission-parity comparison is one-sided
(the web decorators were audited in depth, the bang decorators sampled).

## Suggested order of attack

1. H2 (one-line blocklist), O1 (`.gitignore` — you have uncommitted source), O4 (Pillow).
2. H1 + H3 + X2/X4 (media route allowlist, pixel cap, hunt-delete cleanup, sweep offload) —
   contained, high-value, patterns already in-repo.
3. M1 + M2 (`.solved` boolean; `ChannelType.text`) — tiny diffs, real behaviour fixes; add
   falsifying tests as you go.
4. H4 + T1 + M5 (the atomicity class) — the biggest design fix; scope it deliberately.
5. ~~H5 (deadlock) — structural lock-ordering change; needs care and a repro test.~~ **Done**
   (`0173802`, `cd05c68`).
