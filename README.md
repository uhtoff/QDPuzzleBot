# Discord Bot for Sloopertroup
This is a fork of a [bot originally by Ladder Dogs](https://github.com/azjps/ladder_dogs_discord_bot)

Simple discord bot which manages puzzle channels for puzzle hunts via discord commands, used by a small-to-medium sized team.

This was initially created from [`cookiecutter-discord.py-postgres`](https://github.com/makupi/cookiecutter-discord.py-postgres). It is built on [discord.py](https://discordpy.readthedocs.io/) and uses [`google-api-python-client`](https://github.com/googleapis/google-api-python-client) for (optional) Google Sheets integration.

**Storage:** puzzle/round/hunt metadata lives in a **MySQL** database (configured via `storage: "mysql"` and the `mysql_username` / `mysql_password` / `database` keys in `config.json`). The original JSON-file / Postgres storage described in earlier versions of this README is no longer used.

**Dashboard (optional):** a React + [Mantine](https://mantine.dev/) single-page app (`frontend/`) talks to an embedded [Quart](https://quart.palletsprojects.com/) API (`bot/web/`) to provide a read/write web view of the board with Discord-OAuth login. It is off by default; enable it under the `web` key in `config.json`.

# Usage

## Creating a hunt

For example, run the command

`!hunt MysteryHunt2021:https://perpendicular.institute/`

This hunt URL will be used to guess the link to the puzzle when new puzzles are posted. If the generated
puzzle link is wrong, it can be updated by posting `!link https://correct-hunt-website-link` in the puzzle channel.

Users with the `manage_channel` role can update administrative settings via `!update_setting {key} {value}`, where
all of the settings can be viewed via `!show_settings`.

There are also various settings and links to Google Drive that should be updated prior to the start of hunt,
like

- `drive_parent_id`: the Google drive ID of the parent folder the hunt will be created under
- `drive_resources_id`: optional, Google drive ID of a resources Google doc
- `discord_bot_channel`: if set, most bot commands must be entered in that channel name
- `discord_use_voice_channels`: false by default; if true, voice channels are created alongside text channels

## During hunt

Most users will just need to become familiar with two commands:
1. To post a new puzzle channel, post `!p puzzle-name` in the `#meta` channel of the corresponding puzzle round.
   This will create a new text channel where puzzle discussion can take place, as well as
   a new Google Spreadsheet with a handy `Quick Links` worksheet.
   You can scroll the sidebar or use `Ctrl + K` to help search for existing puzzle channels.
2. When Hunt HQ has confirmed that the puzzle has been solved, post `!solve SOLUTION` in the puzzle channel.
   The channels will be automatically archived afterwards.

----

For a new round/world of puzzles, first start by posting `!round` in the `#bot` channel:
```
!r puzzle-round-name
```
This will create a `#puzzleround-name` [category](https://support.discord.com/hc/en-us/articles/115001580171-Channel-Categories-101)
along with a `#meta` puzzle text for the round. The `#meta` channels are the place for general discussion about the round,
as well as discussion about the meta puzzle (if there is more than one meta, creating new puzzle channels would be prudent).

For a new puzzle, one can either post the puzzle via `!puzzle` in the `#bot` channel:
```
!p puzzle-round-name: puzzle-name
```
Or simply `!p puzzle-name` in the corresponding round's `#meta` channel. This will create a `#puzzle-name` text and voice channel
where discussion of the puzzle can take place.

When the puzzle is solved, post `!solve SOLUTION` in the puzzle's channel. The text channel will automatically get archived (moved
to the `#solved-puzzles` category) after ~5 minutes, and the voice channel will be deleted. If this is mistakenly entered,
this can be undone by posting `!unsolve`.

There are various other commands for updating the status, type, priority, and notes associated with a puzzle.
These fields are mainly for others to easily find out about the status of other puzzles. They can be retrieved
on the discord channels using the corresponding commands (see `!info` for the available commands), or viewed
in aggregate on the Nexus spreadsheet, where all puzzles and links are listed.

## Google Drive

When a puzzle channel is created, if Google Drive integration is enabled, a corresponding spreadsheet is created
in the folder for the puzzle round in the root Google Drive directory. The spreadsheet will have a secondary
"Quick Links" tab created for convenience.

![Puzzle spreadsheet Quick Links tab example](docs/gsheet_puzzle_quick_links.png)

The bot periodically updates a "nexus spreadsheet" which shows a list of all puzzles along with relevant information
such as the puzzle url, spreadsheet link. (The formatting of the nexus spreadsheet can be done manually by the user;
the bot only populates the contents of the spreadsheet cells.)

![Nexus spreadsheet example](docs/gsheet_nexus_example.png)

# Setup

Clone this repository
```
git clone https://github.com/sloop-puzzles/puzzlehunt-discordbot
```
Create a [discord application, bot](https://realpython.com/how-to-make-a-discord-bot-python/), and add the bot's token to a [`config.json` file](https://github.com/makupi/cookiecutter-discord.py-postgres/blob/master/%7B%7Bcookiecutter.bot_slug%7D%7D/config.json) in the root directory of this project:
```json
{
  "discord_bot_token": "{{discord_bot_token}}",
  "prefix": "!",
  "storage": "mysql",
  "mysql_username": "puzzlebot",
  "mysql_password": "{{mysql_password}}",
  "database": "puzzlebot"
}
```
The bot connects to a local MySQL server using `mysql_username` / `mysql_password` and the
`database` (schema) name. To also enable the optional web dashboard, add a `web` block:
```json
{
  "web": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8099,
    "session_secret": "{{a long random string}}",
    "oauth_client_id": "{{discord_oauth_client_id}}",
    "oauth_client_secret": "{{discord_oauth_client_secret}}",
    "oauth_redirect_uri": "https://your-host/api/auth/callback",
    "frontend_url": "https://your-host/"
  }
}
```

For the Google Drive integration (optional), create a [Google service account (for example see these instructions from `gspread`)](
https://gspread.readthedocs.io/en/latest/oauth2.html#enable-api-access), and save the service account key JSON file as `google_secrets.json`.

## Database setup

The bot needs a MySQL/MariaDB database. The full schema is checked in under
[`migrations/`](migrations/) as ordered `NNN_*.sql` files, applied by the
migration runner in [`migrate.py`](migrate.py). The runner reads the same
`config.json` (`mysql_username` / `mysql_password` / `database`) the bot uses
and tracks which files have run in a `schema_migrations` table, so each applies
exactly once.

1. Create the database and a user (once), e.g. in the `mysql` client:
   ```sql
   CREATE DATABASE puzzlebot CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
   CREATE USER 'puzzlebot'@'localhost' IDENTIFIED BY '...';
   GRANT ALL PRIVILEGES ON puzzlebot.* TO 'puzzlebot'@'localhost';
   ```
   Put the matching `mysql_username` / `mysql_password` / `database` in `config.json`.

2. Apply the migrations (run inside the pipenv shell / virtualenv):
   ```bash
   python migrate.py            # apply every pending migration in order
   python migrate.py status     # show which migrations are applied vs pending
   ```

This stands a fresh database up from scratch (`000_base_schema.sql` onward). To
add a schema change later, drop a new `NNN_description.sql` file in
`migrations/` (use full-line `--` comments; separate statements with `;`) and
re-run `python migrate.py`.

**Golden rule: migration → deploy → code.** The bot's generic `commit()` writes
every dataclass field by name, so a column must exist in the database *before*
code that references it ships. Always run pending migrations first.

**Already have a hand-built database?** If your schema predates this runner, tell
it the existing migrations are already in place so it won't try to re-create
tables (which would error):
```bash
python migrate.py baseline   # record all present migrations as applied, run nothing
```
After baselining, future `python migrate.py` runs apply only genuinely new files.

Now you can run the bot by running the following in a shell:
```bash
# Setup python3 environment
pip install pipenv
pipenv install  # creates a new virtualenv
pipenv shell
# Start bot
python run.py
```
The environment variable `$LADDER_SPOT_DATA_DIR` can be used to control the directory where guild settings and puzzle data are stored.

## Tests

Use `pipenv install --dev` to install dev packages, and in the repo root directory, run
```bash
python -m pytest
```

# Credits

Inspired by various open source discord bot python projects like [cookiecutter-discord.py-postgres](https://github.com/makupi/cookiecutter-discord.py-postgres) and [discord-pretty-help](https://github.com/stroupbslayen/discord-pretty-help/). Licensed under [GPL 3.0](https://choosealicense.com/licenses/gpl-3.0/) (due to the aforementioned `cookiecutter`).
