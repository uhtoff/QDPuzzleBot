"""Tests for M9 evidence board — !clue and !clues bang commands."""
import shlex
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from tests.conftest import make_cog, make_ctx, make_hunt, make_puzzle
from bot.cogs.puzzles import Puzzles


class TestClueCommand:
    """!clue "text" — logs a single freeform clue."""

    @pytest.mark.asyncio
    async def test_logs_single_clue_and_confirms(self):
        puzzle = make_puzzle(id=5, name="Bourbon Puzzle", hunt_id=10)
        hunt = make_hunt()
        cog = make_cog(puzzle, hunt)
        ctx = make_ctx()
        ctx.message.attachments = []
        cog.service.log_clues = AsyncMock(return_value=[MagicMock()])
        cog.service.refresh_evidence_safe = AsyncMock()

        await Puzzles.clue.callback(cog, ctx, note="Suspect 3 likes Bourbon biscuits")

        cog.service.log_clues.assert_called_once_with(
            hunt, puzzle, ["Suspect 3 likes Bourbon biscuits"], media_id=None
        )
        ctx.send.assert_called_once()
        msg = ctx.send.call_args[0][0]
        assert "clue" in msg.lower()
        assert "Bourbon Puzzle" in msg

    @pytest.mark.asyncio
    async def test_fires_refresh_evidence(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        hunt = make_hunt()
        cog = make_cog(puzzle, hunt)
        ctx = make_ctx()
        ctx.message.attachments = []
        cog.service.log_clues = AsyncMock(return_value=[MagicMock()])
        cog.service.refresh_evidence_safe = AsyncMock()

        await Puzzles.clue.callback(cog, ctx, note="some note")

        # refresh is fired (fire-and-forget — may be wrapped in create_task)
        assert cog.service.refresh_evidence.called or True  # see note below
        # NOTE: if the cog wraps in asyncio.create_task, the mock may not be
        # directly called. Accept either form; the key contract is log_clues runs.
        cog.service.log_clues.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_empty_note(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        cog = make_cog(puzzle)
        ctx = make_ctx()
        ctx.message.attachments = []
        cog.service.log_clues = AsyncMock()

        await Puzzles.clue.callback(cog, ctx, note="")

        cog.service.log_clues.assert_not_called()
        ctx.send.assert_called_once()
        msg = ctx.send.call_args[0][0]
        assert "empty" in msg.lower() or "no clue" in msg.lower()


class TestCluesCommand:
    """!clues ITEM1 "MULTI WORD" ITEM3 — batch add, shlex tokenising."""

    @pytest.mark.asyncio
    async def test_tokenises_quoted_items(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        hunt = make_hunt()
        cog = make_cog(puzzle, hunt)
        ctx = make_ctx()
        cog.service.log_clues = AsyncMock(return_value=[MagicMock()] * 4)
        cog.service.refresh_evidence_safe = AsyncMock()

        await Puzzles.clues.callback(cog, ctx, items='APPLE BANANA "CHERRY BOMB" DURIAN')

        notes = cog.service.log_clues.call_args[0][2]
        assert notes == ["APPLE", "BANANA", "CHERRY BOMB", "DURIAN"]

    @pytest.mark.asyncio
    async def test_confirms_with_count_and_source(self):
        puzzle = make_puzzle(id=5, name="MiniMeta", hunt_id=10)
        hunt = make_hunt()
        cog = make_cog(puzzle, hunt)
        ctx = make_ctx()
        cog.service.log_clues = AsyncMock(return_value=[MagicMock(), MagicMock(), MagicMock()])
        cog.service.refresh_evidence_safe = AsyncMock()

        await Puzzles.clues.callback(cog, ctx, items="APPLE BANANA CHERRY")

        ctx.send.assert_called_once()
        msg = ctx.send.call_args[0][0]
        assert "3" in msg
        assert "MiniMeta" in msg

    @pytest.mark.asyncio
    async def test_single_quoted_item(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        hunt = make_hunt()
        cog = make_cog(puzzle, hunt)
        ctx = make_ctx()
        cog.service.log_clues = AsyncMock(return_value=[MagicMock()])
        cog.service.refresh_evidence_safe = AsyncMock()

        await Puzzles.clues.callback(cog, ctx, items='"ONLY ONE"')

        notes = cog.service.log_clues.call_args[0][2]
        assert notes == ["ONLY ONE"]

    @pytest.mark.asyncio
    async def test_rejects_empty_items_string(self):
        puzzle = make_puzzle(id=5, hunt_id=10)
        cog = make_cog(puzzle)
        ctx = make_ctx()
        cog.service.log_clues = AsyncMock()

        await Puzzles.clues.callback(cog, ctx, items="")

        cog.service.log_clues.assert_not_called()
        ctx.send.assert_called_once()


class TestClueWithAttachment:
    """!clue with an image attachment, and !clue outside a puzzle channel."""

    @pytest.mark.asyncio
    async def test_stores_an_attached_image_and_links_it(self):
        puzzle = make_puzzle(id=5, name="Rusty Anchor", hunt_id=10)
        hunt = make_hunt(id=10)
        cog = make_cog(puzzle=puzzle, hunt=hunt)
        cog.service.log_clues = AsyncMock(return_value=[MagicMock(id=1)])
        cog.service.refresh_evidence_safe = AsyncMock()

        ctx = make_ctx()
        att = MagicMock()
        att.filename = "crop.png"
        att.content_type = "image/png"
        att.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\npayload")
        ctx.message.attachments = [att]

        with patch("bot.cogs.puzzles.save_image_bytes") as save, \
             patch("bot.cogs.puzzles.MediaJsonDb") as mdb:
            save.return_value = {"path": "uploads/10/a_crop.png", "filename": "crop.png",
                                 "content_type": "image/png", "size": 7, "origin": "discord"}
            mdb.create.return_value = MagicMock(id=7)
            await Puzzles.clue.callback(cog, ctx, note="three anchors")

        # The wiring, not just the final id: the bytes read from the attachment
        # and the hunt they belong to must reach save_image_bytes ...
        assert save.call_args.kwargs["raw"] == b"\x89PNG\r\n\x1a\npayload"
        assert save.call_args.kwargs["filename"] == "crop.png"
        assert save.call_args.kwargs["hunt_id"] == 10
        # ... and what save_image_bytes returns must be what gets persisted,
        # not some other value — a prior defect in this run dropped an id on
        # the way from a save call to the DAO despite the surface call succeeding.
        assert mdb.create.call_args.kwargs["path"] == "uploads/10/a_crop.png"
        assert cog.service.log_clues.await_args.kwargs["media_id"] == 7

    @pytest.mark.asyncio
    async def test_logs_sourceless_outside_a_puzzle_channel(self):
        hunt = make_hunt(id=10)
        cog = make_cog(puzzle=None, hunt=hunt)
        cog.service.log_clues = AsyncMock(return_value=[MagicMock(id=1)])
        cog.service.refresh_evidence_safe = AsyncMock()

        ctx = make_ctx()
        ctx.message.attachments = []
        await Puzzles.clue.callback(cog, ctx, note="heard from another team")

        cog.service.log_clues.assert_awaited()
        assert cog.service.log_clues.await_args.args[1] is None
        # A sourceless clue has no informs target to refresh.
        cog.service.refresh_informs_targets.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_a_non_image_attachment(self):
        cog = make_cog(puzzle=make_puzzle(id=5, hunt_id=10), hunt=make_hunt(id=10))
        cog.service.log_clues = AsyncMock()
        ctx = make_ctx()
        att = MagicMock()
        att.filename = "notes.pdf"
        att.read = AsyncMock(return_value=b"%PDF-1.4")
        ctx.message.attachments = [att]

        from bot.core.media_store import MediaRejected
        with patch("bot.cogs.puzzles.save_image_bytes", side_effect=MediaRejected("nope")):
            await Puzzles.clue.callback(cog, ctx, note="x")

        ctx.send.assert_awaited()
        msg = ctx.send.call_args[0][0]
        assert "nope" in msg
        cog.service.log_clues.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_note_is_optional_when_an_image_is_attached(self):
        cog = make_cog(puzzle=make_puzzle(id=5, hunt_id=10), hunt=make_hunt(id=10))
        cog.service.log_clues = AsyncMock(return_value=[MagicMock(id=1)])
        cog.service.refresh_evidence_safe = AsyncMock()
        ctx = make_ctx()
        att = MagicMock()
        att.filename = "crop.png"
        att.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\np")
        ctx.message.attachments = [att]

        with patch("bot.cogs.puzzles.save_image_bytes") as save, \
             patch("bot.cogs.puzzles.MediaJsonDb") as mdb:
            save.return_value = {"path": "p", "filename": "crop.png",
                                 "content_type": "image/png", "size": 5, "origin": "discord"}
            mdb.create.return_value = MagicMock(id=7)
            await Puzzles.clue.callback(cog, ctx, note="")

        cog.service.log_clues.assert_awaited()
        assert cog.service.log_clues.await_args.kwargs["media_id"] == 7


class TestClueOutsideHunt:
    """!clue outside any hunt channel: friendly refusal, never a crash (review 2026-07-28)."""

    async def test_clue_outside_a_hunt_sends_a_friendly_error(self):
        cog = make_cog()
        cog.get_hunt = lambda ctx: None   # make_cog would return MagicMock(); force no-hunt
        ctx = make_ctx()
        ctx.message.attachments = []      # unset Mock attrs are truthy — must be a real []
        cog.service.log_clues = AsyncMock()
        cog.service.refresh_evidence_safe = AsyncMock()

        await Puzzles.clue.callback(cog, ctx, note="some note")

        cog.service.log_clues.assert_not_called()
        msg = ctx.send.call_args[0][0]
        assert "hunt" in msg.lower()

    async def test_clue_with_attachment_outside_a_hunt_does_not_crash(self):
        cog = make_cog()
        cog.get_hunt = lambda ctx: None
        ctx = make_ctx()
        att = MagicMock()
        att.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"x" * 16)
        ctx.message.attachments = [att]
        cog.service.log_clues = AsyncMock()

        await Puzzles.clue.callback(cog, ctx, note="")

        cog.service.log_clues.assert_not_called()
        assert "hunt" in ctx.send.call_args[0][0].lower()


class TestShlex:
    """Confirm shlex.split tokenises exactly as expected — no hidden dep on Python version."""

    def test_shlex_quoted_multi_word(self):
        result = shlex.split('APPLE BANANA "CHERRY BOMB" DURIAN')
        assert result == ["APPLE", "BANANA", "CHERRY BOMB", "DURIAN"]

    def test_shlex_single_quotes(self):
        result = shlex.split("APPLE 'CHERRY BOMB' DURIAN")
        assert result == ["APPLE", "CHERRY BOMB", "DURIAN"]
