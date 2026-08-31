""" Google Drive integration for puzzle organization

This is a separate cog so that Google Drive integration
can be easily disabled; simply omit this file.
"""
import contextvars
import logging
from typing import Any, List, Optional

import discord
from discord.ext import commands

from bot.utils import config
from bot.store import PuzzleData, TagGroupJsonDb
from bot.core.sheet_tables import (
    EVIDENCE_TABLE_NAME, UNASSIGNED_TABLE_NAME, contribution_table_name, select_table,
)
from bot.utils.gsheets import get_sheet, get_drive, execute_with_retry

logger = logging.getLogger(__name__)

# Request-scoped target spreadsheets. Each Discord command runs in its own asyncio
# Task, and ContextVars are isolated per Task, so concurrent commands from different
# teams/hunts cannot overwrite each other's target spreadsheet.
_current_spreadsheet_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_spreadsheet_id", default=None)
_current_solved_sheet_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_solved_sheet_id", default=None)
# Channel to notify if a Sheets call has to be retried, plus a one-shot flag so a
# single command posts at most one "retrying" notice. Request-scoped, like above.
_current_notify_channel: contextvars.ContextVar[Optional[discord.abc.Messageable]] = contextvars.ContextVar(
    "current_notify_channel", default=None)
_notified_flag: contextvars.ContextVar[Optional[List[bool]]] = contextvars.ContextVar(
    "notified_flag", default=None)


def _formula_string(value: str) -> str:
    """Escape a string for embedding in a Sheets formula argument (double any
    embedded double quotes), so e.g. a puzzle named The "Best" Puzzle doesn't
    produce an unparseable =HYPERLINK("url","The "Best" Puzzle") formula."""
    return (value or "").replace('"', '""')


class GoogleSheets(commands.Cog):
    STRING_INPUT = "stringValue"
    FORMULA_INPUT = "formulaValue"
    PUZZLE_SHEET = "Tab template"   # still used by create_additional_spreadsheet (Extra Sheets)
    INITIAL_OFFSET = 7
    sheets_service = None

    def __init__(self, bot):
        self.bot = bot
        self._puzzle_data = None
        self.overview_page_id = None
        self.sheets_service = get_sheet()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{type(self).__name__} Cog ready.")

    def set_spreadsheet_id(self, spreadsheet_id):
        _current_spreadsheet_id.set(spreadsheet_id)

    def set_solved_sheet_id(self, solved_sheet_id):
        _current_solved_sheet_id.set(solved_sheet_id)

    def get_spreadsheet_id(self):
        return _current_spreadsheet_id.get()

    def get_solved_sheet_id(self):
        return _current_solved_sheet_id.get()

    def set_notify_channel(self, channel):
        """Register the channel to warn if Sheets calls for this command stall."""
        _current_notify_channel.set(channel)
        _notified_flag.set([False])

    async def _notify_retry(self):
        """on_retry hook for execute_with_retry: warn the user once per command
        that a Google Sheets call is being retried (so the wait isn't silent)."""
        channel = _current_notify_channel.get()
        flag = _notified_flag.get()
        if channel is None or flag is None or flag[0]:
            return
        flag[0] = True
        try:
            await channel.send(
                "⏳ Google Sheets returned a temporary error; retrying your change — "
                "this may take a few seconds."
            )
        except Exception:
            logger.exception("Failed to send Sheets retry notice")

    async def batch_update(self, body, archive = False):
        spreadsheet_id = self.get_solved_sheet_id() if archive else self.get_spreadsheet_id()
        req = get_sheet().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=body
        )

        return await execute_with_retry(req, on_retry=self._notify_retry)


    async def sheet_list(self, spreadsheet_id = None):
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        # Every Sheets read in the bot funnels through here, so the payload size
        # is worth constraining (M21). An audit of all call sites found they read
        # only ["sheets"], ["properties"] (sheetId/title/index/hidden) and
        # .get("tables") -- anything added to that list must be added here too.
        response = await execute_with_retry(
            get_sheet().get(spreadsheetId=spreadsheet_id,
                            fields="sheets(properties,tables)"),
            on_retry=self._notify_retry
        )
        if "sheets" not in response:
            # A valid spreadsheet always reports at least one sheet, so a response
            # without the key means the read returned bad/empty data (a transient
            # Google glitch), not an empty spreadsheet. Fail loudly here instead of
            # letting callers raise an opaque KeyError deep in a comprehension — or,
            # worse, treat it as "no tabs" and create a duplicate tab.
            raise RuntimeError(
                f"Sheets get for {spreadsheet_id} returned no 'sheets' field: {response!r}"
            )
        return response

    async def get_unique_tab_name(self, desired_name: str, spreadsheet_id = None) -> str:
        if spreadsheet_id is None:
            existing = {
                s["properties"]["title"].casefold()
                for s in (await self.sheet_list())["sheets"]
            }
        else:
            meta = await execute_with_retry(
                get_sheet().get(spreadsheetId=spreadsheet_id), on_retry=self._notify_retry
            )
            existing = {
                s["properties"]["title"].casefold()
                for s in meta["sheets"]
            }

        if desired_name.casefold() not in existing:
            return desired_name

        i = 2
        while True:
            candidate = f"{desired_name} ({i})"
            if candidate.casefold() not in existing:
                return candidate
            i += 1

    async def get_page_id_by_name(self, name):
        for sheet in (await self.sheet_list())["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['sheetId']

    async def get_page_name_by_id(self, id, spreadsheet_id = None):
        for sheet in (await self.sheet_list(spreadsheet_id))["sheets"]:
            if (sheet['properties']['sheetId']) == int(id):
                return sheet['properties']['title']

    async def get_puzzle_sheet_index(self, puzzle:PuzzleData):
        name = puzzle.name
        for sheet in (await self.sheet_list())["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['index']
        return self.INITIAL_OFFSET

    def get_row(self, ref):
        return int(ref[1]) - 1

    def get_column(self, ref):
        return ord(ref[0].lower()) - 97

    # A target tab shifts its header right so column A is free to be the
    # contribution Table's hidden key column (labels land to the right, col A holds
    # the key). Both freshly generated and in-place-promoted targets use the same
    # geometry — header at TARGET_HEADER_SHIFT, Table at row 4 — so the shift is a
    # constant. A feeder tab — and any additional sheet, which is never a target
    # tab — keeps column A.
    TARGET_HEADER_SHIFT = 1

    def _header_shift(self, puzzle, page_id):
        """Columns to shift header writes right on `page_id`. Only a target's OWN
        main tab is shifted (by TARGET_HEADER_SHIFT); feeders and additional sheets
        (page_id != google_page_id) keep column A. A minimeta's own tab is promoted
        to the same shifted geometry (its mini-children render onto it) but is not a
        tag_group target, so it must be caught here too — otherwise solution/progress
        writes land one column left of the shifted header."""
        is_shifted_tab = getattr(puzzle, "is_target", False) \
            or getattr(puzzle, "is_minimeta", False)
        if not is_shifted_tab or page_id is None \
                or int(page_id) != int(puzzle.google_page_id or 0):
            return 0
        return self.TARGET_HEADER_SHIFT

    # --- Tab formatting (replaces the styling the old template tab carried) -----
    HEADER_FONT = "JetBrains Mono"
    HEADER_GREY = {"red": 0.851, "green": 0.851, "blue": 0.851}
    SOLUTION_GREEN = {"red": 0.0, "green": 0.85, "blue": 0.0}   # bright green once solved
    # Light grey for the "social contract" region below a table — a visual
    # "bot-managed, don't type here" (not a safety mechanism; the bot grows the
    # table by insert). See the Milestone D design doc.
    SOCIAL_GREY = {"red": 0.95, "green": 0.95, "blue": 0.95}
    LABEL_COL_WIDTH = 220   # px — fits "Puzzle Flavor Text:" etc.

    def _tab_format_requests(self, page_id, label_col):
        """Formatting a code-generated tab needs but a blank addSheet lacks: the
        whole tab in JetBrains Mono, the 3 header rows frozen + grey, and the label
        column widened. (The old duplicateSheet template carried these.)"""
        from bot.cogs import sheet_layout
        return [
            {"updateSheetProperties": {
                "properties": {"sheetId": page_id,
                               "gridProperties": {"frozenRowCount": sheet_layout.FROZEN_ROWS}},
                "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {
                "range": {"sheetId": page_id},
                "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": self.HEADER_FONT}}},
                "fields": "userEnteredFormat.textFormat.fontFamily"}},
            {"repeatCell": {
                "range": {"sheetId": page_id, "startRowIndex": 0,
                          "endRowIndex": sheet_layout.FROZEN_ROWS},
                "cell": {"userEnteredFormat": {"backgroundColor": self.HEADER_GREY}},
                "fields": "userEnteredFormat.backgroundColor"}},
            {"updateDimensionProperties": {
                "range": {"sheetId": page_id, "dimension": "COLUMNS",
                          "startIndex": label_col, "endIndex": label_col + 1},
                "properties": {"pixelSize": self.LABEL_COL_WIDTH},
                "fields": "pixelSize"}},
        ]

    def _label_bold_requests(self, page_id, cells, offset):
        """repeatCell requests that bold the static label cells. The captured
        layout marks the labels (Puzzle Title:, Solution: …) bold, but update_cell
        only writes userEnteredValue, so the bold flag is applied here. `offset`
        shifts the label column right for a target tab (matches generate_puzzle_tab)."""
        reqs = []
        for c in cells:
            if not c.get("bold"):
                continue
            row = self.get_row(c["a1"])
            col = self.get_column(c["a1"]) + offset
            reqs.append({"repeatCell": {
                "range": {"sheetId": page_id,
                          "startRowIndex": row, "endRowIndex": row + 1,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold"}})
        return reqs

    def _dynamic_bold_requests(self, page_id, offset):
        """Bold the dynamic header values the bot writes at runtime — the solution
        only (title/flavour/progress stay unbolded; only the static labels are
        bold). update_cell writes only userEnteredValue, so bolding the cell here
        (at tab creation) makes the value render bold and survives later value
        rewrites. `offset` matches the header shift."""
        reqs = []
        for ref in (config.puzzle_cell_solution,):
            row = self.get_row(ref)
            col = self.get_column(ref) + offset
            reqs.append({"repeatCell": {
                "range": {"sheetId": page_id,
                          "startRowIndex": row, "endRowIndex": row + 1,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold"}})
        return reqs

    # A thin solid black border, applied to every edge of each header cell.
    HEADER_BORDER = {"style": "SOLID", "width": 1,
                     "color": {"red": 0.0, "green": 0.0, "blue": 0.0}}
    HEADER_LAST_COL = 17   # header content spans cols A..Q (0..16); endIndex 17

    def _header_border_request(self, page_id, offset):
        """A thin border around every header cell (the frozen rows). The header
        spans columns A..Q, shifted right by `offset` on a target tab."""
        from bot.cogs import sheet_layout
        b = self.HEADER_BORDER
        return {"updateBorders": {
            "range": {"sheetId": page_id,
                      "startRowIndex": 0, "endRowIndex": sheet_layout.FROZEN_ROWS,
                      "startColumnIndex": offset,
                      "endColumnIndex": self.HEADER_LAST_COL + offset},
            "top": b, "bottom": b, "left": b, "right": b,
            "innerHorizontal": b, "innerVertical": b}}

    def _solution_bg_request(self, puzzle, colour):
        """Set the solution cell's background on a puzzle's own tab (green on solve,
        grey on unsolve), honouring the target header shift."""
        offset = self._header_shift(puzzle, puzzle.google_page_id)
        row = self.get_row(config.puzzle_cell_solution)
        col = self.get_column(config.puzzle_cell_solution) + offset
        return {"repeatCell": {
            "range": {"sheetId": puzzle.google_page_id,
                      "startRowIndex": row, "endRowIndex": row + 1,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "cell": {"userEnteredFormat": {"backgroundColor": colour}},
            "fields": "userEnteredFormat.backgroundColor"}}

    async def update_puzzle_info(self, puzzle: PuzzleData, update_tab_name = False, name = None, page_id = None):

        puzzle_name = name or puzzle.name

        new_sheet_id = page_id or puzzle.google_page_id
        offset = self._header_shift(puzzle, new_sheet_id)
        requests = [
            self.update_cell(
                f'=HYPERLINK("{_formula_string(puzzle.url)}","{_formula_string(puzzle_name)}")',
                self.get_row(config.puzzle_cell_name),
                self.get_column(config.puzzle_cell_name) + offset, self.FORMULA_INPUT, new_sheet_id),
        ]
        if update_tab_name:
            target_spreadsheet_id = self.get_solved_sheet_id() if puzzle.on_solved_sheet else self.get_spreadsheet_id()
            unique_name = await self.get_unique_tab_name(puzzle_name, target_spreadsheet_id)
            requests.append(self.set_sheet_name(unique_name, puzzle.google_page_id))
        updates = {
            'requests': requests
        }
        await self.batch_update(updates, puzzle.on_solved_sheet)

    def update_cell(self, value, start_row, start_column, type=STRING_INPUT, sheet_id=None):
        if sheet_id is None:
            raise ValueError("update_cell requires a sheet_id (no puzzle tab to write to)")
        body = {
                    'updateCells': {
                        'rows': [
                            {
                                'values':
                                    {
                                        'userEnteredValue': {
                                            type: value
                                        }
                                    }
                            }
                        ],
                        'fields': 'userEnteredValue',
                        'range': {
                            'sheetId': sheet_id,
                            'startRowIndex': start_row,
                            'endRowIndex': start_row+1,
                            'startColumnIndex': start_column,
                            'endColumnIndex': start_column+1
                        }
                    }
                }
        return body

    @staticmethod
    def _sheet_props_request(sheet_id, key, value):
        """Build an updateSheetProperties request that sets a single property."""
        return {
            'updateSheetProperties': {
                'properties': {
                    'sheetId': sheet_id,
                    key: value
                },
                'fields': key
            }
        }

    def move_sheet_to_index(self, sheet_id, index):
        """Build a request to move a sheet to an arbitrary index."""
        return self._sheet_props_request(sheet_id, 'index', index)

    def move_sheet_to_start(self, sheet_id = None):
        return self.move_sheet_to_index(sheet_id, self.INITIAL_OFFSET)

    async def move_sheet_to_end(self, sheet_id = None, end_index = None):
        # The Sheets API evaluates updateSheetProperties index moves against
        # pre-move positions, so len(sheets) (not len(sheets) - 1) is the index
        # that lands the tab truly last.
        #
        # `end_index` lets a caller building SEVERAL move requests for one batch
        # compute that number once (M21). Nothing has moved while the batch is
        # being assembled, so every fetch after the first returned the same
        # answer -- move_puzzle_to_solved_sheet paid for one per additional
        # sheet. Omit it and the count is fetched, as every other caller does.
        if end_index is None:
            end_index = len((await self.sheet_list()).get('sheets', []))
        return self._sheet_props_request(sheet_id, 'index', end_index)

    async def compute_insert_index(self, is_target: bool, puzzles) -> int:
        """Return the correct tab insertion index for a new puzzle.

        Target puzzles land after the last unsolved-target tab.
        Regular puzzles land after the last unsolved-regular tab (or the last
        target, if no regulars exist). Falls back to INITIAL_OFFSET when
        no unsolved puzzle tabs exist yet.
        """
        target_ids = {
            str(p.google_page_id) for p in puzzles
            if getattr(p, 'is_target', False) and not p.solved and p.google_page_id
        }
        regular_ids = {
            str(p.google_page_id) for p in puzzles
            if not getattr(p, 'is_target', False) and not p.solved and p.google_page_id
        }
        last_target_idx = None
        last_regular_idx = None
        for sheet in (await self.sheet_list())["sheets"]:
            props = sheet["properties"]
            if props.get("hidden"):
                continue
            sid = str(props["sheetId"])
            idx = props["index"]
            if sid in target_ids:
                if last_target_idx is None or idx > last_target_idx:
                    last_target_idx = idx
            elif sid in regular_ids:
                if last_regular_idx is None or idx > last_regular_idx:
                    last_regular_idx = idx
        if is_target:
            return (last_target_idx + 1) if last_target_idx is not None else self.INITIAL_OFFSET
        if last_regular_idx is not None:
            return last_regular_idx + 1
        if last_target_idx is not None:
            return last_target_idx + 1
        return self.INITIAL_OFFSET

    def revert_tab_colour(self, sheet_id):
        return self._sheet_props_request(sheet_id, 'tabColor', None)

    def change_tab_colour(self, colour, sheet_id):
        return self._sheet_props_request(sheet_id, 'tabColor', {colour: 1})

    async def add_new_sheet(self, name, index=INITIAL_OFFSET, sheet_type=PUZZLE_SHEET):
        sheet_id = await self.get_page_id_by_name(sheet_type)
        name = await self.get_unique_tab_name(name)
        body = {
            'requests': [
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'hidden': False
                        },
                        'fields': 'hidden'

                    }
                },
                {
                    "duplicateSheet": {
                        "sourceSheetId": sheet_id,
                        "insertSheetIndex": index,
                        "newSheetName": name
                    }
                },
                {
                    'updateSheetProperties': {
                        'properties': {
                            'sheetId': sheet_id,
                            'hidden': True
                        },
                        'fields': 'hidden'

                    }
                }

            ]
        }
        new_sheet = await self.batch_update(body)
        new_sheet_id = new_sheet['replies'][1]['duplicateSheet']['properties']['sheetId']
        return new_sheet_id

    async def add_new_puzzle_sheet(self, puzzle: PuzzleData, index = INITIAL_OFFSET):
        await self.generate_puzzle_tab(puzzle, is_target=puzzle.is_target, index=index)


    async def create_blank_tab(self, spreadsheet_id: str, name: str, index: Optional[int] = None) -> int:
        """Add a plain blank tab to a spreadsheet. Returns the new sheet's page id.

        `index` inserts the tab at that 0-based position (e.g. INITIAL_OFFSET, so
        new puzzles land at the left after the reserved sheets); when omitted,
        Sheets appends the tab at the end."""
        props: dict[str, Any] = {"title": name}
        if index is not None:
            props["index"] = index
        body = {
            "requests": [{
                "addSheet": {
                    "properties": props
                }
            }]
        }
        req = get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        result = await execute_with_retry(req, on_retry=self._notify_retry)
        return result["replies"][0]["addSheet"]["properties"]["sheetId"]

    def set_sheet_name(self, name, sheet_id = None):
        return self._sheet_props_request(sheet_id, 'title', name)


    async def delete_sheet(self, page_id = None, archive = False):
        body = {
            'requests': [
                {
                    'deleteSheet': {
                        'sheetId': page_id,
                    }
                }
            ]
        }
        await self.batch_update(body, archive)

    async def delete_sheet_tab(self, sheet_id, spreadsheet_id=None):
        """Delete a tab by sheetId via batchUpdate. Tolerates an already-gone tab."""
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        body = {"requests": [{"deleteSheet": {"sheetId": int(sheet_id)}}]}
        req = get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        await execute_with_retry(req, on_retry=self._notify_retry)

    async def update_puzzle(self, puzzle_data,update_name = False):
        await self.update_puzzle_info(puzzle_data, update_name)

    async def delete_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        await self.delete_sheet(puzzle_data.google_page_id, puzzle_data.on_solved_sheet)

    async def move_puzzle_sheet(self, to_solved, page, tab_name):
        """Move a tab between the live and solved spreadsheets. Returns the new page id.

        ORDER MATTERS: copy, rename, THEN delete the original (M19). The delete is
        the only destructive step and the only one whose failure cannot be undone,
        so it goes last.

        The new page id escapes solely through this return value -- callers do
        `sheet.google_page_id = await move_puzzle_sheet(...)` -- so any exception
        before that assignment leaves the DB holding the OLD id, which must
        therefore still name a live tab. Deleting first broke that: a failure in
        the rename window left the original deleted and the DB pointing at it,
        permanently, because _reconcile_tabs repairs a puzzle's main tab only and
        never its additional sheets.

        Both remaining failure windows are now recoverable. A rename failure
        leaves the original untouched and an unnamed copy on the destination; a
        delete failure leaves the named copy plus a duplicate original. A stray
        tab is fixable by hand, a dangling page id is not.

        Renaming before the delete does not change the name chosen: the delete
        targets the SOURCE spreadsheet, and get_unique_tab_name reads the
        destination.
        """
        spreadsheet_to = self.get_solved_sheet_id() if to_solved else self.get_spreadsheet_id()
        spreadsheet_from = self.get_spreadsheet_id() if to_solved else self.get_solved_sheet_id()
        copy_req = get_sheet().sheets().copyTo(
            spreadsheetId=spreadsheet_from,
            sheetId=page,
            body={"destinationSpreadsheetId": spreadsheet_to}
        )
        copied = await execute_with_retry(copy_req, on_retry=self._notify_retry)
        new_sheet_id = copied['sheetId']
        unique_title = await self.get_unique_tab_name(tab_name, spreadsheet_to)
        bu_req = get_sheet().batchUpdate(
            spreadsheetId=spreadsheet_to,
            body={
                "requests": [
                    self.set_sheet_name(unique_title, new_sheet_id),
                ]
            },
        )
        await execute_with_retry(bu_req, on_retry=self._notify_retry)
        await self.delete_sheet(page, not to_solved)
        return new_sheet_id

    async def restore_puzzle_from_solved_sheet(self, puzzle_data: PuzzleData, archive_spreadsheet = None, restore_index=None):
        if puzzle_data.on_solved_sheet:
            puzzle_data.on_solved_sheet = False
            puzzle_data.google_page_id = await self.move_puzzle_sheet(False, puzzle_data.google_page_id,
                                                                            puzzle_data.name)
        move_idx = restore_index if restore_index is not None else self.INITIAL_OFFSET
        requests = []
        for sheet in puzzle_data.additional_sheets:
            if len(sheet.google_page_id) > 0:
                # Each extra returns on ITS OWN flag, not the puzzle's. Keying
                # this on puzzle_data.on_solved_sheet left a target's archived
                # extras behind, then addressed their archive-side page ids to
                # the main spreadsheet -- a non-retryable 400 (H3).
                if self.get_solved_sheet_id() and sheet.on_solved_sheet:
                    current_name = await self.get_page_name_by_id(sheet.google_page_id, self.get_solved_sheet_id())
                    sheet.google_page_id = await self.move_puzzle_sheet(False, sheet.google_page_id, current_name)
                    sheet.on_solved_sheet = False
                requests.extend([self.update_cell("", self.get_row(config.puzzle_cell_solution),
                                             self.get_column(config.puzzle_cell_solution), self.STRING_INPUT,
                                             sheet.google_page_id),
                            self.update_cell("", self.get_row(config.puzzle_cell_progress),
                                             self.get_column(config.puzzle_cell_progress), self.STRING_INPUT,
                                             sheet.google_page_id),
                            self.move_sheet_to_index(sheet.google_page_id, move_idx),
                            self.revert_tab_colour(sheet.google_page_id)])

        main_offset = self._header_shift(puzzle_data, puzzle_data.google_page_id)
        requests.extend([self.update_cell("", self.get_row(config.puzzle_cell_solution),
                                     self.get_column(config.puzzle_cell_solution) + main_offset, self.STRING_INPUT, puzzle_data.google_page_id),
                    self.update_cell("", self.get_row(config.puzzle_cell_progress),
                                     self.get_column(config.puzzle_cell_progress) + main_offset, self.STRING_INPUT, puzzle_data.google_page_id),
                    self._solution_bg_request(puzzle_data, self.HEADER_GREY),   # un-green the solution cell
                    self.move_sheet_to_index(puzzle_data.google_page_id, move_idx),
                    self.revert_tab_colour(puzzle_data.google_page_id)])
        updates = {
            'requests': requests
        }
        await self.batch_update(updates)


    async def update_solution(self, puzzle_data: PuzzleData):
        offset = self._header_shift(puzzle_data, puzzle_data.google_page_id)
        requests = [self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution),
                                     self.get_column(config.puzzle_cell_solution) + offset, self.STRING_INPUT, puzzle_data.google_page_id)
                    ]
        updates = {
            'requests': requests
        }
        await self.batch_update(updates, puzzle_data.on_solved_sheet)

    async def move_puzzle_to_solved_sheet(self, puzzle_data: PuzzleData):
        offset = self._header_shift(puzzle_data, puzzle_data.google_page_id)
        requests = []
        # One tab count for every move request in this batch (M21): they are all
        # assembled before the single batch_update below, so nothing moves in
        # between and each re-fetch returned the same number.
        end_index = len((await self.sheet_list()).get('sheets', []))
        main_move_req = await self.move_sheet_to_end(puzzle_data.google_page_id,
                                                     end_index=end_index)
        requests.extend([self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution), self.get_column(config.puzzle_cell_solution) + offset, self.STRING_INPUT, puzzle_data.google_page_id),
            self.update_cell("Solved", self.get_row(config.puzzle_cell_progress), self.get_column(config.puzzle_cell_progress) + offset, self.STRING_INPUT, puzzle_data.google_page_id),
            self._solution_bg_request(puzzle_data, self.SOLUTION_GREEN),
            main_move_req,
            self.change_tab_colour('green', puzzle_data.google_page_id)])
        for sheet in puzzle_data.additional_sheets:
            if len(sheet.google_page_id) > 0:
                sheet_move_req = await self.move_sheet_to_end(sheet.google_page_id,
                                                              end_index=end_index)
                requests.extend([self.update_cell(puzzle_data.solution, self.get_row(config.puzzle_cell_solution),
                                             self.get_column(config.puzzle_cell_solution), self.STRING_INPUT,
                                             sheet.google_page_id),
                            self.update_cell("Solved", self.get_row(config.puzzle_cell_progress),
                                             self.get_column(config.puzzle_cell_progress), self.STRING_INPUT,
                                             sheet.google_page_id),
                            sheet_move_req,
                            self.change_tab_colour('green', sheet.google_page_id)])
        updates = {
            'requests': requests
        }
        await self.batch_update(updates)
        for sheet in puzzle_data.additional_sheets:
            # Guarded on a real page id, as the in-place loop above already is --
            # an extra with no tab cannot be copied anywhere (H3).
            if self.get_solved_sheet_id() and len(sheet.google_page_id) > 0:
                current_name = await self.get_page_name_by_id(sheet.google_page_id)
                sheet.google_page_id = await self.move_puzzle_sheet(True, sheet.google_page_id, current_name)
                # Record it PER SHEET. A target's own tab stays live, so
                # puzzle_data.on_solved_sheet cannot speak for its extras.
                sheet.on_solved_sheet = True
        if puzzle_data.is_target is False and self.get_solved_sheet_id():
            puzzle_data.google_page_id = await self.move_puzzle_sheet(True, puzzle_data.google_page_id, puzzle_data.name)
            puzzle_data.on_solved_sheet = True
            return
        else:
            return

    async def create_hunt_spreadsheet(self, hunt_name):
        permission = {
            'type': 'anyone',
            'role': 'writer'
        }
        body = {
            'name': hunt_name
        }
        new_file = await execute_with_retry(get_drive().files().copy(fileId=config.master_spreadsheet, body=body), on_retry=self._notify_retry)
        new_file_id = new_file['id']
        await execute_with_retry(get_drive().permissions().create(fileId=new_file_id, body=permission), on_retry=self._notify_retry)
        return new_file_id

    async def create_solved_sheet(self, hunt_name):
        permission = {
            'type': 'anyone',
            'role': 'writer'
        }
        new_file = await execute_with_retry(get_drive().files().create(body={
            "name": hunt_name + " Archive",
            "mimeType": "application/vnd.google-apps.spreadsheet",
        }), on_retry=self._notify_retry)
        new_file_id = new_file['id']
        await execute_with_retry(get_drive().permissions().create(fileId=new_file_id, body=permission), on_retry=self._notify_retry)
        self.set_solved_sheet_id(new_file_id)
        return new_file_id


    async def delete_hunt_spreadsheet(self, file_id: str):
        """Delete a whole Drive spreadsheet file by id. Tolerates already-gone files."""
        try:
            await execute_with_retry(
                get_drive().files().delete(fileId=file_id),
                on_retry=self._notify_retry,
            )
        except Exception:
            logger.exception("delete_hunt_spreadsheet: could not delete file %s (may already be gone)", file_id)

    async def create_puzzle_spreadsheet(self, puzzle_data: PuzzleData, index=None):
        """Creates new puzzle spreadsheet and adds puzzle data to the overview spreadsheet."""
        puzzle_index = index if index is not None else self.INITIAL_OFFSET
        await self.add_new_puzzle_sheet(puzzle_data, puzzle_index)
        await self.update_puzzle_info(puzzle_data)

    async def create_additional_spreadsheet(self, puzzle_data: PuzzleData, name = None, puzzle = False):
        index = await self.get_puzzle_sheet_index(puzzle_data)
        if name:
            sheet_name = f"{name} ({puzzle_data.name})"
        else:
            sheet_name = f"Extra Sheet for {puzzle_data.name}"
        sheet_id = await self.add_new_sheet(sheet_name, index+1)
        await self.update_puzzle_info( puzzle_data, False,name,sheet_id)
        return sheet_id

    async def promote_tab_to_group(self, puzzle_data: PuzzleData):
        """Promote a puzzle's existing tab into a target (group) tab in place.

        Inserts the contribution columns + native Table and regenerates the header
        (including the name hyperlink) via promote_tab_to_target. The tab is NOT
        renamed — the puzzle name is unchanged, and renaming it to its own name
        makes get_unique_tab_name treat the tab as a collision ("Name (2)").
        Inverse of demote_tab_to_puzzle."""
        await self.promote_tab_to_target(puzzle_data)

    async def demote_tab_to_puzzle(self, puzzle_data: PuzzleData):
        """Demote a target tab back to an ordinary puzzle tab in place — the exact
        inverse of promote_tab_to_target.

        Save the user flavour + bot progress/solution from the target (offset 1)
        header, delete the contribution Table and its CONTRIBUTION_WIDTH left
        columns (the solver grid slides back to column A), then regenerate the
        plain puzzle header at offset 0 and restore the saved values. The header
        is regenerated rather than shifted because promote now lands it at offset
        1, so the column delete removes it instead of sliding it back.

        The regenerated header includes the name hyperlink, so the tab is NOT
        renamed — renaming it to its own (unchanged) name makes get_unique_tab_name
        treat the tab as a collision and append "(2)"."""
        from bot.cogs import sheet_layout
        page_id = puzzle_data.google_page_id
        spreadsheet_id = self.get_spreadsheet_id()
        tab_name = await self.get_page_name_by_id(page_id)
        safe_name = tab_name.replace("'", "''") if tab_name else None

        # 0. Only a TARGET tab has contribution columns to remove. A retry after
        #    a mid-operation failure finds the tab already back at offset 0, and
        #    deleting again takes four columns of real solver work -- silently,
        #    since Sheets does not error on it (C5). Step 1 is gated with it: it
        #    reads at offset+1, which is the wrong cells on a plain tab.
        layout = await self._tab_layout(page_id, tab_name)
        in_target_layout = layout == self.LAYOUT_TARGET

        # 1. Save flavour/progress/solution from the current target header (offset 1).
        saved = {}
        if in_target_layout and safe_name is not None:
            shift = self.TARGET_HEADER_SHIFT
            for label, ref in [("flavour", "B2"),
                               ("progress", config.puzzle_cell_progress),
                               ("solution", config.puzzle_cell_solution)]:
                if not ref:
                    continue
                row, col = self.get_row(ref), self.get_column(ref)
                resp = await self._values_get(
                    spreadsheet_id, f"'{safe_name}'!{self._a1_cell(row, col + shift)}")
                vals = resp.get("values") or []
                if vals and vals[0]:
                    saved[label] = (vals[0][0], row, col)

        # 2. Delete the contribution columns (solver grid slides back to column A).
        if in_target_layout:
            await self.batch_update({"requests": [{
                "deleteDimension": {
                    "range": {"sheetId": page_id, "dimension": "COLUMNS",
                              "startIndex": 0, "endIndex": self.CONTRIBUTION_WIDTH},
                }
            }]})

        # 3. Wipe + regenerate the plain puzzle header at offset 0, restore values.
        requests = [
            {"unmergeCells": {"range": {
                "sheetId": page_id, "startRowIndex": 0,
                "endRowIndex": sheet_layout.FROZEN_ROWS}}},
            {"updateCells": {"range": {
                "sheetId": page_id, "startRowIndex": 0,
                "endRowIndex": sheet_layout.FROZEN_ROWS,
                "startColumnIndex": 0, "endColumnIndex": 26},
                "fields": "userEnteredValue"}},
        ]
        requests += self._puzzle_tab_header_requests(
            puzzle_data, page_id, 0, is_target=False)
        for label in ("flavour", "progress", "solution"):
            if label in saved:
                val, row, col = saved[label]
                requests.append(self.update_cell(
                    str(val), row, col, self.STRING_INPUT, page_id))
        await self.batch_update({"requests": requests})

    # --- Generic roll-up engine (M2) -------------------------------------
    # One ctx-free bulk writer replacing the two hand-rolled per-cell renderers.
    # It writes a whole roll-up in two values() calls (clear + update) instead of
    # ~750 single-cell updateCells ops, and snapshots values RAW so an answer like
    # "=A1" is stored as text and never a live formula — puzzles moved to the solved
    # puzzles sheet would otherwise resolve any cross-tab formula to #REF.

    @staticmethod
    def _col_letter(index: int) -> str:
        """0-based column index -> A1 column letters (0->A, 25->Z, 26->AA)."""
        letters = ""
        index += 1
        while index:
            index, rem = divmod(index - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    @classmethod
    def _a1_cell(cls, row: int, column: int) -> str:
        """0-based (row, column) -> A1 cell reference (0,0 -> A1)."""
        return f"{cls._col_letter(column)}{row + 1}"

    @staticmethod
    def _solution_text(puzzle: PuzzleData) -> str:
        """Answer-cell text for a roll-up row. A puzzle marked complete with no
        recorded answer carries the "✅" sentinel (see mark_as_complete); it is
        shown as-is so the row stays aligned instead of being silently dropped —
        the bug the old per-cell renderers had via a continue-before-row-increment."""
        return puzzle.solution if puzzle.solution else "Unsolved"

    def _target_spreadsheet_id(self, puzzle: PuzzleData):
        """Live spreadsheet, or the solved puzzles sheet once the puzzle has moved there."""
        return self.get_solved_sheet_id() if puzzle.on_solved_sheet else self.get_spreadsheet_id()

    # --- Developer metadata: stable tab pointers (M5 substrate, M7 load-bearing) ---
    # A tab's numeric sheetId is reassigned when a sheet is moved to the solved
    # spreadsheet (see move_puzzle_sheet), so a roll-up that keyed only on the
    # sheetId would dangle. Developer metadata is a searchable id that travels with
    # the tab. M5 records it as substrate but still renders via the meta puzzle's
    # google_page_id (the trusted pointer); M7 will resolve the target tab off it.

    async def ensure_tab_metadata(self, spreadsheet_id, page_id, key):
        """Idempotently attach searchable developer metadata `key` to the tab
        `page_id` in `spreadsheet_id`; return its metadataId. If `key` already
        exists in the spreadsheet, return the existing id instead of duplicating."""
        existing = await self._search_tab_metadata(spreadsheet_id, key)
        if existing is not None:
            return existing
        body = {"requests": [{"createDeveloperMetadata": {"developerMetadata": {
            "metadataKey": key,
            "location": {"sheetId": int(page_id)},
            "visibility": "DOCUMENT",
        }}}]}
        req = get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        resp = await execute_with_retry(req, on_retry=self._notify_retry)
        return (resp["replies"][0]["createDeveloperMetadata"]
                ["developerMetadata"]["metadataId"])

    async def _search_tab_metadata(self, spreadsheet_id, key):
        """Return the metadataId of developer metadata `key` in `spreadsheet_id`, or
        None if absent."""
        req = get_sheet().developerMetadata().search(
            spreadsheetId=spreadsheet_id,
            body={"dataFilters": [{"developerMetadataLookup": {"metadataKey": key}}]},
        )
        resp = await execute_with_retry(req, on_retry=self._notify_retry)
        matched = (resp or {}).get("matchedDeveloperMetadata", [])
        if matched:
            return matched[0]["developerMetadata"]["metadataId"]
        return None

    async def find_tab_by_metadata(self, spreadsheet_id, metadata_id):
        """Resolve the tab page id carrying developer metadata `metadata_id`, or None
        if absent (e.g. it didn't survive a copyTo/duplicate). Callers MUST fall back
        to a known page id — never trust this as the sole pointer."""
        req = get_sheet().developerMetadata().get(
            spreadsheetId=spreadsheet_id, metadataId=int(metadata_id))
        resp = await execute_with_retry(req, on_retry=self._notify_retry)
        return (resp or {}).get("location", {}).get("sheetId")

    async def stamp_rollup_metadata(self, puzzle: PuzzleData, key):
        """Best-effort: ensure developer metadata `key` is on this puzzle's tab,
        resolving the live-vs-archive spreadsheet. Returns (tab_page_id, metadata_id),
        or (None, None) if the puzzle has no sheet yet."""
        page_id = puzzle.google_page_id
        if page_id in (None, ""):
            return None, None
        spreadsheet_id = self._target_spreadsheet_id(puzzle)
        metadata_id = await self.ensure_tab_metadata(spreadsheet_id, int(page_id), key)
        return int(page_id), metadata_id

    async def _values_update(self, spreadsheet_id, a1_range, values):
        req = get_sheet().values().update(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            valueInputOption="RAW",   # store literally; never interpret as a formula
            body={"values": values},
        )
        return await execute_with_retry(req, on_retry=self._notify_retry)

    async def _values_clear(self, spreadsheet_id, a1_range):
        req = get_sheet().values().clear(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            body={},
        )
        return await execute_with_retry(req, on_retry=self._notify_retry)

    async def _values_get(self, spreadsheet_id, a1_range):
        req = get_sheet().values().get(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
        )
        return await execute_with_retry(req, on_retry=self._notify_retry)

    async def aggregate_to_tab(self, spreadsheet_id, tab_page_id, columns, rows,
                               start_row=4, start_column=0):
        """Write a `columns` header + `rows` data block to a tab as a snapshot.

        spreadsheet_id  target spreadsheet (caller picks live vs archive)
        tab_page_id     destination sheet's numeric page id
        columns         header strings, one per column
        rows            list of row-lists (cell strings), each len == len(columns)
        start_row/col   0-based top-left of the header (default A5, matching the
                        header row the old renderers used)

        Clears the old block first (replacing the fixed 50-blank-row padding) so a
        roll-up shorter than last time's leaves no stale trailing rows, then writes
        the header + rows in a single values().update.
        """
        tab_name = await self.get_page_name_by_id(tab_page_id, spreadsheet_id)
        if tab_name is None:
            raise RuntimeError(
                f"aggregate_to_tab: no tab with page id {tab_page_id} "
                f"in spreadsheet {spreadsheet_id}"
            )
        safe_name = tab_name.replace("'", "''")   # escape for the quoted A1 range
        start_cell = self._a1_cell(start_row, start_column)
        last_col = self._col_letter(start_column + len(columns) - 1)
        # Open-ended range: clear everything from the header row down across the
        # columns this roll-up owns (leaves the puzzle's own B1/B3/I1 + grid alone).
        await self._values_clear(spreadsheet_id, f"'{safe_name}'!{start_cell}:{last_col}")
        values = [list(columns)] + [list(r) for r in rows]
        await self._values_update(spreadsheet_id, f"'{safe_name}'!{start_cell}", values)

    async def add_metapuzzle_data(self, puzzle: PuzzleData, round_puzzles: List[PuzzleData]):
        rows = [
            [round_puzzle.name, self._solution_text(round_puzzle)]
            for round_puzzle in round_puzzles
            if round_puzzle.id != puzzle.id   # skip the meta's own row
        ]
        await self.aggregate_to_tab(
            self._target_spreadsheet_id(puzzle),
            puzzle.google_page_id,
            ["Puzzle titles", "Puzzle solutions"],
            rows,
        )

    async def add_metametapuzzle_data(self, puzzle: PuzzleData, round_puzzles: List[PuzzleData]):
        rows = []
        for round_puzzle in round_puzzles:
            if round_puzzle.id == puzzle.id:      # skip self
                continue
            if round_puzzle.is_target:      # metas roll up via their feeders, not here
                continue
            if len(round_puzzle.tags) > 1:        # ambiguous multi-round membership
                continue
            if round_puzzle.tags:
                round = TagGroupJsonDb.get_by_attr(id=round_puzzle.tags[0])
                round_name = round.name if round is not None else "*(deleted round)*"
            else:
                round_name = "No Round"
            rows.append([round_name, round_puzzle.name, self._solution_text(round_puzzle)])
        await self.aggregate_to_tab(
            self._target_spreadsheet_id(puzzle),
            puzzle.google_page_id,
            ["Puzzle Round", "Puzzle Title", "Puzzle Solution"],
            rows,
        )

    # Column layout for the Unassigned Puzzles tab. Bot owns A-D; column E
    # (Notes) belongs to solvers and is NEVER written by the reconcile.
    UNASSIGNED_HEADER = ["id", "Puzzle", "Answer", "Length", "Notes"]
    CONTRIBUTION_HEADER = ["key", "Source", "Type", "Content"]
    # Evidence tab columns — all bot-owned. Column A (id) is the hidden key the
    # reconcile diffs on (the evidence item id; Source Puzzle names are not unique).
    EVIDENCE_HEADER = ["id", "Source Puzzle", "Note", "Target Puzzle", "Logged At"]

    async def ensure_unassigned_table(self, page_id, show_round=False):
        """Prepare the Unassigned Puzzles tab: write the header row and, best
        effort, wrap it in a native Table (hidden id column) for nicer styling.

        Uses the context-var spreadsheet id (set by _gsheet_cog). Returns a table
        id to persist on the hunt (the native Table's id when one is created,
        otherwise the page id as a harmless sentinel). The service guards this
        behind a null table id, so it runs once per hunt; the data writes do not
        depend on the native Table succeeding.
        """
        spreadsheet_id = self.get_spreadsheet_id()
        tab_name = await self.get_page_name_by_id(page_id, spreadsheet_id)
        if tab_name is None:
            raise RuntimeError(f"ensure_unassigned_table: no tab with page id {page_id}")
        safe_name = tab_name.replace("'", "''")

        # Pin the Unassigned Feeders tab to the far-left (first) position. It is
        # created with create_blank_tab, which sets no index, so Sheets appends it at
        # the END — move it to index 0. Best effort: tab order is cosmetic.
        try:
            await execute_with_retry(
                get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body={
                    "requests": [self._sheet_props_request(page_id, "index", 0)]}),
                on_retry=self._notify_retry,
            )
        except Exception:
            logger.exception("ensure_unassigned_table: could not move tab to far left")

        # Header row, written plainly so data is visible even without the Table.
        await self._values_update(spreadsheet_id, f"'{safe_name}'!A1", [self.UNASSIGNED_HEADER])

        table_id = page_id
        try:
            body = {"requests": [{"addTable": {"table": {
                "name": UNASSIGNED_TABLE_NAME,
                "range": {
                    "sheetId": page_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(self.UNASSIGNED_HEADER),
                },
                "columnProperties": [
                    {"columnIndex": 0, "columnName": "id"},
                    {"columnIndex": 1, "columnName": "Puzzle"},
                    {"columnIndex": 2, "columnName": "Answer"},
                    {"columnIndex": 3, "columnName": "Length"},
                    {"columnIndex": 4, "columnName": "Notes"},
                ],
            }}}]}
            result = await execute_with_retry(
                get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body),
                on_retry=self._notify_retry,
            )
            table_id = result["replies"][0]["addTable"]["table"]["tableId"]
            # Hide the bookkeeping id column.
            await execute_with_retry(
                get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{
                    "updateDimensionProperties": {
                        "range": {"sheetId": page_id, "dimension": "COLUMNS",
                                  "startIndex": 0, "endIndex": 1},
                        "properties": {"hiddenByUser": True},
                        "fields": "hiddenByUser",
                    }
                }]}),
                on_retry=self._notify_retry,
            )
        except Exception:
            logger.exception("ensure_unassigned_table: native Table styling skipped")
        return table_id

    async def ensure_evidence_table(self, page_id):
        """Prepare the Evidence tab: pin it just after Unassigned Feeders, clear any
        old flat aggregate_to_tab render, write the header, and wrap it in a native
        Table (hidden id key column). Mirrors ensure_unassigned_table.

        Uses the context-var spreadsheet id (set by _gsheet_cog). Returns the native
        Table's id (or the page id as a harmless sentinel if the Table API is
        unavailable). The service guards this behind a null table id, so it runs once
        per hunt; the data writes do not depend on the native Table succeeding.
        """
        spreadsheet_id = self.get_spreadsheet_id()
        tab_name = await self.get_page_name_by_id(page_id, spreadsheet_id)
        if tab_name is None:
            raise RuntimeError(f"ensure_evidence_table: no tab with page id {page_id}")
        safe_name = tab_name.replace("'", "''")

        # Pin the Evidence tab to index 1 — immediately after Unassigned Feeders (0).
        # It is created with create_blank_tab, which sets no index, so Sheets appends
        # it at the END — move it. Best effort: tab order is cosmetic.
        try:
            await execute_with_retry(
                get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body={
                    "requests": [self._sheet_props_request(page_id, "index", 1)]}),
                on_retry=self._notify_retry,
            )
        except Exception:
            logger.exception("ensure_evidence_table: could not move tab after Unassigned Feeders")

        # One-time migration: wipe the old flat aggregate_to_tab render (header at A5
        # + rows) so the native Table anchored at A1 starts clean and no stale rows
        # linger below it. Best effort.
        try:
            await self._values_clear(spreadsheet_id, f"'{safe_name}'")
        except Exception:
            logger.exception("ensure_evidence_table: could not clear old evidence render")

        # Header row, written plainly so data is visible even without the Table, and
        # so the addTable anchors to populated cells (an empty header row lets Sheets
        # pad the front with phantom columns — the contribution-table live-test bug).
        await self._values_update(spreadsheet_id, f"'{safe_name}'!A1", [self.EVIDENCE_HEADER])

        table_id = page_id
        try:
            body = {"requests": [{"addTable": {"table": {
                "name": EVIDENCE_TABLE_NAME,
                "range": {
                    "sheetId": page_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(self.EVIDENCE_HEADER),
                },
                "columnProperties": [
                    {"columnIndex": 0, "columnName": "id"},
                    {"columnIndex": 1, "columnName": "Source Puzzle"},
                    {"columnIndex": 2, "columnName": "Note"},
                    {"columnIndex": 3, "columnName": "Target Puzzle"},
                    {"columnIndex": 4, "columnName": "Logged At"},
                ],
            }}}]}
            result = await execute_with_retry(
                get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body),
                on_retry=self._notify_retry,
            )
            table_id = result["replies"][0]["addTable"]["table"]["tableId"]
            # Hide the bookkeeping id column.
            await execute_with_retry(
                get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": [{
                    "updateDimensionProperties": {
                        "range": {"sheetId": page_id, "dimension": "COLUMNS",
                                  "startIndex": 0, "endIndex": 1},
                        "properties": {"hiddenByUser": True},
                        "fields": "hiddenByUser",
                    }
                }]}),
                on_retry=self._notify_retry,
            )
        except Exception:
            logger.exception("ensure_evidence_table: native Table styling skipped")
        return table_id

    # --- Task 6: contribution Table create --------------------------------

    async def _add_contribution_table(self, page_id, header_row=0):
        """Create the unified contribution native Table on a target's tab.

        Columns: A key (hidden), B Source, C Type, D Content. The table range
        starts at header_row and initially spans the header only; appendCells
        grows it over the body. Returns the native tableId (or page_id sentinel
        if the Table API is unavailable, matching ensure_unassigned_table).

        The header NAMES are written into the header-row cells (A-D) in the SAME
        batch, BEFORE the addTable, so the Table anchors to exactly these four
        populated columns. Creating it with an empty header row + `columnProperties`
        instead lets Sheets auto-size the Table wider — it pads the front with
        phantom "Column 1/2/3" columns and pushes our names to D-G (live-test bug).

        Table names must be unique within a spreadsheet AND char-safe (Google
        rejects '-'/apostrophes), so name the Table "Contributions <page_id>":
        page_id is unique per tab, so no two targets collide, and it sidesteps the
        round bug where a "X - meta" tab name broke addTable. The name is how the
        Table is found again: reconcile_table/_ensure_contribution_table match on
        it exactly (bot/core/sheet_tables.py), so creation and lookup must both
        go through contribution_table_name."""
        table_id = page_id
        # Google rejects Table names that contain '-' or apostrophes (spaces are
        # fine) — and the !round path suffixes its meta name with " - meta", so a
        # tab-derived name breaks addTable and the rollup is silently deferred
        # (the round bug). Use a page-unique, char-safe id.
        table_name = contribution_table_name(page_id)
        header_range = {
            "sheetId": page_id,
            "startRowIndex": header_row,
            "endRowIndex": header_row + 1,
            "startColumnIndex": 0,
            "endColumnIndex": len(self.CONTRIBUTION_HEADER),
        }
        body = {"requests": [
            {"updateCells": {
                "range": header_range,
                "fields": "userEnteredValue",
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": n}}
                    for n in self.CONTRIBUTION_HEADER]}],
            }},
            {"addTable": {"table": {"name": table_name, "range": header_range}}},
        ]}
        result = await self.batch_update(body)
        try:
            add_reply = next(r for r in result.get("replies", []) if "addTable" in r)
            table_id = add_reply["addTable"]["table"]["tableId"]
        except (StopIteration, KeyError, TypeError):
            return table_id
        # Hide the key column.
        await self.batch_update({"requests": [{
            "updateDimensionProperties": {
                "range": {"sheetId": page_id, "dimension": "COLUMNS",
                          "startIndex": 0, "endIndex": 1},
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        }]})
        return table_id

    # --- Task 7: generalized key-anchored reconciler ----------------------

    async def reconcile_table(self, page_id, desired_rows, data_col_count, numeric_cols=(),
                              grey_below=False, table_name=None):
        """Incrementally reconcile the native Table on `page_id` against the FULL
        `desired_rows` (the single source of truth — callers pass the whole desired
        set, not a pre-diff).

        Adaptive: locates the Table on the page and reads its own range, so the
        header row and key (first) column come from where the Table actually sits
        — unassigned at row 0 col A, the contribution table at row 4 col A. Keys are
        matched as strings (sheet cells read back as text) so int ids (Unassigned)
        and string keys ("feeds:2") both work.

        `numeric_cols` lists 0-based column indices (relative to the table's first
        column) written as numbers — blank cell when the value isn't numeric. The
        Unassigned Length column NEEDS this: a number-typed Table column rejects a
        string (esp. "" when unsolved), failing the whole atomic batch.

        After writing, resizes the Table to cover its header + all data rows, so the
        rows are part of the Table (otherwise the header is purely decorative).

        Pruned rows are blanked in place (their table-owned columns only), never
        deleted: `deleteDimension {dimension: ROWS}` is full-width and would shift
        any user-added columns to the right of the table on every other row too.
        A blanked row is never reused for a later append, so a row's identity (and
        anything a user has added beside it) stays stable across reconciles. This
        means the table only grows over a reconcile's lifetime; a user can manually
        sort the table's range in Sheets to reclaim blank rows."""
        from bot.core.unassigned import diff_rows
        spreadsheet_id = self.get_spreadsheet_id()

        sheet = None
        for s in (await self.sheet_list(spreadsheet_id))["sheets"]:
            if s["properties"]["sheetId"] == int(page_id):
                sheet = s
                break
        if sheet is None:
            return
        safe_name = sheet["properties"]["title"].replace("'", "''")

        # Use the Table's own range: header row + the (hidden) key column position.
        # Which Table is ours is decided by name, never by position — a solver may
        # add their own to the tab, and `Sheet.tables` has no documented order, so
        # simply moving a table around the tab can change who comes first.
        tables = sheet.get("tables", []) or []
        table = select_table(tables, table_name)
        if table is None and tables:
            # There ARE Tables here and none of them is ours — including the case
            # of a single one, which is a solver's when the name misses. Writing
            # would append this target's rows into their table and resize it over
            # them. Skip instead; the next reconcile self-heals once the named
            # Table exists (_ensure_contribution_table creates it).
            logger.error(
                "reconcile_table: no Table named %r on page %s (%d present) — "
                "refusing to write into a table that is not ours",
                table_name, page_id, len(tables),
            )
            return
        if table is not None:
            rng = table.get("range", {})
            header_row = rng.get("startRowIndex", 0)
            key_col = rng.get("startColumnIndex", 0)
            # Preserve the Table's column span on resize: the bot writes
            # `data_col_count` columns but the Table may legitimately span more
            # (Unassigned's solver-owned Notes column), which must stay in it.
            end_col = rng.get("endColumnIndex", key_col + data_col_count)
            table_id = table.get("tableId")
        else:
            header_row, key_col, end_col, table_id = 0, 0, data_col_count, None

        key_letter = self._col_letter(key_col)
        resp = await self._values_get(spreadsheet_id, f"'{safe_name}'!{key_letter}:{key_letter}")
        vals = resp.get("values", []) or []
        key_to_row = {}
        for i in range(header_row + 1, len(vals)):
            if vals[i]:
                key_to_row[str(vals[i][0])] = i

        # Stringify keys for the diff; keep the original cell values for writing.
        norm_rows = [[str(r[0])] + list(r[1:]) for r in desired_rows]
        appends, updates, prunes = diff_rows(list(key_to_row), norm_rows)

        numeric = set(numeric_cols)

        def _cell(col, v):
            if col in numeric:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return {"userEnteredValue": {"numberValue": v}}
                s = str(v).strip()
                if s and s.lstrip("-").isdigit():
                    return {"userEnteredValue": {"numberValue": int(s)}}
                return {}   # blank cell — valid in a number-typed Table column
            return {"userEnteredValue": {"stringValue": str(v)}}

        def _row_cells(row):
            return [_cell(c, v) for c, v in enumerate(row)]

        requests = []
        # Updates: rewrite the whole row (cols key_col..key_col+data_col_count-1).
        update_by_key = {row[0]: row for row in updates}
        for key, grid_row in key_to_row.items():
            row = update_by_key.get(key)
            if row is None:
                continue
            requests.append({"updateCells": {
                "range": {"sheetId": page_id, "startRowIndex": grid_row,
                          "endRowIndex": grid_row + 1, "startColumnIndex": key_col,
                          "endColumnIndex": key_col + data_col_count},
                "fields": "userEnteredValue",
                "rows": [{"values": _row_cells(row)}],
            }})
        # Prunes: blank the table's own columns in place, never delete the row —
        # deleteDimension ROWS is full-width and would shift any user-added columns
        # to the right of the table on every other row too.
        blank_cells = [{} for _ in range(data_col_count)]
        for grid_row in (key_to_row[k] for k in prunes):
            requests.append({"updateCells": {
                "range": {"sheetId": page_id, "startRowIndex": grid_row,
                          "endRowIndex": grid_row + 1, "startColumnIndex": key_col,
                          "endColumnIndex": key_col + data_col_count},
                "fields": "userEnteredValue",
                "rows": [{"values": blank_cells}],
            }})
        # Appends: write new rows strictly after the highest row currently in use.
        # Blanked (pruned) rows are never reused, so a row's identity stays stable.
        existing_rows = list(key_to_row.values())
        next_row = (max(existing_rows) + 1) if existing_rows else header_row + 1
        if appends:
            requests.append({"updateCells": {
                "range": {"sheetId": page_id, "startRowIndex": next_row,
                          "endRowIndex": next_row + len(appends), "startColumnIndex": key_col,
                          "endColumnIndex": key_col + data_col_count},
                "fields": "userEnteredValue",
                "rows": [{"values": _row_cells(row)} for row in appends],
            }})

        if requests:
            await self.batch_update({"requests": requests})

        # Grow the Table so the data rows actually belong to it. Never shrinks:
        # blanked prune rows stay part of the table's extent until a user sorts
        # them out manually.
        if table_id is not None:
            end_row = next_row + len(appends)
            await self.batch_update({"requests": [{"updateTable": {
                "table": {
                    "tableId": table_id,
                    "range": {"sheetId": page_id,
                              "startRowIndex": header_row, "endRowIndex": end_row,
                              "startColumnIndex": key_col,
                              "endColumnIndex": end_col},
                },
                "fields": "range",
            }}]})

            # Social-contract grey: shade the table's columns below the last data
            # row down to the bottom of the sheet ("bot-managed, don't type here").
            # Re-applied every reconcile so it tracks the table as it grows/shrinks.
            # The data rows themselves have their fill cleared (no fill) first, so
            # grey left over from a previous, shorter render doesn't show under
            # newly-added entries. (Clearing = naming backgroundColor in the field
            # mask but leaving it unset in the cell, which resets it to no fill.)
            if grey_below:
                row_count = sheet["properties"].get(
                    "gridProperties", {}).get("rowCount", end_row)
                fmt_reqs = []
                if end_row > header_row + 1:
                    fmt_reqs.append({"repeatCell": {
                        "range": {"sheetId": page_id, "startRowIndex": header_row + 1,
                                  "endRowIndex": end_row, "startColumnIndex": key_col,
                                  "endColumnIndex": end_col},
                        "cell": {"userEnteredFormat": {}},
                        "fields": "userEnteredFormat.backgroundColor"}})
                if row_count > end_row:
                    fmt_reqs.append({"repeatCell": {
                        "range": {"sheetId": page_id, "startRowIndex": end_row,
                                  "endRowIndex": row_count, "startColumnIndex": key_col,
                                  "endColumnIndex": end_col},
                        "cell": {"userEnteredFormat": {"backgroundColor": self.SOCIAL_GREY}},
                        "fields": "userEnteredFormat.backgroundColor"}})
                if fmt_reqs:
                    await self.batch_update({"requests": fmt_reqs})

    # --- Task 8: code-generated puzzle tab ---------------------------------

    def _puzzle_tab_header_requests(self, puzzle, page_id, offset, is_target):
        """batchUpdate requests for a puzzle/target tab's header, shifted right by
        `offset`: the captured static cells + merges, the dynamic name/url
        hyperlink (always bot-written, not from the artifact), tab styling
        (JetBrains Mono, frozen + grey header rows, wide label column at the
        offset), and bold static labels. Shared by generate_puzzle_tab (fresh
        tab) and promote_tab_to_target (stamped over an inserted column band) so
        the two paths produce an identical header geometry."""
        from bot.cogs import sheet_layout
        cells, merges = sheet_layout.puzzle_tab_layout(is_target)
        requests = []
        for c in cells:
            requests.append(self.update_cell(
                c["value"], self.get_row(c["a1"]), self.get_column(c["a1"]) + offset,
                c["type"], page_id))
        for start_r, end_r, start_c, end_c in merges:
            requests.append({
                "mergeCells": {
                    "range": {"sheetId": page_id,
                              "startRowIndex": start_r, "endRowIndex": end_r,
                              "startColumnIndex": start_c + offset,
                              "endColumnIndex": end_c + offset},
                    "mergeType": "MERGE_ALL",
                }})
        requests.append(self.update_cell(
            f'=HYPERLINK("{_formula_string(puzzle.url)}","{_formula_string(puzzle.name)}")',
            self.get_row(config.puzzle_cell_name),
            self.get_column(config.puzzle_cell_name) + offset,
            self.FORMULA_INPUT, page_id))
        requests += self._tab_format_requests(page_id, label_col=offset)
        requests += self._label_bold_requests(page_id, cells, offset)
        requests += self._dynamic_bold_requests(page_id, offset)
        requests.append(self._header_border_request(page_id, offset))
        return requests

    async def generate_puzzle_tab(self, puzzle, is_target, index=None):
        """Create a puzzle tab entirely in code (no template duplicateSheet).

        Adds a blank tab, writes the captured header layout + the dynamic
        name/url hyperlink, and (when is_target) the contribution Table.
        Sets puzzle.google_page_id. Returns the new page id."""
        from bot.cogs import sheet_layout
        import inspect as insp

        spreadsheet_id = self.get_spreadsheet_id()
        assert spreadsheet_id is not None  # sheet ops run with a hunt spreadsheet in context
        name = await self.get_unique_tab_name(puzzle.name)
        _result = self.create_blank_tab(spreadsheet_id, name, index)
        page_id = await _result if insp.iscoroutine(_result) else _result
        puzzle.google_page_id = page_id

        # Target tabs shift the header one column right so column A is free for the
        # contribution Table's hidden key column (labels in B, values in C+).
        offset = self.TARGET_HEADER_SHIFT if is_target else 0

        # Static header (captured cells + merges), dynamic name hyperlink, tab
        # styling and bold labels — all shifted right by `offset`. Shared with
        # promote_tab_to_target so a generated and a promoted target end identical.
        requests = self._puzzle_tab_header_requests(puzzle, page_id, offset, is_target)
        if requests:
            await self.batch_update({"requests": requests})

        # Contribution Table below the frozen header (row 4), so it never overlaps
        # the header merges in rows 1-3. Best effort: Google's Sheets write path
        # returns transient 500s ("Internal error encountered") on a busy spreadsheet,
        # and a flicker here must NOT abort meta creation (the user just sees a 500 and
        # a half-made meta). If it fails, the tab is left without the Table; it is
        # created lazily on the first contribution render (see _ensure_contribution_table).
        if is_target:
            try:
                await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS)
            except Exception:
                logger.exception(
                    "generate_puzzle_tab: contribution Table deferred for page %s "
                    "(transient Sheets error); it will be created on first render", page_id)
        return page_id

    # --- Task 9: in-place promotion ----------------------------------------

    CONTRIBUTION_WIDTH = 4   # cols A-D

    # The header layouts a contribution tab can be in. Telling them apart needs
    # A1 AND B1: a live probe against a scratch spreadsheet (D7, 2026-08-31)
    # confirmed that a tab whose columns were inserted but whose header was never
    # stamped returns the SAME empty A1 as a correctly promoted one -- Sheets
    # omits "values" entirely in both cases. A1 alone cannot separate them, so
    # the review's advice to reuse "the same check _ensure_contribution_table
    # uses" (which reads only A1) would not have worked.
    LAYOUT_PLAIN = "plain"      # offset 0: "Puzzle Title:" at A1
    LAYOUT_TARGET = "target"    # offset 1: A1 blank, "Puzzle Title:" at B1
    LAYOUT_UNKNOWN = "unknown"  # neither: half-promoted, or not a puzzle tab

    async def _tab_layout(self, page_id, tab_name=None):
        """Which header layout this tab is currently in. One A1:B1 read.

        The guard for every geometry-shifting mutation on a contribution tab.
        Both M18 (promote inserts 4 more columns per retry) and C5 (demote
        deletes 4 more) are retries that assumed a layout instead of checking
        it, and neither raises when wrong -- a second deleteDimension of the
        same range succeeds silently, eating real solver columns.
        """
        from bot.cogs import sheet_layout
        spreadsheet_id = self.get_spreadsheet_id()
        if tab_name is None:
            tab_name = await self.get_page_name_by_id(page_id)
        if tab_name is None:
            return self.LAYOUT_UNKNOWN
        safe_name = tab_name.replace("'", "''")
        resp = await self._values_get(spreadsheet_id, f"'{safe_name}'!A1:B1")
        rows = resp.get("values") or []
        row = rows[0] if rows else []
        label = sheet_layout.PUZZLE_TAB_CELLS[0]["value"]
        a1 = row[0] if len(row) > 0 else ""
        b1 = row[1] if len(row) > 1 else ""
        if a1 == label:
            return self.LAYOUT_PLAIN
        if b1 == label:
            return self.LAYOUT_TARGET
        return self.LAYOUT_UNKNOWN

    async def promote_tab_to_target(self, puzzle):
        """Transform an existing puzzle tab into a target tab in place, ending in
        the SAME geometry as a freshly generated target: header at offset 1, the
        contribution Table at A4.

        Insert CONTRIBUTION_WIDTH blank columns at the left so the solver grid
        rides clear to the right (cols E+), then unmerge + clear the frozen
        header rows and re-stamp the canonical target header at offset 1 (the
        insert pushes the old header to offset 4, whose merges would otherwise
        collide with the new ones). Finally add the Table at A4. The user's
        flavour text and the bot-written progress/solution values are preserved
        across the insert."""
        from bot.cogs import sheet_layout
        page_id = puzzle.google_page_id
        spreadsheet_id = self.get_spreadsheet_id()
        tab_name = await self.get_page_name_by_id(page_id)
        if tab_name is None:
            return
        safe_name = tab_name.replace("'", "''")
        offset = self.TARGET_HEADER_SHIFT

        # 0. Only a PLAIN tab needs the column insert. Promote is re-entered
        #    whenever step 4 failed (M18), and re-inserting walks the solver grid
        #    four columns right every time. Steps 1-3 are skipped too, not just
        #    the insert: step 1 reads the at-risk values at offset-0 positions,
        #    which on an already-shifted tab are the wrong cells.
        layout = await self._tab_layout(page_id, tab_name)
        if layout != self.LAYOUT_PLAIN:
            if layout == self.LAYOUT_UNKNOWN:
                # Columns in, header never stamped (a step-3 failure) -- or not
                # our tab at all. Either way, shifting it again is the damage.
                logger.warning(
                    "promote_tab_to_target: tab %s (page %s) is in neither plain nor "
                    "target layout; adding the Table without shifting it. Its header "
                    "may need stamping by hand.", tab_name, page_id)
            await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS)
            return

        # 1. Save at-risk values BEFORE the insert: flavour is user-owned; progress
        #    and solution are bot-written but carry solve state worth preserving.
        preserved = [("flavour", "B2"),                       # captured layout: B2
                     ("progress", config.puzzle_cell_progress),
                     ("solution", config.puzzle_cell_solution)]
        saved = {}
        for label, ref in preserved:
            if not ref:
                continue
            resp = await self._values_get(spreadsheet_id, f"'{safe_name}'!{ref}")
            vals = resp.get("values") or []
            if vals and vals[0]:
                saved[label] = (vals[0][0], self.get_row(ref), self.get_column(ref))

        # 2. Insert contribution-width columns at column A (solver grid -> E+).
        await self.batch_update({"requests": [{
            "insertDimension": {
                "range": {"sheetId": page_id, "dimension": "COLUMNS",
                          "startIndex": 0, "endIndex": self.CONTRIBUTION_WIDTH},
                "inheritFromBefore": False,
            }
        }]})

        # 3. Wipe the frozen header rows (unmerge + clear) so the canonical header
        #    can be stamped without colliding with the one the insert pushed right,
        #    then stamp the target header at offset 1 and restore the saved values.
        requests = [
            {"unmergeCells": {"range": {
                "sheetId": page_id, "startRowIndex": 0,
                "endRowIndex": sheet_layout.FROZEN_ROWS}}},
            {"updateCells": {"range": {
                "sheetId": page_id, "startRowIndex": 0,
                "endRowIndex": sheet_layout.FROZEN_ROWS,
                "startColumnIndex": 0, "endColumnIndex": 26},
                "fields": "userEnteredValue"}},
        ]
        requests += self._puzzle_tab_header_requests(puzzle, page_id, offset, is_target=True)
        for label in ("flavour", "progress", "solution"):
            if label in saved:
                val, row, col = saved[label]
                requests.append(self.update_cell(
                    str(val), row, col + offset, self.STRING_INPUT, page_id))
        await self.batch_update({"requests": requests})

        # 4. Add the contribution Table at A4 (below the frozen header).
        await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS)

    # --- Task 10: render contribution table from service -------------------

    async def _ensure_contribution_table(self, puzzle):
        """Ensure the target's contribution Table exists, creating it (below the
        frozen header) if missing. Meta creation adds it, but a transient Sheets
        500 there can leave the tab tableless — retrying the direct add self-heals
        that case.

        A tab imported from a legacy hunt (e.g. Puzzle Boat 10) may never have run
        through promote_tab_to_target/promote_tab_to_group at all, so it still
        carries the plain-puzzle header (offset 0: the "Puzzle Title:" label at
        A1) instead of target layout (offset 1: A1 reserved blank for the Table's
        hidden key column) — real content already occupies A-D there, so a direct
        addTable collides. Detect that via A1 and promote first so the column
        insert makes room, instead of colliding with what's already in place.

        Lets a creation failure propagate so the caller's refresh_target/!rebuild
        badges and reports it, rather than quietly reporting success while never
        writing the rollup."""
        from bot.cogs import sheet_layout
        page_id = puzzle.google_page_id
        spreadsheet_id = self.get_spreadsheet_id()
        for s in (await self.sheet_list(spreadsheet_id))["sheets"]:
            if s["properties"]["sheetId"] == int(page_id):
                # Ours specifically, not "a Table exists": select_table matches
                # strictly on the name the bot gave its own Table, so a solver's
                # table on the tab cannot stand in for the contribution Table.
                # It used to — generate_puzzle_tab defers Table creation past a
                # transient Sheets 500, and in that window a solver's table was
                # the lone one on the tab, so creation was skipped forever and
                # the reconciler wrote the rollup into their data instead.
                if select_table(s.get("tables"), contribution_table_name(page_id)):
                    return
                break
        tab_name = await self.get_page_name_by_id(page_id)
        if tab_name is None:
            await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS)
            return
        # One shared geometry probe rather than this function's own A1 read --
        # see _tab_layout for why A1 alone is not sufficient (D7).
        if await self._tab_layout(page_id, tab_name) == self.LAYOUT_PLAIN:
            await self.promote_tab_to_target(puzzle)
            return
        await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS)

    async def render_minimeta_contribution(self, minimeta, members):
        """Render the contribution table on a minimeta's own tab.

        Synthesizes a 'Feeder' row for each mini-puzzle child, keyed by puzzle id
        for stable reconciliation. Lazily promotes the tab to target layout (inserts
        the 4-column contribution area at A–D) if the Table is not yet present —
        this handles both new minimetas and ones created before this feature."""
        page_id = minimeta.google_page_id
        if page_id is None:
            return
        # _ensure_contribution_table is the stronger form of the bare has_table
        # test that used to live here (M18): it probes the tab's actual geometry
        # before promoting, where "no Table -> promote" re-shifted a
        # tableless-but-already-promoted tab and inserted four more columns.
        await self._ensure_contribution_table(minimeta)
        rows = [
            [f"feeds:{m.id}", m.name, "Feeder", m.solution.upper() if m.solution else ""]
            for m in members
            if m.id != minimeta.id
        ]
        await self.reconcile_table(
            page_id=page_id,
            desired_rows=rows,
            data_col_count=len(self.CONTRIBUTION_HEADER),
            grey_below=True,
            table_name=contribution_table_name(page_id),
        )

    async def render_contribution(self, target_puzzle, tag_group_id, members, evidence):
        """Render the unified contribution table on the target's tab.

        Excludes the target's own row, builds desired rows from edges/evidence,
        and reconciles the native Table on the target's page."""
        from bot.core.contribution import contribution_rows
        # Self-heal: creates/promotes the Table if it's missing (see
        # _ensure_contribution_table); raises if it still can't be made, which
        # refresh_target/!rebuild badge and report rather than silently no-op.
        await self._ensure_contribution_table(target_puzzle)
        # The target's own row is excluded — a meta is not a contribution of itself.
        # Its self-edge is 'target', so select_rollup_members already drops it from
        # every mode (round wants feeds/informs, explicit wants 'none', subtree skips
        # the meta). This guard is therefore defence-in-depth — and a safety net for any
        # un-migrated row whose self-edge is still 'none'. The exception: a meta that
        # explicitly feeds/informs itself (a self-edge loop) IS a contributor and must
        # appear on its own table, so it is kept.
        contributing = [
            p for p in members
            if p.id != target_puzzle.id
            or p.edges_for(tag_group_id) & {"feeds", "informs"}
        ]
        rows = contribution_rows(tag_group_id, contributing, evidence)
        await self.reconcile_table(
            page_id=target_puzzle.google_page_id,
            desired_rows=rows,
            data_col_count=len(self.CONTRIBUTION_HEADER),
            grey_below=True,
            table_name=contribution_table_name(target_puzzle.google_page_id),
        )


async def setup(bot):
    # Comment this out if google-drive-related package are not installed!
    await bot.add_cog(GoogleSheets(bot))