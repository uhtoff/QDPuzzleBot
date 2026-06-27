"""Regression tests for Puzzles.group_code_autocomplete.

The autocomplete used to resolve the hunt by reading
``TagGroupJsonDb.get_by_attr(category_id=channel.category.id).hunt_id``
directly. That crashes in two real contexts:

  * the channel has no category (e.g. a thread / DM-like context), so
    ``channel.category.id`` raises ``AttributeError`` on ``None``; and
  * the channel lives in an overflow/solved category, which is owned via
    the ``tag_group_category`` join table, so the primary lookup returns
    ``None`` and ``None.hunt_id`` raises ``AttributeError``.

Autocomplete must never raise. It should resolve the owning group through
the same fallback the rest of the cog uses (``_tag_group_for_channel``) and
return ``[]`` when the context can't be resolved.
"""
from unittest.mock import MagicMock, patch

from tests.conftest import make_cog, make_round


def _interaction(category):
    interaction = MagicMock()
    channel = MagicMock()
    channel.category = category
    interaction.channel = channel
    return interaction


async def test_returns_empty_when_channel_has_no_category():
    """No category context (e.g. a thread) must yield [] instead of crashing."""
    cog = make_cog()
    interaction = _interaction(category=None)
    with patch("bot.cogs.puzzles.TagGroupJsonDb"), \
         patch("bot.cogs.puzzles.category_layout"):
        result = await cog.group_code_autocomplete(interaction, "")
    assert result == []


async def test_returns_empty_when_category_unowned():
    """A category owned by no group at all resolves to nothing -> []."""
    cog = make_cog()
    interaction = _interaction(category=MagicMock(id=12345))
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as tdb, \
         patch("bot.cogs.puzzles.category_layout") as cl:
        tdb.get_by_attr.return_value = None        # primary lookup misses
        cl.group_id_for_category.return_value = None  # join table misses too
        result = await cog.group_code_autocomplete(interaction, "")
    assert result == []


async def test_resolves_overflow_category_via_join_table():
    """Overflow/solved category: primary lookup misses but the join table
    resolves the owning group, so the hunt's metas are still listed."""
    cog = make_cog()
    interaction = _interaction(category=MagicMock(id=555))
    owning_group = make_round(id=7, hunt_id=10, category_id=555)
    meta = make_round(id=2, name="Meta One", hunt_id=10, group_code="ABC123")

    def fake_get_by_attr(**kw):
        if kw.get("category_id") is not None:
            return None            # overflow/solved category has no primary row
        if kw.get("id") == 7:
            return owning_group    # join table resolves to group 7
        return None

    with patch("bot.cogs.puzzles.TagGroupJsonDb") as tdb, \
         patch("bot.cogs.puzzles.category_layout") as cl:
        tdb.get_by_attr.side_effect = fake_get_by_attr
        tdb.get_all.return_value = [meta]
        cl.group_id_for_category.return_value = 7
        result = await cog.group_code_autocomplete(interaction, "")
    assert [c.value for c in result] == ["ABC123"]


async def test_lists_metas_for_resolved_primary_category():
    """Normal puzzle channel: primary lookup resolves the group directly."""
    cog = make_cog()
    interaction = _interaction(category=MagicMock(id=999))
    owning_group = make_round(id=1, hunt_id=10, category_id=999)
    meta = make_round(id=2, name="Meta One", hunt_id=10, group_code="ABC123")
    with patch("bot.cogs.puzzles.TagGroupJsonDb") as tdb, \
         patch("bot.cogs.puzzles.category_layout"):
        tdb.get_by_attr.return_value = owning_group
        tdb.get_all.return_value = [meta]
        result = await cog.group_code_autocomplete(interaction, "")
    assert [c.value for c in result] == ["ABC123"]
