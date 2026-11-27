"""Live-DB tests for user_google_links and drive_user_shares (migration 066)."""
from bot.store import DriveShareDb, GoogleLinkDb, HuntJsonDb, mydb

GUILD_SNOW = 990000000000000083
USER_A = 990000000000001001
USER_B = 990000000000001002


def _exec(sql, params=()):
    cur = mydb.cursor()
    cur.execute(sql, params)
    last = cur.lastrowid
    cur.close()
    return last


def _cleanup(guild_row_id):
    _exec("DELETE FROM drive_user_shares WHERE discord_user_id IN (%s, %s)", (USER_A, USER_B))
    _exec("DELETE FROM user_google_links WHERE discord_user_id IN (%s, %s)", (USER_A, USER_B))
    _exec("DELETE FROM hunts WHERE guild_id = %s", (guild_row_id,))
    _exec("DELETE FROM guilds WHERE id = %s", (guild_row_id,))


def _setup_guild():
    return _exec(
        "INSERT INTO guilds (guild_id, guild_name) VALUES (%s, %s)",
        (GUILD_SNOW, "google link store"),
    )


def test_google_link_upsert_get_many_and_unlink():
    guild = _setup_guild()
    try:
        assert GoogleLinkDb.get(USER_A) is None
        GoogleLinkDb.upsert(USER_A, "sub-a1", "Alice@Gmail.com")
        row = GoogleLinkDb.get(USER_A)
        assert row is not None
        assert row["google_sub"] == "sub-a1"
        assert row["google_email"] == "alice@gmail.com"

        # Upsert replaces email/sub in place.
        GoogleLinkDb.upsert(USER_A, "sub-a2", "alice2@gmail.com")
        GoogleLinkDb.upsert(USER_B, "sub-b1", "bob@gmail.com")
        assert GoogleLinkDb.get_many([USER_A, USER_B, 999999999]) == {
            USER_A: "alice2@gmail.com",
            USER_B: "bob@gmail.com",
        }
        assert GoogleLinkDb.get_many([]) == {}

        prev = GoogleLinkDb.unlink(USER_A)
        assert prev is not None and prev["google_email"] == "alice2@gmail.com"
        assert GoogleLinkDb.get(USER_A) is None
        assert GoogleLinkDb.unlink(USER_A) is None
    finally:
        _cleanup(guild)


def test_drive_shares_active_filter_shared_email_and_cascade():
    guild = _setup_guild()
    try:
        active_hunt = _exec(
            "INSERT INTO hunts (name, guild_id, archived, google_sheet_id) VALUES (%s, %s, 0, %s)",
            ("Active Hunt", guild, "SHEET_ACTIVE"),
        )
        archived_hunt = _exec(
            "INSERT INTO hunts (name, guild_id, archived, google_sheet_id) VALUES (%s, %s, 1, %s)",
            ("Archived Hunt", guild, "SHEET_ARCHIVED"),
        )

        DriveShareDb.upsert(active_hunt, USER_A, "FOLD_ACTIVE", "shared@gmail.com", "PERM_A1")
        DriveShareDb.upsert(active_hunt, USER_B, "FOLD_ACTIVE", "shared@gmail.com", "PERM_B1")
        DriveShareDb.upsert(archived_hunt, USER_A, "FOLD_ARCHIVED", "shared@gmail.com", "PERM_ARCH")

        # Unique key upserts in place on repeat write.
        DriveShareDb.upsert(active_hunt, USER_A, "FOLD_ACTIVE", "shared@gmail.com", "PERM_A2")
        got = DriveShareDb.get(USER_A, "FOLD_ACTIVE")
        assert got is not None and got["permission_id"] == "PERM_A2"

        # list_active_for_user excludes archived hunts.
        active_rows = DriveShareDb.list_active_for_user(USER_A)
        assert [r["drive_file_id"] for r in active_rows] == ["FOLD_ACTIVE"]

        # Shared Google account detection across two Discord accounts.
        assert DriveShareDb.has_other_user_share("FOLD_ACTIVE", "shared@gmail.com", exclude_user_id=USER_A) is True
        DriveShareDb.delete_for_user_file(USER_B, "FOLD_ACTIVE")
        assert DriveShareDb.has_other_user_share("FOLD_ACTIVE", "shared@gmail.com", exclude_user_id=USER_A) is False

        # Active hunt helper finds only active hunts on this guild.
        guild_hunts = HuntJsonDb.get_active_for_guild(GUILD_SNOW)
        assert [h.id for h in guild_hunts] == [active_hunt]

        # Deleting the hunt cascades to drive_user_shares.
        _exec("DELETE FROM hunts WHERE id = %s", (active_hunt,))
        assert DriveShareDb.get(USER_A, "FOLD_ACTIVE") is None
    finally:
        _cleanup(guild)
