"""The panel's buttons: the custom_id carries action + hunt, the class is
registered at cog load so posted panels survive a restart, and a click always
gets an ephemeral answer."""
import re
from unittest.mock import AsyncMock, MagicMock, patch

from bot.core import role_panel
from bot.core.role_panel import HuntRoleButton, panel_view


def test_custom_ids_carry_the_action_and_the_hunt():
    assert HuntRoleButton("join", 42).custom_id == "hunt_role:join:42"
    assert HuntRoleButton("leave", 42).custom_id == "hunt_role:leave:42"


def test_the_template_matches_only_join_and_leave_with_a_numeric_hunt():
    t = re.compile(role_panel.CUSTOM_ID_TEMPLATE)
    assert t.fullmatch("hunt_role:join:42")
    assert t.fullmatch("hunt_role:leave:7")
    assert not t.fullmatch("hunt_role:delete:42")
    assert not t.fullmatch("hunt_role:join:abc")


async def test_from_custom_id_rebuilds_the_button_after_a_restart():
    match = re.fullmatch(role_panel.CUSTOM_ID_TEMPLATE, "hunt_role:leave:42")
    button = await HuntRoleButton.from_custom_id(MagicMock(), MagicMock(), match)
    assert (button.action, button.hunt_id) == ("leave", 42)


async def test_panel_view_is_persistent_with_join_then_leave():
    view = panel_view(42)
    assert view.timeout is None and view.is_persistent()
    assert [c.custom_id for c in view.children] == ["hunt_role:join:42", "hunt_role:leave:42"]
    assert [c.item.label for c in view.children] == ["Join", "Leave"]


def _interaction():
    i = MagicMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    return i


async def test_a_join_click_answers_privately_with_joins_reply():
    i = _interaction()
    with patch("bot.core.role_panel.join", new=AsyncMock(return_value="welcome")) as j, \
         patch("bot.core.role_panel.leave", new=AsyncMock()) as lv:
        await HuntRoleButton("join", 42).callback(i)
    i.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    j.assert_awaited_once_with(i.client, i.guild, i.user, 42)
    lv.assert_not_awaited()
    i.followup.send.assert_awaited_once_with("welcome", ephemeral=True)


async def test_a_leave_click_calls_leave():
    i = _interaction()
    with patch("bot.core.role_panel.join", new=AsyncMock()) as j, \
         patch("bot.core.role_panel.leave", new=AsyncMock(return_value="bye")) as lv:
        await HuntRoleButton("leave", 42).callback(i)
    lv.assert_awaited_once_with(i.guild, i.user, 42)
    j.assert_not_awaited()
    i.followup.send.assert_awaited_once_with("bye", ephemeral=True)


async def test_a_failure_still_answers_the_click():
    i = _interaction()
    with patch("bot.core.role_panel.join", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await HuntRoleButton("join", 42).callback(i)
    i.followup.send.assert_awaited_once()
    assert "went wrong" in i.followup.send.await_args.args[0]
    assert i.followup.send.await_args.kwargs == {"ephemeral": True}


async def test_cog_setup_registers_the_buttons_so_they_survive_a_restart():
    from bot.cogs import puzzles
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    with patch("bot.cogs.puzzles.Puzzles"):
        await puzzles.setup(bot)
    bot.add_dynamic_items.assert_called_once_with(HuntRoleButton)
