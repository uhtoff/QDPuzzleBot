# Current Spec — Group-type consolidation (code-review finding #1)

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Target model: **Phase 1 → Opus 4.8** (test design) · **Phase 2 & 3 → Sonnet 4.6** (implementation)
Started: 2026-06-27
Status: ready for handoff — Phase 1 (tests)

Full design: `docs/superpowers/specs/2026-06-27-group-type-consolidation-design.md`
Reference plan (background only — this spec is authoritative): `docs/superpowers/plans/2026-06-27-group-type-consolidation.md`

---

## Before you start (executing model)

- Execute **only the current phase**. Do not look ahead and implement Phase 2 while doing Phase 1.
- On any **unexpected** failure (a fatal/import error, a missing attribute, an assertion you don't
  understand), invoke **`superpowers:systematic-debugging`** before changing anything.
- Before claiming a phase done, invoke **`superpowers:verification-before-completion`** and paste the
  acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood fix, write
  `.ai/active/blocker.md` (what you ran, what you expected, what happened, your hypothesis) and STOP.
- Commit at each phase boundary. Trailer on every commit:
  `-m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`.

## Goal

Collapse stored `tag_group.type` to exactly `{Metapuzzle, Capstone, Runaround, Group}` and route
every "is this a group channel / which group type" decision through one canonical constant, so the
membership lists that drive `delete_round`, channel settings, `!list_puzzles`, and `!info` can no
longer drift. `Round` becomes a create-time **input alias** of `Metapuzzle` (keeps the `" - meta"`
meta-name suffix); `Metaless Round` is **removed entirely**, including the `!roundnometa`/`!rnm`
command aliases.

**Domain context (AGENTS.md:45-51):** round / metapuzzle / runaround are *not* distinct structures —
they are presentation labels; "the only thing that differs by label today is the welcome message."
This change makes that explicit in the data model.

## In scope

- `puzzle_service.py`: canonical `GROUP_TYPES` + `GROUP_TYPE_INPUT_ALIASES`; drop the `Round` key from
  `TYPE_DEFAULTS`; simplify `is_group`.
- `puzzles.py`: repoint all four membership lists at `GROUP_TYPES`; `command_map` + decorator aliases +
  docstrings; collapse `!info`; delete the dead `send_initial_round_channel_messages`; drop the unused
  `TagGroupData` import.
- `actions.py`: `create_meta` accepts the `Round` input alias, stores `Metapuzzle`.
- `docs/commands.md`: remove the `!roundnometa`/`!rnm` row + alias mention.
- SQL migration converting existing rows.
- Tests for all of the above.

## Out of scope (do NOT touch)

- Code-review findings #2, #3, #4, #7 (handed to working-2).
- Finding #5 is addressed **only** in the two test files this task already edits — do not hunt for
  other stale comments.
- `forum_config.py` — its `("Round","Metapuzzle")` branch is a harmless defensive read; leave it.
- `tests/test_build_board.py` — its literal `"type": "Round"` rows (lines 235, 252) are board test
  data that never goes through `TYPE_DEFAULTS`; they stay green, **do not edit them**.
- Behaviour of Capstone / Runaround / Group themselves — only their routing changes.

## Relevant files

- `bot/core/puzzle_service.py` — `TYPE_DEFAULTS` (87-92), `is_group` (1596), comment (1567)
- `bot/cogs/puzzles.py` — import (14-15), puzzle_service import (29), `PUZZLE_GROUPS` (83), `_round_impl`
  docstring + `command_map` (1476-1502), `round` decorator + docstring (1545-1558), `sync` (1254-1255),
  `get_settings`/`save_settings` (1739/1750), `list_puzzles` (1901), `info` (2257-2266),
  `send_initial_round_channel_messages` (2148-2187), `delete_round` (2963)
- `bot/web/actions.py` — `create_meta` (1503-1534)
- `docs/commands.md` — lines 43, 46
- `migrations/` — add `016_collapse_group_types.sql` (highest existing is `015_hunt_layout_mode.sql`)
- `tests/conftest.py` (read only) — `make_cog`, `make_ctx`, `make_round`
- `tests/test_group_channel_type.py` (read + edit) — pattern reference + Metaless cleanup
- `tests/test_edge_web.py` (read + edit) — `web` fixture pattern + flip the Round assertion
- `tests/test_forum_layout.py` (edit) — stale comment (164-166)

## Constraints

- Do not touch any file outside the lists above. Do not refactor unrelated code.
- **Impact greps already done — no orphan call sites:** `send_initial_round_channel_messages` has only
  the one `!info` caller (being changed); `!roundnometa`/`!rnm` appear only in `command_map`, the
  decorator, the docstrings, and `docs/commands.md`. No constructor signatures change.
- `bot/store/__init__.py` opens MySQL **at import**, so the test process needs a reachable DB
  (`config.json` → `puzzlebot_test`). Individual tests patch DAOs and run no real queries.

## Test constraints (read — you do not read global instructions)

- **Runner (exact):** `timeout <n> pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1` as a
  **single statement** (no `; echo` tail). `pipenv run python -m pytest` is mandatory — bare `pytest`
  fails `import bot`. A killed run exits 124; read `/tmp/test.log` (`tail -n 30 /tmp/test.log`).
- **`asyncio_mode = auto`** (pytest.ini): write `async def test_*` with **no decorator**. Assert with
  `AsyncMock` methods (`assert_awaited_once()`, `assert_not_awaited()`).
- **Factories, not fixtures:** import `make_cog()`, `make_ctx()`, `make_round()` from `tests.conftest`
  and call them. They are not pytest fixtures.
- **Patch DAOs at the cog import site:** `patch("bot.cogs.puzzles.PuzzleJsonDb")` — mirror
  `tests/test_group_channel_type.py`. Do not patch `bot.store`.
- **Reuse exact seams; invent none.** Cog commands are invoked via `Puzzles.<cmd>.callback(cog, ctx)`;
  `_round_impl` is a plain method called directly; `get_settings` is a plain method.
- **NEVER** write a loop that resolves state "until" a condition; **NEVER** modify test infrastructure
  or stub randomness; if you think you need either, write `blocker.md` and STOP.
- **Confirm every attribute/payload key before use.** `make_round()` returns a `TagGroupData` with
  `.id`, `.name`, `.type`, `.category_id`, etc. `make_ctx()` already provides `ctx.send = AsyncMock()`
  and `ctx.guild = MagicMock()`.
- **Each test file defines/imports every helper it calls** — the parallel gate shards by file. Keep
  helpers local; the new file imports only from `tests.conftest`.
- **A test failing because a method/branch isn't there yet is the right reason.** A fatal, a missing
  attribute, or an unknown payload key means a wrong assumption in the test — fix the test, don't add
  scaffolding.

---

## Phase 1 — write ALL failing tests (model: Opus 4.8)

No production code in this phase. Author the new test file, flip the one web assertion, and remove the
obsolete Metaless tests + stale comments. Commit. Then confirm the right-reason failures.

### 1A. Create `tests/test_group_type_consolidation.py` (near-verbatim)

```python
"""Group-type consolidation (code-review finding #1).

asyncio_mode=auto (pytest.ini) -> async tests need no decorator. conftest
factories are plain functions. Patch DAOs at the cog import site
(bot.cogs.puzzles.*), mirroring tests/test_group_channel_type.py.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_cog, make_ctx, make_round


# --- Round is a create-time alias of Metapuzzle (keeps " - meta" suffix) -----

async def test_round_alias_creates_metapuzzle_with_suffix():
    cog = make_cog()
    cog.bot = MagicMock()                 # DiscordNotifier(self.bot) only stores bot
    cog.service.create_tag_group = AsyncMock(return_value=(make_round(), MagicMock()))
    await cog._round_impl(make_ctx(), "the-bog", "r")
    kwargs = cog.service.create_tag_group.call_args.kwargs
    assert kwargs["round_type"] == "Metapuzzle"
    assert kwargs["meta_puzzle_name"] == "the-bog - meta"


# Regression guard — PASSES on current code (group behaviour is unchanged); it
# proves !group still works after roundnometa/rnm are removed.
async def test_group_alias_creates_group_no_meta():
    cog = make_cog()
    cog.bot = MagicMock()
    cog.service.create_tag_group = AsyncMock(return_value=(make_round(), None))
    await cog._round_impl(make_ctx(), "loose-ends", "group")
    kwargs = cog.service.create_tag_group.call_args.kwargs
    assert kwargs["round_type"] == "Group"
    assert kwargs["meta_puzzle_name"] is False


def test_roundnometa_aliases_removed():
    from bot.cogs.puzzles import Puzzles
    assert "roundnometa" not in Puzzles.round.aliases
    assert "rnm" not in Puzzles.round.aliases


# --- Membership lists route the newer types correctly (the finding) ---------

async def test_delete_round_allows_capstone_channel():
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Capstone"
    cog.get_tag_group = lambda ctx: make_round(id=7)
    cog._confirm_destructive = AsyncMock(return_value=False)   # decline -> stop after the guard
    ctx = make_ctx()
    with patch("bot.cogs.puzzles.PuzzleJsonDb") as PuzzleDb:
        PuzzleDb.get_all_from_tag_group.return_value = []
        await Puzzles.delete_round.callback(cog, ctx)
    cog._confirm_destructive.assert_awaited_once()             # guard passed (not rejected)
    assert all("main group channel" not in str(c) for c in ctx.send.call_args_list)


def test_get_settings_routes_capstone_to_tag_group():
    cog = make_cog()
    tg, guild = object(), object()
    cog.get_channel_type = lambda ctx: "Capstone"
    cog.get_tag_group = lambda ctx: tg
    cog.get_guild_data = lambda ctx: guild
    assert cog.get_settings(make_ctx()) is tg


# --- !info routes meta-bearing types to the metapuzzle welcome --------------

async def test_info_capstone_calls_metapuzzle_handler():
    from bot.cogs.puzzles import Puzzles
    cog = make_cog()
    cog.get_channel_type = lambda ctx: "Capstone"
    cog.send_initial_metapuzzle_channel_messages = AsyncMock()
    await Puzzles.info.callback(cog, make_ctx())
    cog.send_initial_metapuzzle_channel_messages.assert_awaited_once()
```

### 1B. Flip the web Round assertion in `tests/test_edge_web.py` (replace lines 123-130)

```python
async def test_create_meta_round_alias_stores_metapuzzle_with_suffix(web):
    # "Round" is a create-time alias: stored as Metapuzzle, meta named "<name> - meta".
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Round"})
    assert resp.status_code == 200
    kwargs = web.svc.create_tag_group.call_args.kwargs
    assert kwargs.get("round_type") == "Metapuzzle"
    assert kwargs.get("meta_puzzle_name") == "Foo - meta"
```
(If the surrounding tests carry an `@pytest.mark.asyncio` decorator, keep the same decorator on this
one to match the file's existing style.)

### 1C. Remove obsolete Metaless tests + stale comments in `tests/test_group_channel_type.py`

- Delete docstring lines 7-9 (`These FAIL until Phase 2: ...`).
- Delete `test_legacy_metaless_round_type_routes_to_group` (lines 87-111).
- Replace the comment block at lines 114-118 with:
  ```python
  # ---------------------------------------------------------------------------
  # !info dispatch — a Group channel routes to the group welcome handler.
  # ---------------------------------------------------------------------------
  ```
- In `test_info_group_calls_group_handler`, delete the line
  `cog.send_initial_metaless_round_channel_messages = AsyncMock()`.
- Delete `test_info_metaless_round_calls_group_handler` (lines 136-148).

### 1D. Fix the stale comment in `tests/test_forum_layout.py` (replace lines 164-166)

```python
# ---------------------------------------------------------------------------
# Group general-channel group type + service-owned welcomes.
# ---------------------------------------------------------------------------
```

### Phase 1 acceptance

1. Collection is clean (no import/bootstrap fatal):
   `timeout 120 pipenv run python -m pytest tests/test_group_type_consolidation.py --co -q > /tmp/test.log 2>&1`
   Expected: collects 6 tests, exit 0, no errors.
2. New tests fail for the **right reason**:
   `timeout 180 pipenv run python -m pytest tests/test_group_type_consolidation.py tests/test_edge_web.py -q > /tmp/test.log 2>&1`
   Expected FAILS (assertion failures, not errors):
   - `test_round_alias_creates_metapuzzle_with_suffix` — got `round_type == "Round"`
   - `test_roundnometa_aliases_removed` — aliases still present
   - `test_delete_round_allows_capstone_channel` — `_confirm_destructive` not awaited (guard rejects Capstone)
   - `test_get_settings_routes_capstone_to_tag_group` — returns the guild sentinel, not `tg`
   - `test_info_capstone_calls_metapuzzle_handler` — handler not awaited (Capstone hits the else)
   - `test_create_meta_round_alias_stores_metapuzzle_with_suffix` — got `round_type == "Round"`
   Expected PASS now: `test_group_alias_creates_group_no_meta` (regression guard).
3. The two edited files still collect cleanly:
   `timeout 120 pipenv run python -m pytest tests/test_group_channel_type.py tests/test_forum_layout.py -q > /tmp/test.log 2>&1`
   Expected: PASS (Metaless tests removed; remaining tests still pass).
4. Commit:
   `git add tests/test_group_type_consolidation.py tests/test_edge_web.py tests/test_group_channel_type.py tests/test_forum_layout.py`
   `git commit -m "test: failing tests for group-type consolidation (finding #1)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Phase 2 — implementation (model: Sonnet 4.6)

Make the Phase 1 tests pass. No new tests. Work in this order.

1. **`bot/core/puzzle_service.py`** — after the `TYPE_DEFAULTS` closing `}` (line 92) insert:
   ```python

   # Stored tag_group.type values. "Round" is intentionally NOT here: it is a
   # create-time input label (see GROUP_TYPE_INPUT_ALIASES) stored as "Metapuzzle".
   GROUP_TYPES = ("Metapuzzle", "Capstone", "Runaround", "Group")

   # Create-time input labels that are not stored types -> the stored type they map to.
   GROUP_TYPE_INPUT_ALIASES = {"Round": "Metapuzzle"}
   ```
   Change `is_group` (line 1596) to: `is_group = round_type == "Group"`.
   Tidy the comment at line 1567: `Pass a string to override it.  Pass ``False`` to skip (no meta / Group).`

2. **`bot/cogs/puzzles.py`:**
   - Line 29: `from bot.core.puzzle_service import TYPE_DEFAULTS, GROUP_TYPES`
   - Lines 14-15: drop `TagGroupData` →
     ```python
     from bot.store import PuzzleData, PuzzleJsonDb, GuildSettingsDb, \
         TagGroupJsonDb, HuntData, HuntJsonDb, TagGroupCategoryDb, resolve_group
     ```
   - Delete line 83 (`PUZZLE_GROUPS = [...]`).
   - `command_map` (1492-1502) — replace dict body with:
     ```python
             command_map = {
                 "r": ("Metapuzzle", True, " - meta"),
                 "round": ("Metapuzzle", True, " - meta"),
                 "mp": ("Metapuzzle", True, None),
                 "metapuzzle": ("Metapuzzle", True, None),
                 "capstone": ("Capstone", True, None),
                 "cs": ("Capstone", True, None),
                 "runaround": ("Runaround", True, None),
                 "ra": ("Runaround", True, None),
                 "group": ("Group", False, None),
             }
     ```
   - Decorator (1545-1546):
     ```python
         @commands.command(aliases=["r", "mp", "metapuzzle",
                                    "capstone", "cs", "runaround", "ra", "group"])
     ```
   - `_round_impl` docstring (≈1480-1486): remove the `!roundnometa / !rnm → Metaless Round` line;
     change the round line to `!r / !round       → Metapuzzle (creates meta puzzle named "name - meta")`.
   - `round` command docstring (1553-1558): remove the bullet
     `• \`!rnm\` / \`!roundnometa name\` — a round with no meta puzzle`.
   - `get_settings` (1739) and `save_settings` (1750): `... in self.PUZZLE_GROUPS` → `... in GROUP_TYPES`.
   - `delete_round` (2963): `... not in self.PUZZLE_GROUPS` → `... not in GROUP_TYPES`.
   - `sync` (1254-1255): replace the inline list → `if self.get_channel_type(ctx) not in GROUP_TYPES:`.
   - `list_puzzles` (1901): replace the inline list →
     `if self.get_channel_type(ctx) in (*GROUP_TYPES, "Puzzle"):`.
   - `info` dispatch (2257-2266) — replace the if/elif chain with:
     ```python
             ch_type = self.get_channel_type(ctx)
             if ch_type == "Hunt":
                 await self.send_initial_hunt_channel_messages(ctx, ctx.channel, update=update)
             elif ch_type in ("Metapuzzle", "Capstone", "Runaround"):
                 await self.send_initial_metapuzzle_channel_messages(ctx, ctx.channel, update=update)
             elif ch_type == "Group":
                 await self.send_initial_group_channel_messages(ctx, ctx.channel, update=update)
             elif ch_type == "Puzzle":
                 await self.send_initial_puzzle_channel_messages(ctx, ctx.channel, update=update)
             else:
                 await ctx.send(":x: This does not appear to be a hunt channel")
     ```
   - Delete `send_initial_round_channel_messages` (lines 2148-2187) — now unreferenced.

3. **`bot/web/actions.py`** — `create_meta` (1503-1534):
   ```python
       group_type = (body.get("type") or "Metapuzzle").strip()
       from bot.core.puzzle_service import TYPE_DEFAULTS, GROUP_TYPE_INPUT_ALIASES
       if group_type not in TYPE_DEFAULTS and group_type not in GROUP_TYPE_INPUT_ALIASES:
           return jsonify({"error": "bad_type"}), 400
       stored_type = GROUP_TYPE_INPUT_ALIASES.get(group_type, group_type)
   ```
   In the `try` block keep the suffix keyed on the incoming label, pass `stored_type`:
   ```python
           if group_type == "Group":
               meta_puzzle_name = False
           elif group_type == "Round":
               meta_puzzle_name = name + " - meta"
           else:
               meta_puzzle_name = name
           new_round, puzzle = await cog.service.create_tag_group(
               guild, hunt_obj, name,
               notifier=DiscordNotifier(bot),
               meta_puzzle_name=meta_puzzle_name,
               round_type=stored_type,
           )
           if stored_type == "Runaround":
               await cog.service.set_source_mode(hunt_obj, new_round, "subtree")
   ```

4. **`bot/core/puzzle_service.py`** again — now that nothing produces `"Round"`, drop the legacy key:
   - Delete the `"Round": ...` line from `TYPE_DEFAULTS` (line 87).
   - Change the constant to derived: `GROUP_TYPES = tuple(TYPE_DEFAULTS)`.

5. **`docs/commands.md`:**
   - Delete the table row at line 43 (`| \`!roundnometa\` | \`!rnm\` | ... |`).
   - In the note at line 46, remove `, \`!rnm\`` from the alias list so it reads
     `> \`!round\`/\`!r\`, \`!mp\`, \`!cs\`, \`!ra\`, \`!group\` are all aliases of one command;`.

### Phase 2 acceptance

1. `timeout 240 pipenv run python -m pytest tests/test_group_type_consolidation.py tests/test_edge_web.py tests/test_group_channel_type.py tests/test_forum_layout.py tests/test_convert_service.py tests/test_forum_config.py tests/test_forum_tags.py -q > /tmp/test.log 2>&1`
   Expected: PASS (all, including the 6 from Phase 1).
2. `pipenv run ruff check bot/cogs/puzzles.py > /tmp/ruff.log 2>&1` — expected: no errors (the
   `F401 TagGroupData` is gone).
3. Commit:
   `git add bot/core/puzzle_service.py bot/cogs/puzzles.py bot/web/actions.py docs/commands.md`
   `git commit -m "fix: consolidate group types behind GROUP_TYPES; Round->Metapuzzle alias; drop Metaless Round (finding #1)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Phase 3 — migration + final gate (model: Sonnet 4.6)

1. **Verify the next migration number:** `ls migrations/ | sort | tail -3`. Expected highest is
   `015_hunt_layout_mode.sql`. If a `016_*` already exists (parallel worktree), use the next free
   number and adjust the filename below.
2. **Confirm MySQL is the live store:** `grep -rn "class TagGroupJsonDb\|tag_group" bot/store/*.py | head`.
   The `tag_group` table lives in `bot/store/mysqldb.py`. (If a JSON-file store holds live rows, write
   `blocker.md` and STOP — the SQL-only migration would miss it.)
3. **Create `migrations/016_collapse_group_types.sql`:**
   ```sql
   -- Collapse group types (code-review finding #1):
   --   Round          -> Metapuzzle  (Round is now a create-time alias only)
   --   Metaless Round -> Group       (retired entirely)
   -- Spec: docs/superpowers/specs/2026-06-27-group-type-consolidation-design.md
   UPDATE tag_group SET type = 'Metapuzzle' WHERE type = 'Round';
   UPDATE tag_group SET type = 'Group'      WHERE type = 'Metaless Round';
   ```
4. **Apply + verify** against the test DB using the project migration runner (per `README` /
   `migrate.py`), then confirm `SELECT DISTINCT type FROM tag_group;` is a subset of
   `{Metapuzzle, Capstone, Runaround, Group}` with no `Round`/`Metaless Round`.

### Phase 3 acceptance

1. Full suite: `timeout 300 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` — PASS.
2. `pipenv run ruff check > /tmp/ruff.log 2>&1` — no errors.
3. Sweep: `grep -rn '"Metaless Round"' bot/` — no matches. `grep -rn '"Round"\|'"'"'Round'"'"'' bot/`
   — only `forum_config.py`'s defensive `("Round","Metapuzzle")` remains.
4. Commit:
   `git add migrations/016_collapse_group_types.sql`
   `git commit -m "migration: collapse Round->Metapuzzle, Metaless Round->Group (finding #1)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

## Acceptance criteria (whole task)

- Stored `tag_group.type` is one of `{Metapuzzle, Capstone, Runaround, Group}`; `Round`/`Metaless Round`
  appear nowhere in `bot/` except `forum_config.py`'s defensive branch.
- `!r`/`!round` and the web "Round" option both persist `Metapuzzle` with a `"<name> - meta"` meta.
- `delete_round`, `show_settings`/`update_settings`, `!list_puzzles`, and `!info` all work in `Capstone`,
  `Runaround`, and `Group` channels.
- `!roundnometa`/`!rnm` are not registered; `!group` still creates a no-meta `Group`.
- Full suite green; `ruff check` clean (finding #6 cleared); `docs/commands.md` matches the commands.
