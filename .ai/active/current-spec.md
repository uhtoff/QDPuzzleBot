# Current Spec — M3.5 Discord channel-adapter extraction

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot/.claude/worktrees/working-1
Branch: feature/m3.5-channel-adapter (off local main; spec committed at 22ddc7f)
Target model per phase: Phase 1 (tests) = Opus · Phase 2–6 (implementation) = Sonnet
Started: 2026-06-23
Status: ready for handoff — Phase 1 (tests)

Design doc (authoritative background): `docs/superpowers/specs/2026-06-23-m3.5-channel-adapter-design.md`

---

## Before you start (executing model)

- Execute **only the current phase**. Do not skip ahead.
- On any **unexpected** failure (not "class doesn't exist yet"): invoke
  `superpowers:systematic-debugging` BEFORE changing anything.
- Before claiming a phase done: invoke `superpowers:verification-before-completion`,
  run the phase's acceptance command, and paste its output.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood
  fix, write `.ai/active/blocker.md` (what you tried, the exact error, your hypothesis)
  and STOP.
- Commit at each phase boundary. End commit messages with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## Test command (this project)

Single statement, wrapped in `timeout`, output to a file. `pipenv run python -m pytest`
(plain `pytest` fails — repo root not on `sys.path`). Tests need a reachable MySQL
`puzzlebot_test` (creds in gitignored `config.json`) because `bot/store/__init__.py`
opens the DB at import.

- One file: `timeout 120 pipenv run python -m pytest tests/test_FOO.py -q > /tmp/test.log 2>&1`
- Full gate: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`

A killed run exits 124 (read the log). If a multi-file run hangs, isolate per file under
`timeout`.

## Goal

Extract the ctx-free Discord channel/category helpers into `bot/core/discord_channels.py`,
introduce an injected `Notifier` (`bot/core/notifier.py`) so per-channel Discord
notifications fire regardless of caller, promote `create_puzzle`/`create_round` onto
`PuzzleService`, and full-strangle the bang create paths onto them — deleting the
divergent twins.

## In scope

- `bot/core/discord_channels.py` (new), `bot/core/notifier.py` (new).
- `bot/core/puzzle_service.py` — add `create_puzzle`, `create_round`, relocated
  `_cleanup_orphan_puzzle`.
- `bot/cogs/puzzles.py` — delegate helpers; strangle `web_create_puzzle`/`web_create_round`/
  `create_puzzle_channel`/`_round_impl`; delete divergent twins; make `send_initial_*`
  ctx-free; remove `_web_render_info` synthetic-ctx hack and `web_refresh_meta` wrapper.
- `bot/web/actions.py` — thin calls into the service (pass a `DiscordNotifier`).
- New tests + updates to `tests/test_create_orphan_cleanup.py` patch targets.
- `docs/PLANS.md` — mark M3.5 done.

## Out of scope (do NOT do)

- No `WebNotifier`/dashboard-push implementation (designed-for only; build `DiscordNotifier`
  + `NullNotifier` only).
- No schema/migration change (none needed).
- No second `position_lock` — keep the single shared instance.
- Do not touch M5/M6/M7 rollup paths, unrelated cog commands, or the SPA frontend.
- Do not change orphan-cleanup / rollback *semantics* — only their location.

## Relevant files

- `bot/cogs/puzzles.py` — source of all relocated logic. Key lines:
  `clean_name :267`, `get_or_create_channel :1689`, `get_solve_divider_position :927`,
  `get_first_channel :920`, `SOLVE_DIVIDER :77`/`SOLVE_CATEGORY :76`, `position_lock :105`,
  `_web_render_info :670`, `web_refresh_info :708`, `web_create_puzzle :731`,
  `_cleanup_orphan_puzzle :803`, `web_create_round :820`, `web_refresh_meta :656`,
  `create_puzzle_channel :1078`, `_round_impl :1317`,
  `send_initial_* :1713/:1826/:1865/:1898/:1938`, `_add_puzzle_links_field :1761`,
  `_build_embed_list :1801`, `_overview_url :2207`, `get_puzzle_sheet :316`.
- `bot/core/puzzle_service.py` — pattern to mirror (`solve`/`move_to_meta` orchestration).
- `bot/web/actions.py` — create call sites: `web_create_puzzle :593`, `web_create_round :623`,
  `web_refresh_info :202/:237/:274`, `web_post_solved_embed :325`.
- `tests/conftest.py` (read only) — `make_puzzle`, `make_round`, `make_hunt` helpers.
- `tests/test_create_orphan_cleanup.py` — existing cleanup tests; **patch targets must move**
  (see Phase 4 step). Read its `_hunt()`/`_cog_with_sheets()` helpers — reuse that style.
- `docs/PLANS.md` — M3.5 entry at line ~430.

## Constraints

- Touch only files in the lists above.
- The service calls the **module function** `discord_channels.get_or_create_channel(...)`,
  NOT `self.get_or_create_channel`. Any test exercising the service therefore patches
  `bot.core.discord_channels.get_or_create_channel` — not a cog attribute.
- If a phase changes a function signature, grep every caller and update it. Before deleting
  a twin, grep its name across `bot/` to confirm no other caller.
- `send_initial_*` currently read data from `self.environment[ctx.message.id]` with kwargs
  fallback. The kwargs path already works (web uses it). Keep the kwargs path; remove only
  the ctx fallback once nothing relies on it.

## Test constraints (inlined — you do not read global instructions)

- **Reuse exact helpers/seams; invent none.** Use `tests/conftest.py` `make_puzzle`/
  `make_round` and the `_hunt()`/`_cog_with_sheets()` style from
  `tests/test_create_orphan_cleanup.py`. Give each new test file its **own** uniquely-named
  local helpers — never import a top-level function from a sibling test file (the parallel
  gate shards by file → "undefined function" fatal).
- **NEVER write a loop that resolves state "until" a condition.** If you think you need one,
  STOP and write `blocker.md`.
- **NEVER modify test infrastructure or stub randomness** unless a numbered step says so.
- **Confirm every attribute/field exists before use** — grep `bot/store/puzzle_data.py`,
  `round_data.py`, `hunt_data.py`. Do not guess field names (e.g. `google_page_id`,
  `channel_id`, `category_id`, `meta_code`, `meta_id` are real; verify others).
- **When asserting rendered text**, assert the rendered string AND the absence of the wrong
  output — not just structure.
- **Wrap every run in `timeout`, output to a file, single statement** (no `; echo` tail).
- A test failing because `bot.core.discord_channels` / `bot.core.notifier` doesn't exist, or
  `service.create_puzzle` is missing, is the RIGHT reason. An `ImportError` for a *store*
  symbol, a missing DB column, or an `AttributeError` on a real model field means a wrong
  assumption in the test — fix the test, don't add scaffolding.

---

## Phase 1 — write ALL failing tests (model: Opus). NO production code.

Write the four test files below, commit them, and confirm each fails for the right reason.
Skeletons are near-verbatim; fill mock wiring mechanically, reusing the cited helpers.

### File: `tests/test_discord_channels.py` (pure helpers, new module)

```python
"""M3.5: pure channel-adapter helpers, ctx-free."""
from unittest.mock import MagicMock
import pytest
from bot.core import discord_channels   # <-- does not exist yet → right reason


def test_clean_name_lowercases_and_hyphenates():
    assert discord_channels.clean_name("  Big Red Box ") == "big-red-box"

def test_clean_name_strips_matching_quotes():
    assert discord_channels.clean_name('"Quote Me"') == "quote-me"

def test_solve_divider_position_returns_index_when_present():
    cat = MagicMock()
    ch = MagicMock(); ch.name = discord_channels.SOLVE_DIVIDER; ch.position = 7
    cat.channels = [ch]
    assert discord_channels.solve_divider_position(cat) == 7   # confirm exact return vs source :927

def test_solve_divider_position_returns_false_when_absent():
    cat = MagicMock()
    other = MagicMock(); other.name = "general"
    cat.channels = [other]
    assert discord_channels.solve_divider_position(cat) is False
```
> Before finalising the divider assertions, OPEN `get_solve_divider_position` (`:927`) and
> `get_first_channel` (`:920`) and pin the exact return values/branches. Match them.

### File: `tests/test_notifier.py` (Notifier contract)

```python
"""M3.5: Notifier interface + NullNotifier no-op contract."""
import pytest
from bot.core.notifier import Notifier, NullNotifier, DiscordNotifier   # <-- new → right reason


@pytest.mark.asyncio
async def test_null_notifier_methods_are_noops():
    n = NullNotifier()
    # Each interface method awaits cleanly and returns None.
    assert await n.welcome(guild=None, hunt=None, hunt_round=None, puzzle=None, channel=None) is None
    assert await n.refresh_info(guild=None, hunt=None, puzzle=None) is None
    assert await n.puzzle_solved(guild=None, hunt=None, puzzle=None) is None

def test_discord_notifier_implements_interface():
    assert issubclass(DiscordNotifier, Notifier)
```
> OPEN `web_refresh_info`/`_web_render_info`/`web_post_solved_embed` to pin the exact method
> arg shapes before locking these signatures; keep them identical to the relocated callers.

### File: `tests/test_create_service.py` (service create + cleanup)

```python
"""M3.5: PuzzleService.create_puzzle/create_round orchestration + cleanup."""
from unittest.mock import AsyncMock, MagicMock, patch
import discord, pytest
from bot.core.puzzle_service import PuzzleService
from bot.store.hunt_data import HuntData
from tests.conftest import make_round

def _hunt():
    h = HuntData(); h.id = 10; h.google_sheet_id = "S1"
    h.archive_google_sheet_id = "A1"; h.url = None; return h

def _service_with_sheets():
    gsheet = MagicMock()
    gsheet.create_puzzle_spreadsheet = AsyncMock()
    gsheet.delete_puzzle_spreadsheet = AsyncMock()
    bot = MagicMock(); bot.get_cog.return_value = gsheet
    return PuzzleService(bot), gsheet

@pytest.mark.asyncio
async def test_create_puzzle_commits_row_creates_sheet_and_notifies():
    svc, gsheet = _service_with_sheets()
    guild = MagicMock(); guild.channels = []; cat = MagicMock(id=999); guild.categories = [cat]
    notifier = MagicMock(); notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.discord_channels.get_or_create_channel",
               AsyncMock(return_value=(MagicMock(id=222, mention="<#222>", name="newpuz"), True))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        await svc.create_puzzle(guild, _hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier)
    gsheet.create_puzzle_spreadsheet.assert_awaited_once()
    notifier.welcome.assert_awaited_once()

@pytest.mark.asyncio
async def test_create_puzzle_orphan_cleanup_on_channel_failure():
    # Relocated from test_create_orphan_cleanup.py — now targets the SERVICE.
    svc, gsheet = _service_with_sheets()
    guild = MagicMock(); guild.channels = []; guild.categories = [MagicMock(id=999)]
    notifier = MagicMock(); notifier.welcome = AsyncMock()
    round_data = make_round(id=5, category_id=999)
    with patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.discord_channels.get_or_create_channel",
               AsyncMock(side_effect=discord.HTTPException(MagicMock(), "boom"))):
        pdb.commit.side_effect = lambda p: setattr(p, "id", 1)
        with pytest.raises(discord.HTTPException):
            await svc.create_puzzle(guild, _hunt(), round_data, "Newpuz", is_meta=False, notifier=notifier)
    gsheet.delete_puzzle_spreadsheet.assert_awaited_once()
    pdb.delete.assert_called_once_with(1)

@pytest.mark.asyncio
async def test_create_round_creates_round_and_meta_puzzle():
    svc, gsheet = _service_with_sheets()
    # Mirror test_create_orphan_cleanup.py's category-clone mock wiring; assert a RoundData
    # + meta PuzzleData are returned and meta_code/meta_id are populated.
    ...   # fill from web_create_round (:820) behaviour; OPEN it and match assertions.

@pytest.mark.asyncio
async def test_create_round_rolls_back_on_failure():
    ...   # assert round row deleted + orphan puzzle cleaned + category deleted on late failure.
```
> The two `...` tests: OPEN `web_create_round` (`:820-891`) and reuse the exact mock wiring
> already present in `tests/test_create_orphan_cleanup.py::test_web_create_round_cleans_up_*`
> (clone → edit → delete AsyncMocks). Target `svc.create_round(...)`, not the cog.

### File: `tests/test_create_equivalence.py` (bang ≡ web create)

```python
"""M3.5: bang and web create paths produce identical core effects."""
# Both edges must call service.create_puzzle with the same DB/sheet/channel effects.
# Assert via a recording NullNotifier-style spy that both paths reach the service once
# with equivalent args. OPEN _round_impl (:1317) + web_create_round (:820) to pin the
# arg shape before asserting. Use uniquely-named local helpers in THIS file.
```

**Phase 1 acceptance** (must fail for the right reason, AND load under the real runner):
```
timeout 120 pipenv run python -m pytest tests/test_discord_channels.py tests/test_notifier.py tests/test_create_service.py tests/test_create_equivalence.py -q > /tmp/test.log 2>&1
```
- Exit non-zero; failures are `ModuleNotFoundError: bot.core.discord_channels` /
  `bot.core.notifier`, or `AttributeError: 'PuzzleService' object has no attribute
  'create_puzzle'/'create_round'`.
- NO collection/bootstrap fatal (no "undefined function", no store ImportError). If you see
  one, the test is wrong — fix the test.
Commit: `M3.5 Phase 1: failing tests for channel adapter + notifier + service create`.

---

## Phase 2 — extract `bot/core/discord_channels.py` (model: Sonnet)

1. Create `bot/core/discord_channels.py` with module-level, ctx-free functions/constants:
   `clean_name`, `get_or_create_channel`, `solve_divider_position`, `first_channel_position`,
   `clone_category_for_round`, and constants `SOLVE_DIVIDER`, `SOLVE_CATEGORY`,
   `MAX_CHANNELS_PER_GUILD`, `MAX_CHANNELS_PER_CATEGORY`, `MAX_CATEGORIES`. Copy bodies
   verbatim from the cited cog lines, replacing `self.X`/`ctx` usage with explicit params.
   `position_lock` stays on the cog/service (single instance) and is passed where needed.
2. In `bot/cogs/puzzles.py`, replace the bodies of `clean_name`, `get_or_create_channel`,
   `get_solve_divider_position`, `get_first_channel` with thin delegators to the module
   functions (keep the method names so existing `self.` call sites and patches still resolve).
3. Acceptance:
   `timeout 120 pipenv run python -m pytest tests/test_discord_channels.py -q > /tmp/test.log 2>&1`
   passes, AND the full gate
   `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` stays green.
Commit: `M3.5 Phase 2: extract ctx-free discord_channels adapter`.

## Phase 3 — notifier + ctx-free render (model: Sonnet)

1. Decouple the render: change `_add_puzzle_links_field`, `_overview_url`, `_build_embed_list`,
   `get_puzzle_sheet` (and any helper they call, e.g. `create_additional_sheet_list`,
   `get_guild_data`) to accept `hunt`/`guild_data`/`gsheet_cog` explicitly instead of `ctx`.
   Update the `send_initial_*` handlers to pass them. Grep every caller and update.
2. Create `bot/core/notifier.py`: `Notifier` ABC (`welcome`, `refresh_info`, `puzzle_solved`),
   `NullNotifier` (no-ops), `DiscordNotifier(bot)` whose methods call the now-ctx-free render
   (move the `send_initial_*` invocation logic here, or call into the cog's ctx-free methods).
3. Re-express `web_refresh_info` to delegate to `DiscordNotifier.refresh_info`; delete the
   `_web_render_info` synthetic-ctx hack and its `_next_web_ctx_id`/environment usage.
4. Acceptance: `tests/test_notifier.py` passes; full gate green.
Commit: `M3.5 Phase 3: injected Notifier + ctx-free pinned-info render`.

## Phase 4 — service create + web strangle (model: Sonnet)

1. Add `create_puzzle(guild, hunt, round_data, name, is_meta, notifier)` and
   `create_round(guild, hunt, name, notifier)` to `PuzzleService`, plus relocated
   `_cleanup_orphan_puzzle`. Bodies mirror `web_create_puzzle` (`:731`) / `web_create_round`
   (`:820`) but call `discord_channels.*` module functions and `notifier.welcome(...)`.
2. Reduce `web_create_puzzle`/`web_create_round` on the cog to thin delegators (or update
   `bot/web/actions.py:593/:623` to call `cog.service.create_*` directly with a
   `DiscordNotifier(cog.bot)`). Remove the `web_refresh_meta` swallowing wrapper (`:656`);
   callers use `service.refresh_meta` with edge tolerance.
3. **Repoint existing tests:** in `tests/test_create_orphan_cleanup.py`, change the patch
   target from `cog.get_or_create_channel` / `bot.cogs.puzzles.PuzzleJsonDb` to
   `bot.core.discord_channels.get_or_create_channel` / `bot.core.puzzle_service.PuzzleJsonDb`
   to match where cleanup now runs (or delete tests fully superseded by `test_create_service.py`
   — keep coverage, don't drop it).
4. Acceptance: `tests/test_create_service.py` + `tests/test_create_orphan_cleanup.py` pass;
   full gate green.
Commit: `M3.5 Phase 4: PuzzleService.create_puzzle/create_round + web strangle`.

## Phase 5 — bang strangle + delete twins (model: Sonnet)

1. Rewrite `create_puzzle_channel` (`:1078`) and `_round_impl` (`:1317`) to call
   `service.create_puzzle`/`create_round` (passing a `DiscordNotifier`), keeping only the
   bang-specific `ctx.send(...)` invoker acks and `command_map` routing.
2. Delete the now-divergent duplicate orchestration. Before deleting any helper, grep its name
   across `bot/` to confirm no remaining caller.
3. Acceptance: `tests/test_create_equivalence.py` passes; full gate green; manually re-read the
   bang path diff to confirm the `ctx.send` acks survived.
Commit: `M3.5 Phase 5: strangle bang create paths, delete twins`.

## Phase 6 — docs (model: Sonnet)

1. In `docs/PLANS.md`, mark the M3.5 entry ✅ DONE with the branch/commit, mirroring the M3
   entry's style.
2. Acceptance: full gate green:
   `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1`.
Commit: `M3.5 Phase 6: mark milestone done in PLANS.md`.

## Verification (whole task)

Full suite green; bang `!p`/`!mp`/`!r` and dashboard create both route through
`service.create_*`; pinned-info/welcome fire via `DiscordNotifier` regardless of caller;
no synthetic-ctx hack remains; divergent twins deleted.

## Rollback / recovery notes

Each phase is an isolated commit on `feature/m3.5-channel-adapter`. To back out a phase,
`git revert` its commit. If blocked, write `.ai/active/blocker.md` and stop — do not
half-strangle a path (leave it fully on the old twin or fully on the service).
