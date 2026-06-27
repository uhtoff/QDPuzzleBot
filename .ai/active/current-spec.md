# Current Spec — Group channel + forum config

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Target model — Phase 1 (tests): Opus 4.8 · Phases 2–4 (implementation): Sonnet 4.6
Started: 2026-06-27
Status: ready for handoff — Phase 1 (tests)

Design doc (authoritative background): `docs/superpowers/specs/2026-06-27-group-channel-and-forum-config-design.md`.
Already-landed prerequisites (do not redo): commits `912d8e7`, `de6359b`, `4586d3b`
(forum meta classification, web welcome thread resolve, web Round-suffix/Runaround parity).

## Before you start (executing model)

- Execute **only the current phase**. Phase 1 writes ALL failing tests and commits them
  with NO production code. Phases 2–4 implement one feature each.
- On any **unexpected** failure (runtime/import fatal, missing attr, a test failing for a
  reason other than "the thing isn't built yet"), invoke `superpowers:systematic-debugging`
  BEFORE changing anything. A wrong assumption in a *test* is fixed in the test, not by
  adding scaffolding.
- Before claiming a phase done, invoke `superpowers:verification-before-completion` and
  paste the acceptance command output into your report.
- **Two-strikes rule:** if one acceptance command fails twice without a clear, understood
  fix, write `.ai/active/blocker.md` (what you tried, exact output, your hypothesis) and
  STOP.
- Test runner is **always**: `timeout 180 pipenv run python -m pytest <target> -q > /tmp/test.log 2>&1`
  then read `/tmp/test.log`. Single statement, no `; echo` tail. A killed run exits 124.

## Goal

1. **Group** becomes the canonical general-channel group type (successor to `Metaless
   Round`): it creates a general text channel with a welcome and **no target puzzle**.
2. **Forum channels** (forum-mode hunts) are configured on creation — guidelines (rules) =
   rendered welcome, six available tags, 👍 default emoji — kept in sync, with tags
   auto-applied to posts.
3. **All welcomes are posted by the service** (one source of truth); web stops posting
   manually. This also fixes a latent blank-`group_code` bug in the meta welcome.

## In scope

- `bot/core/puzzle_service.py`, `bot/core/notifier.py`, `bot/cogs/puzzles.py`,
  `bot/web/actions.py`, and a new `bot/core/forum_config.py` helper module.
- New/extended tests only under `tests/`.

## Out of scope — DO NOT TOUCH

- Membership/move semantics (`!move_to_*`), thread reparenting/copy-delete.
- Any DB schema / migration / enum backing value (no migration is needed — `category_id`
  is reused).
- The `web_*` legacy twins; rollup/sheet logic; permission overwrite code (set manually).
- Reworking welcome **wording** beyond adding a plain-text render and the Group copy.

## Relevant files

- `bot/core/puzzle_service.py` (read+write) — `create_tag_group` @1488, `create_puzzle`
  @1375 (welcome @1441/@1463), `group_code` gen @1554, forum branch @1526, `delete_round`
  @993, `is_metameta` @72, `TYPE_DEFAULTS` @85, `get_minis` @1247, `convert_to_type` @652,
  `solve` @785, `unsolve` @858.
- `bot/core/notifier.py` (read+write) — `DiscordNotifier.welcome` @91 (handler choice by
  `puzzle.is_target` @117), `refresh_info` @127.
- `bot/cogs/puzzles.py` (read+write) — `cog_before_invoke` @117 (thread branch @131,
  `'Group'` branch @152, `'Puzzle'` branch @159), `send_initial_metaless_round_channel_messages`
  @2109, `send_initial_metapuzzle_channel_messages` @2070, `!info` dispatch @2250,
  `command_map` @1485.
- `bot/core/discord_channels.py` (read only) — `get_or_create_channel(guild, category,
  name, "text") -> (channel, created)` @59, `clean_name` @24, `create_forum_post` @32.
- `bot/web/actions.py` (read+write) — `create_meta` route @1491 (call @1518, manual welcome
  @1522–1531).
- `bot/core/forum_config.py` (new) — render + tag helpers.
- Tests: `tests/test_forum_layout.py` (`_forum_svc`/`_forum_hunt` helpers), `tests/test_edge_web.py`
  (web Quart harness, `_ASYNC_SERVICE_METHODS` @28), `tests/test_minimeta_service.py`
  (`cog_before_invoke` thread test pattern), `tests/conftest.py` (`make_hunt`, `make_round`
  → `TagGroupData`, `make_puzzle`, `make_cog`).

## Constraints

- **Do not** touch files outside the in-scope list. No refactors beyond what a numbered
  step says. Match existing patterns.
- `create_tag_group`/`create_puzzle` keep their current signatures. If you must change a
  signature, grep every call site first (`grep -rn "create_tag_group\|create_puzzle" bot/`).
  Known callers: `bot/cogs/puzzles.py:1502`, `bot/web/actions.py:1518`.
- Forum-only behavior (Phases 3–4) MUST be guarded by `hunt.layout_mode == "forum"`.

## Test constraints (READ — these are the rules; you do not see global instructions)

- **Reuse exact helpers/seams; invent none.** Service tests mirror `tests/test_forum_layout.py`
  (`_forum_svc`, `_forum_hunt`); web tests mirror `tests/test_edge_web.py`; cog classification
  tests mirror `tests/test_minimeta_service.py::TestCogBeforeInvokeThread`. Factories live in
  `tests/conftest.py` — import them; they are NOT fixtures.
- **Each test file defines or imports every helper it calls.** Give NEW files uniquely-named
  local helpers (e.g. `_grp_svc`, `_cfg_forum`). Never call a top-level function defined in a
  sibling test file — `--filter` hides the gap; the suite shards by file and it becomes an
  "undefined function" fatal.
- **Patch DAOs at the service import site:** `patch("bot.core.puzzle_service.TagGroupJsonDb")`,
  `...PuzzleJsonDb`, `...HuntJsonDb` — not `bot.store`.
- **Simulate INSERT id:** `pdb.commit.side_effect = lambda o: setattr(o, "id", 1)` (and
  similarly `rdb.commit.side_effect = lambda r: setattr(r, "id", 5)`).
- **Mock Discord by hand:** `AsyncMock()` for awaitables (`guild.create_forum`,
  `category.clone`, `channel.edit`, `forum.edit`, `thread.edit`), `MagicMock()` for sync;
  set `.id`/`.name`/`.position` explicitly (a bare `MagicMock().name` is a Mock, not a str).
- **`asyncio_mode = auto`** — `async def test_*` runs without a decorator. But a test that
  itself calls `asyncio.run(...)` (the `cog_before_invoke` pattern) must be a **plain `def`**
  (see `test_minimeta_service.py`), never `async def`.
- **Confirm every attribute/payload key before use** — grep the dataclass/handler. Do not
  guess field names. `TagGroupData` has `.type`, `.category_id`, `.target_puzzle_id`,
  `.group_code`, `.name`, `.hunt_id`. `PuzzleData` has `.is_target`, `.solved`, `.tags`,
  `.tag_edges`, `.channel_id`, `.parent_puzzle_id`.
- **When asserting rendered text, assert the substring present AND the wrong text absent.**
- **Never write a loop that resolves state "until" a condition.** If tempted, write
  `blocker.md` and STOP.
- **Do not modify test infrastructure** (conftest, pytest.ini) except the one explicit step
  that adds `set_source_mode`-style names — there is none here; if you think you need a
  conftest change, write `blocker.md` and STOP.
- Every run wrapped in `timeout`, output to a file, single statement.

---

## Phase 1 — write ALL failing tests (Opus 4.8). No production code.

Create the tests below, commit them, and confirm each fails for the RIGHT reason
(target symbol/behavior not built yet — NOT an import/runtime fatal from a wrong
assumption). Acceptance at the bottom of this phase.

### 1A · Group service — extend `tests/test_forum_layout.py`

Reuse `_forum_svc()` and `_forum_hunt()` already in that file. For a channel-mode hunt,
build a local `_channel_hunt()` (copy `_forum_hunt` but `layout_mode = "channel"`).

```python
@pytest.mark.asyncio
async def test_group_forum_mode_creates_plain_text_channel_no_forum():
    svc = _forum_svc()
    hunt_category = MagicMock(id=1, position=0); hunt_category.name = "H"
    guild = MagicMock(); guild.categories = [hunt_category]; guild.channels = []
    guild.create_forum = AsyncMock()                       # must NOT be called for a Group
    text_channel = MagicMock(id=4242); text_channel.mention = "<#4242>"; text_channel.name = "grp"
    notifier = MagicMock(); notifier.welcome = AsyncMock()
    with patch("bot.core.puzzle_service.TagGroupJsonDb") as rdb, \
         patch("bot.core.puzzle_service.PuzzleJsonDb") as pdb, \
         patch("bot.core.puzzle_service.get_or_create_channel",
               AsyncMock(return_value=(text_channel, True))):
        rdb.check_duplicates_in_hunt.return_value = False
        rdb.commit.side_effect = lambda r: setattr(r, "id", 5)
        rdb.generate_uid.return_value = "G1"
        new_round, puzzle = await svc.create_tag_group(
            guild, _forum_hunt(), "Grp", notifier=notifier,
            meta_puzzle_name=False, round_type="Group",
        )
    guild.create_forum.assert_not_awaited()                 # no forum for a Group
    assert puzzle is None                                    # no target puzzle
    pdb.commit.assert_not_called()                           # no PuzzleData
    assert new_round.category_id == 4242                     # group channel id stored
    notifier.welcome.assert_awaited_once()                  # group welcome posted
    # welcome posted for the GROUP (no puzzle):
    assert notifier.welcome.await_args.kwargs.get("puzzle") is None or \
           notifier.welcome.await_args.args[3] is None
```

```python
@pytest.mark.asyncio
async def test_group_channel_mode_clones_category_and_adds_one_channel():
    # channel-mode Group: ref_category.clone() called; one text channel created; no meta.
    # Mirror test_create_tag_group_forum_creates_forum_under_hunt_category but layout_mode
    # = "channel" and round_type="Group", meta_puzzle_name=False. Assert:
    #   hunt_category.clone.assert_awaited_once()
    #   guild.create_forum NOT awaited
    #   pdb.commit.assert_not_called()
    #   notifier.welcome.assert_awaited_once()
```

```python
@pytest.mark.asyncio
async def test_meta_welcome_has_populated_group_code():
    # Regression for the ordering bug: group_code must be set BEFORE the welcome fires.
    # create a Metapuzzle (meta_puzzle_name="M", round_type="Metapuzzle") in forum mode.
    # Capture group_code at welcome time:
    seen = {}
    notifier = MagicMock()
    async def _cap(*a, **k):
        tg = k.get("tag_group") or a[2]
        seen["code"] = tg.group_code
    notifier.welcome = AsyncMock(side_effect=_cap)
    # ... wire create_forum + create_forum_post + get_or_create_channel like the existing
    # forum create_puzzle test; rdb.generate_uid.return_value = "GC9".
    # After the call:
    assert seen["code"] == "GC9"        # NOT None/'' — proves group_code set before welcome
```

### 1B · Group classification + `!info` — NEW file `tests/test_group_channel_type.py`

Mirror `tests/test_minimeta_service.py::TestCogBeforeInvokeThread` exactly (plain `def`,
`asyncio.run`, patch `bot.cogs.puzzles.{GuildSettingsDb,PuzzleJsonDb,HuntJsonDb,TagGroupJsonDb}`).
Local helper `_grp_thread_ctx(channel_id)` and `_grp_text_ctx(channel_id, category_id)`.

- `test_forum_group_channel_classifies_as_group_by_channel_id`: a forum-mode group's
  general channel is a normal text channel whose **own id** is the group's `category_id`.
  `GuildSettingsDb.get_channel_type` returns `'Group'` for `ctx.channel.id`. Assert
  `cog.environment[mid]["channel_type"] == "Group"` and `tag_group` is the group — proving
  the `'Group'` branch looks up by `ctx.channel.id` (currently it only uses
  `ctx.channel.category.id`, so this FAILS until implemented).
- `test_channel_group_classifies_via_category`: channel-mode group channel (its `.category.id`
  is the group's `category_id`); `get_channel_type(channel.id)`→None, `(category.id)`→`'Group'`.
  Assert `channel_type == "Group"`.
- `test_legacy_metaless_round_type_routes_to_group`: a group row with `type == "Metaless
  Round"` still resolves; assert the cog treats it as the group handler path (channel_type
  ends up `"Group"` or the legacy string is normalized — pin to: `cog.environment[mid]
  ["tag_group"].type in ("Group", "Metaless Round")` AND a follow-up `!info` routes it to
  `send_initial_group_channel_messages`, see next test).

`!info` dispatch test (same file, uses `make_cog`):
- `test_info_group_calls_group_handler`: stub `cog.get_channel_type = lambda ctx: "Group"`,
  `cog.send_initial_group_channel_messages = AsyncMock()`, run `await cog.info(ctx)`; assert
  `send_initial_group_channel_messages.assert_awaited_once()`. Add a second case for
  `"Metaless Round"` → same handler. FAILS now: `send_initial_group_channel_messages` does
  not exist and `!info` has no Group branch.

### 1C · Web parity — extend `tests/test_edge_web.py`

Reuse the `web` fixture. Note `_build_cog` sets `create_tag_group` to return
`(make_round(id=5), make_puzzle(id=9, channel_id=0))`; for the Group case override per-test
to return `(make_round(id=5, target_puzzle_id=0), None)`.

```python
@pytest.mark.asyncio
async def test_create_meta_group_makes_no_meta(web):
    web.svc.create_tag_group = AsyncMock(return_value=(make_round(id=5), None))
    resp = await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Group"})
    assert resp.status_code == 200
    kwargs = web.svc.create_tag_group.call_args.kwargs
    assert kwargs.get("meta_puzzle_name") is False          # no target puzzle
    assert kwargs.get("round_type") == "Group"
    assert kwargs.get("notifier") is not None               # service posts the welcome
```

```python
@pytest.mark.asyncio
async def test_create_meta_passes_notifier_for_all_types(web):
    # The web route no longer posts welcomes manually — it hands a real notifier to the
    # service for EVERY type. (Was notifier=None previously.)
    await web.client.post("/api/hunts/U1/metas", json={"name": "Foo", "type": "Metapuzzle"})
    assert web.svc.create_tag_group.call_args.kwargs.get("notifier") is not None
```

NOTE: `Group` must be accepted by the route. `TYPE_DEFAULTS` already contains `"Group"`, so
the existing `bad_type` guard passes it. Confirm by grep before relying on it.

### 1D · Forum config — NEW file `tests/test_forum_config.py`

Local helpers `_cfg_svc()` (copy `_forum_svc`) and `_cfg_forum_hunt()` (copy `_forum_hunt`).

- `test_create_forum_sets_tags_emoji_and_topic`: build the forum-mode meta-create path
  (mirror `test_create_tag_group_forum_creates_forum_under_hunt_category`), but assert the
  `guild.create_forum` call kwargs:
  - `available_tags` is a list of 6 `discord.ForumTag` whose `.name`s ==
    `{"solved","meta","capstone","metameta","runaround","minimeta"}`.
  - `default_reaction_emoji` resolves to 👍 (assert the unicode "👍" appears — accept either
    a `str` or a `PartialEmoji` whose `.name == "👍"`).
  - `topic` is a non-empty `str`.
  Confirm the exact param names by reading discord.py's `Guild.create_forum` signature in the
  installed package BEFORE asserting (`pipenv run python -c "import discord,inspect;
  print(inspect.signature(discord.Guild.create_forum))"`). If a name differs, fix the TEST.
- `test_render_welcome_plaintext_group`: `from bot.core.forum_config import
  render_welcome_plaintext`; render for a Group; assert the string contains the group name
  and `group_code`, and a bang-command bullet, AND does NOT contain `discord.Embed`-only
  artifacts (assert `"Embed" not in out`). Add `_meta` and `_capstone` variants.
- `test_sync_forum_guidelines_edits_topic_when_changed`: `from bot.core.forum_config import
  sync_forum_guidelines`; a forum `MagicMock` with `.topic="old"`, `.edit=AsyncMock()`;
  `guild.get_channel.return_value = forum`; call it; assert `forum.edit` awaited with a
  `topic=` kwarg. Second case: `.topic` already equals the rendered text → `forum.edit`
  NOT awaited.

All FAIL now: `bot/core/forum_config.py` does not exist and `create_forum` sets no config.

### 1E · Auto-apply tags — NEW file `tests/test_forum_tags.py`

Local helpers only. `from bot.core.forum_config import desired_tags_for, apply_forum_tags`.

- `test_desired_tags_truth_table` — parametrize over `make_round`/`make_puzzle` inputs and
  assert the EXACT structural tag by precedence `runaround → metameta → meta → minimeta →
  capstone`, plus `solved`:
  - Runaround group, solved target → `{"runaround","solved"}`.
  - metameta True → `{"metameta"}` (give `is_metameta` inputs that return True, OR pass a
    pre-computed flag — see signature note).
  - Metapuzzle/Round target, fed → `{"meta"}`.
  - minimeta parent (has minis), not a meta type → `{"minimeta"}`.
  - Capstone group → `{"capstone"}`.
  - feeder puzzle (`is_target=False`, no group) → `{"solved"}` iff solved else `set()`.
  SIGNATURE: define `desired_tags_for(group, puzzle, *, metameta: bool, minimeta: bool)
  -> set[str]` — pass `metameta`/`minimeta` as booleans so the unit is pure (the caller
  computes them via `is_metameta(...)` and `get_minis(...)`). Confirm this is the signature
  you implement in Phase 4; the test pins it.
- `test_apply_forum_tags_maps_names_and_skips_noop`: a `forum` with `available_tags =
  [FakeTag("meta"), FakeTag("solved"), ...]` (local `FakeTag` namedtuple with `.name`); a
  `thread = MagicMock(applied_tags=[], edit=AsyncMock())`. Call `apply_forum_tags(thread,
  forum, {"meta"})`; assert `thread.edit` awaited once with `applied_tags=[<meta tag>]`.
  Second call where `thread.applied_tags` already equals the desired set → `thread.edit`
  NOT awaited.

### Phase 1 acceptance

1. `timeout 180 pipenv run python -m pytest tests/test_forum_layout.py tests/test_group_channel_type.py tests/test_edge_web.py tests/test_forum_config.py tests/test_forum_tags.py -q > /tmp/test.log 2>&1`
   — exits non-zero; read `/tmp/test.log`.
2. Every NEW test must fail for the RIGHT reason: an `AttributeError`/`ImportError` for a
   not-yet-built symbol (`forum_config`, `render_welcome_plaintext`, `desired_tags_for`,
   `apply_forum_tags`, `send_initial_group_channel_messages`) or an assertion on
   not-yet-implemented behavior — NOT a Discord/runtime fatal from a wrong test assumption.
   For any test that errors on a wrong assumption (bad kwarg name, missing attr you guessed),
   FIX THE TEST.
3. The whole suite still collects without a bootstrap/import fatal:
   `timeout 180 pipenv run python -m pytest tests/ -q --co > /tmp/co.log 2>&1` exits 0.
4. Commit: `git add tests/ && git commit -m "test: failing tests for Group channel + forum config (phases 1-3)"`.
5. Do NOT write production code in this phase.

---

## Phase 2 — implement Group + service-owned welcomes (Sonnet 4.6)

Makes 1A/1B/1C pass.

1. `bot/core/puzzle_service.py` `create_tag_group` (@1488):
   - Move the `new_round.group_code = TagGroupJsonDb.generate_uid(...)` line to BEFORE the
     `if create_meta:` block (so the meta welcome renders a populated code). Keep the final
     `TagGroupJsonDb.commit(new_round)`.
   - Add a Group path for `round_type == "Group"` (also accept legacy `"Metaless Round"`):
     do NOT create a forum and do NOT call `create_puzzle`. Resolve the host category:
     channel mode → the cloned `new_category`; forum mode → `ref_category`. Create the
     general channel via `get_or_create_channel(guild=guild, category=<host>,
     channel_name=clean_name(name), channel_type="text")`; set `new_round.category_id =
     channel.id` in forum mode (channel mode keeps the cloned-category id). If `notifier`
     is not None: `await notifier.welcome(guild, hunt, tag_group=new_round, puzzle=None,
     channel=channel)`. Return `(new_round, None)`.
   - Extend the existing `except` rollback to delete the group's text channel (and the
     cloned category in channel mode).
2. `bot/core/notifier.py` `welcome` (@91): when `puzzle is None` (group case), route to
   `cog.send_initial_group_channel_messages` instead of the `is_target` ternary.
3. `bot/cogs/puzzles.py`:
   - Rename `send_initial_metaless_round_channel_messages` → `send_initial_group_channel_messages`;
     reword copy to the group's home channel (keep the `update=True` edit-last-pin behavior).
     Grep for the old name and update all references (`grep -rn "metaless_round_channel" bot/`).
   - `cog_before_invoke` `'Group'` branch (@152): look up by
     `TagGroupJsonDb.get_by_attr(category_id=ctx.channel.id)` first, then fall back to
     `category_id=ctx.channel.category.id`. Normalize a resolved `type == "Metaless Round"`
     to behave as Group.
   - `!info` dispatch (@2250): add `elif self.get_channel_type(ctx) in ("Group", "Metaless
     Round"): await self.send_initial_group_channel_messages(ctx, ctx.channel, update=update)`.
   - `command_map` (@1485): change `"roundnometa"` and `"rnm"` entries to
     `("Group", False, None)`.
4. `bot/web/actions.py` `create_meta` (@1491): pass `notifier=DiscordNotifier(bot)` to
   `create_tag_group` for ALL types; for `group_type == "Group"` also pass
   `meta_puzzle_name=False`. DELETE the manual welcome block (@1522–1531) — the service owns
   it now.
5. Acceptance:
   `timeout 180 pipenv run python -m pytest tests/test_forum_layout.py tests/test_group_channel_type.py tests/test_edge_web.py -q > /tmp/test.log 2>&1` exits 0.
   Then full suite + lint:
   `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` exits 0;
   `pipenv run ruff check` clean (review any F401 before fixing).
   Commit: `feat(group): canonical Group general channel + service-owned welcomes`.

## Phase 3 — implement forum config + guidelines sync (Sonnet 4.6)

Makes 1D pass. Forum-only (`hunt.layout_mode == "forum"`).

1. New `bot/core/forum_config.py`:
   - `FORUM_TAGS = ["solved","meta","capstone","metameta","runaround","minimeta"]`,
     `DEFAULT_REACTION = "👍"`.
   - `def forum_create_kwargs() -> dict`: returns `available_tags` (list of
     `discord.ForumTag(name=t)`), `default_reaction_emoji` (a value accepted by
     `create_forum` — confirm via the signature you checked in 1D), and is used to splat into
     `guild.create_forum(...)`.
   - `def render_welcome_plaintext(kind, group, puzzle, hunt) -> str`: a ≤4096-char markdown
     string mirroring the welcome copy for `kind in {"group","meta","round","capstone"}`.
     Reuse the bullet strings; no embed.
   - `async def sync_forum_guidelines(guild, hunt, group, puzzle) -> None`: resolve
     `guild.get_channel(group.category_id)`; if it's a forum and `topic` != rendered text,
     `await forum.edit(topic=rendered)`.
2. `bot/core/puzzle_service.py` `create_tag_group` forum branch (@1526): splat
   `forum_config.forum_create_kwargs()` and `topic=render_welcome_plaintext(...)` into
   `guild.create_forum(...)`. (For the meta the puzzle/group_code may not exist yet at forum
   creation — render with what's known and rely on the guidelines sync below to fill in.)
3. Call `sync_forum_guidelines(...)` from `notifier.refresh_info` (@127) / wherever
   `!info update` regenerates the welcome, and after rename/solve status changes for
   forum-mode metas. Keep it best-effort (`try/except logger.exception`).
4. Acceptance: `timeout 180 pipenv run python -m pytest tests/test_forum_config.py -q >
   /tmp/test.log 2>&1` exits 0; full suite green; ruff clean.
   Commit: `feat(forum): configure rules/tags/emoji on forum creation + guidelines sync`.

## Phase 4 — implement auto-apply tags (Sonnet 4.6)

Makes 1E pass. Forum-only.

1. `bot/core/forum_config.py`:
   - `def desired_tags_for(group, puzzle, *, metameta: bool, minimeta: bool) -> set[str]`
     with precedence `runaround → metameta → meta → minimeta → capstone` (+`solved`), feeders
     get `{"solved"}` only. Use `group.type` for runaround/meta/capstone (per `TYPE_DEFAULTS`)
     and `group.target_puzzle_id` to confirm a real target.
   - `async def apply_forum_tags(thread, forum, desired: set[str]) -> None`: map names →
     `forum.available_tags` by `.name`; if the resulting set differs from
     `thread.applied_tags`, `await thread.edit(applied_tags=[...])`.
2. Wire call sites (compute `metameta` via `is_metameta(group, TagGroupJsonDb.get_all(...),
   <puzzles in group>)` and `minimeta` via `bool(self.get_minis(puzzle.id))`):
   - On meta post create (in `create_puzzle` forum branch, after the thread exists).
   - On `solve` (@785) / `unsolve` (@858): toggle `solved`.
   - On `convert_to_type` (@652) / `convert_to_meta` (@1178): structural tag may change.
   Each call best-effort and guarded by `hunt.layout_mode == "forum"`.
3. Acceptance: `timeout 180 pipenv run python -m pytest tests/test_forum_tags.py -q >
   /tmp/test.log 2>&1` exits 0; full suite green; ruff clean.
   Commit: `feat(forum): auto-apply post tags by kind/solved + keep in sync`.

## Verification (final)

`timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1` exits 0 and
`pipenv run ruff check` is clean after every phase. Update `.ai/active/session-summary.md`
Status line as each phase commits.
