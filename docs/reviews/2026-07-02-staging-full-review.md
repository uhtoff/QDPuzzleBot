# Full codebase review — `staging` (2026-07-02)

Scope requested: correct code, minimal duplication, sensible practices, complete
documentation (incl. welcome messages), and bang ↔ web command parity (incl. Discord
channel messages and info-pin refreshes on settings changes).

Method: two exploration agents mapped the command surfaces and docs/messaging machinery;
11 parallel reviewers (7 correctness-by-subsystem + duplication, practices, documentation,
parity auditors) produced 100 raw findings; every finding was adversarially verified by an
independent agent against the actual code (93 confirmed, 7 refuted); confirmed findings
were deduplicated to 90. The three most consequential high-severity findings were
additionally hand-verified. Known-intentional quirks (stale forum layout, manual hunt-role
seam, tag/edge wording, vestigial pool wording) were excluded by construction.

**Totals: 8 high, 49 medium, 33 low** (the summary undercounts by one — #62 is also HIGH,
making 9). No code was changed at review time.

**Implementation status (2026-07-02, branch `working-1` → merged to `staging` in `8442c15`):**
all 9 HIGH findings (#1, #2, #3, #4, #5, #6, #7, #8, #62) are fixed. Three adjacent
MEDIUM/LOW findings were folded in at the user's request: #28 (fixed), #79 (fixed, and
surfaced/removed an additional dead-code path not itself listed as a finding), #80 (fixed).
#88 is moot as a side effect of the #79 fix (the vulnerable code was deleted). See each
finding below for its commit.

**Implementation status, second pass (2026-07-02, branch `working-1`):** the user picked four
further tranches of related MEDIUM/LOW findings to fix together, worked through in order.
All landed:
- Tranche 1 (`5747523`): missing channel/None guards — #9, #10, #11, #13, #14, #16, #18,
  #19, #21, #23.
- Tranche 2 (`52a5e94`): lying success-ack ordering — #17, #74.
- Tranche 3 (`17afadf`): web↔Discord pin/board staleness — #63, #65, #66, #67, #68, #85.
- Tranche 4 (`be0ae05`): Google Sheets correctness bugs — #25, #26, #47, #48.

**#24** (event-loop-blocking Sheets calls) was scoped out of tranche 4 deliberately (the real
fix required converting 6 sync helper methods to async across ~30 call sites in two source
files plus two test files, a much larger/riskier change than a single-function fix) and left as
its own follow-up task. That follow-up is now done: `sheet_list` and 6 related helpers (plus a
7th, `get_puzzle_sheet_index`, sharing the same defect) in `bot/cogs/puzzles_gsheet.py` are async,
routed through the existing `execute_with_retry` helper instead of the event-loop-blocking
`execute_sync`; every call site across `bot/cogs/puzzles_gsheet.py` and `bot/core/puzzle_service.py`
now awaits them (commits `8319a31`, `494a91f`, `f8fcfe9`, `fdb4cfb`).

Round/hunt-deletion-scoped findings (#29, #30, #32, #42, #45, #54, #55) were excluded from
every tranche above by design — that area was being worked in parallel in `working-2`
(see the delete-round scope work merged separately into `staging`). Not evaluated here for
whether that work incidentally fixed any of them.

No other findings in this document have been actioned.

Severity: high = wrong behaviour/data loss a user will plausibly hit; medium = real but
narrower bug or meaningful parity/doc gap; low = latent bug, drift risk, or cleanup.


## 1. Correctness (61 findings)

### 1. [HIGH] `bot/cogs/puzzles.py:2917`

**Status: FIXED** in `b0225d6` (`fix(archive): preserve attachment/embed-only messages (#1)`, branch `working-1`, merged to `staging` in `8442c15`).

archive_channel's per-message loop gates all archiving (attachment download, webhook re-send, reactions) behind `if content:` at bot/cogs/puzzles.py:2917, so any message with empty text — attachment-only posts (pasted screenshots) and embed-only messages — is silently dropped from the archive thread, and the source channel is then permanently deleted at line 2946, losing those attachments irrecoverably.

**Failure scenario:** A solver posts an image of the puzzle grid with no text; !archive_round archives the channel and deletes it — the `if content:` guard at line 2917 never reaches the attachment-download code, so the image is permanently lost.

**Verification evidence:** bot/cogs/puzzles.py:2916-2917 — `content = message.content` then `if content:` wraps the entire archive block: the attachment-to_file loop (2920-2927), the webhook.send that would carry `files=` and `embeds=` (2930-2931), and reaction copying (2937-2944). A message with attachments but no caption has `message.content == ""`, is falsy, and skips everything; there is no separate attachment-handling path elsewhere in the function or in callers. Line 2946 then does `await channel.delete(reason=self.DELETE_REASON)`, making the loss permanent. Call chain confirmed: !archive_round (2811) → archive_category (2836, calls archive_channel per channel at 2855) → archive_channel (2864). Nothing in the known-intentional quirks list covers this; the attachment try/except at 2922-2927 shows attachments are intended to be preserved, so the guard skipping them is a genuine defect, not a design choice. Screenshot-only posts are routine in puzzle channels, so a user will plausibly hit this — severity high stands.

### 2. [HIGH] `bot/cogs/puzzles_gsheet.py:513`

**Status: FIXED** in `3c4042a` (`fix(gsheet): unsolve archive-spreadsheet desync (#2, #4)`, branch `working-1`, merged to `staging` in `8442c15`). Fixed together with #4 below (same code path).

restore_puzzle_spreadsheet gates moving additional sheets back from the archive on `puzzle_data.solved` (bot/cogs/puzzles_gsheet.py:513), but its only production caller, puzzle_service.unsolve, sets `puzzle.solved = False` (puzzle_service.py:1000) before calling it (line 1012), so the move-back branch is dead code; the requests built at lines 516-523 then reference archive-spreadsheet sheet ids in a batchUpdate sent to the LIVE spreadsheet (batch_update default archive=False, line 78-79), so the entire atomic batch — including the main tab's solution/progress clear and reposition — fails with 400 "no sheet with id", and unsolve raises after the DB solve state was already committed.

**Failure scenario:** Archive spreadsheet configured; a puzzle with an !addsheet extra sheet is solved (archive_puzzle_spreadsheet moves the extra sheet to the archive at lines 571-574 unconditionally) and then unsolved. The solved check is False, the extra sheet stays in the archive, and the queued update_cell/move_sheet_to_index/revert_tab_colour requests for its archive-only sheetId go to the LIVE spreadsheet — batchUpdate returns 400 'no sheet with id', the whole restore batch (including the main tab's solution/progress clear) fails, and unsolve raises after the DB was already flipped.

**Verification evidence:** Confirmed by direct reads. (1) bot/cogs/puzzles_gsheet.py:513 — `if self.get_archive_spreadsheet_id() and puzzle_data.solved:` gates the move-back of each additional sheet; lines 516-523 unconditionally queue update_cell/move_sheet_to_index/revert_tab_colour against `sheet.google_page_id` regardless of whether the move happened. (2) Only production caller: grep shows restore_puzzle_spreadsheet is called only from bot/core/puzzle_service.py:1012 (plus tests/mocks); the same unsolve method sets `puzzle.solved = False` at puzzle_service.py:1000 and commits at 1003 BEFORE the gsheet call at 1012, and the call is not wrapped in try/except — so the `puzzle_data.solved` branch can never fire. (3) Archive path: archive_puzzle_spreadsheet (puzzles_gsheet.py:571-574) moves every additional sheet to the archive whenever get_archive_spreadsheet_id() is set, rewriting `sheet.google_page_id` to the archive-spreadsheet id; solve() persists these via SheetsJsonDb.commit (puzzle_service.py:983-984). (4) Wrong-spreadsheet batch: restore's final `await self.batch_update(updates)` (puzzles_gsheet.py:536) uses the default archive=False, which targets the live spreadsheet (puzzles_gsheet.py:78-79). Google Sheets batchUpdate is atomic, so the invalid sheetId requests fail the whole batch, including the main tab's restore requests (lines 526-532), and execute_with_retry raises out of unsolve after the DB already shows Unsolved. Failure requires: archive spreadsheet configured + puzzle with at least one !addsheet extra sheet + solve then unsolve — a plausible real-hunt sequence, so severity high (sheet state permanently diverges from DB: extra sheets stranded in archive, main tab stays green/Solved).

### 3. [HIGH] `bot/cogs/puzzles_gsheet.py:1202`

**Status: FIXED** in `3ed8ae1` (`fix(gsheet): stop deleting sheet rows on contribution/unassigned prune (#3)`, branch `working-1`, merged to `staging` in `8442c15`).

reconcile_table prunes stale contribution rows with deleteDimension over whole sheet ROWS (bot/cogs/puzzles_gsheet.py:1202-1205), deleting the full-width grid row on the target's own tab; any re-key (feeds->informs re-edge, untag, capstone conversion flipping every feeder, evidence deletion) therefore shifts the solver work grid in cols E+ up one row per prune, silently destroying solver data on the very columns promote_tab_to_target reserves for solvers.

**Failure scenario:** Solvers work a meta in F5:Z30 beside the contribution table (rows 5-14). A feeder is untagged, re-edged feeds->informs (key changes from feeds:<pid> to informs:<pid>, i.e. prune+append), or a group is converted to Capstone (bulk_re_edge prunes EVERY feeder row). Each prune deletes the entire grid row, so the solvers' grid rows shift up and one row of their work is lost per prune — silent data loss on the tab the design explicitly reserves cols E+ for solvers (see promote_tab_to_target docstring and the SOCIAL_GREY shading that covers only the table's own columns).

**Verification evidence:** bot/cogs/puzzles_gsheet.py:1202-1205 — prune uses {"deleteDimension": {"range": {"sheetId", "dimension": "ROWS", startIndex, endIndex}}}, which the Sheets API applies across the entire sheet width (no column bound), unlike the column-bounded updateCells used for updates (1194-1200) and appends (1209-1215). bot/cogs/puzzles_gsheet.py:1349-1350 — promote_tab_to_target docstring: columns are inserted "so the solver grid rides clear to the right (cols E+)", i.e. solvers are expected to work in E+ of the same rows. 1252-1258 — SOCIAL_GREY shading is bounded to key_col..end_col only, signalling E+ as solver-safe. bot/core/contribution.py:41-54 — row keys embed the edge type (feeds:<id>, informs:<id>, none:<id>, informs:<id>:<evidence_id>), so an edge flip or evidence deletion re-keys the row and diff_rows yields a prune; capstone conversion flips every feeder feeds->informs, producing N simultaneous prunes. Callers render_contribution (1488-1493) and render_minimeta_contribution (1457-1462) both run this on the target's own tab. No mitigation found: no deleteRange with column bounds, no preservation/read-back of E+ cells, and this is not among the known-intentional quirks.

### 4. [HIGH] `bot/core/puzzle_service.py:1012`

**Status: FIXED** in `3c4042a` (`fix(gsheet): unsolve archive-spreadsheet desync (#2, #4)`, branch `working-1`, merged to `staging` in `8442c15`). Fixed together with #2 above (same code path — the review's own "minor correction" already noted the overlap).

unsolve (bot/core/puzzle_service.py:1003) commits the puzzle BEFORE restore_puzzle_spreadsheet (line 1012) and never commits after, so the restored google_page_id/archived=False (mutated in puzzles_gsheet.py:505-508, where the old archive tab is deleted) are lost for any caller without its own post-commit. The bang !unsolve path is accidentally rescued by cog_after_invoke (bot/cogs/puzzles.py:197-200), which commits the same ctx puzzle object — but the web unsolve endpoint (bot/web/actions.py:1691) has no commit at all, and !unsolve_mini commits the ctx minimeta, not the mutated mini. After a web unsolve of an archived puzzle, the DB keeps archived=1 plus a deleted archive sheetId; any later sheet write that re-reads the puzzle (e.g. re-solve → archive_puzzle_spreadsheet) targets a nonexistent sheetId and fails with a Sheets 400, desyncing the puzzle's sheet until manual repair. (Minor correction: additional_sheets page ids are not actually re-mutated during unsolve because the line-513 move is gated on puzzle_data.solved, already cleared at line 1000 — a separate latent bug.)

**Failure scenario:** Hunt has an archive spreadsheet; a solved puzzle's tab was moved there (new sheetId). !unsolve moves the tab back to the live spreadsheet under yet another new sheetId, but the DB row still holds archived=1 and the old (now-deleted) archive sheetId. Any later sheet write that re-fetches the puzzle (update_solution, rename, next solve/archive — all re-read via PuzzleJsonDb) targets a nonexistent sheetId in the archive spreadsheet and fails with a 400, permanently desyncing that puzzle's sheet. Contrast solve(), which commits at lines 982-984 AFTER archive_puzzle_spreadsheet mutates the same fields.

**Verification evidence:** bot/core/puzzle_service.py:998-1012 — unsolve clears solve state, commits at 1003, then calls restore_puzzle_spreadsheet at 1012 with no subsequent PuzzleJsonDb.commit/SheetsJsonDb.commit anywhere in the function (verified through line 1051). Contrast solve(): commit at 982-984 AFTER archive_puzzle_spreadsheet (line 979), which mutates the same fields (puzzles_gsheet.py:575-577). bot/cogs/puzzles_gsheet.py:505-508 — restore sets archived=False and reassigns google_page_id via move_puzzle_spreadsheet, which deletes the source archive tab (line 491), so the DB-stored sheetId becomes dangling. bot/store/mysqldb.py:130-143, 166-179 — get_by_attr constructs fresh objects from DB rows on every read; no shared-object cache, so uncommitted mutations are invisible to later reads. Refutation attempt on callers: bot/cogs/puzzles.py:197-200 cog_after_invoke commits self.get_puzzle(ctx) after every bang command, and !unsolve (lines 2785, 2798) passes that exact object — bang path persists correctly, so the original blanket claim is narrowed. bot/web/actions.py:1675-1698 — web unsolve fetches the puzzle, calls cog.service.unsolve, and grep confirms actions.py contains zero PuzzleJsonDb.commit calls; nothing persists the restore. unsolve_mini_by_number (puzzle_service.py:1481) also passes a mini that is not the ctx puzzle, so cog_after_invoke commits the wrong object. Failure path: re-solve re-reads puzzle (stale archived=1 + deleted sheetId) → archive_puzzle_spreadsheet batchUpdate at puzzles_gsheet.py:552-556 on nonexistent sheetId → Sheets 400; solve's docstring (puzzle_service.py:920) confirms it raises on Sheets failure. Severity high: the web dashboard is a first-class write path (project parity principle), the trigger is an ordinary unsolve on any hunt with an archive spreadsheet, and recovery requires manual DB/sheet repair.

### 5. [HIGH] `bot/core/puzzle_service.py:1073`

**Status: FIXED** in `26e8e3f` (`fix(puzzle_service): clear tag_edges on convert_to_puzzle demote (#5)`, branch `working-1`, merged to `staging` in `8442c15`).

convert_to_puzzle (bot/core/puzzle_service.py:1069-1075) removes the deleted group's id from puzzle.tags but never touches puzzle.tag_edges, so the meta's 'target' self-edge survives in memory; the immediately following PuzzleJsonDb.commit tag-sync (bot/store/mysqldb.py:571-576) computes mem_pairs - db_pairs = {(deleted_gid, 'target')} and INSERTs a tags row whose tag_group_id FK (ON DELETE CASCADE, migrations/000_base_schema.sql:118, repointed by 011_taggroup_rename.sql) references the just-deleted group — raising IntegrityError after the group deletion is committed and before demote_tab_to_puzzle runs, for both the bang and web demote paths.

**Failure scenario:** !convert_to_puzzle (or the web demote) on any meta: TagGroupJsonDb.delete removes the group and its tags rows, then PuzzleJsonDb.commit(puzzle) computes mem_pairs-db_pairs = {(dead_gid,'target')} and inserts it — the tags.RoundID FK (migrations/000_base_schema.sql:118, ON DELETE CASCADE) rejects it with IntegrityError, so the command errors AFTER the group is deleted and BEFORE demote_tab_to_puzzle runs (sheet stays in group layout, no board push). Tests mock PuzzleJsonDb so this is never exercised against real commit semantics.

**Verification evidence:** Confirmed by direct code reading. (1) Every meta carries a 'target' self-edge in tag_edges: set on convert_to_meta at bot/core/puzzle_service.py:1396-1397, on re-edge at :845-846, backfilled at :1661, and loaded from DB rows at bot/store/mysqldb.py:440. (2) convert_to_puzzle at bot/core/puzzle_service.py:1071-1073 calls TagGroupJsonDb.delete(target_group.id) then only `puzzle.tags.remove(target_group.id)` — it does NOT call puzzle.remove_edge (bot/store/puzzle_data.py:103-112 is the helper that keeps tags+tag_edges in sync), so tag_edges retains {deleted_gid: {'target'}}. (3) TagGroupJsonDb.delete removes the group row and its tags rows (bot/store/mysqldb.py:192-196). (4) PuzzleJsonDb.commit (bot/store/mysqldb.py:553-578) then builds mem_pairs from puzzle.tag_edges (:561-565); db_pairs no longer contains the pair, so :571-576 INSERTs (deleted_gid, puzzle.id, 'target') into tags. (5) tags.tag_group_id has an FK to tag_group(id) — original constraint RoundID at migrations/000_base_schema.sql:118, auto-repointed by migrations/011_taggroup_rename.sql (RENAME TABLE rounds→tag_group, CHANGE COLUMN round_id→tag_group_id at :7,:16) — so the INSERT hits errno 1452 IntegrityError. (6) Ordering: super().commit inside PuzzleJsonDb.commit issues mydb.commit() (bot/store/mysqldb.py:66) before the tag-sync, so the group deletion and puzzle-row update are durably committed, then the exception propagates before the sheet revert at bot/core/puzzle_service.py:1077-1079. Both entrypoints (bang !convert_to_puzzle and the web demote) converge on this one service method, so both fail. Refutation attempts failed: no other code clears tag_edges on this path, and the FK was not dropped by any later migration (checked migrations/005, 018, 019, 020). The only correction to the original claim: the finding's cited FK name/line is the pre-rename `RoundID` on column round_id; after migration 011 the live column is tag_group_id — cosmetic, the constraint still exists and cascades/rejects identically. Line anchor 1073 is accurate.

### 6. [HIGH] `bot/web/notifier.py:22`

**Status: FIXED** in `0da35ea` (`fix(web): publish the guild snowflake from board_changed, not the row id (#6)`, branch `working-1`, merged to `staging` in `8442c15`).

WebNotifier.board_changed (bot/web/notifier.py:22) publishes guild_id as hunt.guild_id, which is the guilds-table row id (hunts are created with guild_id=settings.id, the auto-inc row id, distinct from the snowflake), but ws_activity (bot/web/api.py:254) filters events against a membership set of Discord guild snowflakes — so every board event routed through the service's @pushes_board → CompositeNotifier → WebNotifier path is silently dropped for all WebSocket clients. This covers all bang-command mutations AND nearly all web mutations (actions.py now relies on the service push everywhere except the single remaining _notify_board call at actions.py:1494), so live board invalidation effectively never reaches the dashboard.

**Failure scenario:** Any bang command, or any web mutation that relies on the service's notifier push (e.g. add_note at actions.py:147, update_puzzle, solve), fires board_changed with a small row id; api.py:254 compares it against snowflakes and never matches, so no connected dashboard ever receives the board-invalidation event and the live board silently never refreshes. actions.py:59-61 explicitly documents that guild_id must be the int snowflake to pass this filter; hunts are created with guild_id=settings.id (puzzles.py:541) and data.py keys guilds_map by guilds.id, confirming hunt.guild_id is the row id, not the snowflake.

**Verification evidence:** bot/web/notifier.py:22 publishes {"guild_id": hunt.guild_id}. hunt.guild_id is the row id: bot/cogs/puzzles.py:542 creates hunts with guild_id=settings.id; bot/store/puzzle_settings.py:27-28 shows GuildSettings has separate guild_id (snowflake) and id (row id); bot/web/data.py:88-97 keys guilds_map by guilds.id and stores the snowflake separately as guild_snowflake; bot/web/api.py:82 and actions.py:100 look up guilds_map.get(hunt["guild_id"]), confirming hunts.guild_id references guilds.id. The filter: bot/web/api.py:254 `event.get("guild_id") in membership`, with membership built in bot/web/auth.py:150-154 as a set of discord guild.id snowflakes — a small row id can never match. In-repo confirmation of the requirement: bot/web/actions.py:60-63 _board_event docstring states guild_id must be the int snowflake to pass the ws_activity membership filter and converts the snowflake; but only actions.py:1494 still uses _notify_board — other web mutations (add_note actions.py:145, solve actions.py:344, etc.) rely on the service's @pushes_board decorator (bot/core/puzzle_service.py:84 → CompositeNotifier bot/core/notifier.py:245-247 → WebNotifier registered at bot/web/__init__.py:110), which carries the row id. tests/test_web_notifier.py only asserts publish is called, not that the id matches membership, so the mismatch is untested. No translation layer exists between events.publish and the ws filter. Impact is broader than the original claim (web mutations affected too, not just bang commands).

### 7. [HIGH] `frontend/src/components/AdminSettingsPanel.tsx:75`

**Status: FIXED** in `1740bab` (`fix(web): hunt-start timezone picker + stop wiping in-progress edits (#7, #8)`, branch `working-1`, merged to `staging` in `8442c15`). Fixed together with #8 below (the fixes interact — both touch how `start_local` is derived/synced). Chose a UTC-offset picker (not full IANA timezones) per user decision.

frontend/src/components/AdminSettingsPanel.tsx round-trips the hunt start time through mismatched timezones: line 75 renders the default with toISOString().slice(0,16) (UTC wall time) into a datetime-local input, while handleSave (line 107) parses values.start_local with new Date(), which ECMAScript interprets as LOCAL time for the offset-less "YYYY-MM-DDTHH:mm" form. For any user not in UTC, the displayed start time is wrong by their offset, and every Save that touches any field silently PATCHes start_timestamp shifted by -offset (e.g. -3600 for UTC+1), compounding on repeated saves.

**Failure scenario:** A user in UTC+1 changes only the shared password and presses Save: values.start_local equals the untouched UTC-rendered default, handleSave parses it as local time, gets ts = start_timestamp - 3600 != start_timestamp, and silently includes the shifted start_timestamp in the PATCH — every save moves the hunt start one hour earlier (compounding on repeated saves), and the displayed start time is wrong by the offset to begin with.

**Verification evidence:** frontend/src/components/AdminSettingsPanel.tsx:73-76 — startLocalDefault = new Date(start_timestamp*1000).toISOString().slice(0,16): a UTC wall-clock string fed to a datetime-local input (line 208-214), which browsers treat as local time, so the display is off by the user's UTC offset. frontend/src/components/AdminSettingsPanel.tsx:106-110 — handleSave does ts = Math.round(new Date(values.start_local).getTime()/1000); per ES spec, date-time strings without a timezone offset are parsed as LOCAL time, so for an untouched field ts = start_timestamp - utcOffsetSeconds. Since ts !== start_timestamp whenever the offset is nonzero, patch.start_timestamp is included on EVERY save even when the user only edited e.g. the password (the claimed scenario is exact). The backend applies it unconditionally: bot/web/actions.py:775 cog.service.update_hunt_settings(hunt_obj, **body) with start_timestamp in _SETTINGS_KEYS (line 747). Compounding: after save the refetched hunt yields a new startLocalDefault and the sync block at lines 88-97 overwrites values.start_local, so the next save shifts by the offset again. No mitigating code found (no getTimezoneOffset usage anywhere in the file; no other timezone handling in frontend/src/api.ts for this field). Only users whose local timezone is exactly UTC are unaffected, so this is wrong behaviour a real user will plausibly hit — high severity confirmed.

### 8. [HIGH] `frontend/src/components/AdminSettingsPanel.tsx:88`

**Status: FIXED** in `1740bab` (`fix(web): hunt-start timezone picker + stop wiping in-progress edits (#7, #8)`, branch `working-1`, merged to `staging` in `8442c15`). Fixed together with #7 above.

The render-phase sync in frontend/src/components/AdminSettingsPanel.tsx:88 compares local form state to incoming props (values.url !== url || values.start_local !== startLocalDefault) instead of comparing previous props to current props, so every keystroke in Website URL or Start time triggers setValues() back to the server values on the very next render — those two fields are uneditable from the web and Save can never change url/start_timestamp. Whenever the condition does fire (any user edit to those fields, or a server-side url/start change arriving via useHunt's refetch), it also resets username, password, url_sep, and puzzle_prefix, silently discarding any in-progress edits to those fields.

**Failure scenario:** Admin opens Manage > Hunt Settings and types a new character into Website URL: onChange sets values.url, the next render sees values.url !== url and immediately setValues() back to the server values, discarding the keystroke — the field visually snaps back and the URL/start time can never be changed from the web (Save always sends the old values). It also wipes any in-progress username/password edits whenever the condition fires after a background refetch (useHunt refetches every 30s and on every board WebSocket push).

**Verification evidence:** frontend/src/components/AdminSettingsPanel.tsx:88-97 — the sync block runs unconditionally during render and compares editable state to props: `if (values.url !== url || values.start_local !== startLocalDefault) { setValues({ url, start_local: startLocalDefault, username, ... }) }`. The URL input at lines 201-206 is controlled (`value={values.url}`, onChange sets values.url), and Start time at 208-214 likewise. When the user types, onChange sets values.url; on the resulting render values.url !== url is true, so setValues immediately restores the prop values — React's setState-during-render pattern re-renders with the reset state before committing, so the controlled input snaps back and the keystroke is lost. This is the misapplied "derived state" pattern: the correct form stores previous props in state and compares prop-to-prevProp, not state-to-prop. No memoization or alternate path mitigates it: props come straight from live query data (frontend/src/pages/HuntBoard.tsx:151-163, url={data.hunt.url}, start_timestamp={data.hunt.start_timestamp}), and there is no key remount or other sync mechanism. Consequently handleSave (lines 102-113) can never see a changed url/start_local, so those settings are unchangeable from the web UI. The reset object (lines 89-96) also overwrites username/password/url_sep/puzzle_prefix with props whenever it fires, so a server-side url or start change (e.g. from a bang command, arriving via refetch) mid-edit wipes those in-progress edits — this secondary wipe requires the server url/start to actually change, slightly narrower than the reviewer's "whenever a refetch fires", but the primary uneditable-fields defect is exactly as claimed. One caveat: startLocalDefault (lines 73-76) is derived via toISOString (UTC), so even an experienced user pasting a "correct" value can't stabilise it; nothing rescues the flow.

### 9. [MEDIUM] `bot/cogs/puzzles.py:464`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

check_available_space (bot/cogs/puzzles.py:458-491) dereferences ctx.channel.category (lines 464, 474, 489) and the looked-up bot channel (lines 474, 480, 484, 488) without None checks, and it measures the CURRENT channel's category fullness even for !hunt and !round, which create their own new categories — so a full bot-channel/hunt-channel category yields a false "not enough space" refusal.

**Failure scenario:** (a) `!hunt name:url` from a bot channel that sits outside any category: line 464 `ctx.channel.category.channels` raises AttributeError -> hunt creation dies with a generic error. (b) If settings.discord_bot_channel is unset/renamed, bot_channel is None and any warning branch (line 474/480) crashes. (c) If the bot channel's category has >45 channels, `!hunt` is refused with 'not enough space' even though the new hunt gets its own fresh category — a lying error.

### 10. [MEDIUM] `bot/cogs/puzzles.py:598`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

!puzzle (bot/cogs/puzzles.py:595-598) dereferences hunt.id without a None guard; in any untracked channel cog_before_invoke leaves hunt=None (channel_type 'Guild'), so `!p name` raises AttributeError and the user gets the generic "Something went wrong" error instead of a "run this in a hunt channel" message like !rebuild gives.

**Failure scenario:** `!p my-puzzle` in any untracked channel (channel_type 'Guild', hunt None): PuzzleJsonDb.check_duplicates_in_hunt(puzzle_name, hunt.id) raises AttributeError -> generic 'Something went wrong' instead of a 'run this in a hunt channel' message.

### 11. [MEDIUM] `bot/cogs/puzzles.py:999`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

!list_tags crashes on an untagged puzzle: tag_info stays "" and embed.add_field(name="Current tags", value="") makes Discord reject the embed with HTTP 400, so the organiser gets an error instead of the listing. The dangling-tag-id half of the claim is only latent: line 999 would indeed raise AttributeError on None.name, but TagGroupJsonDb.delete cleans the tags rows for the deleted group and !add_tag's append never persists, so a dangling id requires abnormal state (manual DB edit or crash mid-delete).

**Failure scenario:** A puzzle carries a tag id whose group was deleted (the scenario !list_tags exists to debug): TagGroupJsonDb.get_by_attr(id=tag) returns None and `.name` raises AttributeError. Separately, on an untagged puzzle tag_info stays '' and embed.add_field(value='') makes Discord reject the embed with HTTP 400 — either way the organiser gets 'Something went wrong' instead of the listing.

### 12. [MEDIUM] `bot/cogs/puzzles.py:1135`

**Status: FIXED** in `eff54d5` (`fix(puzzles): close pin/parity staleness gaps
(#12, #15, #70)`, branch `working-1`). Both bang commands now call
`self.info(ctx, update=True)`; both web endpoints now call
`cog.web_refresh_info`.

!remove_minimeta and !make_minimeta never refresh the pinned info message (no self.info(ctx, update=True)), unlike !add_mini/!solve_mini/!unsolve_mini; the web endpoints (make-minimeta / remove-minimeta in bot/web/actions.py) have the same gap, never calling cog.web_refresh_info. After remove_minimeta the pinned embed keeps showing the mini list with links to deleted threads and Type "Minimeta" until some other pin-refreshing command runs.

**Failure scenario:** After `!remove_minimeta`, the pinned info embed (which renders the mini list via create_mini_list at line 2146) still shows the numbered list of now-deleted mini-puzzles with links to dead threads until some other command happens to refresh it; !make_minimeta likewise leaves the pin without the new minimeta state.

### 13. [MEDIUM] `bot/cogs/puzzles.py:1262`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

!clue (bot/cogs/puzzles.py:1251) and !clues (:1268) never check that they are running in a puzzle channel: get_puzzle returns None outside one, and service.log_clues then dereferences the None puzzle (first at puzzle.tags in _sole_informs_target, bot/core/puzzle_service.py:1884, then puzzle.id at :1902), raising AttributeError that surfaces as the generic "Something went wrong" apology instead of the send_not_puzzle_channel guidance every comparable command gives.

**Failure scenario:** User runs `!clue every answer is an element` in the hunt general channel (a natural place to note a hunt-wide observation): puzzle is None, service.log_clues does puzzle.id (bot/core/puzzle_service.py:1900-1903) -> AttributeError -> generic 'Something went wrong' instead of the send_not_puzzle_channel guidance every comparable command gives.

### 14. [MEDIUM] `bot/cogs/puzzles.py:1280`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

!clues parses its input with shlex.split (bot/cogs/puzzles.py:1280) with no try/except, so any unbalanced quote — including a plain apostrophe like DON'T — raises ValueError('No closing quotation'), which surfaces as the generic ':exclamation: Something went wrong' message; no clue is logged and the user gets no hint that the quote/apostrophe was the cause.

**Failure scenario:** `!clues DON'T PANIC "SECOND CLUE"`: shlex.split raises ValueError('No closing quotation'), which is unhandled, so the user gets ':exclamation: Something went wrong' and no clue is logged; nothing tells them the apostrophe was the problem.

### 15. [MEDIUM] `bot/cogs/puzzles.py:1331`

**Status: FIXED** in `eff54d5` (`fix(puzzles): close pin/parity staleness gaps
(#12, #15, #70)`, branch `working-1`). `!sync` now also walks every category
recorded for the group in `TagGroupCategoryDb` (solved/overflow), not just the
primary category.

!sync (alias !sync_round) iterates only the group's primary category (ctx.channel.category.channels, bot/cogs/puzzles.py:1331) and never consults TagGroupCategoryDb/category_layout.group_categories, so in a split round any untagged puzzles that were moved into the "— solved" or overflow categories are silently skipped while the command replies ":white_check_mark: Synced N puzzle(s)" with no warning.

**Failure scenario:** In a split round where several solved puzzles were moved to the solved category, `!sync` (alias !sync_round, documented as tagging the round's puzzles) walks only meta_category.channels, never consults TagGroupCategoryDb for the solved/overflow categories, and replies ':white_check_mark: Synced N puzzle(s)' — the solved feeders stay untagged and absent from the meta roll-up with no warning.

### 16. [MEDIUM] `bot/cogs/puzzles.py:1366`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

The tagging commands (feeds/feeds_only/informs/informs_only/groups_with/groups_only_with/remove_from) guard only `channel_type == 'Hunt'` (bot/cogs/puzzles.py:1366, 1392, 1418, 1447, 1472, 1497, 1522), not a missing puzzle: in a group main channel whose group has no target puzzle they crash with AttributeError on None (user sees the generic "Something went wrong" apology), and in an untracked 'Guild' channel they reply ":x: This hunt has no groups to assign to yet" — a misleading message, since the real problem is running the command in the wrong channel.

**Failure scenario:** Run `!feeds AB12CD` in a Group-type main channel whose group has no target puzzle: cog_before_invoke leaves puzzle=None, the guard passes, and service.tag_with_edge(None, ...) raises AttributeError on puzzle.id -> user gets the generic 'Something went wrong' message. In an untracked ('Guild') channel, hunt is None so resolve_target_group replies ':x: This hunt has no groups to assign to yet' — a lie, since the real problem is the wrong channel.

### 17. [MEDIUM] `bot/cogs/puzzles.py:1399`

**Status: FIXED** in `52a5e94` (`fix(puzzles): ack tag mutations after they succeed, not before (#17, #74)`, branch `working-1`).

feeds_only (bot/cogs/puzzles.py:1399), remove_from (:1428), informs_only (:1479) and groups_only_with (:1529) send the ':white_check_mark:' success message BEFORE awaiting the service mutation, so if untag_from raises ValueError('cannot remove the last tag from a metapuzzle') or tag_edge_only/tag_with_edge raises SheetRefreshError (or channel.edit raises a Discord error), the user gets a success ack for a mutation that never completed — unlike feeds (:1373-1376), informs (:1454-1457) and groups_with (:1504-1507), which ack only after the mutation succeeds.

**Failure scenario:** `!remove_from AB12CD` on a puzzle that is the target of ANOTHER group and holds only this tag: the success message 'This puzzle has been removed from ...' is sent at line 1428, then service.untag_from raises ValueError('cannot remove the last tag from a metapuzzle') — user sees success followed by a generic error, and the puzzle was never removed. Same lying-ack pattern if tag_edge_only's sheet refresh raises SheetRefreshError in feeds_only/informs_only/groups_only_with.

### 18. [MEDIUM] `bot/cogs/puzzles.py:1624`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

!round unpacks _round_impl's result at bot/cogs/puzzles.py:1624 (`tag_group, puzzle = await self._round_impl(...)`), but _round_impl returns bare None on every handled RuntimeError (round_exists, puzzle_name_clash, no_category_space/category_exists) at line 1599, so the unpack raises TypeError; the cog's on_command_error fallback (lines 438-441) then logs it as an unhandled error and sends a spurious ':exclamation: Something went wrong running that command' to the user right after the intended friendly error message.

**Failure scenario:** User runs `!round the-bog` when a round of that name exists: _round_impl sends the ':exclamation: already exists' message and returns None (line 1599), then `tag_group, puzzle = None` at line 1624 raises 'cannot unpack non-iterable NoneType', producing a command error/traceback on every duplicate-name attempt.

### 19. [MEDIUM] `bot/cogs/puzzles.py:1988`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

!list (list_puzzles) dereferences self.get_tag_group(ctx).id at bot/cogs/puzzles.py:1988 without a None check; in a Puzzle channel whose category has no TagGroup row — concretely, a split-layout group's solved or overflow category, which is tracked only in the TagGroupCategoryDb join table that cog_before_invoke's fallback (line 180) does not consult — the environment tag_group is None and the command raises AttributeError, so the user gets the generic ":exclamation: Something went wrong" apology (with a server-side traceback) instead of the puzzle list or a targeted message.

**Failure scenario:** User runs !list in an unassigned feeder's channel or in a puzzle channel sitting in the hunt's solved category (no TagGroup row for that category): get_tag_group returns None and line 1988 raises AttributeError, a traceback instead of a list or a friendly message.

### 20. [MEDIUM] `bot/cogs/puzzles.py:2084`

The update=True pin-refresh path assumes the bot's info message is always the last element of await channel.pins(): send_initial_hunt/metapuzzle/group_channel_messages do channel_pins[-1].edit() with no empty-pins guard and no check the message is the bot's own, so if a mod unpins/deletes the bot's info pin the next bang command that refreshes (e.g. !solve/!status/!info in a meta channel) raises IndexError or discord.Forbidden after the DB mutation has committed — in _solve_impl this loses the stale-sheet warning and the refresh_unassigned task. The web path is guarded (web_refresh_info never raises) but the ~20 bang call sites are not; only the puzzle variant guards empty pins, and there it silently skips the refresh instead of re-sending. With >50 pins, discord.py 2.7.1 pins() returns only the 50 newest, so [-1] is not the oldest pin and the wrong message is edited.

**Failure scenario:** A mod unpins (or deletes) the bot's pinned info message in a meta channel; the next !solve/!status/!info there either raises IndexError (no pins) or tries to edit whatever message is now oldest-pinned — a user's message, giving 403 Forbidden — so the command tracebacks after the DB mutation (in _solve_impl the sheet warning and refresh_unassigned task are lost). Also asymmetric: only the puzzle variant (line 2285) guards empty pins, and there it silently drops the refresh instead of re-sending; lines 2200 and 2234 have the same unguarded pattern. With >50 pins, awaiting pins() returns only the newest 50, so [-1] is not the oldest pin at all.

### 21. [MEDIUM] `bot/cogs/puzzles.py:2767`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

mark_as_complete (bot/cogs/puzzles.py:2767) calls _solve_impl directly without the not-a-puzzle-channel and already-solved guards that solve/partial have: in a non-puzzle channel it passes puzzle=None into service.solve, which tracebacks with AttributeError at puzzle_service.py:929 instead of the friendly ':x: not a puzzle channel' reply; in an already-solved channel it re-runs the full solve path, appending '/✅' to the recorded solution (puzzle_service.py:933-934), stamping a new solve_time and committing, where !s correctly refuses with ':x: already solved'.

**Failure scenario:** Running !complete in the hunt general channel: get_puzzle(ctx) is None (cog_before_invoke sets no puzzle), so _solve_impl passes puzzle=None/hunt=None into service.solve → AttributeError traceback instead of the friendly ':x: not a puzzle channel'. Running !complete in an already-solved channel re-runs the full solve path (re-moves the channel, overwrites the recorded solution with ✅) instead of the ':x: already solved' refusal that !s gives.

### 22. [MEDIUM] `bot/cogs/puzzles.py:2889`

**Status: FIXED** in `40b079c` (`fix(archive,evidence): commit archive_time and
fix evidence timestamp tz (#22, #33)`, branch `working-1`). `archive_channel`
now commits the puzzle after setting `archive_time`, using the same tz-aware
UTC stamp as the service path.

archive_channel (bot/cogs/puzzles.py:2889) sets archive_time (naive local time, unlike the UTC-aware service path at bot/core/puzzle_service.py:980) on a freshly-fetched PuzzleData instance that is never committed — the function ends by deleting the channel (line 2946) with no PuzzleJsonDb.commit, and cog_after_invoke (lines 197-207) only commits the environment's puzzle, a different instance resolved from ctx.channel. The DB row keeps archive_time=NULL and a dangling channel_id, so consumers like get_solved_puzzles_to_archive (bot/store/mysqldb.py:377) never see these puzzles as archived.

**Failure scenario:** !archive_round archives a round: for each puzzle, PuzzleJsonDb.get_by_attr(channel_id=...) returns a fresh object, archive_time is set at line 2889, then the object is discarded — cog_after_invoke only commits the environment's puzzle. The DB row keeps archive_time=NULL and a dangling channel_id to a deleted channel, so anything keying off puzzles.archive_time (e.g. mysqldb.py:377, dashboard backfill checks) never sees these puzzles as archived.

### 23. [MEDIUM] `bot/cogs/puzzles.py:2961`

**Status: FIXED** in `5747523` (`fix(puzzles): add missing channel/None guards (review tranche 1, #9-23)`, branch `working-1`).

archive_solved_manually is doubly broken: bot/cogs/puzzles.py:2961 reads settings.hunt_settings[hunt_id], but GuildSettings (bot/store/puzzle_settings.py:26-35, and the MySQL loader MySQLGuildSettingsDb.get at bot/store/mysqldb.py:750-759 which builds it via GuildSettings.import_dict) has no hunt_settings attribute, so the command raises AttributeError immediately; and even if fixed, line 2973 calls .flatten() on channel.history(), which was removed in discord.py 2.0 (Pipfile.lock pins discord.py 2.7.1), so it would AttributeError again. The advertised admin command can never succeed.

**Failure scenario:** An admin runs !archive_solved_manually: line 2961 `settings.hunt_settings[hunt_id]` raises AttributeError immediately (and even if that were fixed, `await channel.history(limit=10).flatten()` raises AttributeError in discord.py 2.7.1). The command can never succeed — dead-but-advertised admin functionality.

### 24. [MEDIUM] `bot/cogs/puzzles_gsheet.py:90`

**Status: FIXED** in `8319a31`, `494a91f`, `f8fcfe9`, `fdb4cfb` (async conversion of `sheet_list`
and its helpers, plus a 7th method — `get_puzzle_sheet_index` — sharing the same defect; every
call site now awaits them via `execute_with_retry`), branch `working-1`. Originally deferred out
of tranche 4 (2026-07-02) alongside #25/#26/#47/#48 at the user's request: the real fix requires
converting 6 sync helper methods (`sheet_list`, `get_unique_tab_name`, `get_page_id_by_name`,
`get_page_name_by_id`, `move_sheet_to_end`, `compute_insert_index`) from sync to async, rippling
to ~30 call sites across `bot/cogs/puzzles_gsheet.py` and `bot/core/puzzle_service.py`, plus two
test files (`tests/test_sheet_list_guard.py`, `tests/test_sheet_ordering.py`) that call them
synchronously today. A couple of call sites (`move_sheet_to_end` used inline inside a
request-list literal) can't just get an `await` — the surrounding code needs restructuring.

sheet_list (bot/cogs/puzzles_gsheet.py:90) calls the blocking execute_sync (network call plus time.sleep backoff up to 60s, serialized behind the module-wide _execute_lock) directly on the bot event loop: it is invoked with no thread offload from async methods reconcile_table (line 1137) and _ensure_contribution_table (line 1422), and via sync helpers get_unique_tab_name (106, plus a second bare execute_sync at 109) called from async add_new_sheet (391), compute_insert_index (363), move_sheet_to_end (341), and get_page_name_by_id (133). Any Sheets 429/5xx during these paths sleeps on the loop, stalling every command, web request, and the gateway heartbeat; even without retries the loop can block on _execute_lock while a worker thread (execute_with_retry) holds it.

**Failure scenario:** During a solve burst Sheets returns 429/500 on the metadata GET inside a rollup render; execute_sync's time.sleep backoff (up to _MAX_DELAY=60s, bot/utils/gsheets.py:118) runs ON the event loop, so every Discord command, web request and the gateway heartbeat stall; a long backoff disconnects the bot mid-hunt. Even without a retry, execute_sync blocks the loop waiting on _execute_lock while a worker thread holds it for its own Sheets call.

### 25. [MEDIUM] `bot/cogs/puzzles_gsheet.py:282`

**Status: FIXED** in `be0ae05` (`fix(gsheet): quote-escape hyperlinks, fix rename target, dead fallback, off-by-one (#25,26,47,48)`, branch `working-1`).

The =HYPERLINK formula is built by naive string interpolation of the puzzle name at bot/cogs/puzzles_gsheet.py:282 (update_puzzle_info) and :1289 (_puzzle_tab_header_requests), with no doubling of embedded double quotes. A puzzle named e.g. The "Best" Puzzle yields =HYPERLINK("url","The "Best" Puzzle"), an unparseable formula; the Sheets API stores it and the puzzle tab's name cell renders #ERROR! instead of the name/hyperlink (rather than reliably failing the whole batchUpdate as originally claimed). Both the create path (generate_puzzle_tab) and every rename/update (rename_puzzle → update_puzzle) hit this; single-quote doubling exists for A1 tab-name references (safe_name) but no equivalent for formula string args.

**Failure scenario:** Create or rename a puzzle titled e.g. The "Best" Puzzle: the cell gets =HYPERLINK("url","The "Best" Puzzle") — Sheets rejects the unparseable formulaValue, failing the whole header batchUpdate, so the new tab is created blank (no header/hyperlink) or the rename batch 400s after the DB commit. Quote-doubling escaping is applied to tab names elsewhere (safe_name) but not to formula arguments.

### 26. [MEDIUM] `bot/cogs/puzzles_gsheet.py:286`

**Status: FIXED** in `be0ae05` (`fix(gsheet): quote-escape hyperlinks, fix rename target, dead fallback, off-by-one (#25,26,47,48)`, branch `working-1`).

update_puzzle_info's rename path (bot/cogs/puzzles_gsheet.py:286) computes tab-name uniqueness against the LIVE spreadsheet (get_unique_tab_name with no spreadsheet arg defaults via sheet_list to get_spreadsheet_id) but applies the rename to the ARCHIVE spreadsheet when puzzle.archived (batch_update(updates, puzzle.archived) at line 291), so renaming an archived puzzle checks collisions in the wrong spreadsheet.

**Failure scenario:** Rename a solved/archived puzzle (rename_puzzle, puzzle_service.py:1432, works on archived puzzles) to a name that already exists as a tab in the archive spreadsheet but not the live one: get_unique_tab_name returns the bare name, the archive batchUpdate fails with 'duplicate sheet name' (non-retryable 400) after the DB rename was committed — sheet tab and DB name permanently diverge. Conversely, a collision that exists only in the live sheet needlessly renames the archive tab to 'Name (2)'.

### 27. [MEDIUM] `bot/core/puzzle_service.py:802`

bulk_re_edge (bot/core/puzzle_service.py:802) flips member edge rows via raw SQL while holding no per-member puzzle locks (bang !convert_to_capstone holds no lock at all, web holds only the target's lock), so any concurrently in-flight command on a member puzzle that loaded it pre-flip will, at its next PuzzleJsonDb.commit, have the bidirectional tag-sync (bot/store/mysqldb.py:566-576) silently revert that member's edge back to the stale in-memory type.

**Failure scenario:** !convert_to_capstone on group G (flips member P feeds→informs in DB) races with an in-flight !solve of P (P's mutex held, P loaded with 'feeds' before the flip): solve's commit at line 982 computes db_pairs={(G,'informs')} vs mem_pairs={(G,'feeds')}, DELETEs the informs row and re-INSERTs feeds — the type conversion is undone for P with no error anywhere.

### 28. [MEDIUM] `bot/core/puzzle_service.py:979`

**Status: FIXED** in `5435deb` (`fix(service): isolate own-sheet failures in solve/change/add (#28)`, branch `working-1`, merged to `staging` in `8442c15`). Folded in after the HIGH findings landed, at the user's request.

In solve(), a plain Sheets failure (HttpError) from archive_puzzle_spreadsheet at bot/core/puzzle_service.py:979 propagates after the solve is already committed (line 939): @pushes_board treats any non-SheetRefreshError as "mutation not applied", so no board_changed WebSocket push fires on either the bang or web path and dashboard clients keep showing the puzzle unsolved; the second commit (line 982: archive_time, moved additional-sheet page ids) and forum-tag update are skipped. On the bang path _solve_impl (bot/cogs/puzzles.py:2630) aborts before the solved embed/channel move/pin update and on_command_error (puzzles.py:431) tells the user their change "may not have been saved" even though the DB says solved; the web path (bot/web/actions.py:353-355) does message correctly but still misses the board push. Same post-commit-raise pattern in change_solution/add_solution (lines 1229/1247), where it additionally swallows the pending SheetRefreshError meta badging.

**Failure scenario:** Puzzle committed as Solved at line 939, metas refreshed, then a Sheets 500 in archive_puzzle_spreadsheet raises: dashboard WebSocket clients never get the board_changed event (stale unsolved board), the user is told the solve failed, and the final commit at 982 plus forum-tag update are skipped. Same pattern in change_solution/add_solution where update_solution raises after commit (lines 1229/1247).

### 29. [MEDIUM] `bot/core/puzzle_service.py:1149`

delete_round (bot/core/puzzle_service.py:1143) deletes every puzzle in the group (PuzzleJsonDb.delete at line 1158) without ever calling refresh_targets, unlike delete_puzzle which collects each deleted puzzle's tag groups and refreshes them (lines 1135-1137); neither the bang caller (bot/cogs/puzzles.py:3054) nor the web caller (bot/web/actions.py:1326) compensates, so any OTHER group's meta fed by a deleted puzzle keeps a stale contribution row on its sheet until some unrelated mutation next refreshes that target.

**Failure scenario:** Puzzle P feeds meta M1 and is also tagged into round R2; !delete_round R2 (bang or web, neither edge refreshes) deletes P from the DB, but M1's contribution table still shows P's row until some unrelated event next re-renders M1 — the meta sheet displays a feeder that no longer exists.

**Status: PARTIALLY MITIGATED** as of `ad26791` (`feat(delete-round): add keep_puzzles scope (default: keep member puzzles)`, branch `working-2`, merged to `staging` in `9c3a428`). `delete_round` gained a `keep_puzzles` scope; the new web default (`keep_puzzles=True`) no longer deletes feeder puzzles at all — it keeps their DB rows and relocates their Discord channels instead — so the exact failure scenario above can no longer happen via the default dashboard delete. The underlying bug is otherwise untouched: `!delete_round` is pinned to `keep_puzzles=False` (`5195931`, to preserve its historical "delete everything" behavior) and the web dashboard still offers an explicit "Round + all puzzles" option that also runs with `keep_puzzles=False` — both paths still delete every member puzzle with no `refresh_targets` call, so the original failure scenario remains fully reachable through either of them.

### 30. [MEDIUM] `bot/core/puzzle_service.py:1158`

delete_round (bot/core/puzzle_service.py:1149-1163) deletes every member puzzle with no is_target guard, so when a round contains a nested group's target puzzle (e.g. a meta created via convert_to_meta that still feeds the parent round), that target puzzle's DB row and channel are deleted while the nested group survives with a dangling target_puzzle_id; delete_puzzle explicitly forbids exactly this case (line 1127), and neither the bang command nor the web endpoint adds a guard.

**Failure scenario:** Round R contains meta M's target puzzle P (P feeds R, metameta nesting). !delete_round R deletes P's row and channel; group M survives with target_puzzle_id pointing at a deleted puzzle — refresh_target silently no-ops (metapuzzle None), M's category/channels leak, and M can never be solved or demoted normally.

**Status: PARTIALLY MITIGATED** as of `ad26791` (`feat(delete-round): add keep_puzzles scope (default: keep member puzzles)`, branch `working-2`, merged to `staging` in `9c3a428`). Under the new default (`keep_puzzles=True`), `delete_round` only ever deletes the round's OWN target puzzle (matched by `tag_group.target_puzzle_id`, if set) — a feeder that happens to also be some OTHER nested meta's target, as in the failure scenario above, is now kept rather than deleted, so that scenario can't happen via the default dashboard delete. `keep_puzzles=False` (still used by `!delete_round`, `5195931`, and the web's explicit "Round + all puzzles" option) retains the original no-guard full delete of every member puzzle, so the failure scenario remains fully reachable through either of those paths.

### 31. [MEDIUM] `bot/core/puzzle_service.py:1265`

**Status: FIXED** in `ad90908` (`fix(puzzles): stop status/backsolved from being
set outside solve/unsolve (#31)`, branch `working-1`). Implemented the other way
round from the original finding's framing (which suggested running solve
machinery when status is manually set to "Solved"): `update_puzzle_field` now
refuses to set or clear "solved"/"backsolved" at all — those labels are owned
by `solve()`/`unsolve()` — except toggling between the two on an
already-solved puzzle. The dashboard's status picker (`PuzzleEditCells.tsx`)
mirrors this: it only offers Solved/Backsolved once solved, and only the
non-solved statuses beforehand.

update_puzzle_field validates status against STATUSES, which includes "solved" and "backsolved" (bot/core/constants.py:13-14), and then only does setattr + commit (bot/core/puzzle_service.py:1272-1273) — none of the solve machinery in service.solve (puzzle_service.py:917-991: solved=True, solve_time, meta/group rollup refresh, sheet archive) runs. Both entrypoints expose "Solved"/"Backsolved" as pickable values (bang !status choices at bot/cogs/puzzles.py:2403-2410 → service call at 2425; web status dropdown STATUS_OPTIONS at frontend/src/components/PuzzleEditCells.tsx:25-26 → PATCH handler at bot/web/actions.py:209), so a user can label a puzzle Solved while puzzle.solved stays False: the dashboard hide-solved filter (frontend/src/components/PuzzleTable.tsx:191, keyed on p.solved) still shows it, meta sheet rollups and the group solved-derivation (puzzle_service.py:955-963) never fire, and the sheet is never archived.

**Failure scenario:** !status solved (puzzles.py:2425) or web PATCH status='solved' (actions.py:209): puzzle.status becomes 'solved' but solved=False, no solve_time, no meta rollup refresh, no sheet archive; the dashboard's hide-solved filter (keyed on p.solved) still shows it, metas never receive its answer, and the group solved-derivation never fires.

### 32. [MEDIUM] `bot/store/mysqldb.py:192`

MySQLTagGroupJsonDb.delete (bot/store/mysqldb.py:192) removes the tag_group row and its tags but never removes tag_group_category join rows, and tag_group_category has no FK (migrations/014_tag_group_category.sql), so nothing cascades. delete_hunt (bot/core/puzzle_service.py:2188) and convert_to_puzzle (:1071) call it without TagGroupCategoryDb.delete_for_group — unlike delete_round, which routes through category_layout.delete_group_categories. Result: !delete_hunt on a hunt with split (unsolved/solved/overflow) rounds tears down only tag_group.category_id, leaving the extra solved/overflow Discord categories standing and stale tag_group_category rows forever; convert_to_puzzle likewise leaks stale rows. Minor correction: stale rows cause get_by_category to resolve to a deleted group id, which _tag_group_for_channel (bot/cogs/puzzles.py:886-887) degrades to None rather than misrouting to a wrong live group (barring auto-increment id reuse) — the tangible cost is the undeleted Discord categories plus permanent orphan rows.

**Failure scenario:** Delete a hunt (or demote a metapuzzle) whose group had grown into split unsolved/solved/overflow categories: delete_hunt tears down only tag_group.category_id, leaving the extra solved/overflow Discord categories standing, and the stale tag_group_category rows persist forever — get_by_category (mysqldb.py:217) then resolves those still-live category ids to a deleted group id, misrouting later layout/lookup logic (tag_group_category has no FK to tag_group, so nothing cascades).

### 33. [MEDIUM] `bot/store/mysqldb.py:625`

**Status: FIXED** in `40b079c` (`fix(archive,evidence): commit archive_time and
fix evidence timestamp tz (#22, #33)`, branch `working-1`).
`EvidenceData.import_dict` and `MySQLEvidenceDb.log()` now both reattach
`tzinfo=UTC`, matching `PuzzleData.import_dict`'s pattern for every other
timestamp field. The frontend display bug was a separate fix on top — `f25ba82`
(`fix(frontend): render evidence timestamps in local time, not raw UTC (#33)`)
— since EvidencePanel sliced the raw string instead of parsing it, so a UTC
offset alone wouldn't have changed what was displayed.

Evidence created_at is written as naive datetime.utcnow() (bot/store/mysqldb.py:625) and EvidenceData.import_dict (bot/store/evidence_data.py:16-21) copies DB rows without re-attaching tzinfo=UTC (unlike PuzzleData.import_dict, puzzle_data.py:72), so bot/web/data.py:556 serialises evidence created_at via raw isoformat() with no +00:00 offset while every other web timestamp goes through _iso() (data.py:78-82). The frontend EvidencePanel (frontend/src/components/EvidencePanel.tsx:159) renders the string as-is via slice, so non-UTC users see evidence 'logged at' in bare UTC while other dashboard times are local (e.g. HuntInfoPanel toLocaleString) or correctly offset-parsed (util.ts relativeTime) — shifted by their UTC offset. Any future comparison of EvidenceData.created_at against the codebase's standard aware datetimes raises TypeError.

**Failure scenario:** bot/web/data.py:556 serialises i.created_at.isoformat() with no offset; the EvidencePanel renders it as-is, so a user in any non-UTC timezone sees evidence 'logged at' times shifted by their UTC offset relative to every other dashboard timestamp (activity, solve times, which are UTC-annotated); any future code comparing an EvidenceData.created_at to the codebase's standard aware datetimes raises TypeError (naive vs aware).

### 34. [MEDIUM] `bot/web/actions.py:125`

**Status: FIXED** in `bbe2777` (`fix(web): archived-hunt write guards +
malformed create_hunt input (#34, #35, #37)`, branch `working-1`). Both
endpoints now return `hunt_archived` (403) when the hunt is archived, matching
every sibling mutation.

add_note (bot/web/actions.py:124) and erase_note (bot/web/actions.py:153) are the only puzzle-mutating web endpoints missing the ctx["hunt"]["archived"] guard (delete_hunt's omission at 1261 is deliberate and commented), so POST /api/puzzles/<id>/notes and DELETE .../notes/<index> succeed on an archived hunt's puzzle — mutating the frozen DB record and emitting a board push — despite api.py declaring archived hunts read-only. (Correction: the service methods only commit notes to the DB; no archive sheet is touched.)

**Failure scenario:** hunt_detail (api.py:98-99) states 'Archived hunts are read-only even for organisers' and returns can_manage=false, and every sibling mutation (update_puzzle:185, add_sheet:233, solve:312, etc.) returns 403 hunt_archived — but POST /api/puzzles/<id>/notes and DELETE .../notes/<index> on an archived hunt's puzzle succeed, mutating the frozen record and (via service.add_note) touching its archive sheet.

### 35. [MEDIUM] `bot/web/actions.py:712`

**Status: FIXED** in `bbe2777` (`fix(web): archived-hunt write guards +
malformed create_hunt input (#34, #35, #37)`, branch `working-1`).
`start_timestamp` is now validated (numeric, in-range for
`datetime.fromtimestamp`) before any Discord/Sheets resources are created.

create_hunt (bot/web/actions.py:712) passes the unvalidated JSON value body["start_timestamp"] straight to service.create_hunt, where datetime.fromtimestamp (bot/core/puzzle_service.py:2149) runs only after the Discord role/category/channels and Google spreadsheet are created but before HuntJsonDb.commit (2161); a string or null value raises TypeError, which the route's except clauses (actions.py:716-721) do not catch, yielding a 500 with orphaned Discord/Sheets resources and no hunts row.

**Failure scenario:** An admin client sends start_timestamp as a string (e.g. "1720000000" from a form field) or null; the required-keys check at actions.py:678 only verifies presence, service.create_hunt creates the team role, category, #general channel, solved divider and the Google sheet, then `datetime.datetime.fromtimestamp(start_timestamp, tz=pytz.UTC)` (puzzle_service.py ~2149) raises TypeError → 500 with all Discord/Sheets artifacts created but no hunts row, requiring manual cleanup (reconcile can't see a hunt that doesn't exist).

### 36. [MEDIUM] `bot/web/api.py:40`

**Status: FIXED** in `1e3fe6b` (`fix(web): close multi-team visibility/permission
gaps (#36, #38, #86)`, branch `working-1`). `list_guilds` now filters to guilds
the user is actually a member of before building the response.

GET /api/guilds (bot/web/api.py:31-51) contradicts its own docstring ("Return the guilds the current user belongs to"): the loop over guilds_map at line 39 never checks membership (resolve_membership is imported but not called here), so every guild row — name, snowflake, and per-guild admin flag — is returned to any logged-in session unless ?admin=1. Combined with OAuth login being open to any Discord account (bot/web/auth.py uses only the "identify" scope with no membership gate in login/callback), a complete outsider can enumerate all teams' guild names and snowflakes. Hunt data itself stays protected (list_hunts and _load_visible_hunt apply hunt_visible), so this is an information-disclosure gap, not a hunt-visibility bypass.

**Failure scenario:** Any person with a Discord account completes /api/auth/login (no guild-membership requirement anywhere in auth.py) and calls GET /api/guilds: the loop appends every guilds_map entry unless ?admin=1, disclosing all teams' guild names and snowflakes to complete outsiders — a visibility bypass in the multi-team model where even hunts are role-gated per guild.

### 37. [MEDIUM] `bot/web/api.py:213`

**Status: FIXED** in `bbe2777` (`fix(web): archived-hunt write guards +
malformed create_hunt input (#34, #35, #37)`, branch `working-1`). Now guards
on `hunt["archived"]` and validates every element of `notes` is a string
before writing any of them (rejecting the whole request instead of a partial
write).

POST /api/evidence (bot/web/api.py:196-221) lacks the hunt-archived guard that every other web mutation enforces, so any guild member can write evidence rows (and fire a sheet refresh) into a read-only archived hunt; and the per-note write loop at line 213 calls n.strip() on each raw JSON element, so a non-string note (e.g. notes=["ok", 42]) raises AttributeError mid-loop after earlier notes are already committed, returning a 500 with a partial write and no refresh_evidence task. Mitigating: the SPA never calls this POST (clue creation goes through /actions/puzzles/<id>/clues, which is guarded), so it is only reachable by direct authenticated API calls.

**Failure scenario:** 1) Any team member POSTs evidence for a puzzle in an archived hunt: _load_visible_hunt passes (archived hunts are visible to all guild members) and there is no `hunt["archived"]` check, so rows are written and a sheet refresh task fired against a read-only hunt. 2) A request with notes=["ok", 42] logs "ok" via EvidenceJsonDb.log, then `n.strip()` raises AttributeError on 42 → 500 returned while the first note is already persisted and no refresh_evidence task is scheduled.

### 38. [MEDIUM] `bot/web/api.py:254`

**Status: FIXED** in `1e3fe6b` (`fix(web): close multi-team visibility/permission
gaps (#36, #38, #86)`, branch `working-1`). "activity" events are now checked
against the live Discord channel permission overwrites for the requesting
member; "board" events are checked against `hunt_visible`.

/ws/activity (bot/web/api.py:254) filters events only by guild membership, not the per-hunt team-role visibility model that every REST endpoint enforces via hunt_visible, so guild members without a hunt's team role still receive live activity events (channel_id, author display name, timestamp) for that hidden hunt's channels and board events carrying the hidden hunt's uid.

**Failure scenario:** A guild has two teams; a member of team A (lacking team B's hunt role, so hunt_visible=False for B's hunts) opens the dashboard WebSocket. Every human message in team B's puzzle channels publishes {channel_id, author display name, timestamp, guild_id=snowflake} (bot/cogs/activity.py:60), which passes the guild-membership-only filter at api.py:254 and streams to the team-A user — leaking who is active on which hidden puzzle and when. Web-action board events (actions.py:69) similarly leak hidden hunt uids.

### 39. [MEDIUM] `frontend/src/components/MiniPuzzlePanel.tsx:58`

MiniPuzzlePanel fires solvePuzzle.mutate / createMini.mutate with no per-call onError and clears the typed input synchronously (solve: lines 58-59 and 71-72; create-mini: lines 91-92 and 102-103), and neither the hooks nor the QueryClient show an error notification, so a failed mini solve or mini creation silently discards the user's input with no visible error.

**Failure scenario:** User types a mini answer and presses Enter while the hunt was just archived or Discord returns 429: the input is cleared on the spot, the mutation fails, no toast or message appears, and the mini still shows unsolved with the typed answer lost — the user cannot tell whether the solve landed; the same pattern at line 91/102 loses new mini names.

### 40. [MEDIUM] `frontend/src/components/PuzzleTable.tsx:113`

The web "No self-feed" off-switch for a target's deliberate self-feed is doubly broken: (1) frontend/src/components/PuzzleTable.tsx:113 disables the menu item whenever r.edges includes "target", and since enabling a self-feed (AddToGroupPicker "feeds itself" → mode=add, edge=feeds → tag_with_edge) never drops the target self-edge, edges become {target, feeds} and the off-switch is permanently greyed out; (2) even where clickable (legacy self-feeders that migration 019 skipped, whose edges are {feeds} without "target"), the resulting mode=add edge=target call reaches puzzle_service.tag_with_edge (bot/core/puzzle_service.py:691-698), whose exclusivity handling only covers feeds/informs vs none — it adds the "target" row without dropping feeds/informs, so the self-feed persists in rollups and the menu item then greys out. No other web path can remove it: DragBoard hides "Remove" for a group's own target (DragBoard.tsx:111) and the API rejects mode=remove for the own meta (bot/web/actions.py:906-907).

**Failure scenario:** Organiser uses Arrange > 'Add to group… (feeds itself)' to make a meta self-feed (edges become {target, feeds}); later they open All puzzles > Debug tags to undo it, but 'No self-feed' is greyed out (edges include 'target'). Even for legacy pre-migration-019 self-feeding targets where the item is clickable, the backend tag_with_edge (bot/core/puzzle_service.py:691-698) only enforces feeds/informs<->none exclusivity and does not drop feeds/informs when adding 'target', so the retag is a no-op and the meta keeps feeding itself in rollups.

### 41. [MEDIUM] `frontend/src/components/PuzzleTable.tsx:306`

Multiple mutation call sites fire with no onError handler and the QueryClient (frontend/src/main.tsx:13) defines no MutationCache/global mutation error handler, so failed writes give zero user feedback: debug-edge retags (PuzzleTable.tsx:306, 314), status/priority/type updates (PuzzleTable.tsx:347/361/375, BoardGroup.tsx:270, PuzzleDrawer.tsx:163/169/175), link/sheet/erase-note mutations (PuzzleDrawer.tsx:200-213, 272, 369), and evidence edits (EvidencePanel.tsx:123-129). This is an omission, not a design choice — sibling components (DragBoard, ReconcilePanel, PuzzleEditCells, CreateControls) consistently pass notifyError per call.

**Failure scenario:** User clicks a new status while Discord is rate-limiting channel edits (or after the CSRF cookie expired): the PATCH returns 4xx/5xx, React Query records the error, but nothing is rendered or toasted — the badge simply stays at the old value and the user assumes the click did not register or that it saved; main.tsx's QueryClient (line 13) defines no mutation defaults, so the errors are swallowed everywhere a per-call onError is missing.

### 42. [MEDIUM] `migrations/009_evidence.sql:2`

evidence_items has no foreign keys (migrations/009_evidence.sql lines 3-6: hunt_id, source_puzzle_id, target_puzzle_id are bare indexed columns) and no delete path cleans it: delete_puzzle (bot/core/puzzle_service.py:1118) and delete_hunt (puzzle_service.py:2164-2237) never touch evidence_items, and MySQLPuzzleJsonDb.delete (bot/store/mysqldb.py:580) removes only tags rows. Orphaned evidence stays on the hunt board forever; one correction — dangling puzzles render as the "Puzzle {id}" fallback, not a blank name, because resolve_puzzle_names (puzzle_service.py:95-104) fills every id, so data.py:552's "" default never fires.

**Failure scenario:** Delete a puzzle that has evidence logged: its items stay on the hunt's evidence board with a dangling source_puzzle_id, rendered with a blank source name (bot/web/data.py:552 names.get(...) returns "") and un-navigable; delete a hunt and its evidence rows are orphaned permanently, and if hunts.id is ever reused (auto-increment recalculated from MAX(id) after a server restart on older MariaDB) the old hunt's evidence resurfaces on the new hunt.

### 43. [LOW] `bot/cogs/puzzles.py:642`

!rebuild's targeted-mode resolution (bot/cogs/puzzles.py:642) uses only TagGroupJsonDb.get_by_attr(category_id=...) — the primary-category lookup — instead of the overflow/solved-aware _tag_group_for_channel helper (line 871), so when run in a channel sitting in a split round's solved/overflow category the target stays None and the command silently degrades to rebuilding every meta sheet in the hunt, contradicting its docstring ("In a round/meta channel rebuilds just that round's meta").

**Failure scenario:** Organiser runs `!rebuild` in a puzzle channel that lives in a round's solved/overflow category: TagGroupJsonDb.get_by_attr(category_id=...) returns None (the join table at category_layout.group_id_for_category is never consulted, unlike line 886), target stays None, and the command rebuilds ALL meta sheets — a mass Sheets-quota hit contradicting the docstring's 'rebuilds just that round's meta'.

### 44. [LOW] `bot/cogs/puzzles.py:1022`

!add_tag (bot/cogs/puzzles.py:1021-1035) is the only tag-mutating bang command without @with_puzzle_mutex (its twin !remove_tag at 1038-1050 has it), and it appends any integer — nonexistent group id or duplicate — straight into puzzle.tags, which cog_after_invoke commits; a dangling id then crashes !list_tags at line 999 (get_by_attr returns None → AttributeError on .name) and would poison other tag walks (e.g. refresh_targets at 2564, puzzle_service.py:1135).

**Failure scenario:** `!add_tag 9999` (a typo'd id, or the same id twice) appends an id with no matching group / a duplicate to puzzle.tags, which cog_after_invoke commits; the puzzle then trips the dangling-id crash in !list_tags (line 999), and because there is no puzzle mutex the write can race a concurrent tagging command on the same puzzle.

### 45. [LOW] `bot/cogs/puzzles.py:3036`

Round/hunt teardown runs without any per-puzzle locking on BOTH edges: !delete_round (bot/cogs/puzzles.py:3036) and !delete_hunt (:3096) have no @with_puzzle_mutex, and the web endpoints (bot/web/actions.py:1326 delete_round, :1280 delete_hunt) likewise call the service without puzzle_lock — while the ctx-free service deliberately never locks (bot/core/locks.py:9-11). So a concurrent !solve or dashboard solve on a member puzzle (which does hold its per-puzzle lock) can interleave with the deletion loop, writing to a sheet/DB row mid-teardown.

**Failure scenario:** While !delete_round is awaiting its 30s confirmation and then deleting channels/sheets, a teammate's !solve (or a dashboard solve, which uses the same per-puzzle locks) on a member puzzle proceeds in parallel — writing to a sheet/DB row mid-deletion, yielding Sheets errors or a re-committed row for a deleted puzzle.

### 46. [LOW] `bot/cogs/puzzles.py:3155`

start_reminders (bot/cogs/puzzles.py:3155) calls self.reminder_loop.start(channel) unconditionally; discord.py tasks.Loop.start raises RuntimeError if the loop is already launched, so a second !start_reminders (same or different hunt) fails — the error is caught by the cog's on_command_error generic branch, which logs it and replies "Something went wrong", not a crash but a confusing failure with no way to point the single shared loop at a second hunt's channel (one Loop per cog instance, bound to whichever channel started it first). The reset of self.reminder_index at line 3154 also happens before the failing start, perturbing the running loop's message cadence.

**Failure scenario:** An organiser runs !start_reminders twice (or organisers of two concurrent hunts each run it): the second call raises an unhandled RuntimeError instead of a message, and the second hunt can never get its own reminder loop since the cog holds exactly one tasks.loop bound to the first channel.

### 47. [LOW] `bot/cogs/puzzles_gsheet.py:295`

**Status: FIXED** in `be0ae05` (`fix(gsheet): quote-escape hyperlinks, fix rename target, dead fallback, off-by-one (#25,26,47,48)`, branch `working-1`).

update_cell's sheet_id-is-None fallback (bot/cogs/puzzles_gsheet.py:295) calls self.get_puzzle_data(), which is defined nowhere in the repo, so if that branch is ever reached it raises AttributeError instead of a useful error. Reachability nuance: the common "unset" value for google_page_id is the dataclass default "" (empty string), which is falsy but not None, so it bypasses the fallback and instead sends sheetId "" to the Sheets API; only a literal None page_id (e.g. NULL from the DB) reaches the broken fallback. Either way the fallback is dead/broken code that can never do what it pretends to.

**Failure scenario:** Any puzzle whose google_page_id is None/unset (e.g. tab creation previously failed) reaches update_puzzle_info -> update_cell with new_sheet_id=None; instead of a clear error the command dies with AttributeError: 'GoogleSheets' object has no attribute 'get_puzzle_data', masking the real problem (missing sheet).

### 48. [LOW] `bot/cogs/puzzles_gsheet.py:342`

**Status: FIXED** in `be0ae05` (`fix(gsheet): quote-escape hyperlinks, fix rename target, dead fallback, off-by-one (#25,26,47,48)`, branch `working-1`).

move_sheet_to_end (bot/cogs/puzzles_gsheet.py:342) sets index = len(sheets) - 1, but the Sheets API evaluates updateSheetProperties index moves in before-the-move positions, so a truly-final move needs index = len(sheets); solved tabs therefore land second-to-last, leaving whatever tab is currently last stranded to the right of all solved tabs when no archive spreadsheet is configured (and for target puzzles).

**Failure scenario:** On solve without an archive spreadsheet, archive_puzzle_spreadsheet 'moves the tab to the end' but it actually lands second-to-last: whatever tab happens to be last (e.g. the trailing hidden 'Tab template' or the most recently appended tab) is permanently stranded to the right of every solved tab, so solved-tab ordering drifts from the intended layout.

### 49. [LOW] `bot/cogs/puzzles_gsheet.py:878`

add_metapuzzle_data (bot/cogs/puzzles_gsheet.py:855) and add_metametapuzzle_data (:868) are legacy A5-anchored aggregate_to_tab renderers with zero production callers — the live rollup path is PuzzleService._render_rollup → gsheet.render_contribution → reconcile_table — and add_metametapuzzle_data dereferences TagGroupJsonDb.get_by_attr(id=...).name (:878-879) without a None check, so a member whose sole tag points at a deleted group would raise AttributeError if the function were ever re-wired.

**Failure scenario:** Dead-code drift: the live rollup path is render_contribution/reconcile_table, but these A5-anchored aggregate_to_tab writers remain callable and would clobber a target tab's hidden key column A if ever re-wired; and a member whose sole tag references a deleted group makes `round.name` raise AttributeError. Cost is maintenance confusion (tests like test_refresh_meta exist mainly to assert they are NOT called) plus a latent crash if resurrected.

### 50. [LOW] `bot/core/category_layout.py:145`

_allocate (bot/core/category_layout.py:145) computes next_seq from role_rows read before layout_lock was acquired and never re-reads under the lock (unlike split_group's re-check at line 108), so two concurrent allocations for the same group/role compute the same seq; however, the second task's dao.commit does NOT insert a duplicate join row — the UNIQUE KEY uq_group_category (group_id, category_id) in migrations/014_tag_group_category.sql:12 rejects the plain INSERT (bot/store/mysqldb.py:55-63) with an IntegrityError, so the second puzzle create/solve-move fails with an unhandled DB error instead of corrupting the join table. Real but narrow transient failure; no dangling-row data corruption.

**Failure scenario:** Two puzzles created near-simultaneously in a round whose unsolved categories are all at the soft limit: both tasks pass the room check, task B enters the lock after A, reuses A's '(2)' category via the name match, and dao.commit inserts a second join row for the same category_id/seq; cleanup_empty_overflow later deletes the category but only one row (line 232), leaving a dangling join row pointing at a deleted category.

### 51. [LOW] `bot/core/puzzle_service.py:338`

_reconcile_channel's relink step (bot/core/puzzle_service.py:338) searches guild.channels by name with no hunt or category scoping, so when a puzzle's stored channel is gone, !reconcile apply can adopt a same-named channel belonging to another hunt (or another puzzle) in the same guild: it moves that channel into the puzzle's home category (line 344) and rewrites the puzzle's channel_id/name/mention to it (lines 345-348), hijacking the other hunt's live channel while the other hunt's puzzle record still points at the now-relocated channel.

**Failure scenario:** Two hunts each have a puzzle named 'meta'; hunt A's channel is deleted out-of-band, then !reconcile --apply on hunt A finds hunt B's #meta by name, rewrites A's puzzle.channel_id to B's channel and MOVES that channel into A's category (line 344) — hunt B loses its live puzzle channel.

### 52. [LOW] `bot/core/puzzle_service.py:806`

bulk_re_edge's target re-pin (bot/core/puzzle_service.py:805-807) cannot enforce what its comment (lines 802-804) claims: under migration 020's per-edge unique key (tag_group_id, puzzle_id, edge_type), tag_puzzle_with_edge(target, 'target') is an insert-if-absent that never removes a contributing edge. If the target carries a feeds/informs/none self-edge to its own group (reachable via the deliberately-supported web self-feed loop, bang !feeds run in the meta's own channel, or legacy un-migrated 'none' self-edges), bulk_update_edge_type flips that row to new_edge and the re-pin no-ops (or, in the legacy-'none' case, adds a 'target' row alongside), leaving the target with new_edge + 'target' — i.e. feeding/informing itself after a type conversion, which select_rollup_members' round-mode filter (lines 623-628) then surfaces as the meta contributing to its own table. The comment is also wrong that "bulk_update flips every row including the target's" (bulk_update_edge_type only touches feeds/informs/none rows), and tests/test_edge_primitives.py:308-320 asserts the invariant only against a mocked store, so it never exercises the real SQL.

**Failure scenario:** If the target puzzle carries a feeds/informs row to its own group (added via tag_with_edge/retag, or legacy data), bulk_update_edge_type flips it to new_edge and the re-pin adds 'target' alongside it rather than replacing it; select_rollup_members' contributing filter (line 624-627) then includes the target as its own feeder and the meta's contribution table lists the meta itself as a member.

### 53. [LOW] `bot/core/puzzle_service.py:1668`

In PuzzleService.create_puzzle, forum_channel is assigned only inside the `round_data is not None` forum branch (bot/core/puzzle_service.py:1608), but the channel-creation step branches on hunt.layout_mode alone (line 1667), so creating an unassigned puzzle (round_data=None via !p from the hunt channel, or web create with empty group_code) in a forum-layout hunt raises UnboundLocalError at line 1668; the except block then deletes the just-created sheet tab and DB row and re-raises, so the round-less create always fails in forum mode (bang path surfaces an unhandled command error, web path a 500).

**Failure scenario:** Creating an unassigned puzzle (!p from the hunt channel, or the web unassigned-create) in a forum-layout hunt: line 1668 raises UnboundLocalError('forum_channel'), the except block deletes the just-created sheet tab and DB row, and the create always fails — the feeders-first workflow is impossible in forum mode.

### 54. [LOW] `bot/core/puzzle_service.py:2164`

create_hunt (bot/core/puzzle_service.py:2086) and delete_hunt (:2164) are the only service mutations without @pushes_board, and no caller on either path (bang: puzzles.py:535/:3111; web: actions.py:708/:1280) publishes a board event — so no WebSocket invalidation fires on hunt create/delete. Impact is smaller than claimed: the hunt board self-heals within 30s via useHunt's refetchInterval (frontend/src/api.ts:123), and the hunt list would stay stale even WITH a push, because useActivityFeed only invalidates the ["hunt", uid] query, never ["hunts"].

**Failure scenario:** !delete_hunt from Discord (puzzles.py:3111): every dashboard client watching that hunt keeps rendering the deleted board — no board_changed/WebSocket invalidation ever fires, and subsequent API refetches 404 only after a manual reload; a bang !hunt creation similarly never appears on open hunt lists until reload.

### 55. [LOW] `bot/store/mysqldb.py:580`

MySQLPuzzleJsonDb.delete (bot/store/mysqldb.py:580-584) deletes the puzzles row and its tags rows but never touches additional_sheets, and the additional_sheets table (migrations/000_base_schema.sql:121-130) has no FK/cascade to puzzles — so every puzzle/round/hunt delete (puzzle_service.delete_puzzle:1136, delete_round:1158, delete_hunt:2180) orphans that puzzle's additional_sheets rows in the DB forever. The DB-corruption follow-on (ghost sheets attaching to a new puzzle via construct_puzzle's puzzle_id eager-load) is real code-wise but requires puzzles.id reuse, which only happens on auto-increment recalculation (MySQL<8/MariaDB<10.2.4 restart) — unlikely on a modern MariaDB, so the practical cost is an unbounded but small row leak.

**Failure scenario:** Delete a round or hunt containing puzzles with additional sheets: the additional_sheets rows persist forever (unbounded leak); because construct_puzzle eager-loads additional_sheets by puzzle_id on every read, any future reuse of a puzzles.id (auto-increment recalculated after restart on older MariaDB/MySQL<8) attaches the dead puzzle's ghost sheets to a brand-new puzzle, corrupting its solve/sheet handling.

### 56. [LOW] `bot/store/puzzle_data.py:85`

PuzzleData.sort_by_puzzle_start (bot/store/puzzle_data.py:85) sorts on start_time with no None guard while puzzles.start_time is nullable (migrations/000_base_schema.sql:76); a single NULL start_time row (legacy data or manual insert — both live code paths do stamp it) makes sorted() raise TypeError inside PuzzleDataMySQL.get_all (mysqldb.py:331) and get_all_from_tag_group (mysqldb.py:358), crashing every listing that touches that hunt/group instead of tolerating the one bad row.

**Failure scenario:** A single puzzle row with NULL start_time (legacy data, manual insert, or a future code path that commits before stamping start_time) makes sorted() compare None to a datetime and raise TypeError inside get_all_from_tag_group / get_all — every round listing, rollup refresh and delete_round that touches that group then crashes wholesale instead of skipping the one bad row.

### 57. [LOW] `bot/web/actions.py:806`

post_hunt_archive (bot/web/actions.py:806) passes a dict ({"archived": bool(archived)}) as the `detail` SQL parameter to _audit; the pure-Python mysql-connector (2.2.9, HAVE_CEXT=False) cannot convert a dict, so the INSERT into web_audit_log raises, _audit swallows the exception (logger.exception), and hunt archive/unarchive actions are never recorded in the audit log. The archive itself still succeeds; only the audit row is silently lost.

**Failure scenario:** Every archive/unarchive via the web calls `_audit("hunt_archive", ..., detail={"archived": bool(archived)})`; cursor.execute at actions.py:50-54 raises ProgrammingError ('Failed processing pyformat-parameters') on the dict, _audit swallows it (logging an exception), and web_audit_log permanently lacks any record of who archived which hunt — every other action passes detail as a string.

### 58. [LOW] `bot/web/actions.py:1757`

PATCH /api/evidence/<id> (update_evidence, bot/web/actions.py:1701-1757) mutates the evidence note and/or target puzzle with no _audit call, unlike every other mutating endpoint in the file — including DELETE on the same resource, which logs "delete_evidence" at line 1791. (Minor correction to the original claim: there is no web "add evidence" endpoint to compare against — evidence creation is Discord-side; the audited sibling is DELETE only.)

**Failure scenario:** A user re-targets or rewrites an evidence note via the dashboard; web_audit_log records nothing, so when an organiser later investigates a wrong informs-edge or altered clue text there is no attribution trail, while the matching delete and add paths are audited.

### 59. [LOW] `bot/web/auth.py:150`

resolve_membership (bot/web/auth.py:146-156) issues a failing Discord REST fetch_member call for every guild the user is not a member of, on every authenticated request that resolves membership, with no negative caching — and the dashboard polls hunt_detail every 30s, so this is sustained traffic. Minor correction: the two user_can_manage calls in hunt_detail (api.py:99-100) do NOT normally add REST round-trips, because a user who passed hunt_visible is a member of that hunt's guild, so resolve_role_names hits the get_member cache.

**Failure scenario:** With several guilds in the bot and a user who belongs to one, every /api/hunts, hunt_detail, and each write action re-runs resolve_membership → one failing fetch_member REST round-trip per non-member guild per request on the bot's event loop; a dashboard left open polling adds sustained Discord REST traffic and latency, and hunt_detail additionally calls user_can_manage twice (api.py:99-100), each doing another fetch_member.

### 60. [LOW] `frontend/src/components/DragBoard.tsx:692`

Drag-retag errors in DragBoard are surfaced only via the shared useRetagPuzzle hook's retag.isError (frontend/src/components/DragBoard.tsx:692), which React Query resets on every new mutate call; with overlapping retags an earlier failure is cleared/masked by a later call, so the failed card silently snaps back after refetch — pendingIds correctly tracks multiple in-flight cards but errors were not given the same per-call treatment.

**Failure scenario:** User drags puzzle A (fails with discord_rate_limited) then immediately drags puzzle B (succeeds): the second mutate resets the hook's error state, the red message under the board never appears (or vanishes instantly), and card A silently snaps back to its old column after the invalidated refetch with no explanation.

### 61. [LOW] `migrations/000_base_schema.sql:87`

puzzles.url is varchar(150) while hunts.url is varchar(250) (migrations/000_base_schema.sql:87 vs :51), and create_puzzle composes the puzzle URL as hunt.url + "/" + prefix + "/" + slug (bot/core/puzzle_service.py:1642) with no length validation or truncation anywhere, so a hunt URL of ~130+ chars (legal for its column) yields a composed puzzle URL over 150 chars; under default strict sql_mode the generic INSERT (bot/store/mysqldb.py:63) fails with a data-too-long error — aborting puzzle creation after the Google sheet was already created at line 1650 and before the try/cleanup block at 1665, stranding an orphan sheet (not an orphan Discord channel, which is created only after the commit) — or silently truncates the stored link on lenient servers.

**Failure scenario:** Create a hunt whose URL is ~130+ characters (well within its 250-char column) and add a puzzle with a normal-length name: the composed puzzle URL exceeds 150 chars, so the generic commit() INSERT fails with a data-too-long error under default strict sql_mode (aborting puzzle creation after the Discord channel already exists), or silently truncates the stored puzzle link on lenient servers.


## 2. Bang ↔ web parity (Discord messages & info pins) (7 findings)

### 62. [HIGH] `bot/web/actions.py:361`

**Status: FIXED** in `25154f0` (`fix(web): post embed + refresh pin for partial/change/add solves (#62)`, branch `working-1`, merged to `staging` in `8442c15`).

Web solve modes 'partial', 'change' and 'add' perform the service mutation but skip both the puzzle-channel announcement embed and the pinned-info refresh that the bang equivalents always do: bot/web/actions.py:359-362 gates web_post_solved_embed (which itself ends with web_refresh_info) behind mode == "full" only, so after a dashboard 'change' the Discord pin keeps displaying the OLD solution and no update is announced in the channel.

**Failure scenario:** A user fixes a typo via the dashboard (mode 'change'): bang !change_solution posts a 'PUZZLE SOLUTION UPDATED!' embed and edits the pin (puzzles.py:2718-2721), but the web path posts nothing and the pinned info keeps showing the OLD solution, so solvers in the Discord channel act on a wrong answer; dashboard partials are similarly invisible in the channel.

**Verification evidence:** Web path: bot/web/actions.py:342-362 — service.add_partial/change_solution/add_solution are called for the non-full modes (lines 345-350), but the Discord-side refresh block at lines 359-362 is 'if mode == "full":' only ('Post solved embed + refresh minimeta pin only for full solves.'). web_post_solved_embed (bot/cogs/puzzles.py:810-839) posts the solved message AND calls web_refresh_info at line 839, so non-full modes get neither. Bang parity: !change_solution posts a 'PUZZLE SOLUTION UPDATED!' embed and refreshes the pin via self.info(ctx, update=True) (bot/cogs/puzzles.py:2707-2715); !add_solution does the same (2751-2755); !partial/_partial_impl posts a 'Partially SOLVED!' embed and edits the pin via send_initial_puzzle_channel_messages(update=True) (2592-2600). The pinned info embed genuinely displays the solution: _add_puzzle_status_fields adds a 'Solution' field from puzzle.solution (bot/cogs/puzzles.py:2157-2158), so after a web 'change' the pin keeps showing the old, wrong answer until some unrelated refresh. This violates the project's stated web/bang parity principle and is a user-visible wrong answer in the channel, so high severity stands (DB/sheet data are correct; only the Discord display is stale/missing).

### 63. [MEDIUM] `bot/cogs/puzzles.py:1969`

**Status: FIXED** in `17afadf` (`fix(web,notifier): close web↔Discord pin/board staleness gaps (#63,65,66,67,68,85)`, branch `working-1`). Added a pin refresh + board push after the commit; did NOT add a web equivalent for guild/round settings (that's a feature gap, out of scope for this fix).

!update_settings (bot/cogs/puzzles.py:1967-1971) commits directly to the store (save_settings → *JsonDb.commit, or PuzzleJsonDb.commit for the update_puzzle_settings alias), bypassing puzzle_service — so no @pushes_board event and no pinned-info refresh — and the web has no equivalent for guild/round (TagGroup) settings or hunt keys outside the 6-key whitelist in bot/web/actions.py:747. Mitigating context: the common puzzle fields (status/priority/puzzle_type) do have proper dual paths (!type/!status/!priority → service.update_puzzle_field with @pushes_board, and web PATCH /puzzles/<id> with web_refresh_info), so the staleness only bites when an admin uses the escape-hatch command instead.

**Failure scenario:** Admin runs `!update_puzzle_settings puzzle_type crossword`: the pinned info's Type field and every connected dashboard stay stale (no board push) until an unrelated mutation fires; guild/round settings can only ever be changed from Discord, violating the project's bang↔web parity principle.

### 64. [MEDIUM] `bot/cogs/puzzles.py:2811`

The archive family of bang commands — !archive_round (bot/cogs/puzzles.py:2811), !archive_category (2836), !archive_channel (2864), !archive_solved_manually (2951) — has no web/dashboard equivalent: bot/web/actions.py exposes only a whole-hunt archived-flag toggle (line 783) and archive-sheet creation (line 810), and no round/category/channel-level archive exists in bot/core/puzzle_service.py or the frontend API, so an organiser must switch to Discord to archive a finished round mid-hunt.

**Failure scenario:** Mid-hunt an organiser wants to archive a finished round to reclaim the guild's channel budget; the dashboard offers nothing round-level, forcing a context switch to Discord — a real parity gap against the project's stated every-bang-has-a-web-equivalent principle (unlike sync/move_to_*/add_tag, which are Discord-category-native or debug-only and reasonably bang-only).

### 65. [MEDIUM] `bot/core/notifier.py:136`

**Status: FIXED** in `17afadf` (`fix(web,notifier): close web↔Discord pin/board staleness gaps (#63,65,66,67,68,85)`, branch `working-1`). Extended `DiscordNotifier.refresh_info` to handle a hunt-level (puzzle=None) pin refresh, wired into !set_login, !set_start, and the web PATCH /hunts/<uid>/settings endpoint.

No path — bang !set_login (bot/cogs/puzzles.py:1900), !set_start (1919), !update_settings (1927), or web PATCH /hunts/<uid>/settings (bot/web/actions.py:776) — refreshes the hunt channel's pinned info message after hunt settings change, and DiscordNotifier.refresh_info structurally cannot do so (bot/core/notifier.py:136 early-returns when puzzle is None and its handler dispatch at 157-161 only covers metapuzzle/puzzle channels), even though username, password, and start_timestamp render in that pin (puzzles.py:2051-2064). A user running !info gets a fresh non-pinned duplicate (update defaults False at puzzles.py:2307; users cannot pass kwargs to a discord.py command), so the stale pinned message persists.

**Failure scenario:** An organiser corrects the shared hunt password from the dashboard (or via !set_login); every solver keeps reading the old password/start time from the pinned hunt-channel message until someone manually runs !info in that channel, and !info without update posts a duplicate rather than editing the pin.

### 66. [MEDIUM] `bot/core/puzzle_service.py:1892`

**Status: FIXED** in `17afadf` (`fix(web,notifier): close web↔Discord pin/board staleness gaps (#63,65,66,67,68,85)`, branch `working-1`). Converted log_clues to async + @pushes_board(); also made frontend/src/pages/HuntBoard.tsx's WS handler invalidate `["evidence", uid]` alongside `["hunt", uid]`.

log_clues (bot/core/puzzle_service.py:1892) carries no @pushes_board and none of its three entrypoints (!clue at bot/cogs/puzzles.py:1262, !clues at 1284, web action at bot/web/actions.py:444) publishes any board event; even if one did, the WS handler (frontend/src/pages/HuntBoard.tsx:37) only invalidates ["hunt", uid], never ["evidence", uid], and useEvidence (frontend/src/api.ts:514-519) has no refetchInterval — so clues logged by others never live-update an open Evidence panel; only the logger's own web mutation invalidates ["evidence", uid] (api.ts:170) for themselves. React-query's default refetch-on-window-focus means a viewer will pick it up on tab refocus, but not while the tab stays focused.

**Failure scenario:** Solver A logs `!clue ...` in Discord while solver B has the hunt's Evidence tab open; B never sees the clue until a full page reload or an unrelated mutation of their own, undermining the shared-evidence workflow.

### 67. [MEDIUM] `bot/web/actions.py:380`

**Status: FIXED** in `17afadf` (`fix(web,notifier): close web↔Discord pin/board staleness gaps (#63,65,66,67,68,85)`, branch `working-1`).

Web POST /puzzles/<id>/link (bot/web/actions.py:380-412) sets the puzzle URL via service.set_puzzle_url but never calls cog.web_refresh_info, so the pinned info message — whose links field renders the puzzle URL (puzzles.py:2091) — stays stale; the bang path !link does refresh it (puzzles.py:2396 → send_state → info(update=True) at 2330), and sibling web endpoints (PATCH update_puzzle, add/remove sheet at actions.py:220/255/292) all do refresh.

**Failure scenario:** A user sets/corrects the puzzle URL from the dashboard; the pinned info message in the Discord puzzle channel keeps linking to the old (or missing) hunt-site URL, so channel solvers click a dead link until someone runs !info.

### 68. [LOW] `bot/core/puzzle_service.py:2244`

**Status: FIXED** in `17afadf` (`fix(web,notifier): close web↔Discord pin/board staleness gaps (#63,65,66,67,68,85)`, branch `working-1`). Converted to async + @pushes_board(), matching every other board-visible service mutation.

update_hunt_settings (bot/core/puzzle_service.py:2244) is a plain sync method with no @pushes_board, and none of its callers — !set_login (bot/cogs/puzzles.py:1900), !set_start (bot/cogs/puzzles.py:1919), or web PATCH /hunts/<uid>/settings (bot/web/actions.py:776) — emit a board_changed event, so hunt-settings changes (url, start time, login, prefix) never push to connected dashboards; other clients render stale hunt header values until an unrelated mutation fires a push or they reload.

**Failure scenario:** Admin updates the hunt URL or start time (either via !set_start or the web settings dialog); other users' open dashboards keep rendering the stale hunt header values until an unrelated puzzle mutation happens to fire a board push.


## 3. Documentation & welcome messages (5 findings)

### 69. [MEDIUM] `README.md:69`

README.md:69 documents a `!p puzzle-round-name: puzzle-name` colon syntax for creating a puzzle into a named round from the #bot channel, but the bang command (bot/cogs/puzzles.py:582-594) assigns the raw argument verbatim to puzzle_name with no colon parsing, and the target round is taken solely from the invoking channel's category context (cog_before_invoke environment, puzzles.py:150-195); no colon-splitting code exists anywhere in bot/.

**Failure scenario:** A user in the bot channel types `!p the-bog: clueless-crossword` expecting the puzzle to land in the-bog; instead the bot treats the full string `the-bog: clueless-crossword` as the puzzle name (round taken from the current channel's context), creating a mis-named puzzle/sheet in the wrong place.

### 70. [MEDIUM] `bot/cogs/puzzles.py:2311`

**Status: FIXED** in `eff54d5` (`fix(puzzles): close pin/parity staleness gaps
(#12, #15, #70)`, branch `working-1`). The welcome now names the channel kind
correctly (capstone/runaround/meta) and, for capstones, leads with `!informs`
and warns against `!feeds_only`/`!feeds`. Both `!info` and the web
pin-refresh path share this one function, so the fix covers both entrypoints.

Capstone and Runaround channels get the generic metapuzzle pinned welcome on both entrypoints — bang !info (bot/cogs/puzzles.py:2311-2312) and web refresh/welcome (bot/core/notifier.py:120, :158 route on puzzle.is_target with no capstone case) — so the pin says "Welcome to the meta channel for ..." and leads with `!feeds_only`/`!feeds` hints, which are the wrong edge for a capstone whose members should use `!informs`; a solver following the top hint tags a feeds edge (and `!feeds_only` also moves their channel), silently shifting the capstone toward meta semantics and its sheet rollup toward an answer list.

**Failure scenario:** A solver in a capstone channel reads the pinned info (posted at creation and on every !info / web refresh), follows the top hint `!feeds_only <code>` from a feeder channel, and tags a feeds edge into the capstone — since meta vs capstone is derived from incoming edges, this silently changes the group's semantics and rollup rendering.

### 71. [MEDIUM] `docs/commands.md:54`

The convert table in docs/commands.md (lines 54-60) omits `!convert_to_metapuzzle` (bot/cogs/puzzles.py:1757-1760), the command that converts a capstone/runaround/group back to a plain metapuzzle (feeders revert to the 'feeds' edge); the command appears nowhere in docs/.

**Failure scenario:** An organiser who accidentally converted a meta to a capstone consults the canonical command reference, finds no reverse operation listed (the table implies convert_to_meta/capstone/runaround/group/puzzle is the complete set), and cannot discover that feeders can be flipped back to the feeds edge.

### 72. [MEDIUM] `docs/dashboard.md:1`

docs/dashboard.md is stale: its "Phase 2 (write)" description (lines 5-10) says web writes are only notes/retag/create-puzzles-and-metas, and its architecture list (lines 14-24) omits bot/web/actions.py (38 write routes including solve/unsolve, sheets, clues, minimeta/convert-to-* family, rename, hunt create/settings/archive/delete, meta retag, tag_group delete/rename, reconcile, rollup rebuild, evidence edit/delete) and bot/web/notifier.py entirely, while the api.py bullet (lines 17-18) lists four endpoints but omits /guilds, /rollups/<id>/answers, and /evidence (GET+POST).

**Failure scenario:** A developer reading the architecture doc concludes web writes are limited to notes/retag/create (line 5-8) and that api.py holds four read endpoints, then hunts in the wrong place for the solve/convert/reconcile web paths or re-implements an existing action route, since the doc never mentions actions.py at all.

### 73. [MEDIUM] `docs/dashboard.md:44`

docs/dashboard.md line 44 (deploy step 2) still instructs applying the dashboard schema by piping the stale copy scripts/sql/001_dashboard.sql directly into mysql, but the repo has since moved to a ledgered migration runner (migrations/000-021 applied via `python migrate.py`, as README.md 135-172 documents); following it applies 001 outside the schema_migrations ledger, so a later `python migrate.py` re-applies 001 and errors on the already-existing table, and the operator is never pointed at migrations 002-021.

**Failure scenario:** An operator enabling the dashboard follows docs/dashboard.md's deploy steps, runs only 001_dashboard.sql, and never applies migrations 002-021 (rollups, typed groups, edge model, minimeta, evidence, multi-edge tags); the bot then fails at runtime on missing tables/columns and the migrate.py ledger never records what was applied.


## 4. Duplication (10 findings)

### 74. [MEDIUM] `bot/cogs/puzzles.py:1385`

**Status: FIXED** in `52a5e94` (`fix(puzzles): ack tag mutations after they succeed, not before (#17, #74)`, branch `working-1`). Reordered the four drifted commands to ack-after-mutation, matching their siblings — a pure statement reorder, no new duplication removed (the six-way copy-paste itself was out of scope).

The feeds/feeds_only/informs/informs_only/groups_with/groups_only_with (plus remove_from) command family in bot/cogs/puzzles.py is six near-identical copy-pasted bodies differing only in edge string and message text, and they have drifted: the plain variants (feeds 1373-1376, informs 1454-1457, groups_with 1504-1507) send the ✅ success message AFTER the service call, while feeds_only (1399-1401), remove_from (1428-1430), informs_only (1479-1481) and groups_only_with (1529-1531) send ✅ BEFORE the mutation, so a service failure leaves the user with a false success confirmation (only partially mitigated by a trailing ⚠️ from on_command_error).

**Failure scenario:** If tag_edge_only raises (Sheets/Discord error) in !feeds_only/!informs_only/!groups_only_with/!remove_from the user has already been told ':white_check_mark: assigned' for a mutation that never happened; adding a shared behavior (e.g. a new refresh or guard) requires six coordinated edits and will plausibly miss one.

### 75. [MEDIUM] `bot/web/actions.py:1153`

convert_to_puzzle (bot/web/actions.py:1153) duplicates the convert_to_feeder handler body instead of delegating, and the copies have drifted: it lacks the HttpError catch (a Sheets HttpError after the already-committed demotion propagates to a 500 while the /convert-to-feeder twin returns ok+sheet_warning), drops the orphaned-feeders count from the response, and its SheetRefreshError branch returns early, skipping refresh_unassigned_safe and the audit record. Correction: the SPA only calls /convert-to-feeder (frontend/src/api.ts:395), so the drift affects backward-compat/external callers of the alias route, not the dashboard.

**Failure scenario:** A Google Sheets HttpError during POST /puzzles/<id>/convert-to-puzzle propagates to a 500 even though the demotion already committed (the /convert-to-feeder twin returns ok+sheet_warning for the same failure), and the SPA never learns how many feeders were orphaned; any future fix to one route silently misses the other.

### 76. [LOW] `README.md:369`

The nginx reverse-proxy config (websocket upgrade map, the proxy_set_header block, and the 3600s read/send timeouts) is duplicated near-verbatim between README.md:359-399 (generic template) and docs/dashboard.md:98-133 (souwester test-site instance), with the timeout comment already micro-drifted; a third Plesk-adapted variant at README.md:435-461 shares the same directive block, so a proxy fix must be hand-propagated to three places.

**Failure scenario:** A proxy fix — e.g. a changed timeout, a new header, or a WebSocket path change — edited in one doc leaves the other stale, and an operator following the un-updated copy deploys a broken or churn-prone activity feed.

### 77. [LOW] `bot/cogs/puzzles.py:1670`

convert_to_meta / convert_to_capstone / convert_to_runaround / convert_to_group / convert_to_metapuzzle (bot/cogs/puzzles.py:1637-1786) are five ~30-line clones differing only in the target type string, the is_target guard direction (inverted for convert_to_meta), and the confirmation sentence; any shared change (e.g. adding the @with_puzzle_mutex the other mutating commands carry, or changing RuntimeError presentation) must be repeated five times, while the web path already factored the same operation into a single parameterized _convert_to_type_handler (bot/web/actions.py:1051).

**Failure scenario:** Any shared fix — e.g. adding the puzzle mutex the other mutating commands carry, or changing the RuntimeError presentation — must be applied five times; the web side already needed a parameterized _convert_to_type_handler for the same operations, showing the shape, so each new group type adds another full copy here.

### 78. [LOW] `bot/cogs/puzzles.py:2261`

The 'Puzzle commands' embed field is duplicated between _add_metapuzzle_command_fields (bot/cogs/puzzles.py:2114-2128) and send_initial_puzzle_channel_messages (bot/cogs/puzzles.py:2261-2277) and has mildly drifted (!clue/!clues collapsed to one line and !info added only in the puzzle copy), so new/renamed commands must be edited in two places; however, the claimed meta-pin gap is overstated — !feeds/!informs/!info/!rename_meta are already listed in the meta pin's General Commands field (lines 2182-2188), so meta-channel solvers are not actually missing tagging commands.

**Failure scenario:** Every new or renamed command needs the help text edited in two embeds; today a solver in a meta channel sees a different (staler) command list than in a puzzle channel, e.g. no mention of !feeds/!informs tagging from the meta pin's shared field.

### 79. [LOW] `bot/cogs/puzzles.py:2642`

**Status: FIXED** in `4ec4444` (`refactor: dedupe the solved embed and change/add-solution tails (#79, #80)`, branch `working-1`, merged to `staging` in `8442c15`). Folded in at the user's request. Extracted a shared `bot.core.notifier.build_solved_embed` formula. While fixing this, also found and removed (`32ef6fc`) the `Notifier.puzzle_solved` interface method and its four implementations, plus `_post_solved_embed` — none of it was reachable from anywhere outside `notifier.py`; this also makes #88 below moot (the vulnerable code no longer exists).

The 'PUZZLE SOLVED!' embed is built in three places — _solve_impl (bot/cogs/puzzles.py:2642), web_post_solved_embed (bot/cogs/puzzles.py:821), and the cog-unavailable fallback _post_solved_embed (bot/core/notifier.py:261) — so any wording/emoji change must be made up to three times; the notifier fallback additionally sends via get_event_loop().create_task without awaiting (notifier.py:266) and will silently rot. Note: the bang-vs-web Follow-up wording difference is at least partly intentional (dashboard-specific copy, mirrored by web_post_unsolved), so 'already-divergent' overstates drift there.

**Failure scenario:** A wording/emoji/field change to the solve announcement must be made three times; the bang and dashboard solve messages have already drifted, and the notifier fallback copy additionally fires via get_event_loop().create_task without awaiting, so it silently rots as the other two evolve.

### 80. [LOW] `bot/cogs/puzzles.py:2678`

**Status: FIXED** in `4ec4444` (`refactor: dedupe the solved embed and change/add-solution tails (#79, #80)`, branch `working-1`, merged to `staging` in `8442c15`). Folded in at the user's request. Merged into one `_change_or_add_solution_impl`; also added the Follow-up field `add_solution`'s embed was missing.

change_solution (bot/cogs/puzzles.py:2678-2717) and add_solution (2721-2757) duplicate a near-identical ~30-line tail — not-a-puzzle guard, unsolved fallback (to _solve_impl / _partial_impl respectively), _normalize_solution, empty-solution check, service call, SheetRefreshError capture, celebration embed, self.info(ctx, update=True), and _warn_stale_sheets — differing only in the service method, embed title, and the fallback impl; the copies have already drifted in that add_solution's embed omits the Follow-up field that change_solution (2710-2713), _solve_impl (2644-2647) and _partial_impl (2595-2598) all include.

**Failure scenario:** A fix to the shared flow (e.g. also refreshing unassigned, or changing the stale-sheet warning) must be duplicated across both plus _solve_impl/_partial_impl; the missing Follow-up guidance on !add_solution shows the copies are already diverging.

### 81. [LOW] `bot/web/actions.py:726`

The post-and-pin welcome wrapper flow for the hunt channel is duplicated inline in the web layer (bot/web/actions.py:729-739) and the bang path (bot/cogs/puzzles.py:546-547) instead of going through DiscordNotifier.welcome, which has no hunt-channel branch (bot/core/notifier.py:110-116 dispatches only group/metapuzzle/puzzle); the wrappers already diverge on guild_data sourcing and pin error handling, so future changes to welcome/pin semantics made in the notifier will not reach hunt creation. Correction: the pins[-1] edit convention and message content live inside the shared helper send_initial_hunt_channel_messages (puzzles.py:2040-2086), so those specific semantics DO stay in sync across all callers; only the wrapper (pin call, error handling, settings sourcing) is triplicated.

**Failure scenario:** A change to welcome/pin semantics (e.g. the pins[-1] edit convention, guild-settings sourcing, or pin error handling) made in DiscordNotifier.welcome never reaches web hunt creation, so web-created and bang-created hunts get different pinned welcomes and the update=True refresh path can edit the wrong message.

### 82. [LOW] `bot/web/actions.py:1535`

The ~15-line handler preamble is genuinely copy-pasted across ~22 handlers in bot/web/actions.py, and two copies have drifted: create_puzzle (actions.py:1535) and create_meta (actions.py:1578) call bot.get_guild(int(ctx["guild_snowflake"])) without the "if snowflake else None" guard used by the other 20 occurrences, so a NULL guilds.guild_id yields a TypeError 500 on POST /hunts/<uid>/puzzles or /metas. However, the claimed contrast is overstated: only 2 guarded handlers (lines 703, 1423) actually handle guild=None; the rest pass None into service code that dereferences it (e.g. guild.categories in puzzle_service.py:1620), also producing a 500. The real cost is drift risk from duplication, not a distinct user-visible failure mode.

**Failure scenario:** A hunt whose guild row has a NULL guild_snowflake: POST /hunts/<uid>/puzzles or /metas raises TypeError at int(None) and returns a 500, while every other handler degrades to guild=None; each new handler re-copies the block and can drift the same way.

### 83. [LOW] `docs/dashboard.md:34`

Deploy instructions are near-duplicated between docs/dashboard.md (steps 1-6 at lines 34-60 plus the nginx reference at lines 98-142) and README.md sections 4-7 (lines 255-466), and they have already drifted: docs/dashboard.md line 44 still instructs `mysql <db> < scripts/sql/001_dashboard.sql`, whereas the README's current mechanism is `python migrate.py` over migrations/ (000-009, tracked in schema_migrations), so a reader deploying from dashboard.md skips migrations 002-009 and bypasses migration tracking; the two nginx blocks differ only cosmetically.

**Failure scenario:** Every future deploy change (new config key, port change, proxy tweak) must be made in two places; the DB-migration step already updated in README but not dashboard.md shows the drift is real, and readers of whichever copy is stale deploy incorrectly.


## 5. Practices (7 findings)

### 84. [MEDIUM] `bot/core/puzzle_service.py:1909`

refresh_evidence (bot/core/puzzle_service.py:1909) and link/unlink_informs_for_evidence (:1973/:1985) have no internal exception guard yet are launched via bare, unreferenced asyncio.create_task at bot/web/actions.py:445, 1740, 1743, 1755, 1785, 1789, bot/web/api.py:220, and bot/cogs/puzzles.py:1263/1285 — violating the codebase's own safe-wrapper convention (refresh_unassigned_safe docstring, puzzle_service.py:2072-2073: "Use this for every asyncio.create_task(...) trigger") that the sibling helpers refresh_informs_targets (:1968-1971) and refresh_target_for_puzzle (:2009-2012) do follow.

**Failure scenario:** A Sheets HttpError in create_blank_tab/reconcile_table makes the Evidence tab silently stop updating; the only trace is a delayed, context-free 'Task exception was never retrieved' emitted whenever the GC collects the unreferenced task (which can also be collected mid-flight, cancelling the refresh), so the operator cannot tie the failure to a hunt or action.

### 85. [MEDIUM] `bot/web/__init__.py:156`

**Status: FIXED** in `17afadf` (`fix(web,notifier): close web↔Discord pin/board staleness gaps (#63,65,66,67,68,85)`, branch `working-1`). Added a `task.add_done_callback` that logs (with exc_info) if the background task raises.

The web-dashboard task's exception is never observed: start_web (bot/web/__init__.py:156-164) creates the run_task task with no done-callback, and bot.web_task (bot/__init__.py:191) holds a permanent strong reference, so if hypercorn fails after task creation (e.g. the port is already bound) the exception is neither logged nor retrieved and the bot runs on with the dashboard silently down.

**Failure scenario:** Another process holds config.web_port when the bot starts; hypercorn's bind fails inside the task, the bot keeps running normally, discord.log shows only 'Starting web dashboard', and the operator discovers the dashboard is down hours later with no error anywhere.

### 86. [MEDIUM] `bot/web/actions.py:1060`

**Status: FIXED** in `1e3fe6b` (`fix(web): close multi-team visibility/permission
gaps (#36, #38, #86)`, branch `working-1`). All six convert-to-* endpoints now
gate on `require_admin` (ADMIN_ROLES) instead of `require_organiser`, matching
the bang commands' role requirement. (The bang commands additionally require
the Discord `manage_channels` permission, which has no clean web equivalent —
these are hunt-wide actions, not scoped to a specific channel the invoker is
in — so this fix closes the role-list gap, not the permission-check gap.)

All web convert endpoints (convert-to-meta at bot/web/actions.py:549, the shared _convert_to_type_handler at :1061 used by convert-to-capstone/runaround/group/metapuzzle, convert-to-feeder at :1006, and convert-to-puzzle at :1168) gate on require_organiser (ORGANISER_ROLES = ADMIN_ROLES + 'Organisers') with no Discord-permission check, while every equivalent bang command requires the stricter ADMIN_ROLES plus manage_channels — so an Organisers-only user is refused the bang command but can perform the identical conversion from the dashboard.

**Failure scenario:** A user holding only the 'Organisers' role (no admin role, no Manage Channels) is refused !convert_to_capstone by the bot but can perform the identical structural conversion from the dashboard, silently bypassing the stricter Discord-side policy.

### 87. [LOW] `.github/workflows/ci.yml:67`

CI's only check step is `pytest -v` (.github/workflows/ci.yml:66-67); no workflow step, pre-commit config, or git hook runs `ruff check`, so the F-ruleset lint documented in AGENTS.md "Linting" (with ruff a Pipfile dev dependency and ruff.toml selecting F rules) is enforced only by developers remembering to run it manually.

**Failure scenario:** A change introducing an F821 undefined-name in a rarely-exercised branch passes CI (tests don't reach it) and merges to staging; the bug only fires as a NameError at runtime mid-hunt, exactly the class the F ruleset exists to catch.

### 88. [LOW] `bot/core/notifier.py:265`

**Status: MOOT** as of `32ef6fc` (`refactor: remove dead puzzle_solved notifier path`, branch `working-1`, merged to `staging` in `8442c15`). Not fixed directly — while fixing #79, this whole fallback path (`_post_solved_embed`, called only from the dead `DiscordNotifier.puzzle_solved`) was found to be unreachable from anywhere outside `notifier.py` and deleted, so the fire-and-forget bug described here no longer has any code to apply to.

On the cog-unavailable fallback path, _post_solved_embed (bot/core/notifier.py:265) schedules channel.send via asyncio.get_event_loop().create_task without holding a task reference, awaiting, or catching exceptions — so a failed SOLVED post (missing perms, deleted channel) is silently lost with no log line, unlike welcome/refresh_info/the normal puzzle_solved path which all await and logger.exception.

**Failure scenario:** On the cog-unavailable fallback path, if the send fails (missing permissions, deleted channel) the SOLVED confirmation silently never appears and no log line is written; the unreferenced task can also be GC'd before running.

### 89. [LOW] `bot/store/__init__.py:35`

MySQLReconnector._ensure (bot/store/__init__.py:32-42) runs a blocking conn.ping(reconnect=True, attempts=2, delay=1) — and on failure a fresh blocking mysql.connector.connect() — before every cursor()/commit()/rollback(); all bang-command and web-write mutations call the store synchronously on the bot event loop via bot/core/puzzle_service.py, so while MySQL is down or restarting each DB touch stalls the loop for multiple seconds (ping retries plus connect timeout), lagging Discord heartbeats and all commands bot-wide. Only the activity writer (bot/cogs/activity.py:95, asyncio.to_thread) and the web read path (bot/web/data.py:66-75) were moved off-loop; the write path was not.

**Failure scenario:** MySQL restarts mid-hunt; every bang command and web write path stalls the event loop ~2-3s per DB call during the outage window, Discord heartbeats and all other commands lag bot-wide instead of just the one failing operation.

### 90. [LOW] `bot/web/auth.py:151`

resolve_membership (bot/web/auth.py:149-152) and resolve_role_names (bot/web/auth.py:167-170) catch all fetch_member exceptions with a bare `except Exception` and no logging, so a transient Discord API failure (rate limit, 5xx, timeout) is indistinguishable from "not a member" — but only on the fallback path when the member is absent from the bot's cache, which is rare since the bot runs with Intents.all() and get_member is tried first.

**Failure scenario:** During a transient Discord API outage a logged-in team member's fetch_member raises; hunt_visible/require_organiser then return 403 'forbidden' on every hunt and action, and because nothing is logged the operator has no trail explaining why legitimate users were suddenly locked out.


## Appendix: refuted findings (reported by a reviewer, rejected on verification)

- `bot/core/contribution.py:14` — _feeder_answer renders any non-empty solution as the feeder's contributed answer, contradicting both the module contract ('solved answer (blank if unsolved)') and the domain rule that a feeds edge con…
  - Rejected: The behaviour is real but deliberate, so the finding fails as a correctness bug. (1) bot/core/puzzle_service.py:1197-1214 — add_partial sets puzzle.solution (solved stays False), commits, then explicitly calls refresh_targets on every tagged group. The ONLY table-affecting state a partial changes is…

- `bot/cogs/puzzles.py:1373` — !feeds and !informs have no guard against tagging a group's own target puzzle into that same group, creating a self-edge (the meta 'feeds' itself), while !remove_from explicitly guards the inverse at …
  - Rejected: The mechanics the reviewer describes are accurate but the interpretation is wrong. (1) bot/cogs/puzzles.py:1373 (!feeds) and :1454 (!informs) indeed call service.tag_with_edge with no target_puzzle_id check, and :1425-1427 (!remove_from) blocks removing the target from its own group. (2) However, se…

- `bot/web/actions.py:539` — convert_to_meta duplicates the _convert_to_type_handler flow (which even has an error_if_not_meta=False knob built for this case) and has drifted from it: it omits the discord.RateLimited/HTTPExceptio…
  - Rejected: Refutation of the two failure scenarios: (1) SheetRefreshError does NOT 500 — bot/web/actions.py:577-579 has `except (HttpError, SheetRefreshError):` which sets a warning and falls through to the ok:true + sheet_warning response at lines 585-588, essentially the same non-fatal treatment as the share…

- `bot/web/api.py:72` — api.py _load_visible_hunt duplicates actions.py _resolve_visible_hunt — the same get_hunt/resolve_membership/hunt_visible sequence maintained in two modules with different return shapes.…
  - Rejected: bot/web/api.py:72-82 (_load_visible_hunt) and bot/web/actions.py:87-102 (_resolve_visible_hunt) both delegate to the SAME shared primitives: data.get_hunt_by_uid / lookup coroutine, resolve_membership (bot/web/auth.py), data.get_guilds_map, and critically the single hunt_visible function at bot/web/…

- `bot/core/forum_config.py:30` — Forum-layout welcome plaintext duplicates the channel-layout welcome embeds and has drifted well behind them: it lacks the !feeds/!feeds_only/!informs/!remove_from/!sync lines and the group-code usage…
  - Rejected: bot/core/forum_config.py:36-39 (group), 47-50 (meta), 59-62 (round/capstone) list only !p/!list/!info; the group code is appended bare at lines 65-66 with no usage guidance. Channel-layout embeds at bot/cogs/puzzles.py:2183-2188 (meta) and 2222-2226 (group) include !feeds_only/!feeds/!informs/!remov…

- `bot/utils/gsheets.py:40` — The googleapiclient service is built with no HTTP timeout (httplib2 default = none), and every .execute() is serialized through the global threading.Lock _execute_lock, so a single hung HTTPS call to …
  - Rejected: bot/utils/gsheets.py:52,64 call build("sheets"/"drive", credentials=...) with no http= argument. In that case discovery.build wires http via _auth.authorized_http (.venv/lib/python3.13/site-packages/googleapiclient/discovery.py:643), which constructs google_auth_httplib2.AuthorizedHttp(credentials, …

- `bot/core/forum_config.py:42` — Forum plaintext welcomes (render_welcome_plaintext) have drifted from the category-layout embeds: the forum meta welcome omits the feeds/informs/remove_from/sync command hints entirely and the capston…
  - Rejected: bot/core/forum_config.py:41-51 (meta branch: only !p/!list/!info, no edge-command hints); bot/core/forum_config.py:52-63 (capstone hits generic "General discussion of the round" else branch, kind set at :89-90); bot/cogs/puzzles.py:2183-2187 and 2222-2226 (embed welcomes teach !feeds_only/!feeds/!in…
