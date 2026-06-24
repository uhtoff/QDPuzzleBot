# Open Questions

All planning-time queries have been resolved by reading the code (2026-06-24). None remain blocking
or non-blocking. The resolutions are folded into `current-spec.md` (Phases 1a, 2e, 4a).

## Blocking

(none)

## Non-blocking

(none — all resolved below)

## Resolved

### Q1 — `web_create_puzzle`'s extra meta-refresh loop → REDUNDANT (web-side), not bang-missing
**Evidence:** `service.create_puzzle` already refreshes metas for every assigned tag internally
(`puzzle_service.py:1201-1208`), inside the same try/except-swallow. A freshly created puzzle has at
most one tag (`:1163-1164`, `new_puzzle.tags.append(round_data.id)` only when a round is given). The
web loop (`puzzles.py:755-756`) re-runs that exact work after the fact; its own comment says
"service already does this internally; duplicate calls are cheap."
**Resolution (Phase 2e):** when collapsing `web_create_puzzle`, have web call `service.create_puzzle`
directly and **drop the loop** — no behaviour change, error tolerance preserved by the service's
internal swallow. Do **not** move anything into the service (it's already there).
**Note on `web_create_round`:** its welcome-deferral (`notifier=None` then `_web_send_welcome` once
`meta_code`/`meta_id` are populated) IS genuine and web-specific — the service's early welcome fires
before `meta_code` is set. Preserve it when collapsing (web calls `service.create_round(notifier=None)`
then sends the welcome, i.e. keep the two-step; just delete the `web_create_round` wrapper shell).

### Q2 — `send_not_puzzle_channel` solved-category comparison → vestigial; fix with a suffix check
**Evidence:** `SOLVED_PUZZLES_CATEGORY = "solved"` (`puzzles.py:80`); `get_solved_puzzle_category(h)`
returns `f"{h}-solved"` (`:1968-1969`). The guard at `:1963` passes `""`, so it compares against
`"-solved"`, which can only match a hunt literally named "" — i.e. it never fires. Real solved
categories are `"<HuntName>-solved"` (created at `:451` with the actual name).
**Resolution (Phase 1a):** fix the `None` crash AND make the intent work without needing the hunt
name, via a suffix check:
```python
async def send_not_puzzle_channel(self, ctx):
    category = ctx.channel.category if ctx.channel else None
    if category is not None and category.name.endswith(f"-{self.SOLVED_PUZZLES_CATEGORY}"):
        await ctx.send(":x: This puzzle appears to already be solved")
    else:
        await ctx.send(":x: This does not appear to be a puzzle channel")
```

### Q3 — `!read` command → dead diagnostic, remove it
**Evidence:** `puzzles_gsheet.py:50-52` — an ungated `@commands.command()` whose entire body is
`print(await execute_with_retry(get_sheet().get(spreadsheetId=...)))`: a raw stdout dump of full
spreadsheet metadata, no role gate. No references anywhere in `bot/`, `docs/`, or `tests/`.
**Resolution (Phase 4a):** delete the command. (Also a minor footgun: unrestricted + live Sheets
call.) The `on_ready` print at `:48` is the same cleanup pass — downgrade/remove per 4a.

## Resolved during planning (recorded for the executor)

- **Answer casing** → uppercase everywhere, in the service. (was divergence #4)
- **Note attribution** → keep surface-specific; `service.add_note` takes an `attribution` string param.
- **Solved-state truthiness** → normalize to the boolean `puzzle.solved`.
- **Scope** → archive_channel extraction and frontend optimistic-model unification are OUT.
