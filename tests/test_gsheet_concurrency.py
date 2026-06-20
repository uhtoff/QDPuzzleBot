import asyncio

from bot.cogs.puzzles_gsheet import GoogleSheets


def test_spreadsheet_id_is_task_isolated():
    cog = GoogleSheets.__new__(GoogleSheets)  # skip __init__; set/get don't touch self

    async def team(sheet_id):
        cog.set_spreadsheet_id(sheet_id)
        await asyncio.sleep(0)                 # yield so the other task interleaves
        return cog.get_spreadsheet_id()

    async def scenario():
        return await asyncio.gather(
            asyncio.create_task(team("SHEET_A")),
            asyncio.create_task(team("SHEET_B")),
        )

    a, b = asyncio.run(scenario())
    assert a == "SHEET_A"   # FAILS on shared-attr code (reads "SHEET_B"); passes after fix
    assert b == "SHEET_B"
