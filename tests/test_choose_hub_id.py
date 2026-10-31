"""choose_hub_id: !archive_hunt_to_hub's hub is the server holding the hunt's hub
role when there is one; an explicit id is for hunts without one, and an id that
disagrees with the hub role is refused rather than trusted."""
import pytest

from bot.core.hunt_archive import HubUnavailable, choose_hub_id

HUB, OTHER = 1000, 2000


def test_no_id_uses_the_hub_role_s_server():
    assert choose_hub_id(None, HUB) == HUB


def test_no_id_and_no_hub_role_asks_for_the_id():
    with pytest.raises(HubUnavailable, match="server id"):
        choose_hub_id(None, None)


def test_an_id_is_used_when_the_hunt_has_no_hub_role():
    assert choose_hub_id(OTHER, None) == OTHER


def test_an_id_matching_the_hub_role_is_fine():
    assert choose_hub_id(HUB, HUB) == HUB


def test_an_id_disagreeing_with_the_hub_role_is_refused():
    with pytest.raises(HubUnavailable) as exc:
        choose_hub_id(OTHER, HUB)
    assert str(HUB) in str(exc.value) and str(OTHER) in str(exc.value)
