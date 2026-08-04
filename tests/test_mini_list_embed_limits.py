"""The pinned mini-puzzle list must respect Discord's embed limits.

Live regression (2026-08-01/02, discord.log): a minimeta accumulated enough
minis that ``create_mini_list`` rendered more than 1024 characters into a single
embed field, and every command that refreshes the pinned info - ``!add_mini``,
``!info``, ``!solve`` - died with:

    400 Bad Request (error code: 50035): Invalid Form Body
    In embeds.1.fields.0.value: Must be 1024 or fewer in length.

The solve itself had already been persisted; only the pin refresh (the last step
of ``_solve_impl``) failed, so users saw a scary error on a completed solve.
"""
from unittest.mock import MagicMock

import discord
import pytest

from tests.conftest import make_hunt, make_puzzle
from bot.cogs.puzzles import Puzzles
from bot.utils.chunking import (
    DESCRIPTION_LIMIT,
    EMBED_TOTAL_LIMIT,
    FIELD_VALUE_LIMIT,
    MAX_FIELDS,
    split_lines_for_embed,
)


def _embed_total(embed: discord.Embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    if embed.footer and embed.footer.text:
        total += len(embed.footer.text)
    for field in embed.fields:
        total += len(field.name or "") + len(field.value or "")
    return total


def _cog_with_minis(minis):
    cog = Puzzles.__new__(Puzzles)
    cog.service = MagicMock()
    cog.service.get_minis.return_value = minis
    return cog


def _make_minis(count, *, name="Mini Puzzle With A Fairly Long Name"):
    """Minis that render a full row: thread mention + hyperlinked sheet tab.

    That is the live shape - the sheet URL alone is ~90 chars, so a row runs
    130-160 and roughly 7-9 minis overflow a 1024-char field.
    """
    minis = []
    for i in range(1, count + 1):
        mini = make_puzzle(id=100 + i, name=f"{name} {i}", channel_id=9000 + i)
        mini.google_page_id = str(700000 + i)
        minis.append(mini)
    return minis


class TestBuildEmbedListRespectsDiscordLimits:
    """The regression the live 400s came from."""

    @pytest.mark.parametrize("count", [8, 25, 60])
    @pytest.mark.parametrize("name", ["Mini", "Mini Puzzle With A Fairly Long Name"])
    def test_no_field_exceeds_the_field_value_limit(self, count, name):
        """Short names pack more rows into more fields, so they expose the
        per-field name spend that long names hide."""
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        hunt = make_hunt()
        cog = _cog_with_minis(_make_minis(count, name=name))

        embeds = cog._build_embed_list(
            ctx=None, embed=discord.Embed(title="Minimeta"), puzzle=parent, hunt=hunt
        )

        for embed in embeds:
            assert len(embed.description or "") <= DESCRIPTION_LIMIT
            assert len(embed.fields) <= MAX_FIELDS
            assert _embed_total(embed) <= EMBED_TOTAL_LIMIT
            for field in embed.fields:
                assert len(field.value or "") <= FIELD_VALUE_LIMIT

    def test_lists_every_mini_when_it_fits(self):
        """A modest list must not lose rows to the new chunking."""
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        hunt = make_hunt()
        minis = _make_minis(8)
        cog = _cog_with_minis(minis)

        embeds = cog._build_embed_list(
            ctx=None, embed=discord.Embed(title="Minimeta"), puzzle=parent, hunt=hunt
        )

        rendered = "\n".join(
            (e.description or "") + "\n".join(f.value or "" for f in e.fields)
            for e in embeds
        )
        for mini in minis:
            assert mini.name in rendered

    def test_does_not_drop_rows_that_would_have_fitted(self):
        """Over-reserving budget for field names that are never added would
        truncate a list the embed could actually hold."""
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        hunt = make_hunt()
        minis = _make_minis(45, name="Mini")
        cog = _cog_with_minis(minis)

        embeds = cog._build_embed_list(
            ctx=None, embed=discord.Embed(title="Minimeta"), puzzle=parent, hunt=hunt
        )

        minis_embed = embeds[-1]
        assert _embed_total(minis_embed) <= EMBED_TOTAL_LIMIT
        rendered = (minis_embed.description or "") + "".join(
            f.value for f in minis_embed.fields
        )
        for mini in minis:
            assert mini.name in rendered
        assert "more" not in rendered.lower()

    def test_overflow_says_how_many_were_dropped(self):
        """Silent truncation would read as 'that's all the minis'."""
        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        hunt = make_hunt()
        cog = _cog_with_minis(_make_minis(200))

        embeds = cog._build_embed_list(
            ctx=None, embed=discord.Embed(title="Minimeta"), puzzle=parent, hunt=hunt
        )

        rendered = "\n".join(
            (e.description or "") + "\n".join(f.value or "" for f in e.fields)
            for e in embeds
        )
        assert "more" in rendered.lower()
        assert "!list_minis" in rendered


class TestSplitLinesForEmbed:
    """The helper itself: fill the description, spill to fields, never mid-line."""

    def test_short_text_goes_entirely_in_the_description(self):
        result = split_lines_for_embed("one\ntwo\nthree")

        assert result.description == "one\ntwo\nthree"
        assert result.fields == []
        assert result.dropped == 0

    def test_spills_past_the_description_limit_into_fields(self):
        lines = [f"line {i:04d} " + "x" * 100 for i in range(80)]

        result = split_lines_for_embed("\n".join(lines))

        assert len(result.description) <= DESCRIPTION_LIMIT
        assert result.fields, "text beyond the description limit must spill to fields"
        for value in result.fields:
            assert len(value) <= FIELD_VALUE_LIMIT

    def test_never_splits_a_line_across_chunks(self):
        """A row cut in half would corrupt a markdown link into visible junk."""
        lines = [f"line {i:04d} " + "x" * 100 for i in range(80)]

        result = split_lines_for_embed("\n".join(lines))

        emitted = []
        for block in [result.description, *result.fields]:
            emitted.extend(block.split("\n"))
        kept = [line for line in lines if line in emitted]
        assert kept == lines[: len(kept)], "lines must be kept whole and in order"
        assert len(kept) == len(lines) - result.dropped

    def test_reports_dropped_lines_rather_than_truncating_silently(self):
        lines = [f"line {i:04d} " + "x" * 200 for i in range(200)]

        result = split_lines_for_embed("\n".join(lines))

        assert result.dropped > 0
        assert result.dropped == len(lines) - result.kept

    def test_reserved_budget_shrinks_what_is_emitted(self):
        """The minis embed shares the 6000-char budget with the main embed."""
        text = "\n".join(f"line {i:04d} " + "x" * 100 for i in range(80))

        unreserved = split_lines_for_embed(text)
        reserved = split_lines_for_embed(text, reserved=3000)

        assert reserved.kept < unreserved.kept


class TestListMinisCommandStaysWithinLimits:
    """!list_minis puts the same text in a description (4096, not 1024) - it has
    more headroom but is not immune."""

    @pytest.mark.asyncio
    async def test_description_stays_within_the_limit(self):
        from tests.conftest import make_ctx

        parent = make_puzzle(id=1, name="Minimeta", puzzle_type="Minimeta")
        cog = _cog_with_minis(_make_minis(200))
        cog.get_puzzle = MagicMock(return_value=parent)
        cog.get_hunt = MagicMock(return_value=make_hunt())
        ctx = make_ctx()

        await Puzzles.list_minis.callback(cog, ctx)

        embed = ctx.send.call_args.kwargs["embed"]
        assert len(embed.description or "") <= DESCRIPTION_LIMIT
        for field in embed.fields:
            assert len(field.value or "") <= FIELD_VALUE_LIMIT
        assert _embed_total(embed) <= EMBED_TOTAL_LIMIT
