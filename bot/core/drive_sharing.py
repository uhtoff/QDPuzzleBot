"""Optional Google account Drive sharing for named Google Sheets cursors.

When a user links their Discord-backed Puzzbot identity to a verified Google
account, Puzzbot grants their Google email ``writer`` permission (silently,
``sendNotificationEmail=False``) on each active hunt's Drive target(s):

- ``files`` mode: the hunt's Drive folder (``drive_folder_id``); Drive inherits
  writer access to the reference sheet, every current and future puzzle sheet,
  and ``Solved/``. Falls back to ``google_sheet_id`` if a legacy hunt has no
  folder id.
- ``tabs`` mode: ``google_sheet_id`` and (when present) ``solved_sheet_id``.

Existing ``anyone``-with-link sharing is untouched, so solvers who do not link
or do not have a Google account participate identically as Anonymous Animals.
All sharing/revocation is best-effort and never blocks or fails hunt/board
operations.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
import logging
import time
from typing import Any, Coroutine, Optional
import weakref

from googleapiclient.errors import HttpError

from bot.store import DriveShareDb, GoogleLinkDb, HuntJsonDb

logger = logging.getLogger(__name__)

# Cap concurrent Drive permission API calls during team-wide pre-shares so a
# burst at hunt creation does not starve interactive sheet operations.
_MAX_CONCURRENT_SHARES = 4
_loop_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()

# Serialize share/revoke/email-switch for the same (drive_file_id, user) so
# concurrent pre-share + first-load or unlink + immediate re-link never race.
_loop_locks: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, defaultdict[tuple[str, int], asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _share_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _loop_semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(_MAX_CONCURRENT_SHARES)
        _loop_semaphores[loop] = sem
    return sem


def _file_user_lock(file_id: str, discord_user_id: int) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _loop_locks.get(loop)
    if locks is None:
        locks = defaultdict(asyncio.Lock)
        _loop_locks[loop] = locks
    return locks[(file_id, discord_user_id)]

# Strong references to fire-and-forget tasks so asyncio cannot GC them mid-flight.
_background_tasks: set[asyncio.Task[Any]] = set()

# Failure cooldown cache: (drive_file_id, discord_user_id, email) -> monotonic
# expiry timestamp. Prevents 30-second dashboard polling from hammering Drive
# when a user's Google account rejects shares (400/403) or Drive is degraded.
_TRANSIENT_COOLDOWN_SECONDS = 300
_PERMANENT_COOLDOWN_SECONDS = 1800
_COOLDOWN_MAX_ENTRIES = 10_000
_failed_cooldowns: dict[tuple[str, int, str], float] = {}


def _hunt_field(hunt: Any, name: str, default: Any = None) -> Any:
    if isinstance(hunt, dict):
        return hunt.get(name, default)
    return getattr(hunt, name, default)


def hunt_share_targets(hunt: Any) -> list[str]:
    """Drive file/folder ids to share for *hunt* (empty for archived hunts)."""
    if not hunt or _hunt_field(hunt, "archived", False):
        return []
    sheet_mode = _hunt_field(hunt, "sheet_mode", "tabs") or "tabs"
    if sheet_mode == "files":
        folder_id = _hunt_field(hunt, "drive_folder_id", "") or ""
        if folder_id:
            return [str(folder_id)]
        sheet_id = _hunt_field(hunt, "google_sheet_id", "") or ""
        return [str(sheet_id)] if sheet_id else []
    targets: list[str] = []
    for key in ("google_sheet_id", "solved_sheet_id"):
        fid = _hunt_field(hunt, key, "") or ""
        if fid and str(fid) not in targets:
            targets.append(str(fid))
    return targets


def _remember_failure(file_id: str, discord_user_id: int, email: str, ttl: float) -> None:
    now = time.monotonic()
    key = (file_id, int(discord_user_id), email)
    _failed_cooldowns.pop(key, None)
    _failed_cooldowns[key] = now + ttl
    if len(_failed_cooldowns) <= _COOLDOWN_MAX_ENTRIES:
        return
    for stale in [k for k, exp in _failed_cooldowns.items() if now >= exp]:
        del _failed_cooldowns[stale]
    while len(_failed_cooldowns) > _COOLDOWN_MAX_ENTRIES:
        del _failed_cooldowns[next(iter(_failed_cooldowns))]


def _is_cooled_down(file_id: str, discord_user_id: int, email: str) -> bool:
    exp = _failed_cooldowns.get((file_id, int(discord_user_id), email))
    if exp is None:
        return False
    if time.monotonic() < exp:
        return True
    _failed_cooldowns.pop((file_id, int(discord_user_id), email), None)
    return False


def clear_user_cooldowns(discord_user_id: int) -> None:
    """Drop failure cooldown entries for *discord_user_id* (called on re-link)."""
    uid = int(discord_user_id)
    for key in [k for k in _failed_cooldowns if k[1] == uid]:
        del _failed_cooldowns[key]


def _spawn(coro: Coroutine[Any, Any, Any], label: str) -> Optional[asyncio.Task[Any]]:
    """Run *coro* in the background with strong task retention and error logging."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        logger.debug("drive_sharing: no running event loop for %s; skipping", label)
        return None
    task = loop.create_task(coro, name=f"drive-share:{label}")
    _background_tasks.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.exception("drive_sharing background task %s failed", label, exc_info=exc)

    task.add_done_callback(_done)
    return task


async def _unshare_if_unneeded(
    gsheet_cog: Any,
    file_id: str,
    email: str,
    permission_id: str,
    exclude_user_id: int,
) -> None:
    if gsheet_cog is None or not permission_id:
        return
    if DriveShareDb.has_other_user_share(file_id, email, exclude_user_id=exclude_user_id):
        return
    try:
        async with _share_semaphore():
            await gsheet_cog.unshare_permission(file_id, permission_id)
    except Exception:
        logger.warning(
            "drive_sharing: could not revoke permission %s on %s for user %s",
            permission_id, file_id, exclude_user_id, exc_info=True,
        )


async def _ensure_file_shared(
    gsheet_cog: Any,
    hunt_id: int,
    discord_user_id: int,
    file_id: str,
    email: str,
) -> None:
    norm_email = email.strip().lower()
    if not norm_email or not file_id:
        return
    uid = int(discord_user_id)
    async with _file_user_lock(file_id, uid):
        existing = DriveShareDb.get(uid, file_id)
        if existing is not None and existing["google_email"] == norm_email:
            return
        if _is_cooled_down(file_id, uid, norm_email):
            return
        # If the user switched Google accounts while a share row for the old
        # email is still present, clear and revoke the stale permission first.
        if existing is not None and existing["google_email"] != norm_email:
            old_email = existing["google_email"]
            old_perm = existing["permission_id"]
            DriveShareDb.delete_for_user_file(uid, file_id, email=old_email)
            await _unshare_if_unneeded(gsheet_cog, file_id, old_email, old_perm, uid)
        try:
            async with _share_semaphore():
                perm_id = await gsheet_cog.share_user_writer(file_id, norm_email)
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            ttl = _PERMANENT_COOLDOWN_SECONDS if status in (400, 403, 404) else _TRANSIENT_COOLDOWN_SECONDS
            _remember_failure(file_id, uid, norm_email, ttl)
            logger.warning(
                "drive_sharing: failed to share %s with user %s (HTTP %s); cooling down %ss",
                file_id, uid, status, ttl,
            )
            return
        except Exception:
            _remember_failure(file_id, uid, norm_email, _TRANSIENT_COOLDOWN_SECONDS)
            logger.warning(
                "drive_sharing: unexpected failure sharing %s with user %s",
                file_id, uid, exc_info=True,
            )
            return
        DriveShareDb.upsert(hunt_id, uid, file_id, norm_email, perm_id)


async def ensure_user_hunt_shared(
    bot: Any,
    hunt: Any,
    discord_user_id: int,
    *,
    email: Optional[str] = None,
) -> None:
    """Ensure every Drive target of active *hunt* is shared with *discord_user_id*'s
    linked Google account. No-op for archived hunts or unlinked users."""
    targets = hunt_share_targets(hunt)
    if not targets:
        return
    gsheet_cog = bot.get_cog("GoogleSheets") if bot is not None else None
    if gsheet_cog is None:
        return
    if email is None:
        link = GoogleLinkDb.get(discord_user_id)
        if link is None:
            return
        email = str(link["google_email"])
    hunt_id = int(_hunt_field(hunt, "id", 0) or 0)
    if not hunt_id:
        return
    for file_id in targets:
        await _ensure_file_shared(gsheet_cog, hunt_id, discord_user_id, file_id, email)


async def preshare_hunt(bot: Any, hunt: Any, *, guild: Any = None) -> None:
    """Proactively share active *hunt*'s Drive target(s) with every guild member
    who currently has access to the hunt and has linked a Google account."""
    targets = hunt_share_targets(hunt)
    if not targets or bot is None:
        return
    gsheet_cog = bot.get_cog("GoogleSheets")
    if gsheet_cog is None:
        return
    hunt_id = int(_hunt_field(hunt, "id", 0) or 0)
    if not hunt_id:
        return
    if guild is None:
        snowflake = HuntJsonDb.guild_snowflake_for_hunt(hunt_id)
        if not snowflake:
            return
        guild = bot.get_guild(snowflake)
    if guild is None:
        return
    if not getattr(guild, "chunked", True):
        try:
            await guild.chunk()
        except Exception:
            logger.warning("drive_sharing: could not chunk guild %s", getattr(guild, "id", "?"), exc_info=True)
    role_id = int(_hunt_field(hunt, "role_id", 0) or 0)
    if role_id:
        role = guild.get_role(role_id)
        if role is None:
            return
        members = list(getattr(role, "members", []))
    else:
        members = list(getattr(guild, "members", []))
    eligible_ids = [m.id for m in members if not getattr(m, "bot", False)]
    if not eligible_ids:
        return
    links = GoogleLinkDb.get_many(eligible_ids)
    if not links:
        return
    await asyncio.gather(
        *(ensure_user_hunt_shared(bot, hunt, uid, email=em) for uid, em in links.items())
    )


async def preshare_user_active_hunts(bot: Any, discord_user_id: int) -> None:
    """Share every currently active hunt visible to *discord_user_id* (called
    after linking or switching their Google account)."""
    if bot is None:
        return
    link = GoogleLinkDb.get(discord_user_id)
    if link is None:
        return
    email = str(link["google_email"])
    uid = int(discord_user_id)
    for hunt, guild_snowflake in HuntJsonDb.get_all_active_with_guild():
        guild = bot.get_guild(guild_snowflake)
        if guild is None:
            continue
        member = guild.get_member(uid)
        if member is None:
            continue
        role_id = int(getattr(hunt, "role_id", 0) or 0)
        if role_id and role_id not in {r.id for r in member.roles}:
            continue
        await ensure_user_hunt_shared(bot, hunt, uid, email=email)


async def revoke_user_active_shares(
    bot: Any,
    discord_user_id: int,
    *,
    hunt_id: Optional[int] = None,
    email: Optional[str] = None,
) -> None:
    """Best-effort revoke Drive permissions recorded for *discord_user_id* on
    active hunts (archived hunts are never revoked)."""
    uid = int(discord_user_id)
    rows = DriveShareDb.list_active_for_user(uid, hunt_id=hunt_id, email=email)
    if not rows:
        return
    gsheet_cog = bot.get_cog("GoogleSheets") if bot is not None else None
    norm_email = email.strip().lower() if email is not None else None
    for row in rows:
        file_id = str(row["drive_file_id"])
        async with _file_user_lock(file_id, uid):
            cur = DriveShareDb.get(uid, file_id)
            if cur is None:
                continue
            if norm_email is not None and cur["google_email"] != norm_email:
                continue
            cur_email = str(cur["google_email"])
            perm_id = str(cur["permission_id"])
            DriveShareDb.delete_for_user_file(uid, file_id, email=cur_email)
            await _unshare_if_unneeded(gsheet_cog, file_id, cur_email, perm_id, uid)


async def on_member_join(bot: Any, member: Any) -> None:
    """Share active open hunts (or any hunt whose role the arrival already holds)
    when a linked member joins a guild."""
    if getattr(member, "bot", False):
        return
    link = GoogleLinkDb.get(member.id)
    if link is None:
        return
    email = str(link["google_email"])
    held = {r.id for r in getattr(member, "roles", [])}
    for hunt in HuntJsonDb.get_active_for_guild(member.guild.id):
        role_id = int(getattr(hunt, "role_id", 0) or 0)
        if role_id == 0 or role_id in held:
            await ensure_user_hunt_shared(bot, hunt, member.id, email=email)


async def on_member_remove(bot: Any, member: Any) -> None:
    """Revoke active-hunt Drive shares for a member who leaves a guild."""
    if getattr(member, "bot", False):
        return
    for hunt in HuntJsonDb.get_active_for_guild(member.guild.id):
        await revoke_user_active_shares(bot, member.id, hunt_id=hunt.id)


async def on_member_roles_changed(
    bot: Any,
    before: Any,
    after: Any,
    added: set[int],
    removed: set[int],
) -> None:
    """Grant shares when a linked member receives a hunt's team role; revoke
    active-hunt shares when they lose one."""
    if getattr(after, "bot", False):
        return
    if not added and not removed:
        return
    active_hunts = HuntJsonDb.get_active_for_guild(after.guild.id)
    if not active_hunts:
        return
    held = {r.id for r in getattr(after, "roles", [])}
    if added:
        link = GoogleLinkDb.get(after.id)
        if link is not None:
            email = str(link["google_email"])
            for hunt in active_hunts:
                role_id = int(getattr(hunt, "role_id", 0) or 0)
                if role_id and role_id in added:
                    await ensure_user_hunt_shared(bot, hunt, after.id, email=email)
    if removed:
        for hunt in active_hunts:
            role_id = int(getattr(hunt, "role_id", 0) or 0)
            if role_id and role_id in removed and role_id not in held:
                await revoke_user_active_shares(bot, after.id, hunt_id=hunt.id)


# --- Non-blocking schedulers for web routes and service hooks -----------------

def schedule_ensure_user_hunt_shared(bot: Any, hunt: Any, discord_user_id: int) -> Optional[asyncio.Task[Any]]:
    hunt_id = _hunt_field(hunt, "id", "?")
    return _spawn(
        ensure_user_hunt_shared(bot, hunt, discord_user_id),
        f"ensure-hunt-{hunt_id}-user-{discord_user_id}",
    )


def schedule_preshare_hunt(bot: Any, hunt: Any, *, guild: Any = None) -> Optional[asyncio.Task[Any]]:
    hunt_id = _hunt_field(hunt, "id", "?")
    return _spawn(
        preshare_hunt(bot, hunt, guild=guild),
        f"preshare-hunt-{hunt_id}",
    )


def schedule_preshare_user_active_hunts(bot: Any, discord_user_id: int) -> Optional[asyncio.Task[Any]]:
    return _spawn(
        preshare_user_active_hunts(bot, discord_user_id),
        f"preshare-user-{discord_user_id}",
    )


def schedule_revoke_user_active_shares(
    bot: Any,
    discord_user_id: int,
    *,
    hunt_id: Optional[int] = None,
    email: Optional[str] = None,
) -> Optional[asyncio.Task[Any]]:
    return _spawn(
        revoke_user_active_shares(bot, discord_user_id, hunt_id=hunt_id, email=email),
        f"revoke-user-{discord_user_id}",
    )


def forget_file(drive_file_id: str) -> None:
    """Best-effort cleanup of ledger rows for a deleted Drive file/folder."""
    if not drive_file_id:
        return
    try:
        DriveShareDb.delete_for_file(drive_file_id)
    except Exception:
        logger.warning(
            "drive_sharing: could not prune share rows for deleted file %s",
            drive_file_id, exc_info=True,
        )

