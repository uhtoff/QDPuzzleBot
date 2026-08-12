"""Tests for the feeder-pool bang commands (Task 10 of the feeder-pool plan)."""
import re
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_cog, make_ctx, make_puzzle
from bot.cogs.puzzles import Puzzles
from bot.utils.chunking import DESCRIPTION_LIMIT, EMBED_TOTAL_LIMIT, FIELD_VALUE_LIMIT


def _embed_total(embed):
    total = len(embed.title or "") + len(embed.description or "")
    if embed.footer and embed.footer.text:
        total += len(embed.footer.text)
    for f in embed.fields:
        total += len(f.name or "") + len(f.value or "")
    return total


def _item(item_id, text, note=None):
    item = MagicMock()
    item.id = item_id
    item.text = text
    item.note = note
    return item


def _claim(item_id, target_id, confirmed=False):
    claim = MagicMock()
    claim.item_id = item_id
    claim.target_id = target_id
    claim.confirmed = confirmed
    return claim


class TestFeederContext:
    def test_in_mini_thread_returns_parent_and_mini(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        mini = make_puzzle(id=2, name="Mini A", puzzle_type="Mini", parent_puzzle_id=1)
        cog = make_cog(mini)
        ctx = make_ctx()

        with patch("bot.cogs.puzzles.PuzzleJsonDb") as db:
            db.get_by_attr.return_value = parent
            minimeta, target = cog._feeder_context(ctx)

        # Both elements matter: a helper that returned (mini, mini) must fail here.
        assert minimeta is parent
        assert target is mini

    def test_in_minimeta_channel_returns_minimeta_for_both(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()

        minimeta, target = cog._feeder_context(ctx)

        assert minimeta is parent
        assert target is parent

    def test_outside_a_puzzle_channel_returns_none_none(self):
        cog = make_cog(None)
        ctx = make_ctx()

        minimeta, target = cog._feeder_context(ctx)

        assert minimeta is None
        assert target is None

    @pytest.mark.asyncio
    async def test_command_outside_puzzle_channel_sends_error_and_skips_service(self):
        cog = make_cog(None)
        ctx = make_ctx()
        ctx.channel.category = None  # not a solved-category channel either

        await Puzzles.feeders.callback(cog, ctx)

        ctx.send.assert_awaited_once()
        assert "puzzle channel" in ctx.send.call_args[0][0].lower()
        cog.service.get_feeder_pool.assert_not_called()


class TestClaimFeeder:
    @pytest.mark.asyncio
    async def test_unknown_id_reports_error_and_does_not_claim(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=None)

        await Puzzles.claim_feeder.callback(cog, ctx, 99)

        ctx.send.assert_awaited_once()
        assert ":x:" in ctx.send.call_args[0][0]
        assert "99" in ctx.send.call_args[0][0]
        cog.service.claim_feeder.assert_not_called()

    @pytest.mark.asyncio
    async def test_known_id_claims_and_confirms(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=_item(13, "ALBATROSS"))

        await Puzzles.claim_feeder.callback(cog, ctx, 13)

        cog.service.claim_feeder.assert_called_once_with(13, parent.id)
        ctx.send.assert_awaited_once()
        assert "ALBATROSS" in ctx.send.call_args[0][0]


class TestUnclaimFeeder:
    @pytest.mark.asyncio
    async def test_unknown_id_reports_error(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=None)

        await Puzzles.unclaim_feeder.callback(cog, ctx, 5)

        cog.service.unclaim_feeder.assert_not_called()
        assert ":x:" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_known_id_unclaims_an_unconfirmed_claim(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=_item(13, "ALBATROSS"))
        cog.service.get_claim = MagicMock(
            return_value=_claim(13, parent.id, confirmed=False))

        await Puzzles.unclaim_feeder.callback(cog, ctx, 13)

        cog.service.unclaim_feeder.assert_called_once_with(13, parent.id)
        assert "no longer claims" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_confirmed_claim_is_refused_not_deleted(self):
        """Confirmed claims are bot-owned -- solving proved them. Unclaiming must
        refuse rather than delete, so !unsolve_mini still has a row to demote."""
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=_item(13, "ALBATROSS"))
        cog.service.get_claim = MagicMock(
            return_value=_claim(13, parent.id, confirmed=True))

        await Puzzles.unclaim_feeder.callback(cog, ctx, 13)

        cog.service.unclaim_feeder.assert_not_called()
        msg = ctx.send.call_args[0][0]
        assert ":x:" in msg
        assert "!unsolve_mini" in msg

    @pytest.mark.asyncio
    async def test_no_claim_at_all_reports_honestly_instead_of_a_false_success(self):
        """The old behaviour replied a plain success even though the DELETE
        matched zero rows -- this pins the honest failure message instead."""
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=_item(13, "ALBATROSS"))
        cog.service.get_claim = MagicMock(return_value=None)

        await Puzzles.unclaim_feeder.callback(cog, ctx, 13)

        cog.service.unclaim_feeder.assert_not_called()
        msg = ctx.send.call_args[0][0]
        assert ":x:" in msg
        assert "white_check_mark" not in msg


class TestEditFeeder:
    @pytest.mark.asyncio
    async def test_unknown_id_reports_error(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=None)

        await Puzzles.edit_feeder.callback(cog, ctx, 5, text="FOO")

        cog.service.edit_feeder_item.assert_not_called()
        assert ":x:" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_known_id_edits(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=_item(13, "ALBATROS"))

        await Puzzles.edit_feeder.callback(cog, ctx, 13, text=" ALBATROSS ")

        cog.service.edit_feeder_item.assert_called_once_with(13, "ALBATROSS")


class TestAddFeeders:
    @pytest.mark.asyncio
    async def test_empty_paste_is_rejected(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()

        await Puzzles.add_feeders.callback(cog, ctx, pasted="   \n  ")

        cog.service.add_feeder_items.assert_not_called()
        assert ":x:" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_added_count_and_dupe_note(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.add_feeder_items = MagicMock(
            return_value=[_item(1, "A")]
        )

        await Puzzles.add_feeders.callback(cog, ctx, pasted="A\nB\n\n")

        cog.service.add_feeder_items.assert_called_once_with(parent, "A\nB\n\n")
        msg = ctx.send.call_args[0][0]
        assert "Added 1 feeder" in msg
        assert "1 already there" in msg


class TestFeedersCommand:
    @pytest.mark.asyncio
    async def test_empty_pool_sends_plain_text_not_embed(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()
        cog.service.get_feeder_pool = MagicMock(return_value=([], []))

        await Puzzles.feeders.callback(cog, ctx)

        ctx.send.assert_awaited_once()
        assert "no feeder pool yet" in ctx.send.call_args[0][0].lower()
        assert "embed" not in ctx.send.call_args.kwargs

    @pytest.mark.asyncio
    async def test_renders_distinct_states_and_rivals_for_two_items(self):
        """A renderer that showed the same state for both items must fail."""
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        mini1 = make_puzzle(id=2, name="Mini A", puzzle_type="Mini", parent_puzzle_id=1)
        mini2 = make_puzzle(id=3, name="Mini B", puzzle_type="Mini", parent_puzzle_id=1)
        cog = make_cog(parent)
        ctx = make_ctx()

        item_free = _item(10, "ALBATROSS")
        item_claimed_by_mini2 = _item(11, "BEAGLE")
        cog.service.get_feeder_pool = MagicMock(
            return_value=(
                [item_free, item_claimed_by_mini2],
                [_claim(11, mini2.id, confirmed=False)],
            )
        )
        cog.service.get_minis = MagicMock(return_value=[mini1, mini2])

        await Puzzles.feeders.callback(cog, ctx)

        embed = ctx.send.call_args.kwargs["embed"]
        text = embed.description
        # Item 10 is free, item 11 is claimed by Mini B — the two lines must differ.
        line_10 = next(line for line in text.splitlines() if "ALBATROSS" in line)
        line_11 = next(line for line in text.splitlines() if "BEAGLE" in line)
        assert "free" in line_10
        assert "claimed" in line_11
        assert "Mini B" in line_11
        assert "Mini B" not in line_10

    @pytest.mark.asyncio
    async def test_embed_respects_discord_limits_for_a_large_pool(self):
        """60 feeders with long rival-claim text - big enough to force the
        description-then-fields spill AND overflow into the drop-counted note
        (verified below: some ids are NOT in the rendered text, so the
        overflow-note branch is genuinely exercised, not merely permitted)."""
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        ctx = make_ctx()

        items = [
            _item(
                i,
                f"A fairly long given feeder answer string number {i:03d} "
                f"padded out with extra filler text to guarantee overflow",
            )
            for i in range(1, 61)
        ]
        # Give every item a rival claim so rival_names actually renders text too.
        claims = [_claim(item.id, 999, confirmed=False) for item in items]
        cog.service.get_feeder_pool = MagicMock(return_value=(items, claims))
        cog.service.get_minis = MagicMock(
            return_value=[make_puzzle(id=999, name="Some Mini", puzzle_type="Mini", parent_puzzle_id=1)]
        )

        await Puzzles.feeders.callback(cog, ctx)

        embed = ctx.send.call_args.kwargs["embed"]
        assert len(embed.description or "") <= DESCRIPTION_LIMIT
        for field in embed.fields:
            assert len(field.value or "") <= FIELD_VALUE_LIMIT
        assert _embed_total(embed) <= EMBED_TOTAL_LIMIT

        rendered = "\n".join([embed.description or ""] + [f.value for f in embed.fields])
        rendered_ids = {int(m) for m in re.findall(r"`(\d+)`", rendered)}
        missing = {item.id for item in items} - rendered_ids
        # This pool is deliberately too big to fit whole - the drop must be real
        # (not just permitted-but-unexercised), and it must be counted, not silent.
        assert missing, "test fixture must be large enough to force a real drop"
        assert "more" in rendered.lower()
        assert str(len(missing)) in rendered


class TestRemoveFeeder:
    @pytest.mark.asyncio
    async def test_unknown_id_reports_error_and_skips_confirm(self):
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        cog._confirm_destructive = MagicMock()
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=None)

        await Puzzles.remove_feeder.callback(cog, ctx, 5)

        cog._confirm_destructive.assert_not_called()
        cog.service.remove_feeder_item.assert_not_called()
        assert ":x:" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cancelled_confirmation_removes_nothing(self):
        from unittest.mock import AsyncMock

        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        cog._confirm_destructive = AsyncMock(return_value=False)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=_item(13, "ALBATROSS"))

        await Puzzles.remove_feeder.callback(cog, ctx, 13)

        cog.service.remove_feeder_item.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmed_removal_deletes_and_confirms(self):
        from unittest.mock import AsyncMock

        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        cog._confirm_destructive = AsyncMock(return_value=True)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=_item(13, "ALBATROSS"))

        await Puzzles.remove_feeder.callback(cog, ctx, 13)

        cog.service.remove_feeder_item.assert_called_once_with(13)
        assert "ALBATROSS" in ctx.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_confirmation_copy_does_not_claim_sheets_are_deleted(self):
        """sheets_deleted=False is required — no Google sheet is touched by a
        feeder delete, unlike !delete_puzzle/!remove_minimeta."""
        from unittest.mock import AsyncMock

        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = make_cog(parent)
        cog._confirm_destructive = AsyncMock(return_value=True)
        ctx = make_ctx()
        cog.service.get_feeder_item = MagicMock(return_value=_item(13, "ALBATROSS"))

        await Puzzles.remove_feeder.callback(cog, ctx, 13)

        kwargs = cog._confirm_destructive.call_args.kwargs
        assert kwargs.get("sheets_deleted") is False
