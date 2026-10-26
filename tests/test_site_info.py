"""/api/auth/site: the public facts the login page shows before anyone signs in.

The logged-out page used to be a bare "Log in with Discord" card with no name,
no explanation and no owner — the shape of a phishing kit, which helped get the
test site flagged. Each instance now describes itself from its own config, so the
test site can say it is a test site while the live site says something else.
"""
from unittest.mock import MagicMock, patch


def _client(**web):
    from bot.web import create_app
    bot = MagicMock()
    bot.get_cog.return_value = None          # skip the WebNotifier sink wiring
    with patch("bot.web.config") as cfg:
        cfg.web_session_secret = "x" * 40
        cfg.web_oauth_redirect_uri = "https://example.test/cb"
        cfg.web_attachment_max_bytes = 1024
        cfg.web_frontend_dist = ""
        app = create_app(bot)
    site = patch.multiple("bot.web.auth.config", create=True, **web)
    return app.test_client(), site


async def test_site_info_is_public_and_comes_from_config():
    client, site = _client(web_site_name="Puzzbot", web_test_instance=True,
                           web_contact="ops@example.test")
    with site:
        resp = await client.get("/api/auth/site")          # no session at all
    assert resp.status_code == 200
    assert await resp.get_json() == {
        "name": "Puzzbot", "test_instance": True, "contact": "ops@example.test"}


async def test_an_unconfigured_instance_is_neutral_not_a_test_site():
    client, site = _client(web_site_name="Team Dashboard", web_test_instance=False,
                           web_contact="")
    with site:
        resp = await client.get("/api/auth/site")
    assert await resp.get_json() == {
        "name": "Team Dashboard", "test_instance": False, "contact": ""}


def _config(tmp_path, web):
    import json
    from bot.utils.config import Config
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"web": web}))
    return Config(filename=str(path))


def test_config_defaults_when_the_keys_are_absent(tmp_path):
    cfg = _config(tmp_path, {})
    assert (cfg.web_site_name, cfg.web_test_instance, cfg.web_contact) == ("Puzzbot", False, "")


def test_config_reads_the_keys(tmp_path):
    cfg = _config(tmp_path, {"site_name": "QD Puzzbot", "test_instance": True, "contact": "@russ"})
    assert (cfg.web_site_name, cfg.web_test_instance, cfg.web_contact) == ("QD Puzzbot", True, "@russ")
