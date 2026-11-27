"""POST /api/media/<id>/post: post a marked-up image snapshot into a channel."""
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from quart.datastructures import FileStorage

from tests.conftest import make_web_app, start_patches, stop_patches

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPG = b"\xff\xd8\xff\xe0" + b"0" * 32
HUNT = {"id": 4, "uid": "huntuid", "guild_id": 7, "archived": 0, "channel_id": 900,
        "name": "My Hunt", "url_sep": "-"}
GUILD_INFO = {"guild_snowflake": 777, "website_url": "https://dash.example/"}
URL = "/api/media/50/post"


def _puzzle(pid=11, hunt_id=4, channel_id=555, name="Cool Puzzle", parent_puzzle_id=None):
    return SimpleNamespace(id=pid, hunt_id=hunt_id, channel_id=channel_id, name=name,
                           parent_puzzle_id=parent_puzzle_id)


def _evidence(eid=21, hunt_id=4, media_id=50, source=11):
    return SimpleNamespace(id=eid, hunt_id=hunt_id, media_id=media_id, source_puzzle_id=source)


@asynccontextmanager
async def _client(*, hunt=HUNT, visible=True, view=True, send=True, media=True,
                  puzzles=None, own_images=(50,), ev_images=(), evidence=None,
                  guild_info=GUILD_INFO, send_side_effect=None, thread=False, thread_send=True,
                  attach=True, locked=False, manage_threads=False):
    puzzles = {11: _puzzle()} if puzzles is None else puzzles
    member = MagicMock()
    member.is_timed_out.return_value = False
    # A mini puzzle's channel is a thread: guild.get_channel() cannot see it.
    channel = MagicMock(spec=discord.Thread) if thread else MagicMock()
    channel.id = 555
    channel.locked = locked
    channel.permissions_for.return_value = SimpleNamespace(
        view_channel=view, send_messages=send, send_messages_in_threads=thread_send,
        attach_files=attach, manage_threads=manage_threads)
    guild = MagicMock()
    guild.get_channel.return_value = None if thread else channel
    guild.get_channel_or_thread.return_value = channel
    app = make_web_app("api", cog=MagicMock(), get_guild=guild)
    send_as = AsyncMock(return_value=999, side_effect=send_side_effect)
    audit = AsyncMock()
    media_db = MagicMock()
    media_db.get_by_id.return_value = SimpleNamespace(id=50, hunt_id=4) if media else None
    hunt_db = MagicMock()
    hunt_db.get_by_attr.return_value = SimpleNamespace(uid="huntuid")
    puzzle_db = MagicMock()
    puzzle_db.get_by_attr.side_effect = lambda id: puzzles.get(id)
    pimg_db = MagicMock()
    pimg_db.get_by_puzzle.return_value = [SimpleNamespace(media_id=m) for m in own_images]
    ev_db = MagicMock()
    ev_db.get_image_evidence_for_puzzle.return_value = [SimpleNamespace(media_id=m) for m in ev_images]
    ev_db.get_by_id.return_value = evidence
    patches = [
        patch("bot.web.auth.session", {"user": {"id": 1, "username": "t"}}),
        patch("bot.web.api._load_visible_hunt",
              AsyncMock(return_value=(hunt, guild_info) if visible else (None, 403))),
        patch("bot.web.api.data.get_guilds_map", AsyncMock(return_value={7: guild_info})),
        patch("bot.web.api.get_or_fetch_member", AsyncMock(return_value=member)),
        patch("bot.web.api.channel_relay.send_as", send_as),
        patch("bot.web.actions._audit", audit),
        patch("bot.store.MediaJsonDb", media_db),
        patch("bot.store.HuntJsonDb", hunt_db),
        patch("bot.store.PuzzleJsonDb", puzzle_db),
        patch("bot.store.PuzzleImageJsonDb", pimg_db),
        patch("bot.store.EvidenceJsonDb", ev_db),
    ]
    start_patches(patches)
    try:
        c = app.test_client()
        c.send_as, c.channel, c.member, c.audit = send_as, channel, member, audit
        yield c
    finally:
        stop_patches(patches)


async def _post(c, file_bytes=PNG, **fields):
    return await c.post(URL, form=fields, files={"file": FileStorage(BytesIO(file_bytes), filename="s.png")})


async def _refused(c, resp, status, error):
    assert resp.status_code == status
    assert (await resp.get_json())["error"] == error
    c.send_as.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_via_puzzle_own_image():
    async with _client() as c:
        resp = await _post(c, puzzle_id="11", content="  look  ")
        assert resp.status_code == 201
        assert await resp.get_json() == {"id": "999"}
        args = c.send_as.await_args.args
        assert args[0] is c.channel and args[1] is c.member
        assert args[2] == "look\n\n[Open in dashboard](<https://dash.example/hunts/huntuid?puzzle=11&image=50>)"
        assert args[3] == PNG
        assert args[4] == "cool-puzzle-markup.png"
        c.audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_via_puzzle_evidence_image_jpeg():
    async with _client(own_images=(), ev_images=(50,)) as c:
        resp = await _post(c, JPG, puzzle_id="11")
        assert resp.status_code == 201
        args = c.send_as.await_args.args
        assert args[2] == "[Open in dashboard](<https://dash.example/hunts/huntuid?puzzle=11&image=50>)"
        assert args[4] == "cool-puzzle-markup.jpg"


@pytest.mark.asyncio
async def test_post_via_evidence_with_source_puzzle():
    async with _client(evidence=_evidence(), own_images=()) as c:
        resp = await _post(c, evidence_id="21")
        assert resp.status_code == 201
        assert "puzzle=11&image=50" in c.send_as.await_args.args[2]


@pytest.mark.asyncio
async def test_post_via_evidence_without_source_goes_to_hunt_channel():
    async with _client(evidence=_evidence(source=None)) as c:
        c.channel.id = 900
        resp = await _post(c, evidence_id="21", content="hi")
        assert resp.status_code == 201
        c.channel.permissions_for.assert_called()
        text = c.send_as.await_args.args[2]
        assert text.endswith("[Open in dashboard](<https://dash.example/hunts/huntuid?image=50>)")
        assert c.send_as.await_args.args[4] == "my-hunt-markup.png"


@pytest.mark.asyncio
async def test_no_website_url_posts_without_link():
    async with _client(guild_info={**GUILD_INFO, "website_url": ""}) as c:
        resp = await _post(c, puzzle_id="11", content="hello")
        assert resp.status_code == 201
        assert c.send_as.await_args.args[2] == "hello"


@pytest.mark.asyncio
async def test_media_not_on_puzzle_is_404():
    async with _client(own_images=(77,)) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 404, "not_on_puzzle")


@pytest.mark.asyncio
async def test_puzzle_from_another_hunt_is_404():
    async with _client(puzzles={11: _puzzle(hunt_id=99)}) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 404, "not_on_puzzle")


@pytest.mark.asyncio
async def test_unknown_puzzle_is_404():
    async with _client(puzzles={}) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 404, "not_on_puzzle")


@pytest.mark.asyncio
async def test_evidence_media_mismatch_is_404():
    async with _client(evidence=_evidence(media_id=51)) as c:
        await _refused(c, await _post(c, evidence_id="21"), 404, "not_on_puzzle")


@pytest.mark.asyncio
async def test_evidence_from_another_hunt_is_404():
    async with _client(evidence=_evidence(hunt_id=99)) as c:
        await _refused(c, await _post(c, evidence_id="21"), 404, "not_on_puzzle")


@pytest.mark.asyncio
async def test_evidence_source_puzzle_from_another_hunt_is_404():
    async with _client(evidence=_evidence(), puzzles={11: _puzzle(hunt_id=99)}) as c:
        await _refused(c, await _post(c, evidence_id="21"), 404, "not_on_puzzle")


@pytest.mark.asyncio
async def test_unknown_media_is_404():
    async with _client(media=False) as c:
        # Same code as a hidden hunt, so media ids cannot be probed.
        await _refused(c, await _post(c, puzzle_id="11"), 404, "not_found_or_forbidden")


@pytest.mark.asyncio
async def test_hidden_hunt_is_404():
    async with _client(visible=False) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 404, "not_found_or_forbidden")


@pytest.mark.asyncio
async def test_archived_is_403():
    async with _client(hunt={**HUNT, "archived": 1}) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 403, "archived")


@pytest.mark.asyncio
async def test_without_send_permission_is_403():
    async with _client(send=False) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 403, "forbidden")


@pytest.mark.asyncio
async def test_timed_out_member_is_403():
    async with _client() as c:
        c.member.is_timed_out.return_value = True
        await _refused(c, await _post(c, puzzle_id="11"), 403, "forbidden")


@pytest.mark.asyncio
async def test_bang_command_is_400():
    async with _client() as c:
        await _refused(c, await _post(c, puzzle_id="11", content="!s X"), 400, "command")


@pytest.mark.asyncio
async def test_too_long_is_400():
    async with _client() as c:
        await _refused(c, await _post(c, puzzle_id="11", content="x" * 2000), 400, "too_long")


@pytest.mark.asyncio
async def test_non_image_bytes_is_400():
    async with _client() as c:
        await _refused(c, await _post(c, b"<svg></svg>", puzzle_id="11"), 400, "bad_image")


@pytest.mark.asyncio
async def test_gif_is_not_allowed():
    async with _client() as c:
        await _refused(c, await _post(c, b"GIF89a" + b"0" * 20, puzzle_id="11"), 400, "bad_image")


@pytest.mark.asyncio
async def test_over_ten_mib_is_400():
    async with _client() as c:
        await _refused(c, await _post(c, PNG + b"0" * (10 * 1024 * 1024), puzzle_id="11"), 400, "bad_image")


@pytest.mark.asyncio
async def test_both_or_neither_id_is_400():
    async with _client() as c:
        await _refused(c, await _post(c, puzzle_id="11", evidence_id="21"), 400, "bad_target")
        await _refused(c, await _post(c), 400, "bad_target")


@pytest.mark.asyncio
async def test_non_numeric_id_is_400():
    async with _client() as c:
        await _refused(c, await _post(c, puzzle_id="abc"), 400, "bad_target")


@pytest.mark.asyncio
async def test_discord_failure_is_502():
    err = discord.HTTPException(MagicMock(status=500, reason="x"), "boom")
    async with _client(send_side_effect=err) as c:
        resp = await _post(c, puzzle_id="11")
        assert resp.status_code == 502
        assert (await resp.get_json())["error"] == "send_failed"
        c.audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_text_over_1500_is_too_long_and_1500_is_accepted():
    async with _client() as c:
        await _refused(c, await _post(c, puzzle_id="11", content="x" * 1501), 400, "too_long")
        resp = await _post(c, puzzle_id="11", content="x" * 1500)
        assert resp.status_code == 201


@pytest.mark.asyncio
async def test_hunt_without_channel_is_404():
    for chan in (None, 0):
        async with _client(hunt={**HUNT, "channel_id": chan}, evidence=_evidence(source=None)) as c:
            await _refused(c, await _post(c, evidence_id="21"), 404, "not_found_or_forbidden")


@pytest.mark.asyncio
async def test_puzzle_without_channel_is_404():
    for chan in (None, 0):
        async with _client(puzzles={11: _puzzle(channel_id=chan)}) as c:
            await _refused(c, await _post(c, puzzle_id="11"), 404, "not_found_or_forbidden")


MINI = {11: _puzzle(name="Tiny", parent_puzzle_id=3)}


@pytest.mark.asyncio
async def test_post_for_a_mini_goes_into_its_thread_with_an_image_only_link():
    async with _client(puzzles=MINI, thread=True) as c:
        resp = await _post(c, puzzle_id="11", content="see")
        assert resp.status_code == 201
        args = c.send_as.await_args.args
        assert args[0] is c.channel
        # Minis are not in the board's puzzle list, so no puzzle param.
        assert args[2] == "see\n\n[Open in dashboard](<https://dash.example/hunts/huntuid?image=50>)"
        assert args[4] == "tiny-markup.png"
        assert "puzzle_id=11" not in c.audit.await_args.kwargs.get("detail", "")
        assert c.audit.await_args.kwargs["puzzle_id"] == 11


@pytest.mark.asyncio
async def test_mini_source_evidence_link_is_image_only():
    async with _client(puzzles=MINI, thread=True, evidence=_evidence(), own_images=()) as c:
        resp = await _post(c, evidence_id="21")
        assert resp.status_code == 201
        assert c.send_as.await_args.args[2] == \
            "[Open in dashboard](<https://dash.example/hunts/huntuid?image=50>)"


@pytest.mark.asyncio
async def test_mini_thread_without_thread_send_permission_is_403():
    async with _client(puzzles=MINI, thread=True, thread_send=False) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 403, "forbidden")


@pytest.mark.asyncio
async def test_mini_thread_without_view_is_403():
    async with _client(puzzles=MINI, thread=True, view=False) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 403, "forbidden")


@pytest.mark.asyncio
async def test_thread_with_uncached_parent_is_404():
    async with _client(puzzles=MINI, thread=True) as c:
        c.channel.permissions_for.side_effect = discord.ClientException("Parent channel not found")
        await _refused(c, await _post(c, puzzle_id="11"), 404, "not_found_or_forbidden")


@pytest.mark.asyncio
async def test_without_attach_files_permission_is_403():
    # The relay webhook would attach the snapshot regardless; Discord's own
    # attach_files rule has to be enforced here.
    async with _client(attach=False) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 403, "forbidden")


@pytest.mark.asyncio
async def test_locked_mini_thread_is_403_without_manage_threads():
    async with _client(puzzles=MINI, thread=True, locked=True) as c:
        await _refused(c, await _post(c, puzzle_id="11"), 403, "forbidden")


@pytest.mark.asyncio
async def test_locked_mini_thread_allows_a_thread_manager():
    async with _client(puzzles=MINI, thread=True, locked=True, manage_threads=True) as c:
        resp = await _post(c, puzzle_id="11")
        assert resp.status_code == 201
        c.send_as.assert_awaited_once()
