import json
import os

default_config = {
    "discord_bot_token": "",
    "prefix": "!",
    "database": "postgresql://localhost/postgres",
    "storage": "mysql",
    "debug": False
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
        self.database = os.getenv("DB_DSN")  # for docker
        self.owner_email = self.config.get("owner_email", None)
        self.master_spreadsheet = self.config.get("master_spreadsheet", None)
        self.storage = self.config.get("storage", default_config.get("storage"))
        if self.storage == "mysql":
            self.mysql_username = self.config.get("mysql_username", None)
            self.mysql_password = self.config.get("mysql_password", None)
        self.puzzle_addons_path = self.config.get("puzzle_addons_path", None)
        if not self.database:
            self.database = self.config.get("database", default_config.get("database"))
        self.debug = self.config.get("debug", default_config.get("debug"))

    def store(self):
        data = {"prefix": self.prefix, "discord_bot_token": self.token, "database": self.database}
        with open(self.filename, "w") as file:
            json.dump(data, file)
