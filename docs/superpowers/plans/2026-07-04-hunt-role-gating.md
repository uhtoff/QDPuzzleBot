# Hunt Role Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a hunt's team role actually gate the hunt in Discord (category permission overwrites) as well as on the web dashboard, add the creating user to the role, confirm reuse of an existing role, and add a post-creation "add a role to an open hunt" path (web + bang).

**Architecture:** One shared service helper `_resolve_gating_role` enforces two invariants (never gate a zero-member role; confirm reuse of an existing role) and is used by both `create_hunt` and a new `set_hunt_role`. A second helper `_gating_overwrites` builds the `view_channel` overwrite dict. `create_hunt` passes it to `create_category`; `set_hunt_role` applies it retroactively to existing categories. Entry points (`!hunt`, `!set_hunt_role`, `POST /hunts`, `POST /hunts/<uid>/role`) translate the helper's `RuntimeError` signals into a confirm prompt (bang: `ConfirmView`; web: `needs_confirm` JSON) or user messages.

**Tech Stack:** Python, discord.py, Quart (async Flask) web layer, pytest + `pytest-asyncio` (`asyncio_mode = auto`), MySQL via `HuntJsonDb`/`TagGroupJsonDb`.

**Spec:** `docs/superpowers/specs/2026-07-04-hunt-role-gating-design.md`

**Test command (always wrap in `timeout`, write to a file):**
```bash
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py -q > /tmp/test.log 2>&1
```
`pipenv run python -m pytest` is required — plain `pytest`/`python` aren't on PATH and won't put the repo root on `sys.path`.

---

## File Structure

- **Modify** `bot/core/puzzle_service.py`
  - New: `_gating_overwrites(self, guild, team_role)` — build the overwrite dict.
  - New: `async _resolve_gating_role(self, guild, role_name, actor, confirm_existing_role)` — shared invariants; returns a `discord.Role` or `None`, or raises `RuntimeError("existing_role_confirm")` / `RuntimeError("role_not_applied")`.
  - New: `async _apply_gating_overwrites(self, guild, hunt, overwrites)` — retroactive category edits; returns failed-category names.
  - New: `async set_hunt_role(self, guild, hunt, *, role_name, requester, confirm_existing_role=False)` — decorated `@pushes_board()`.
  - Change `create_hunt`: add params `creator`, `confirm_existing_role`; replace the inline role block with the helper; pass `overwrites=` to `create_category` calls; capture the solved category.
- **Modify** `bot/cogs/puzzles.py`
  - New cog helper `async _confirm_reuse_role(self, ctx, role_name)` (uses `ConfirmView`).
  - Change `Puzzles.create_hunt` wrapper + `!hunt` command: pass `creator=ctx.author`, handle the confirm loop, surface dropped gating.
  - New command `!set_hunt_role`.
- **Modify** `bot/web/actions.py`
  - Change `POST /hunts`: resolve creator member, pass `creator`/`confirm_existing_role`, translate `existing_role_confirm` → `needs_confirm`, surface dropped gating warning.
  - New route `POST /hunts/<uid>/role`.
- **Modify** `tests/test_create_hunt_service.py` — new tests for all of the above (service-level; the cog/web layers are thin translators tested via the service).

**Note on the solved category:** `SOLVE_CATEGORY` is `False` by default, so the separate `<name>-solved` category normally does not exist (the solved *divider channel* lives inside the main category and inherits its overwrites). All solved-category handling below is guarded by `if SOLVE_CATEGORY:` and only runs when a deployment enables it. When it does run, we align the created name with `category_layout.solved_category_name(name)` so the Part 2 retroactive lookup finds it.

---

## Phase 1 — Shared helpers

### Task 1: `_gating_overwrites` — build the overwrite dict

**Files:**
- Modify: `bot/core/puzzle_service.py` (add a method to `PuzzleService`, near `create_hunt` ~line 2403)
- Test: `tests/test_create_hunt_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_create_hunt_service.py` (top-of-file imports already include `discord`, `MagicMock`, `pytest`; `_create_service` exists):

```python
def test_gating_overwrites_denies_everyone_allows_role_and_bot():
    svc = _create_service()
    guild = MagicMock()
    everyone = MagicMock(name="everyone")
    me = MagicMock(name="me")
    guild.default_role = everyone
    guild.me = me
    role = MagicMock(name="team")

    ov = svc._gating_overwrites(guild, role)

    assert ov[everyone].view_channel is False
    assert ov[role].view_channel is True
    assert ov[me].view_channel is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
timeout 120 pipenv run python -m pytest "tests/test_create_hunt_service.py::test_gating_overwrites_denies_everyone_allows_role_and_bot" -q > /tmp/test.log 2>&1
```
Expected: FAIL — `AttributeError: ... object has no attribute '_gating_overwrites'`.

- [ ] **Step 3: Write minimal implementation**

In `bot/core/puzzle_service.py`, add this method to `PuzzleService` (place it just above `create_hunt`):

```python
def _gating_overwrites(self, guild, team_role):
    """Overwrite dict that gates a category to ``team_role``: @everyone hidden,
    the team role visible, and the bot always visible (so denying @everyone can
    never lock the bot out of channels it manages)."""
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        team_role: discord.PermissionOverwrite(view_channel=True),
        guild.me: discord.PermissionOverwrite(view_channel=True),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
timeout 120 pipenv run python -m pytest "tests/test_create_hunt_service.py::test_gating_overwrites_denies_everyone_allows_role_and_bot" -q > /tmp/test.log 2>&1
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_create_hunt_service.py
git commit -m "feat(core): _gating_overwrites helper (deny @everyone, allow role + bot)"
```

---

### Task 2: `_resolve_gating_role` — shared invariants

**Files:**
- Modify: `bot/core/puzzle_service.py` (add method to `PuzzleService`)
- Test: `tests/test_create_hunt_service.py`

Behaviour contract:
- `role_name` falsy → return `None` (no gating).
- `actor is None` → raise `RuntimeError("role_not_applied")` (can't seed / guarantee visibility).
- role name matches an existing role **and** `confirm_existing_role` is False → raise `RuntimeError("existing_role_confirm")` (nothing created yet).
- otherwise: create the role if missing; add `actor` if not already a member (best-effort); if the actor wasn't seeded **and** the role has no members → delete the role *iff we created it* and raise `RuntimeError("role_not_applied")`; else return the role.

- [ ] **Step 1: Write the failing tests**

Add a small helper and tests to `tests/test_create_hunt_service.py`:

```python
def _member_with_add(role_after_add):
    """A fake actor whose add_roles() seeds the given role (so role.members
    becomes non-empty), mirroring Discord updating the member cache."""
    actor = MagicMock(name="actor")
    async def _add(role, reason=None):
        role.members.append(actor)
    actor.add_roles = AsyncMock(side_effect=_add)
    return actor


@pytest.mark.asyncio
async def test_resolve_gating_role_none_when_no_role_name():
    svc = _create_service()
    assert await svc._resolve_gating_role(MagicMock(), None, MagicMock(), False) is None


@pytest.mark.asyncio
async def test_resolve_gating_role_missing_creates_and_seeds_actor():
    svc = _create_service()
    guild = MagicMock()
    guild.roles = []
    new_role = MagicMock(name="new")
    new_role.members = []
    guild.create_role = AsyncMock(return_value=new_role)
    actor = _member_with_add(new_role)

    result = await svc._resolve_gating_role(guild, "Team A", actor, False)

    assert result is new_role
    guild.create_role.assert_awaited_once()
    actor.add_roles.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_gating_role_existing_needs_confirm():
    svc = _create_service()
    guild = MagicMock()
    existing = MagicMock(name="existing")
    existing.name = "Team A"
    existing.members = [MagicMock()]
    guild.roles = [existing]
    actor = MagicMock()

    with pytest.raises(RuntimeError, match="existing_role_confirm"):
        await svc._resolve_gating_role(guild, "Team A", actor, False)


@pytest.mark.asyncio
async def test_resolve_gating_role_existing_confirmed_adds_actor():
    svc = _create_service()
    guild = MagicMock()
    existing = MagicMock(name="existing")
    existing.name = "Team A"
    existing.members = [MagicMock()]  # already has a member
    guild.roles = [existing]
    actor = _member_with_add(existing)

    result = await svc._resolve_gating_role(guild, "Team A", actor, True)

    assert result is existing
    actor.add_roles.assert_awaited_once()  # actor not yet in it -> added


@pytest.mark.asyncio
async def test_resolve_gating_role_new_role_add_fails_deletes_and_raises():
    svc = _create_service()
    guild = MagicMock()
    guild.roles = []
    new_role = MagicMock(name="new")
    new_role.members = []
    new_role.delete = AsyncMock()
    guild.create_role = AsyncMock(return_value=new_role)
    actor = MagicMock()
    actor.add_roles = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))

    with pytest.raises(RuntimeError, match="role_not_applied"):
        await svc._resolve_gating_role(guild, "Team A", actor, False)
    new_role.delete.assert_awaited_once()  # cleaned up our own role


@pytest.mark.asyncio
async def test_resolve_gating_role_existing_populated_add_fails_still_gates():
    svc = _create_service()
    guild = MagicMock()
    existing = MagicMock(name="existing")
    existing.name = "Team A"
    existing.members = [MagicMock()]  # other members present
    existing.delete = AsyncMock()
    guild.roles = [existing]
    actor = MagicMock()
    actor.add_roles = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))

    result = await svc._resolve_gating_role(guild, "Team A", actor, True)

    assert result is existing            # still visible to its members
    existing.delete.assert_not_called()  # never delete a pre-existing role


@pytest.mark.asyncio
async def test_resolve_gating_role_no_actor_raises():
    svc = _create_service()
    with pytest.raises(RuntimeError, match="role_not_applied"):
        await svc._resolve_gating_role(MagicMock(), "Team A", None, False)
```

Ensure `AsyncMock` is imported at the top of the test file (it already imports from `unittest.mock`; add `AsyncMock` to that import if missing).

- [ ] **Step 2: Run tests to verify they fail**

```bash
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py -q -k resolve_gating_role > /tmp/test.log 2>&1
```
Expected: FAIL — `AttributeError: ... '_resolve_gating_role'`.

- [ ] **Step 3: Write minimal implementation**

Add to `PuzzleService` in `bot/core/puzzle_service.py` (below `_gating_overwrites`):

```python
async def _resolve_gating_role(self, guild, role_name, actor, confirm_existing_role):
    """Resolve the role to gate a hunt with, enforcing two invariants:
    never gate a zero-member role, and confirm reuse of an existing role.

    Returns the ``discord.Role`` to gate with, or ``None`` when no role was
    requested. Raises ``RuntimeError("existing_role_confirm")`` when the name
    matches an existing role and it hasn't been confirmed, or
    ``RuntimeError("role_not_applied")`` when a requested role can't be made
    visible (no actor, or the actor couldn't be added and the role is empty).
    """
    if not role_name:
        return None
    if actor is None:
        raise RuntimeError("role_not_applied")

    role = discord.utils.get(guild.roles, name=role_name)
    if role is not None and not confirm_existing_role:
        raise RuntimeError("existing_role_confirm")

    created = False
    if role is None:
        role = await guild.create_role(
            name=role_name, colour=discord.Colour.random(),
            mentionable=True, reason="bot-role",
        )
        created = True

    seeded = actor in role.members
    if not seeded:
        try:
            await actor.add_roles(role, reason="hunt-gating")
            seeded = True
        except discord.HTTPException:
            logger.warning("could not add %s to gating role %r", actor, role_name)
            seeded = False

    if not seeded and not role.members:
        # The role would have zero members -> an invisible hunt. Don't gate.
        if created:
            try:
                await role.delete(reason="rollback: gating role left empty")
            except discord.HTTPException:
                logger.warning("could not delete empty rollback role %r", role_name)
        raise RuntimeError("role_not_applied")

    return role
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py -q -k resolve_gating_role > /tmp/test.log 2>&1
```
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_create_hunt_service.py
git commit -m "feat(core): _resolve_gating_role — shared role-gating invariants"
```

---

## Phase 2 — Creation-time gating

### Task 3: Wire `create_hunt` to gate at creation

**Files:**
- Modify: `bot/core/puzzle_service.py` — `create_hunt` (2405-2488)
- Test: `tests/test_create_hunt_service.py`

- [ ] **Step 1: Write the failing tests**

```python
def _guild_for_create():
    """Guild mock that records overwrites passed to create_category."""
    guild = MagicMock()
    guild.id = 999
    guild.categories = []
    guild.roles = []
    guild.default_role = MagicMock(name="everyone")
    guild.me = MagicMock(name="me")
    cat = MagicMock(); cat.id = 111
    chan = MagicMock(); chan.id = 222
    guild.create_category = AsyncMock(return_value=cat)
    guild.create_text_channel = AsyncMock(return_value=chan)
    return guild


@pytest.mark.asyncio
async def test_create_hunt_gates_categories_when_role_given():
    svc = _create_service()
    guild = _guild_for_create()
    role = MagicMock(name="role"); role.id = 333; role.members = []
    guild.create_role = AsyncMock(return_value=role)
    creator = _member_with_add(role)

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        hunt = await svc.create_hunt(
            guild, name="H", url="https://h/", start_timestamp=0,
            role_name="Team A", creator=creator,
        )

    assert hunt.role_id == 333
    # main category created WITH gating overwrites
    _args, kwargs = guild.create_category.call_args_list[0]
    ov = kwargs["overwrites"]
    assert ov[guild.default_role].view_channel is False
    assert ov[role].view_channel is True


@pytest.mark.asyncio
async def test_create_hunt_open_when_no_role():
    svc = _create_service()
    guild = _guild_for_create()

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        hunt = await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0)

    assert hunt.role_id == 0
    _args, kwargs = guild.create_category.call_args_list[0]
    assert kwargs["overwrites"] == {}  # no gating


@pytest.mark.asyncio
async def test_create_hunt_existing_role_requires_confirm():
    svc = _create_service()
    guild = _guild_for_create()
    existing = MagicMock(name="existing"); existing.name = "Team A"
    existing.members = [MagicMock()]
    guild.roles = [existing]
    creator = MagicMock()

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        with pytest.raises(RuntimeError, match="existing_role_confirm"):
            await svc.create_hunt(
                guild, name="H", url="https://h/", start_timestamp=0,
                role_name="Team A", creator=creator,
            )
    guild.create_category.assert_not_called()  # nothing created before confirm


@pytest.mark.asyncio
async def test_create_hunt_falls_back_to_open_when_gating_fails():
    svc = _create_service()
    guild = _guild_for_create()
    guild.roles = []
    new_role = MagicMock(name="new"); new_role.members = []
    new_role.delete = AsyncMock()
    guild.create_role = AsyncMock(return_value=new_role)
    creator = MagicMock()
    creator.add_roles = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb:
        hdb.check_duplicates.return_value = False
        hdb.generate_uid.return_value = "abc123"
        hunt = await svc.create_hunt(
            guild, name="H", url="https://h/", start_timestamp=0,
            role_name="Team A", creator=creator,
        )

    assert hunt.role_id == 0                  # dropped to open, not invisible
    new_role.delete.assert_awaited_once()     # our role cleaned up
    _args, kwargs = guild.create_category.call_args_list[0]
    assert kwargs["overwrites"] == {}
```

- [ ] **Step 1b: Update the existing role test for the new contract**

`test_create_hunt_captures_sheet_and_role` (currently ~lines 114-128) passes
`role_name="Team"` with **no** creator. Under the new contract a role with no
seedable actor yields an open hunt (`role_id 0`), so this test must now pass a
`creator`. Replace its body's `create_hunt` call and role assertion:

```python
@pytest.mark.asyncio
async def test_create_hunt_captures_sheet_and_role():
    svc, guild = _create_service(), _create_guild()
    role = guild.create_role.return_value
    role.members = []
    creator = _member_with_add(role)
    gsheet = MagicMock()
    gsheet.create_hunt_spreadsheet = AsyncMock(return_value="SHEET_ID")
    gsheet.create_solved_sheet = AsyncMock(return_value="ARCH_ID")
    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch.object(svc, "_gsheet_cog", return_value=gsheet):
        hdb.check_duplicates.return_value = False
        await svc.create_hunt(guild, name="H", url="https://h/", start_timestamp=0,
                              role_name="Team", create_solved_sheet=True, creator=creator)
    hunt = _committed_hunt(hdb.commit)
    assert hunt.google_sheet_id == "SHEET_ID"
    assert hunt.solved_sheet_id == "ARCH_ID"
    assert hunt.role_id == 333
```

(All the other existing `create_hunt` tests pass no `role_name`, so they gate
nothing and are unaffected — `overwrites` defaults to `{}` and `create_category`
already accepts arbitrary kwargs.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py -q -k "create_hunt_gates or create_hunt_open or create_hunt_existing or create_hunt_falls_back" > /tmp/test.log 2>&1
```
Expected: FAIL — `create_hunt() got an unexpected keyword argument 'creator'`.

- [ ] **Step 3: Write minimal implementation**

In `bot/core/puzzle_service.py`, change the `create_hunt` signature (line 2405-2408) to add the two params:

```python
async def create_hunt(self, guild, *, name, url, start_timestamp,
                      role_name=None, username=None, password=None,
                      create_solved_sheet=False, layout_mode="channel",
                      guild_id=0, creator=None, confirm_existing_role=False):
```

Replace the current role block (lines 2424-2431):

```python
        team_role = None
        if role_name:
            team_role = discord.utils.get(guild.roles, name=role_name)
            if team_role is None:
                team_role = await guild.create_role(
                    name=role_name, colour=discord.Colour.random(),
                    mentionable=True, reason="bot-role",
                )
```

with:

```python
        # existing_role_confirm must propagate (entry point prompts + retries);
        # role_not_applied is non-fatal here -> fall back to an OPEN hunt.
        try:
            team_role = await self._resolve_gating_role(
                guild, role_name, creator, confirm_existing_role
            )
        except RuntimeError as exc:
            if "existing_role_confirm" in str(exc):
                raise
            team_role = None
        overwrites = self._gating_overwrites(guild, team_role) if team_role else {}
```

Then change the category-creation block (lines 2434-2448) to thread `overwrites`:

```python
        category_name = clean_category_name(name)
        category = await guild.create_category(
            category_name, position=max(len(guild.categories) - 2, 0),
            overwrites=overwrites,
        )
        text_channel = await guild.create_text_channel(
            "general", category=category, reason="bot-hunt-general", position=0
        )
        if SOLVE_CATEGORY:
            await guild.create_category(
                category_layout.solved_category_name(name),
                position=max(len(guild.categories) - 2, 0),
                overwrites=overwrites,
            )
        else:
            await guild.create_text_channel(
                SOLVE_DIVIDER, category=category, reason="bot-round", position=500
            )
```

(The `text_channel` and `SOLVE_DIVIDER` channels are synced children of `category` and inherit its overwrites — no `overwrites=` needed on them.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py -q > /tmp/test.log 2>&1
```
Expected: PASS — the four new tests plus all pre-existing `create_hunt` tests (the added kwargs default to `None`/`False`, so existing callers are unaffected).

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_create_hunt_service.py
git commit -m "feat(core): create_hunt gates categories + adds creator to the role"
```

---

### Task 4: Bang `!hunt` — pass creator, confirm reuse, warn on drop

**Files:**
- Modify: `bot/cogs/puzzles.py` — `!hunt` (502-536), `Puzzles.create_hunt` (538-558); add `_confirm_reuse_role`.

This layer is a thin translator over the service (already covered by service tests). No new automated test; verify by reading the diff and a manual smoke test noted at the end.

- [ ] **Step 1: Add the `_confirm_reuse_role` cog helper**

Add near `_confirm_destructive` (~line 3203) in `bot/cogs/puzzles.py`:

```python
    async def _confirm_reuse_role(self, ctx, role_name):
        """Confirm reusing an existing role for a hunt. Returns True only on an
        explicit confirm; False on cancel/timeout."""
        view = ConfirmView(ctx.author.id)
        embed = discord.Embed(
            title="Role already exists",
            description=(
                f"A role named **{role_name}** already exists. Normally each team "
                f"gets its own role per hunt. Reuse it for this hunt (you'll be "
                f"added to it)?"
            ),
            color=discord.Color.orange(),
        )
        view.message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if view.value is None:
            await ctx.send(":hourglass: Confirmation timed out — no hunt created.")
        return view.value is True
```

- [ ] **Step 2: Rework the `Puzzles.create_hunt` wrapper**

Replace the body of `Puzzles.create_hunt` (538-558) with a confirm loop; return the new hunt on success or `None` when it did not create (cancel / no space):

```python
    async def create_hunt(self, ctx, hunt_name: str, hunt_url: str, role_name: Optional[str] = None,
                          layout_mode: str = "channel"):
        if await self.check_available_space(ctx, 5, 2) is False:
            await ctx.send(":x: There is not enough enough space on the server to create a new hunt, please inform the moderators.")
            return None

        settings = self.get_guild_data(ctx)
        confirm_existing = False
        while True:
            try:
                new_hunt = await self.service.create_hunt(
                    ctx.guild,
                    name=hunt_name,
                    url=hunt_url,
                    start_timestamp=0,
                    role_name=role_name,
                    layout_mode=layout_mode,
                    guild_id=settings.id if settings else 0,
                    creator=ctx.author,
                    confirm_existing_role=confirm_existing,
                )
                break
            except RuntimeError as exc:
                if "existing_role_confirm" in str(exc) and not confirm_existing:
                    if not await self._confirm_reuse_role(ctx, role_name):
                        await ctx.send(":x: Cancelled — no hunt created.")
                        return None
                    confirm_existing = True
                    continue
                raise

        text_channel = self.bot.get_channel(new_hunt.channel_id)
        from bot.core.notifier import DiscordNotifier
        await DiscordNotifier(self.bot).welcome(ctx.guild, new_hunt, channel=text_channel)

        if role_name and not new_hunt.role_id:
            await ctx.send(
                f":warning: Created the hunt, but couldn't gate it to **{role_name}** "
                f"— it's open (ungated). Fix the role in Discord, then re-gate."
            )
        return new_hunt
```

- [ ] **Step 3: Guard the `!hunt` success message on the wrapper result**

In the `!hunt` command (531-534), change:

```python
            await self.create_hunt(ctx, hunt_name, hunt_url, role, layout_mode=layout_mode)
            return await ctx.send(
                f":white_check_mark: I've created a new hunt category and channel for {hunt_name}"
            )
```

to:

```python
            new_hunt = await self.create_hunt(ctx, hunt_name, hunt_url, role, layout_mode=layout_mode)
            if new_hunt is None:
                return  # wrapper already messaged (cancelled / no space)
            return await ctx.send(
                f":white_check_mark: I've created a new hunt category and channel for {hunt_name}"
            )
```

- [ ] **Step 4: Sanity-check imports compile**

```bash
timeout 60 pipenv run python -c "import bot.cogs.puzzles" > /tmp/test.log 2>&1
```
Expected: no output, exit 0. (`ConfirmView` and `discord` are already imported in the module.)

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py
git commit -m "feat(cog): !hunt gates via creator, confirms existing-role reuse, warns on drop"
```

---

### Task 5: Web `POST /hunts` — resolve creator, confirm, warn

**Files:**
- Modify: `bot/web/actions.py` — `POST /hunts` (689-758), `_CREATE_HUNT_OPTIONAL` (685).

- [ ] **Step 1: Allow the new optional field**

In `bot/web/actions.py`, add `confirm_existing_role` to `_CREATE_HUNT_OPTIONAL` (line 685):

```python
_CREATE_HUNT_OPTIONAL = {"username", "password", "role_name", "create_solved_sheet",
                         "layout_mode", "confirm_existing_role"}
```

- [ ] **Step 2: Resolve the creating member and pass it through**

In `create_hunt()` after the `guild` is resolved (after line 728, `guild is None` guard) and before building `fields`, add:

```python
    creator = guild.get_member(int(current_user()["id"]))
    if creator is None:
        try:
            creator = await guild.fetch_member(int(current_user()["id"]))
        except discord.HTTPException:
            creator = None
```

`current_user` is already imported (used at line 669). `confirm_existing_role` flows via `**fields`; `creator` is passed explicitly.

- [ ] **Step 3: Pass `creator` and translate the confirm signal**

Change the service call + error handling (730-745) to:

```python
    fields = {k: v for k, v in body.items() if k in _CREATE_HUNT_OPTIONAL}
    try:
        new_hunt = await cog.service.create_hunt(
            guild,
            name=body["name"],
            url=body["url"],
            start_timestamp=body["start_timestamp"],
            guild_id=guild_id,
            creator=creator,
            **fields,
        )
    except RuntimeError as exc:
        if "hunt_exists" in str(exc):
            return jsonify({"error": "hunt_exists"}), 409
        if "existing_role_confirm" in str(exc):
            role_name = body.get("role_name")
            return jsonify({
                "needs_confirm": "existing_role",
                "message": (f"Role '{role_name}' already exists. Normally each team "
                            f"gets its own role per hunt — reuse it and add you to it?"),
            }), 200
        raise
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)
```

- [ ] **Step 4: Surface a dropped gating in the success response**

Change the final `return jsonify({"uid": new_hunt.uid})` (758) to:

```python
    resp = {"uid": new_hunt.uid}
    if body.get("role_name") and not new_hunt.role_id:
        resp["warning"] = "role_not_applied"
    return jsonify(resp)
```

- [ ] **Step 5: Sanity-check compile + commit**

```bash
timeout 60 pipenv run python -c "import bot.web.actions" > /tmp/test.log 2>&1
```
Expected: exit 0.

```bash
git add bot/web/actions.py
git commit -m "feat(web): POST /hunts resolves creator, confirms existing role, warns on drop"
```

---

## Phase 3 — Add a role to an open hunt (service)

### Task 6: `_apply_gating_overwrites` — retroactive category edits

**Files:**
- Modify: `bot/core/puzzle_service.py` (add method to `PuzzleService`)
- Test: `tests/test_create_hunt_service.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_apply_gating_overwrites_edits_main_and_round_categories():
    svc = _create_service()
    guild = MagicMock()
    guild.default_role = MagicMock(); guild.me = MagicMock()
    main = MagicMock(name="main"); main.edit = AsyncMock()
    round_cat = MagicMock(name="round"); round_cat.edit = AsyncMock()
    guild.get_channel = MagicMock(return_value=main)
    hunt = MagicMock(); hunt.category_id = 111; hunt.id = 5; hunt.name = "H"
    role = MagicMock()
    ov = svc._gating_overwrites(guild, role)

    with patch("bot.core.puzzle_service.SOLVE_CATEGORY", False), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tg, \
         patch("bot.core.puzzle_service.category_layout") as cl:
        tg.get_all.return_value = [MagicMock()]
        cl.primary_category.return_value = round_cat
        failed = await svc._apply_gating_overwrites(guild, hunt, ov)

    assert failed == []
    main.edit.assert_awaited_once_with(overwrites=ov)
    round_cat.edit.assert_awaited_once_with(overwrites=ov)


@pytest.mark.asyncio
async def test_apply_gating_overwrites_collects_failures():
    svc = _create_service()
    guild = MagicMock()
    guild.default_role = MagicMock(); guild.me = MagicMock()
    main = MagicMock(name="main"); main.id = 111; main.name = "H"
    main.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "nope"))
    guild.get_channel = MagicMock(return_value=main)
    hunt = MagicMock(); hunt.category_id = 111; hunt.id = 5; hunt.name = "H"
    ov = svc._gating_overwrites(guild, MagicMock())

    with patch("bot.core.puzzle_service.SOLVE_CATEGORY", False), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tg:
        tg.get_all.return_value = []
        failed = await svc._apply_gating_overwrites(guild, hunt, ov)

    assert failed == ["H"]  # main category name reported
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py -q -k apply_gating_overwrites > /tmp/test.log 2>&1
```
Expected: FAIL — `AttributeError: ... '_apply_gating_overwrites'`.

- [ ] **Step 3: Write minimal implementation**

Add to `PuzzleService`:

```python
async def _apply_gating_overwrites(self, guild, hunt, overwrites):
    """Retroactively apply ``overwrites`` to a hunt's existing categories: the
    main category, the solved category (iff SOLVE_CATEGORY), and each existing
    round/group category. Synced child channels inherit, so we edit categories
    only. Returns a list of category names that failed to update."""
    categories = []
    main = guild.get_channel(hunt.category_id)
    if main is not None:
        categories.append(main)
    if SOLVE_CATEGORY:
        solved = discord.utils.get(
            guild.categories, name=category_layout.solved_category_name(hunt.name)
        )
        if solved is not None:
            categories.append(solved)
    for tag_group in TagGroupJsonDb.get_all(hunt.id):
        cat = category_layout.primary_category(guild, tag_group)
        if cat is not None:
            categories.append(cat)

    failed = []
    for category in categories:
        try:
            await category.edit(overwrites=overwrites)
        except discord.HTTPException:
            name = getattr(category, "name", str(getattr(category, "id", "?")))
            logger.warning("gating overwrite failed for category %s", name)
            failed.append(name)
    return failed
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py -q -k apply_gating_overwrites > /tmp/test.log 2>&1
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_create_hunt_service.py
git commit -m "feat(core): _apply_gating_overwrites — retroactive category gating"
```

---

### Task 7: `set_hunt_role` — service entry

**Files:**
- Modify: `bot/core/puzzle_service.py` (add method; decorate `@pushes_board()`)
- Test: `tests/test_create_hunt_service.py`

**Note:** `@pushes_board()` calls `self.notifier.board_changed(...)` with **no
`None` guard** (`puzzle_service.py:85`), so a service whose `set_hunt_role`
returns normally must be built **with** a notifier. Add this helper to the test
file (mirrors the existing `test_..._pushes_board_when_notifier_present`):

```python
def _create_service_with_notifier():
    from bot.core.puzzle_service import PuzzleService
    bot = MagicMock()
    bot.get_cog.return_value = None
    notifier = MagicMock()
    notifier.board_changed = AsyncMock()
    return PuzzleService(bot, notifier=notifier), notifier
```

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_set_hunt_role_gates_open_hunt():
    svc, notifier = _create_service_with_notifier()
    guild = MagicMock()
    guild.default_role = MagicMock(); guild.me = MagicMock()
    guild.roles = []
    role = MagicMock(); role.id = 777; role.members = []
    guild.create_role = AsyncMock(return_value=role)
    main = MagicMock(); main.edit = AsyncMock()
    guild.get_channel = MagicMock(return_value=main)
    requester = _member_with_add(role)
    hunt = MagicMock(); hunt.role_id = 0; hunt.category_id = 111; hunt.id = 5; hunt.name = "H"

    with patch("bot.core.puzzle_service.HuntJsonDb") as hdb, \
         patch("bot.core.puzzle_service.SOLVE_CATEGORY", False), \
         patch("bot.core.puzzle_service.TagGroupJsonDb") as tg:
        tg.get_all.return_value = []
        failed = await svc.set_hunt_role(
            guild, hunt, role_name="Team A", requester=requester,
            confirm_existing_role=True,
        )

    assert hunt.role_id == 777
    hdb.commit.assert_called_once_with(hunt)
    main.edit.assert_awaited_once()
    notifier.board_changed.assert_awaited_once_with(hunt)
    assert failed == []


@pytest.mark.asyncio
async def test_set_hunt_role_rejects_already_gated():
    svc, _ = _create_service_with_notifier()
    hunt = MagicMock(); hunt.role_id = 42
    with pytest.raises(RuntimeError, match="already_gated"):
        await svc.set_hunt_role(
            MagicMock(), hunt, role_name="Team A", requester=MagicMock(),
        )


@pytest.mark.asyncio
async def test_set_hunt_role_existing_role_needs_confirm():
    svc, _ = _create_service_with_notifier()
    guild = MagicMock()
    existing = MagicMock(); existing.name = "Team A"; existing.members = [MagicMock()]
    guild.roles = [existing]
    hunt = MagicMock(); hunt.role_id = 0
    with pytest.raises(RuntimeError, match="existing_role_confirm"):
        await svc.set_hunt_role(
            guild, hunt, role_name="Team A", requester=MagicMock(),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py -q -k set_hunt_role > /tmp/test.log 2>&1
```
Expected: FAIL — `AttributeError: ... 'set_hunt_role'`.

- [ ] **Step 3: Write minimal implementation**

Add to `PuzzleService` (place near `update_hunt_settings` ~line 2692, which is the `@pushes_board()` template):

```python
@pushes_board()
async def set_hunt_role(self, guild, hunt, *, role_name, requester,
                        confirm_existing_role=False):
    """Gate an OPEN hunt (role_id 0) to ``role_name``: set role_id and apply the
    Discord overwrite to every existing category. Raises RuntimeError with
    "already_gated" / "existing_role_confirm" / "role_not_applied". Returns the
    list of category names whose overwrite update failed (empty on full success).
    """
    if hunt.role_id != 0:
        raise RuntimeError("already_gated")

    team_role = await self._resolve_gating_role(
        guild, role_name, requester, confirm_existing_role
    )
    hunt.role_id = team_role.id
    HuntJsonDb.commit(hunt)

    overwrites = self._gating_overwrites(guild, team_role)
    return await self._apply_gating_overwrites(guild, hunt, overwrites)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
timeout 120 pipenv run python -m pytest tests/test_create_hunt_service.py -q > /tmp/test.log 2>&1
```
Expected: PASS (all tests, new and old).

- [ ] **Step 5: Commit**

```bash
git add bot/core/puzzle_service.py tests/test_create_hunt_service.py
git commit -m "feat(core): set_hunt_role — gate an open hunt (web + Discord)"
```

---

## Phase 4 — Add-role entry points

### Task 8: Bang `!set_hunt_role`

**Files:**
- Modify: `bot/cogs/puzzles.py` — new command.

Thin translator over `set_hunt_role`; verify by reading the diff + manual smoke test.

- [ ] **Step 1: Add the command**

Add to the `Puzzles` cog (near `!hunt`, ~line 560), reusing `_confirm_reuse_role` from Task 4:

```python
    @commands.command()
    @commands.has_any_role(*ADMIN_ROLES)
    async def set_hunt_role(self, ctx, *, arg):
        """*Gate an existing OPEN hunt behind a team role (web + Discord).*

        Usage: `!set_hunt_role hunt-name, role-name`. Admins/mods only; run in
        the bot channel."""
        if not (await self.check_is_bot_channel(ctx)):
            return
        if ", " not in arg:
            return await ctx.send("Usage: `!set_hunt_role hunt-name, role-name`")
        hunt_name, role_name = (part.strip() for part in arg.split(", ", 1))
        hunt = HuntJsonDb.get_by_attr(name=hunt_name)
        if hunt is None:
            return await ctx.send(f":x: No hunt named **{hunt_name}**.")

        confirm_existing = False
        while True:
            try:
                failed = await self.service.set_hunt_role(
                    ctx.guild, hunt, role_name=role_name,
                    requester=ctx.author, confirm_existing_role=confirm_existing,
                )
                break
            except RuntimeError as exc:
                s = str(exc)
                if "already_gated" in s:
                    return await ctx.send(f":x: **{hunt.name}** already has a role.")
                if "existing_role_confirm" in s and not confirm_existing:
                    if not await self._confirm_reuse_role(ctx, role_name):
                        return await ctx.send(":x: Cancelled.")
                    confirm_existing = True
                    continue
                if "role_not_applied" in s:
                    return await ctx.send(
                        f":x: Couldn't gate to **{role_name}** — the role couldn't "
                        f"be made visible (are you addable to it?)."
                    )
                raise

        msg = f":white_check_mark: Gated **{hunt.name}** behind **{role_name}**."
        if failed:
            msg += f"\n:warning: These categories weren't updated: {', '.join(failed)}"
        await ctx.send(msg)
```

- [ ] **Step 2: Sanity-check compile**

```bash
timeout 60 pipenv run python -c "import bot.cogs.puzzles" > /tmp/test.log 2>&1
```
Expected: exit 0. (`HuntJsonDb`, `ADMIN_ROLES`, `commands` already imported.)

- [ ] **Step 3: Commit**

```bash
git add bot/cogs/puzzles.py
git commit -m "feat(cog): !set_hunt_role — gate an open hunt from Discord"
```

---

### Task 9: Web `POST /hunts/<uid>/role`

**Files:**
- Modify: `bot/web/actions.py` — new route (model on `patch_hunt_settings`, 766-797).

- [ ] **Step 1: Add the route**

Add to `bot/web/actions.py`:

```python
@actions_bp.route("/hunts/<uid>/role", methods=["POST"])
@login_required
async def set_hunt_role(uid):
    """Gate an open hunt behind a team role. Admin only. Returns
    {"ok": True, "failed_categories": [...]}, or a needs_confirm prompt when the
    role already exists."""
    ctx, err = await _visible_hunt_by_uid(uid)
    if err:
        return err
    perm = await require_admin(ctx["guild_snowflake"])
    if perm:
        return perm

    body = await request.get_json(silent=True) or {}
    role_name = (body.get("role_name") or "").strip()
    if not role_name:
        return jsonify({"error": "missing_role_name"}), 400
    confirm_existing = bool(body.get("confirm_existing_role"))

    from bot.store import HuntJsonDb
    hunt_obj = HuntJsonDb.get_by_attr(id=ctx["hunt"]["id"])
    if hunt_obj is None:
        return jsonify({"error": "hunt_not_found"}), 404
    cog = current_app.bot.get_cog("Puzzles")
    if cog is None:
        return jsonify({"error": "bot_unavailable"}), 503
    guild = current_app.bot.get_guild(int(ctx["guild_snowflake"])) if ctx["guild_snowflake"] else None
    if guild is None:
        return jsonify({"error": "guild_unavailable"}), 503

    requester = guild.get_member(int(current_user()["id"]))
    if requester is None:
        try:
            requester = await guild.fetch_member(int(current_user()["id"]))
        except discord.HTTPException:
            requester = None

    try:
        failed = await cog.service.set_hunt_role(
            guild, hunt_obj, role_name=role_name,
            requester=requester, confirm_existing_role=confirm_existing,
        )
    except RuntimeError as exc:
        s = str(exc)
        if "already_gated" in s:
            return jsonify({"error": "already_gated"}), 409
        if "existing_role_confirm" in s:
            return jsonify({
                "needs_confirm": "existing_role",
                "message": (f"Role '{role_name}' already exists. Normally each team "
                            f"gets its own role per hunt — reuse it and add you to it?"),
            }), 200
        if "role_not_applied" in s:
            return jsonify({"error": "role_not_applied"}), 422
        raise
    except (discord.RateLimited, discord.HTTPException) as exc:
        return _discord_error_response(exc)

    await _audit("set_hunt_role", hunt_uid=uid, detail=role_name)
    return jsonify({"ok": True, "failed_categories": failed})
```

- [ ] **Step 2: Sanity-check compile**

```bash
timeout 60 pipenv run python -c "import bot.web.actions" > /tmp/test.log 2>&1
```
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add bot/web/actions.py
git commit -m "feat(web): POST /hunts/<uid>/role — gate an open hunt"
```

---

## Phase 5 — Full suite + manual verification

### Task 10: Run the full suite and smoke-test

- [ ] **Step 1: Full test suite**

```bash
timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1
```
Expected: PASS (no regressions). If anything fails, read `/tmp/test.log` (`grep -m 20 -E "FAILED|Error" /tmp/test.log`).

- [ ] **Step 2: Manual smoke test (record results in the PR / handoff)**

On a test guild via Discord:
1. `!hunt smoke-new:https://x/, FreshRoleName` → role created, you're in it, main category hidden from @everyone and visible to you; hunt visible on the web dashboard.
2. `!hunt smoke-existing:https://x/, FreshRoleName` (reuse the now-existing role) → confirm prompt appears; confirm → gated.
3. `!hunt smoke-open:https://x/` (no role) → open hunt, visible to all.
4. `!set_hunt_role smoke-open, AnotherFreshRole` → hunt gated afterwards; category overwrites applied.
5. `!set_hunt_role smoke-existing, X` → `already has a role`.

- [ ] **Step 3: Commit any fixups** (only if the smoke test surfaced issues)

---

## Deferred (separate follow-up plan)

**Frontend confirm UI for the web dashboard.** The web routes return
`{"needs_confirm": "existing_role", "message": ...}` (HTTP 200) for both create
and add-role; the SPA needs to (a) show the message with a confirm button and
re-submit the same body plus `"confirm_existing_role": true`, and (b) add an
"add role" affordance on open hunts that POSTs to `/hunts/<uid>/role` and shows
`failed_categories` / `warning` responses. This requires exploring the dashboard
SPA and is scoped as its own plan — the backend contract above is complete and
testable without it (via bang + direct API calls).

---

## Self-Review notes

- **Spec coverage:** overwrite shape (Task 1) ✔; shared invariants incl. confirm + never-empty + clean-up-own-role (Task 2) ✔; creation gating + open fallback + capture solved (Task 3) ✔; bang creation confirm/warn (Task 4) ✔; web creation confirm/warn (Task 5) ✔; retroactive category loop + partial failure (Task 6) ✔; set_hunt_role + already_gated + pushes_board (Task 7) ✔; bang add-role (Task 8) ✔; web add-role route (Task 9) ✔; rate-limit approach honoured (categories-only edits, sequential await, no `max_ratelimit_timeout`) ✔. Frontend confirm UI explicitly deferred.
- **Signatures are consistent across tasks:** `_resolve_gating_role(guild, role_name, actor, confirm_existing_role)`, `_gating_overwrites(guild, team_role)`, `_apply_gating_overwrites(guild, hunt, overwrites)`, `set_hunt_role(guild, hunt, *, role_name, requester, confirm_existing_role=False)`, `create_hunt(..., creator=None, confirm_existing_role=False)`.
- **RuntimeError sentinels** used uniformly: `existing_role_confirm`, `role_not_applied`, `already_gated`, plus pre-existing `hunt_exists` — all matched via `"x" in str(exc)` per the codebase convention.
