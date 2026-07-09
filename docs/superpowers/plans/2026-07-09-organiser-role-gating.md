# Organiser Role Gating Bypass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** If a guild has no role named `Organisers`, Organiser-tier permission checks (Discord cog + web dashboard) pass for everyone; Admin-tier checks are never affected. The web "View as regular" preview reflects this truthfully.

**Architecture:** A single pure predicate, `organiser_gate_active(guild)`, lives in `bot/core/constants.py` next to `ORGANISER_ROLES`/`ADMIN_ROLES`. The Discord cog gets a new `is_organiser()` check (replacing `commands.has_any_role(*ORGANISER_ROLES)`) that consults it. The web layer gets a small `organiser_tier_bypassed(guild_snowflake, roles)` helper in `bot/web/auth.py`, consulted by both `user_can_manage` (auth.py) and `require_organiser` (actions.py). The hunt-detail API response gains one new boolean field, `organiser_gate_active`, which the frontend's `applyViewAs` uses to decide whether "View as regular" should still show Organiser-tier UI.

**Tech Stack:** Python 3 / discord.py (`discord.ext.commands`) for the bot, Quart for the web API, React/TypeScript for the frontend. Test runner: `pipenv run python -m pytest` (pytest-asyncio, `asyncio_mode = auto`). Frontend has no test runner configured — verification there is `tsc --noEmit` plus manual reasoning, not an automated test.

Spec: `docs/superpowers/specs/2026-07-09-organiser-role-gating-design.md`

---

### Task 1: Shared predicate in `bot/core/constants.py`

**Files:**
- Modify: `bot/core/constants.py`
- Test: `tests/test_core_constants.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_core_constants.py`:

```python
from types import SimpleNamespace


def _guild_with_roles(role_names):
    return SimpleNamespace(roles=[SimpleNamespace(name=n) for n in role_names])


def test_organiser_gate_active_when_organisers_role_present():
    guild = _guild_with_roles(["everyone", "Organisers"])
    assert core.organiser_gate_active(guild) is True


def test_organiser_gate_inactive_when_no_organisers_role():
    guild = _guild_with_roles(["everyone", "Moderator"])
    assert core.organiser_gate_active(guild) is False


def test_organiser_gate_inactive_when_guild_has_no_roles():
    guild = _guild_with_roles([])
    assert core.organiser_gate_active(guild) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 60 pipenv run python -m pytest tests/test_core_constants.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: FAIL with `AttributeError: module 'bot.core.constants' has no attribute 'organiser_gate_active'`

- [ ] **Step 3: Write minimal implementation**

Modify `bot/core/constants.py` — add after the `ORGANISER_ROLES` line (line 11):

```python
ADMIN_ROLES = ('Moderator', 'mod', 'admin')
ORGANISER_ROLES = ADMIN_ROLES + ('Organisers',)


def organiser_gate_active(guild) -> bool:
    """False if the guild has no role literally named 'Organisers' — when
    False, Organiser-tier permission checks pass for everyone. Never affects
    the Admin tier (Moderator/mod/admin), which is always strictly gated."""
    return any(r.name == 'Organisers' for r in guild.roles)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 60 pipenv run python -m pytest tests/test_core_constants.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: PASS (all tests in the file, including the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add bot/core/constants.py tests/test_core_constants.py
git commit -m "feat(core): add organiser_gate_active predicate"
```

---

### Task 2: Discord cog `is_organiser()` check

**Files:**
- Modify: `bot/cogs/puzzles.py`
- Test: `tests/test_is_organiser_check.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_is_organiser_check.py`:

```python
"""is_organiser() bypasses Organiser-tier gating when the guild has no role
literally named 'Organisers'; Admin-tier role names (Moderator/mod/admin)
still satisfy the check when the gate is active, since ORGANISER_ROLES
includes ADMIN_ROLES."""
from unittest.mock import MagicMock

import pytest
from discord.ext import commands

from bot.cogs.puzzles import is_organiser
from tests.conftest import make_ctx


def _role(name):
    role = MagicMock()
    role.name = name
    return role


async def test_bypasses_when_no_organisers_role():
    ctx = make_ctx()
    ctx.guild.roles = [_role("everyone")]
    ctx.author.roles = []
    assert await is_organiser().predicate(ctx) is True


async def test_allows_organiser_when_organisers_role_exists_and_held():
    ctx = make_ctx()
    ctx.guild.roles = [_role("Organisers")]
    ctx.author.roles = [_role("Organisers")]
    assert await is_organiser().predicate(ctx) is True


async def test_allows_admin_role_when_organisers_role_exists():
    ctx = make_ctx()
    ctx.guild.roles = [_role("Organisers")]
    ctx.author.roles = [_role("mod")]
    assert await is_organiser().predicate(ctx) is True


async def test_denies_regular_user_when_organisers_role_exists():
    ctx = make_ctx()
    ctx.guild.roles = [_role("Organisers")]
    ctx.author.roles = [_role("everyone")]
    with pytest.raises(commands.MissingAnyRole):
        await is_organiser().predicate(ctx)


async def test_raises_no_private_message_outside_guild():
    ctx = make_ctx()
    ctx.guild = None
    with pytest.raises(commands.NoPrivateMessage):
        await is_organiser().predicate(ctx)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 60 pipenv run python -m pytest tests/test_is_organiser_check.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: FAIL with `ImportError: cannot import name 'is_organiser' from 'bot.cogs.puzzles'`

- [ ] **Step 3: Write minimal implementation**

Modify `bot/cogs/puzzles.py` — add the import and the check function. First, update the existing import line 17:

```python
from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES, STATUSES, PRIORITIES, organiser_gate_active
```

Then insert this function after the `from bot.cogs.ui import ConfirmView` import (line 30) and before `def with_puzzle_mutex(*, wait: bool = True):` (line 33):

```python
def is_organiser():
    """Like commands.has_any_role(*ORGANISER_ROLES), but the check passes
    unconditionally when the guild has no role named 'Organisers' — see
    organiser_gate_active. Mirrors has_any_role's NoPrivateMessage/
    MissingAnyRole behaviour for parity outside a guild / when denied."""
    def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not organiser_gate_active(ctx.guild):
            return True
        if any(discord.utils.get(ctx.author.roles, name=item) is not None for item in ORGANISER_ROLES):
            return True
        raise commands.MissingAnyRole(list(ORGANISER_ROLES))
    return commands.check(predicate)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 60 pipenv run python -m pytest tests/test_is_organiser_check.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/puzzles.py tests/test_is_organiser_check.py
git commit -m "feat(bot): add is_organiser() check with Organiser-tier bypass"
```

---

### Task 3: Swap the decorator on every Organiser-gated command

**Files:**
- Modify: `bot/cogs/puzzles.py`

- [ ] **Step 1: Confirm the exact set of call sites**

Run: `grep -n "has_any_role(\*ORGANISER_ROLES)" bot/cogs/puzzles.py`
Expected: 19 lines, all reading exactly `    @commands.has_any_role(*ORGANISER_ROLES)`. (If the count differs from 19, stop and re-check — a differently-formatted line needs manual handling instead of the blind replace below.)

- [ ] **Step 2: Replace every occurrence**

This is a literal, identical string on every line — safe to do as one mechanical substitution:

```bash
sed -i 's/@commands\.has_any_role(\*ORGANISER_ROLES)/@is_organiser()/' bot/cogs/puzzles.py
```

- [ ] **Step 3: Verify no occurrences remain and the replacement count matches**

Run: `grep -c "has_any_role(\*ORGANISER_ROLES)" bot/cogs/puzzles.py; grep -c "@is_organiser()" bot/cogs/puzzles.py`
Expected: first command prints `0`, second prints `19`.

- [ ] **Step 4: Confirm Admin-gated commands are untouched**

Run: `grep -c "has_any_role(\*ADMIN_ROLES)" bot/cogs/puzzles.py`
Expected: same count as before this task (unchanged) — these lines were never matched by the sed pattern.

- [ ] **Step 5: Run the full cog-relevant test suite**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; tail -n 40 /tmp/test.log`
Expected: PASS, same pass count as the pre-existing baseline plus the new tests from Tasks 1–2 (no regressions — this is a decorator swap, not a behaviour change for guilds that already have an `Organisers` role).

- [ ] **Step 6: Commit**

```bash
git add bot/cogs/puzzles.py
git commit -m "refactor(bot): use is_organiser() on all Organiser-gated commands"
```

---

### Task 4: Web layer bypass in `bot/web/auth.py`

**Files:**
- Modify: `bot/web/auth.py`
- Test: `tests/test_organiser_gate_web.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_organiser_gate_web.py`:

```python
"""Web-layer Organiser-tier bypass: user_can_manage (auth.py) and
require_organiser (actions.py) both allow any guild member through when the
guild has no role named 'Organisers'. The Admin tier is never bypassed."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES
from bot.web import auth


def _bot_with_guild(role_names):
    guild = SimpleNamespace(roles=[SimpleNamespace(name=n) for n in role_names])
    bot = MagicMock()
    bot.get_guild.return_value = guild
    return bot


class TestOrganiserTierBypassed:
    def test_true_when_organiser_tier_and_no_organisers_role(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
            assert auth.organiser_tier_bypassed(123, ORGANISER_ROLES) is True

    def test_false_when_organisers_role_present(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["Organisers"]))):
            assert auth.organiser_tier_bypassed(123, ORGANISER_ROLES) is False

    def test_false_for_admin_tier_regardless_of_organisers_role(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
            assert auth.organiser_tier_bypassed(123, ADMIN_ROLES) is False

    def test_false_when_guild_unknown(self):
        bot = MagicMock()
        bot.get_guild.return_value = None
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=bot)):
            assert auth.organiser_tier_bypassed(123, ORGANISER_ROLES) is False


class TestUserCanManageBypass:
    async def test_bypassed_for_member_without_organiser_role(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))), \
             patch("bot.web.auth.resolve_role_names", AsyncMock(return_value={"everyone"})):
            assert await auth.user_can_manage(1, 123) is True

    async def test_non_member_still_denied_even_when_gate_inactive(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))), \
             patch("bot.web.auth.resolve_role_names", AsyncMock(return_value=None)):
            assert await auth.user_can_manage(1, 123) is False

    async def test_admin_tier_still_requires_role_when_organisers_role_absent(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))), \
             patch("bot.web.auth.resolve_role_names", AsyncMock(return_value={"everyone"})):
            assert await auth.user_can_manage(1, 123, roles=ADMIN_ROLES) is False

    async def test_existing_behaviour_preserved_when_organisers_role_present(self):
        with patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["Organisers"]))), \
             patch("bot.web.auth.resolve_role_names", AsyncMock(return_value={"everyone"})):
            assert await auth.user_can_manage(1, 123) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 60 pipenv run python -m pytest tests/test_organiser_gate_web.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: FAIL with `AttributeError: module 'bot.web.auth' has no attribute 'organiser_tier_bypassed'`

- [ ] **Step 3: Write minimal implementation**

Modify `bot/web/auth.py`. Change the import on line 21:

```python
from bot.core.constants import ADMIN_ROLES, ORGANISER_ROLES, organiser_gate_active  # noqa: F401  # ADMIN_ROLES re-exported via bot.web.auth
```

Add this new function directly above `async def user_can_manage(...)` (currently line 228):

```python
def organiser_tier_bypassed(guild_snowflake, roles):
    """True if `roles` is the Organiser tier (contains 'Organisers') and the
    guild has no role literally named 'Organisers' — Organiser-tier checks
    then pass for every guild member. Always False for the Admin tier."""
    if 'Organisers' not in roles:
        return False
    bot = bot_app().bot
    guild = bot.get_guild(guild_snowflake)
    if guild is None:
        return False
    return not organiser_gate_active(guild)
```

Then modify `user_can_manage` (currently lines 228-235):

```python
async def user_can_manage(user_id, guild_snowflake, roles=ORGANISER_ROLES):
    """True if the user holds an organiser/admin role in the guild, or the
    Organiser tier is bypassed for this guild (see organiser_tier_bypassed)."""
    if not guild_snowflake:
        return False
    names = await resolve_role_names(user_id, guild_snowflake)
    if not names:
        return False
    if organiser_tier_bypassed(guild_snowflake, roles):
        return True
    return any(r in names for r in roles)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 60 pipenv run python -m pytest tests/test_organiser_gate_web.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the existing auth/constants tests to check for regressions**

Run: `timeout 60 pipenv run python -m pytest tests/test_core_constants.py tests/test_auth_avatar.py tests/test_fetch_member_cached_logging.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add bot/web/auth.py tests/test_organiser_gate_web.py
git commit -m "feat(web): bypass Organiser-tier gating in user_can_manage when guild has no Organisers role"
```

---

### Task 5: Web layer bypass in `bot/web/actions.py` (`require_organiser`)

**Files:**
- Modify: `bot/web/actions.py`
- Test: `tests/test_organiser_gate_web.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_organiser_gate_web.py`:

```python
from bot.web import actions


class TestRequireOrganiserBypass:
    async def test_bypassed_for_member_without_organiser_role(self):
        with patch("bot.web.actions.current_user", return_value={"id": 1}), \
             patch("bot.web.actions.resolve_role_names", AsyncMock(return_value={"everyone"})), \
             patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
            assert await actions.require_organiser(123) is None

    async def test_non_member_still_denied(self):
        with patch("bot.web.actions.current_user", return_value={"id": 1}), \
             patch("bot.web.actions.resolve_role_names", AsyncMock(return_value=None)), \
             patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
            result = await actions.require_organiser(123)
            assert result is not None
            _, status = result
            assert status == 403

    async def test_require_admin_still_denied_when_organisers_role_absent(self):
        with patch("bot.web.actions.current_user", return_value={"id": 1}), \
             patch("bot.web.actions.resolve_role_names", AsyncMock(return_value={"everyone"})), \
             patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["everyone"]))):
            result = await actions.require_admin(123)
            assert result is not None
            _, status = result
            assert status == 403

    async def test_existing_behaviour_preserved_when_organisers_role_present(self):
        with patch("bot.web.actions.current_user", return_value={"id": 1}), \
             patch("bot.web.actions.resolve_role_names", AsyncMock(return_value={"everyone"})), \
             patch("bot.web.auth.bot_app", return_value=MagicMock(bot=_bot_with_guild(["Organisers"]))):
            result = await actions.require_organiser(123)
            assert result is not None
            _, status = result
            assert status == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 60 pipenv run python -m pytest tests/test_organiser_gate_web.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: FAIL — `require_organiser` still denies the bypassed case (the first new test fails with a 403 instead of `None`).

- [ ] **Step 3: Write minimal implementation**

Modify `bot/web/actions.py`. Update the import block (currently lines 23-31) to add `organiser_tier_bypassed`:

```python
from .auth import (
    ADMIN_ROLES,
    ORGANISER_ROLES,
    current_user,
    hunt_visible,
    login_required,
    organiser_tier_bypassed,
    resolve_membership,
    resolve_role_names,
)
```

Modify `require_organiser` (currently lines 710-719):

```python
async def require_organiser(guild_snowflake, roles=ORGANISER_ROLES):
    """Return None if the user holds an allowed role in the guild (or the
    Organiser tier is bypassed for this guild), else an error tuple."""
    user = current_user()
    assert user is not None  # every calling route is behind @login_required
    names = await resolve_role_names(user["id"], guild_snowflake)
    if names is None:
        return jsonify({"error": "forbidden"}), 403
    if organiser_tier_bypassed(guild_snowflake, roles):
        return None
    if not any(r in names for r in roles):
        return jsonify({"error": "insufficient_role"}), 403
    return None
```

`require_admin` is unchanged — it forwards to `require_organiser` with `roles=ADMIN_ROLES`, and `organiser_tier_bypassed` returns `False` unconditionally for that tier.

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 60 pipenv run python -m pytest tests/test_organiser_gate_web.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Run the full suite**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; tail -n 40 /tmp/test.log`
Expected: PASS, no regressions (this only changes behaviour for guilds with no `Organisers` role, which no existing test constructs).

- [ ] **Step 6: Commit**

```bash
git add bot/web/actions.py tests/test_organiser_gate_web.py
git commit -m "feat(web): bypass Organiser-tier gating in require_organiser when guild has no Organisers role"
```

---

### Task 6: Expose `organiser_gate_active` on the hunt-detail API response

**Files:**
- Modify: `bot/web/api.py`
- Test: `tests/test_organiser_gate_web.py` (extend) — or a focused new test if `hunt_detail` already has dedicated coverage elsewhere; check first.

- [ ] **Step 1: Note the existing hunt-route test harness pattern**

`tests/test_add_evidence_web.py` registers `bot.web.api.api_bp` on a bare `Quart` app and drives it with `app.test_client()`, patching `bot.web.auth.session` (a plain dict) for login and `bot.web.api._load_visible_hunt` for hunt resolution. The test below follows that exact pattern for `GET /api/hunts/<uid>`.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_organiser_gate_web.py`:

```python
class TestHuntDetailOrganiserGateField:
    @pytest.fixture
    def hunt_web(self):
        from quart import Quart
        from bot.web.api import api_bp

        app = Quart(__name__)
        app.secret_key = "test-secret-key-must-be-long-enough-0123456789"
        app.register_blueprint(api_bp)

        guild = SimpleNamespace(roles=[SimpleNamespace(name="everyone")])
        bot = MagicMock()
        bot.get_guild.return_value = guild

        hunt_row = {
            "id": 10, "uid": "U1", "name": "Hunt", "url": "http://x",
            "archived": False, "guild_id": 1, "role_id": 0,
            "google_sheet_id": None, "solved_sheet_id": None,
        }

        patches = [
            patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
            patch("bot.web.api._load_visible_hunt", AsyncMock(return_value=(hunt_row, {"guild_snowflake": 123}))),
            patch("bot.web.api.data.get_hunt_puzzles", AsyncMock(return_value=[])),
            patch("bot.web.api.data.get_rollups_by_tag_group", AsyncMock(return_value={})),
            patch("bot.web.api.data.get_all_tag_groups", AsyncMock(return_value=[])),
            patch("bot.web.api.data.build_board", return_value=[]),
            patch("bot.web.api.user_can_manage", AsyncMock(return_value=False)),
            patch("bot.web.api.bot_app", return_value=MagicMock(bot=bot)),
        ]
        started = [p.start() for p in patches]
        try:
            yield SimpleNamespace(client=app.test_client(), guild=guild)
        finally:
            for p in patches:
                p.stop()

    async def test_true_when_organisers_role_present(self, hunt_web):
        hunt_web.guild.roles = [SimpleNamespace(name="Organisers")]
        resp = await hunt_web.client.get("/api/hunts/U1")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["hunt"]["organiser_gate_active"] is True

    async def test_false_when_no_organisers_role(self, hunt_web):
        hunt_web.guild.roles = [SimpleNamespace(name="everyone")]
        resp = await hunt_web.client.get("/api/hunts/U1")
        assert resp.status_code == 200
        body = await resp.get_json()
        assert body["hunt"]["organiser_gate_active"] is False
```

This needs `import pytest` and `from types import SimpleNamespace` at the top of `tests/test_organiser_gate_web.py` alongside the existing imports from Task 4 (`SimpleNamespace` is already imported there; add `import pytest`).

- [ ] **Step 3: Run test to verify it fails**

Run: `timeout 60 pipenv run python -m pytest tests/test_organiser_gate_web.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: FAIL with `KeyError: 'organiser_gate_active'` (the field doesn't exist in the response yet).

- [ ] **Step 4: Write minimal implementation**

Modify `bot/web/api.py`. Add `organiser_gate_active` to the import from `bot.core.constants` — currently there is no such import in this file, so add a new import line near the top (after the existing `from .auth import (...)` block, currently ending at line 19):

```python
from bot.core.constants import organiser_gate_active
```

Modify `hunt_detail` (currently lines 96-124) to compute `_guild` unconditionally and add the new field:

```python
@api_bp.route("/hunts/<uid>")
@login_required
async def hunt_detail(uid):
    user = current_user()
    assert user is not None  # @login_required
    hunt, guild_or_status = await _load_visible_hunt(uid)
    if hunt is None:
        assert isinstance(guild_or_status, int)  # the (None, status) branch
        return jsonify({"error": "not_found_or_forbidden"}), guild_or_status
    assert isinstance(guild_or_status, dict)  # the (hunt, guild_info) branch

    guild_snowflake = guild_or_status.get("guild_snowflake")
    puzzles = await data.get_hunt_puzzles(hunt["id"], guild_snowflake)
    rollups = await data.get_rollups_by_tag_group(hunt["id"])
    all_groups = await data.get_all_tag_groups(hunt["id"])
    board = data.build_board(puzzles, rollups, all_groups)
    solved = sum(1 for p in puzzles if p["solved"])
    # Archived hunts are read-only even for organisers.
    can_manage = (not hunt["archived"]) and await user_can_manage(user["id"], guild_snowflake)
    can_administer = await user_can_manage(user["id"], guild_snowflake, roles=ADMIN_ROLES)
    _guild = bot_app().bot.get_guild(int(guild_snowflake)) if guild_snowflake else None
    # Fail safe: an unresolvable guild is treated as gated (organiser_gate_active
    # True), never as a bypass.
    gate_active = organiser_gate_active(_guild) if _guild is not None else True
    # Gating status: a non-zero role_id means the hunt is gated to a team role.
    # Resolve the role's name (best-effort) so the dashboard can show it.
    gating_role_id = hunt.get("role_id") or 0
    gating_role_name = None
    if gating_role_id and _guild is not None:
        _role = _guild.get_role(gating_role_id)
        if _role is not None:
            gating_role_name = _role.name
    from bot.core.puzzle_service import CREATE_META_TYPES
    return jsonify({
        "hunt": {
            "uid": hunt["uid"],
            "name": hunt["name"],
            "url": hunt["url"],
            "archived": bool(hunt["archived"]),
            "is_gated": bool(gating_role_id),
            "role_name": gating_role_name,
            "guild_snowflake": str(guild_snowflake) if guild_snowflake else None,
            "sheet_base_url": _sheet_base(hunt["google_sheet_id"]),
            "solved_sheet_base_url": _sheet_base(hunt["solved_sheet_id"]),
            "can_manage": can_manage,
            "can_administer": can_administer,
            "organiser_gate_active": gate_active,
            "start_timestamp": hunt.get("start_timestamp"),
            "username": hunt.get("username") or "",
            "password": hunt.get("password") or "",
            "url_sep": hunt.get("url_sep") or "-",
            "puzzle_prefix": hunt.get("puzzle_prefix") or "puzzle",
        },
```

(Only the lines shown above through the `"hunt": {...}` dict change; everything after `"puzzle_prefix"` — `"summary"` and onward — is unchanged.)

- [ ] **Step 5: Run test to verify it passes**

Run: `timeout 60 pipenv run python -m pytest tests/test_organiser_gate_web.py -q > /tmp/test.log 2>&1; cat /tmp/test.log`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; tail -n 40 /tmp/test.log`
Expected: PASS, no regressions — this only adds a field, it doesn't change `can_manage`/`can_administer` values or any other existing response shape.

- [ ] **Step 7: Commit**

```bash
git add bot/web/api.py tests/test_organiser_gate_web.py
git commit -m "feat(web): expose organiser_gate_active on hunt-detail response"
```

---

### Task 7: Frontend type + "View as regular" fix

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/context/ViewAsContext.tsx`

- [ ] **Step 1: Add the field to the type**

Modify `frontend/src/types.ts` — in the `HuntDetail.hunt` interface (currently lines 104-122), add the new field next to `can_administer` (currently line 116):

```typescript
    can_manage: boolean;
    can_administer: boolean;
    organiser_gate_active: boolean;
```

- [ ] **Step 2: Update `applyViewAs`**

Modify `frontend/src/context/ViewAsContext.tsx` (currently lines 23-35):

```typescript
/**
 * Cap the role the UI renders as. Effective role per hunt = min(real role, ceiling).
 *   admin     -> unchanged
 *   organiser -> drop admin, keep organiser (can_manage) if real
 *   regular   -> drop can_administer always; drop can_manage only when the
 *                Organiser gate is actually active for this guild (a real
 *                regular user keeps can_manage when it isn't — see
 *                organiser_gate_active)
 * Every gated site reads detail.hunt.can_manage / can_administer, so overriding
 * those two fields cascades to all of them with no per-component edits.
 */
export function applyViewAs(detail: HuntDetail, viewAs: ViewAs): HuntDetail {
  if (viewAs === "admin") return detail;
  const can_manage =
    viewAs === "organiser"
      ? detail.hunt.can_manage
      : detail.hunt.organiser_gate_active
        ? false
        : detail.hunt.can_manage;
  return { ...detail, hunt: { ...detail.hunt, can_manage, can_administer: false } };
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && timeout 120 npx tsc --noEmit -p . > /tmp/tsc.log 2>&1; cat /tmp/tsc.log; echo "exit:$?"`
Expected: exit 0, no output (clean typecheck).

- [ ] **Step 4: Manual verification note**

There is no frontend test runner configured in this project (`frontend/package.json` has no `test` script and no `*.test.*` files exist). Do not invent one for this change. Instead, verify by reasoning through the four `(viewAs, organiser_gate_active)` combinations against the updated `applyViewAs` body:
- `admin`, any gate state → unchanged (early return).
- `organiser`, any gate state → `can_manage` = real value, `can_administer` = false. (Unaffected by this change — same as before.)
- `regular`, gate active (`true`) → `can_manage` = false (unchanged from before this change).
- `regular`, gate inactive (`false`) → `can_manage` = real value (this is the new behaviour — previously always `false`).

Confirm each of the four lines above matches the code before proceeding.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/context/ViewAsContext.tsx
git commit -m "feat(web): View as regular reflects real Organiser-gate bypass state"
```

---

### Task 8: Final verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Full backend test suite**

Run: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; tail -n 40 /tmp/test.log`
Expected: PASS, 0 failures.

- [ ] **Step 2: Ruff**

Run: `pipenv run ruff check bot/ tests/ > /tmp/ruff.log 2>&1; cat /tmp/ruff.log`
Expected: no errors touching files changed in this plan (`bot/core/constants.py`, `bot/cogs/puzzles.py`, `bot/web/auth.py`, `bot/web/actions.py`, `bot/web/api.py`, `tests/test_core_constants.py`, `tests/test_is_organiser_check.py`, `tests/test_organiser_gate_web.py`).

- [ ] **Step 3: basedpyright**

Run: `pipenv run basedpyright bot/ > /tmp/pyright.log 2>&1; tail -n 40 /tmp/pyright.log`
Expected: 0 errors.

- [ ] **Step 4: Frontend build**

Run: `cd frontend && timeout 120 npm run build > /tmp/build.log 2>&1; tail -n 40 /tmp/build.log; echo "exit:$?"`
Expected: exit 0 (this runs `tsc --noEmit && vite build`).

- [ ] **Step 5: Confirm no leftover references to the old decorator or field names**

Run: `grep -rn "has_any_role(\*ORGANISER_ROLES)" bot/ ; grep -rn "\"is_admin\"\|\.is_admin\b" bot/ frontend/src/`
Expected: no output from either command.

- [ ] **Step 6: Report**

Summarize: files changed (list from `git log --oneline` since the branch point of this plan, or `git diff --stat` against the commit before Task 1), tests run and results, any deviations from the plan and why.
