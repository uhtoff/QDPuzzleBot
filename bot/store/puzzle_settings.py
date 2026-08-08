from dataclasses import dataclass
import datetime
import json
from dataclasses_json import dataclass_json


@dataclass_json
@dataclass
class GuildSettings:
    guild_id: int = 0
    id: int = 0
    guild_name: str = ""
    website_url: str = ""
    discord_bot_channel: str = ""   # Channel to listen for bot commands
    discord_bot_emoji: str = ":biohazard: :mag_right:"  # Short description string or emoji for bot messages
    discord_use_voice_channels: bool = False  # Whether to create voice channels for puzzles
    drive_parent_id: str = ""
    drive_resources_id: str = ""    # Document with resources links, etc
    # Comma-separated Discord role snowflakes granting each authorisation tier
    # (M8). Empty/NULL means NOT CONFIGURED, and the tier falls back to matching
    # the role NAMES in bot.core.constants — the pre-M8 behaviour, and the state
    # every guild starts in.
    admin_role_ids: str = ""
    organiser_role_ids: str = ""

    @classmethod
    def import_dict(cls, settings: dict):
        r = GuildSettings()
        for attr, value in r.__dict__.items():
            if attr == "notes":
                setattr(r, attr, json.loads(settings.get(attr) or "null"))
            elif attr.endswith("_time"):
                db_date = settings.get(attr,None)
                if db_date is not None:
                    utc_date = db_date.replace(tzinfo=datetime.timezone.utc)
                    setattr(r,attr,utc_date)
            else:
                setattr(r, attr, settings.get(attr, None))

        return r

