import json
import os

default_config = {
    "discord_bot_token": "",
    "prefix": "!",
    # MySQL database name (the bot is all-MySQL; see mysql_username/mysql_password).
    "database": "",
    "storage": "mysql",
    "debug": False,
    "debug_host": "127.0.0.1",
    "debug_port": 29781,
    "development": False,
    # Discord's real per-category channel cap is 50; overridable so a test
    # config can exercise category split/overflow without creating 45+ channels.
    "max_channels_per_category": 50,
}

class Config:
    def __init__(self, filename="config.json"):
        self.filename = filename
        self.config = {}
        if not os.path.isfile(filename):
            with open(filename, "w") as file:
                json.dump(default_config, file)
        with open(filename) as file:
            self.config = json.load(file)
        self.prefix = self.config.get("prefix", default_config.get("prefix"))
        self.token = self.config.get("discord_bot_token", default_config.get("discord_bot_token"))
        # Read before the config.json fallback below; the env var wins so a
        # container can point at a different database without a config edit.
        self.database = os.getenv("DB_DSN")
        self.master_spreadsheet = self.config.get("master_spreadsheet", None)
        self.puzzle_cell_name = self.config.get("puzzle_cell_name", None)
        self.puzzle_cell_solution = self.config.get("puzzle_cell_solution", None)
        self.puzzle_cell_progress = self.config.get("puzzle_cell_progress", None)
        self.storage = self.config.get("storage", default_config.get("storage"))
        if self.storage == "mysql":
            self.mysql_username = self.config.get("mysql_username", None)
            self.mysql_password = self.config.get("mysql_password", None)
        # Optional web dashboard config. When absent or disabled, the bot runs
        # exactly as before and no web server is started.
        web = self.config.get("web", {}) or {}
        self.web_enabled = bool(web.get("enabled", False))
        self.web_host = web.get("host", "127.0.0.1")
        self.web_port = int(web.get("port", 8099))
        self.web_session_secret = web.get("session_secret", "")
        self.web_oauth_client_id = web.get("oauth_client_id", "")
        self.web_oauth_client_secret = web.get("oauth_client_secret", "")
        self.web_oauth_redirect_uri = web.get("oauth_redirect_uri", "")
        # Where the SPA is served from; used to build post-login redirects.
        self.web_frontend_url = web.get("frontend_url", "")
        # Path to the built SPA (frontend/dist). Empty -> default alongside repo.
        self.web_frontend_dist = web.get("frontend_dist", "")
        # Message bridge (Discord -> web). Off by default so capture ships dark.
        self.web_bridge_enabled = bool(web.get("bridge_enabled", False))
        # Root dir for downloaded attachment bytes. Required when bridge is on.
        self.web_archive_media_dir = web.get("archive_media_dir", "")
        # Reject attachments larger than this (bytes). Default 25 MB (Discord free cap).
        self.web_attachment_max_bytes = int(web.get("attachment_max_bytes", 26214400))
        if not self.database:
            self.database = self.config.get("database", default_config.get("database"))
        self.debug = self.config.get("debug", default_config.get("debug"))
        # Remote-debugger endpoint, only consulted when debug is true (run.py).
        # Loopback by default: the previous hardcoded LAN address was one
        # developer's IDE and meant nothing on any other machine.
        self.debug_host = self.config.get("debug_host", default_config.get("debug_host"))
        self.debug_port = int(self.config.get("debug_port", default_config.get("debug_port")))
        self.development = self.config.get("development", default_config.get("development"))
        self.max_channels_per_category = int(self.config.get(
            "max_channels_per_category", default_config.get("max_channels_per_category")))
