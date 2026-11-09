""" Google Drive integration for puzzle organization

This is a separate cog so that Google Drive integration
can be easily disabled; simply omit this file.
"""
import datetime
import contextvars
import logging
from typing import Any, List, Mapping, Optional

import discord
from googleapiclient.errors import HttpError
from discord.ext import commands

from bot.utils import config
from bot.store import PuzzleData
from bot.cogs.sheet_layout import COMPACT_EXTRA_GEOMETRY, COMPACT_GEOMETRY, HeaderGeometry
from bot.core.constants import HEADER_COMPACT, HEADER_FULL
from bot.core.sheet_location import Link, spreadsheet_for
from bot.core.sheet_tables import (
    EVIDENCE_TABLE_NAME, UNASSIGNED_TABLE_NAME, contribution_table_name, select_table,
)
from bot.utils.gsheets import get_sheet, get_drive, execute_with_retry, _is_retryable

FOLDER_MIME = "application/vnd.google-apps.folder"
_TEMPLATE_TABS: list[dict] | None = None

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

    def spreadsheet_of(self, obj) -> str:
        """The spreadsheet a puzzle's or extra's tab lives in: the location rule
        (sheet_location.spreadsheet_for) applied to the hunt ids in context.
        Hunt-level tabs (Unassigned, Evidence, hunt sheets) do not come through
        here -- they are on the hunt's live spreadsheet (spreadsheet_id=None)."""
        return spreadsheet_for(obj, self.get_spreadsheet_id() or "",
                               self.get_solved_sheet_id() or "")

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

    async def batch_update(self, body, spreadsheet_id: str | None = None,
                           retry_ambiguous: bool = True):
        """One batchUpdate on `spreadsheet_id`; None (or "") means the hunt's
        live spreadsheet. Pass retry_ambiguous=False for a batch that is unsafe to
        replay (see execute_with_retry)."""
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        req = get_sheet().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=body
        )

        return await execute_with_retry(req, on_retry=self._notify_retry,
                                        retry_ambiguous=retry_ambiguous)


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
        # One read path for every spreadsheet (None -> live): the same single
        # Sheets get as before, now with sheet_list's fields mask (M21) on the
        # explicit-id path too -- callers read only the titles.
        return self._unique_name(desired_name, [
            s["properties"]["title"]
            for s in (await self.sheet_list(spreadsheet_id))["sheets"]
        ])

    @staticmethod
    def _unique_name(desired_name: str, titles) -> str:
        """`desired_name`, or the first free "<name> (n)", against `titles`
        (casefolded)."""
        existing = {t.casefold() for t in titles}
        if desired_name.casefold() not in existing:
            return desired_name

        i = 2
        while True:
            candidate = f"{desired_name} ({i})"
            if candidate.casefold() not in existing:
                return candidate
            i += 1

    async def get_page_id_by_name(self, name, spreadsheet_id: str | None = None):
        for sheet in (await self.sheet_list(spreadsheet_id))["sheets"]:
            if (sheet['properties']['title']) == name:
                return sheet['properties']['sheetId']

    async def get_page_name_by_id(self, id, spreadsheet_id = None):
        for sheet in (await self.sheet_list(spreadsheet_id))["sheets"]:
            if (sheet['properties']['sheetId']) == int(id):
                return sheet['properties']['title']

    async def get_puzzle_sheet_index(self, puzzle:PuzzleData, spreadsheet_id: str | None = None):
        name = puzzle.name
        for sheet in (await self.sheet_list(spreadsheet_id))["sheets"]:
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

    def _full_geometry(self) -> HeaderGeometry:
        """The full (captured-template) header geometry, read at call time."""
        from bot.cogs import sheet_layout
        return HeaderGeometry(
            layout=HEADER_FULL,
            title_a1=config.puzzle_cell_name,
            status_a1=config.puzzle_cell_solution,
            progress_a1=config.puzzle_cell_progress,
            feeds_a1=sheet_layout.FEEDS_LINKS_A1,
            frozen_rows=sheet_layout.FROZEN_ROWS,
            band_rows=sheet_layout.FROZEN_ROWS,
            band_cols=self.HEADER_LAST_COL,
            grey_whole_rows=True)

    def _header_geometry(self, puzzle, page_id=None) -> HeaderGeometry:
        """The header geometry for one of `puzzle`'s tabs (main, or `page_id`).
        Compact only when the puzzle says so (a MagicMock attribute is full)."""
        if getattr(puzzle, "header_layout", None) == HEADER_COMPACT:
            if page_id is not None and int(page_id) != int(puzzle.google_page_id or 0):
                return COMPACT_EXTRA_GEOMETRY
            return COMPACT_GEOMETRY
        return self._full_geometry()

    def _tab_format_requests(self, page_id, label_col, geom=None):
        """Formatting a code-generated tab needs but a blank addSheet lacks: the
        whole tab in JetBrains Mono, the 3 header rows frozen + grey, and the label
        column widened. (The old duplicateSheet template carried these.)"""
        geom = geom or self._full_geometry()
        grey_range = {"sheetId": page_id, "startRowIndex": 0,
                      "endRowIndex": geom.band_rows}
        if not geom.grey_whole_rows:
            grey_range["startColumnIndex"] = label_col
            grey_range["endColumnIndex"] = label_col + geom.band_cols
        reqs: list = []
        if geom.frozen_rows:
            reqs.append({"updateSheetProperties": {
                "properties": {"sheetId": page_id,
                               "gridProperties": {"frozenRowCount": geom.frozen_rows}},
                "fields": "gridProperties.frozenRowCount"}})
        reqs += [
            {"repeatCell": {
                "range": {"sheetId": page_id},
                "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": self.HEADER_FONT}}},
                "fields": "userEnteredFormat.textFormat.fontFamily"}},
            {"repeatCell": {
                "range": grey_range,
                "cell": {"userEnteredFormat": {"backgroundColor": self.HEADER_GREY}},
                "fields": "userEnteredFormat.backgroundColor"}},
            {"updateDimensionProperties": {
                "range": {"sheetId": page_id, "dimension": "COLUMNS",
                          "startIndex": label_col, "endIndex": label_col + 1},
                "properties": {"pixelSize": self.LABEL_COL_WIDTH},
                "fields": "pixelSize"}},
        ]
        return reqs

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

    def _dynamic_bold_requests(self, page_id, offset, geom=None):
        """Bold the dynamic header values the bot writes at runtime — the solution
        only (title/flavour/progress stay unbolded; only the static labels are
        bold). update_cell writes only userEnteredValue, so bolding the cell here
        (at tab creation) makes the value render bold and survives later value
        rewrites. `offset` matches the header shift."""
        geom = geom or self._full_geometry()
        reqs = []
        for ref in (geom.status_a1,):
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

    def _header_border_request(self, page_id, offset, geom=None):
        """A thin border around every header cell (the frozen rows). The header
        spans columns A..Q, shifted right by `offset` on a target tab."""
        geom = geom or self._full_geometry()
        b = self.HEADER_BORDER
        return {"updateBorders": {
            "range": {"sheetId": page_id,
                      "startRowIndex": 0, "endRowIndex": geom.band_rows,
                      "startColumnIndex": offset,
                      "endColumnIndex": geom.band_cols + offset},
            "top": b, "bottom": b, "left": b, "right": b,
            "innerHorizontal": b, "innerVertical": b}}

    def _solution_bg_request(self, puzzle, colour):
        """Set the solution cell's background on a puzzle's own tab (green on solve,
        grey on unsolve), honouring the target header shift."""
        offset = self._header_shift(puzzle, puzzle.google_page_id)
        geom = self._header_geometry(puzzle, puzzle.google_page_id)
        row = self.get_row(geom.status_a1)
        col = self.get_column(geom.status_a1) + offset
        return {"repeatCell": {
            "range": {"sheetId": puzzle.google_page_id,
                      "startRowIndex": row, "endRowIndex": row + 1,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "cell": {"userEnteredFormat": {"backgroundColor": colour}},
            "fields": "userEnteredFormat.backgroundColor"}}

    def _title_request(self, puzzle, page_id, offset, geom, name=None) -> dict:
        """The name/url hyperlink write for the title cell. Compact: rich text
        (never a formula), so a name that looks like one is shown verbatim."""
        if geom.layout == HEADER_COMPACT:
            row = self.get_row(geom.title_a1)
            col = self.get_column(geom.title_a1) + offset
            url = puzzle.url
            return {"updateCells": {
                "rows": [{"values": [{
                    "userEnteredValue": {"stringValue": name or puzzle.name},
                    "textFormatRuns": [{"startIndex": 0,
                                        "format": {"link": {"uri": url}} if url else {}}]}]}],
                "fields": "userEnteredValue,textFormatRuns",
                "range": {"sheetId": page_id,
                          "startRowIndex": row, "endRowIndex": row + 1,
                          "startColumnIndex": col, "endColumnIndex": col + 1}}}
        return self.update_cell(
            f'=HYPERLINK("{_formula_string(puzzle.url)}","{_formula_string(name or puzzle.name)}")',
            self.get_row(geom.title_a1),
            self.get_column(geom.title_a1) + offset, self.FORMULA_INPUT, page_id)

    def _status_cell_request(self, page_id, offset, geom, answer: str | None) -> dict:
        """Compact A2: "Unsolved" on grey, or the answer on green and bold. Always
        a stringValue, so an answer that looks like a formula stays text."""
        solved = answer is not None
        row = self.get_row(geom.status_a1)
        col = self.get_column(geom.status_a1) + offset
        return {"updateCells": {
            "rows": [{"values": [{
                "userEnteredValue": {"stringValue": answer if solved else "Unsolved"},
                "userEnteredFormat": {
                    "backgroundColor": self.SOLUTION_GREEN if solved else self.HEADER_GREY,
                    "textFormat": {"bold": solved}}}]}],
            "fields": "userEnteredValue,userEnteredFormat.backgroundColor,"
                      "userEnteredFormat.textFormat.bold",
            "range": {"sheetId": page_id,
                      "startRowIndex": row, "endRowIndex": row + 1,
                      "startColumnIndex": col, "endColumnIndex": col + 1}}}

    def _compact_header_requests(self, puzzle, page_id, offset, geom, *,
                                 header_links=None, title=None) -> list:
        """The compact header: title, status, (feeds,) then format and border."""
        answer = puzzle.solution if puzzle.solved else None
        # Band grey and border first: the grey repeatCell covers the whole band,
        # so the status cell (green when solved) must be written after it.
        reqs = self._tab_format_requests(page_id, label_col=offset, geom=geom)
        reqs.append(self._header_border_request(page_id, offset, geom))
        reqs.append(self._title_request(puzzle, page_id, offset, geom, name=title))
        if geom.feeds_a1:
            reqs.append(self.header_links_request(
                page_id, offset, header_links or [], geom))
        reqs.append(self._status_cell_request(page_id, offset, geom, answer))
        return reqs

    def _solve_header_requests(self, puzzle, page_id, offset, geom, *, own_tab: bool) -> list:
        """Solution + "Solved" writes (and, on the puzzle's own tab, the green
        solution cell) for one tab. Compact: the A2 status cell alone (it carries
        its own green background)."""
        if geom.layout == HEADER_COMPACT:
            return [self._status_cell_request(page_id, offset, geom, puzzle.solution)]
        reqs = [
            self.update_cell(puzzle.solution, self.get_row(geom.status_a1),
                             self.get_column(geom.status_a1) + offset, self.STRING_INPUT, page_id),
            self.update_cell("Solved", self.get_row(geom.progress_a1),
                             self.get_column(geom.progress_a1) + offset, self.STRING_INPUT, page_id),
        ]
        if own_tab:
            reqs.append(self._solution_bg_request(puzzle, self.SOLUTION_GREEN))
        return reqs

    def _unsolve_header_requests(self, puzzle, page_id, offset, geom, *, own_tab: bool) -> list:
        """Clear the solution + progress cells (and, on the puzzle's own tab,
        un-green the solution cell) for one tab. Compact: A2 back to "Unsolved"."""
        if geom.layout == HEADER_COMPACT:
            return [self._status_cell_request(page_id, offset, geom, None)]
        reqs = [
            self.update_cell("", self.get_row(geom.status_a1),
                             self.get_column(geom.status_a1) + offset, self.STRING_INPUT, page_id),
            self.update_cell("", self.get_row(geom.progress_a1),
                             self.get_column(geom.progress_a1) + offset, self.STRING_INPUT, page_id),
        ]
        if own_tab:
            reqs.append(self._solution_bg_request(puzzle, self.HEADER_GREY))
        return reqs

    async def update_puzzle_info(self, puzzle: PuzzleData, update_tab_name = False, name = None, page_id = None,
                                 spreadsheet_id: str | None = None):
        """Write the name hyperlink on the puzzle's main tab, or on `page_id`
        (an extra tab, created by create_additional_spreadsheet, which passes
        the spreadsheet it made the tab in). Otherwise the puzzle's own
        spreadsheet by the location rule."""
        puzzle_name = name or puzzle.name
        target_spreadsheet_id = spreadsheet_id or self.spreadsheet_of(puzzle)

        new_sheet_id = page_id or puzzle.google_page_id
        offset = self._header_shift(puzzle, new_sheet_id)
        geom = self._header_geometry(puzzle, new_sheet_id)
        requests = [self._title_request(puzzle, new_sheet_id, offset, geom, name=puzzle_name)]
        if update_tab_name:
            unique_name = await self.get_unique_tab_name(puzzle_name, target_spreadsheet_id)
            requests.append(self.set_sheet_name(unique_name, puzzle.google_page_id))
        updates = {
            'requests': requests
        }
        await self.batch_update(updates, spreadsheet_id=target_spreadsheet_id)

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


    async def move_sheet_to_end(self, sheet_id = None, end_index = None,
                                spreadsheet_id: str | None = None):
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
            end_index = len((await self.sheet_list(spreadsheet_id)).get('sheets', []))
        return self._sheet_props_request(sheet_id, 'index', end_index)

    async def compute_insert_index(self, is_target: bool, puzzles,
                                   spreadsheet_id: str | None = None) -> int:
        """Return the correct tab insertion index for a new puzzle, among the
        tabs of `spreadsheet_id` (None -> live: where a tab hunt's new puzzle
        and unsolved puzzles are).

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
        for sheet in (await self.sheet_list(spreadsheet_id))["sheets"]:
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

    async def add_new_sheet(self, name, index=INITIAL_OFFSET, sheet_type=PUZZLE_SHEET,
                            spreadsheet_id: str | None = None):
        sheet_id = await self.get_page_id_by_name(sheet_type, spreadsheet_id)
        name = await self.get_unique_tab_name(name, spreadsheet_id)
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
        new_sheet = await self.batch_update(body, spreadsheet_id=spreadsheet_id)
        new_sheet_id = new_sheet['replies'][1]['duplicateSheet']['properties']['sheetId']
        return new_sheet_id

    async def add_new_puzzle_sheet(self, puzzle: PuzzleData, index = INITIAL_OFFSET,
                                   header_links=None):
        await self.generate_puzzle_tab(puzzle, is_target=puzzle.is_target, index=index,
                                       header_links=header_links)


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


    async def delete_sheet(self, page_id = None, spreadsheet_id: str | None = None):
        body = {
            'requests': [
                {
                    'deleteSheet': {
                        'sheetId': page_id,
                    }
                }
            ]
        }
        await self.batch_update(body, spreadsheet_id=spreadsheet_id)

    async def delete_sheet_tab(self, sheet_id, spreadsheet_id=None):
        """Delete a tab by sheetId via batchUpdate. Tolerates an already-gone tab."""
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        body = {"requests": [{"deleteSheet": {"sheetId": int(sheet_id)}}]}
        req = get_sheet().batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        await execute_with_retry(req, on_retry=self._notify_retry)

    async def update_puzzle(self, puzzle_data,update_name = False):
        await self.update_puzzle_info(puzzle_data, update_name)

    async def delete_puzzle_spreadsheet(self, puzzle_data: PuzzleData):
        """Delete a puzzle's sheet. A puzzle with its own file (files mode: a
        file id and no parent) loses the whole file; anything else -- a tab
        puzzle, or a mini, whose file id is its minimeta's -- loses only its
        tab, in the spreadsheet the location rule names. Decided from the row,
        so a mini can never take its minimeta's file with it."""
        if puzzle_data.google_sheet_id and not puzzle_data.parent_puzzle_id:
            if puzzle_data.google_sheet_id in (self.get_spreadsheet_id(),
                                               self.get_solved_sheet_id()):
                # Never take a hunt-level spreadsheet (the reference or solved
                # sheet) with a puzzle: a row pointing there is corrupt.
                logger.warning("delete_puzzle_spreadsheet: refusing to delete %s for "
                               "puzzle %s -- it is the hunt's own spreadsheet",
                               puzzle_data.google_sheet_id, puzzle_data.id)
                return
            await self.delete_file(puzzle_data.google_sheet_id)
            return
        await self.delete_sheet(puzzle_data.google_page_id, self.spreadsheet_of(puzzle_data))

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
        await self.delete_sheet(page, spreadsheet_from)
        return new_sheet_id

    @staticmethod
    def _files_placement(puzzle_data, hunt) -> tuple[bool, bool]:
        """Solve/unsolve placement dispatch: (files, own_file). Files hunt
        only when the caller passes one whose sheet_mode is "files"; own_file is
        a puzzle with its own spreadsheet (targets and minimetas included) --
        a mini (parent_puzzle_id) lives in its minimeta's file instead."""
        files = hunt is not None and getattr(hunt, "sheet_mode", "tabs") == "files"
        if files and not puzzle_data.google_sheet_id:
            # No file (its copy failed): there is nothing to address, and the
            # location rule would otherwise fall back to the reference sheet.
            raise RuntimeError("puzzle_sheet_missing")
        return files, files and not puzzle_data.parent_puzzle_id

    async def _move_puzzle_file(self, puzzle_data, to_folder, from_folder) -> None:
        """Files mode: move a solved/unsolved puzzle's file between the hunt's
        folders. Best-effort by design: the header writes already made the
        solve visible, and the file keeps its id and link, so a failed move is
        logged rather than reported as a failed solve; !reconcile apply's
        file_move puts it in the right folder."""
        if not (to_folder and from_folder):
            logger.warning("puzzle %s: hunt has no Drive folders; file %s not moved",
                           puzzle_data.id, puzzle_data.google_sheet_id)
            return
        try:
            await self.move_file(puzzle_data.google_sheet_id, to_folder, from_folder)
        except Exception:
            logger.exception("puzzle %s: could not move file %s to folder %s "
                             "(!reconcile apply repairs it)", puzzle_data.id,
                             puzzle_data.google_sheet_id, to_folder)

    async def restore_puzzle_from_solved_sheet(self, puzzle_data: PuzzleData, archive_spreadsheet = None,
                                               restore_index=None, *, hunt=None):
        """Unsolve placement (the dispatch point). Tabs: back from the solved
        spreadsheet to `restore_index` on the live one. Files (`hunt` given in
        files mode): the header is cleared in the puzzle's own spreadsheet;
        an own file keeps its tabs where they are and moves back to the hunt
        folder; a mini's tab moves to `restore_index` in its minimeta's file
        (no move when None)."""
        files, own_file = self._files_placement(puzzle_data, hunt)
        if puzzle_data.on_solved_sheet:
            puzzle_data.on_solved_sheet = False
            puzzle_data.google_page_id = await self.move_puzzle_sheet(False, puzzle_data.google_page_id,
                                                                            puzzle_data.name)
        if files:
            move_idx = None if own_file else restore_index
        else:
            move_idx = restore_index if restore_index is not None else self.INITIAL_OFFSET

        def move(page):
            return [] if move_idx is None else [self.move_sheet_to_index(page, move_idx)]
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
                extra_geom = self._header_geometry(puzzle_data, sheet.google_page_id)
                requests.extend([*self._unsolve_header_requests(
                                puzzle_data, sheet.google_page_id, 0, extra_geom, own_tab=False),
                            *move(sheet.google_page_id),
                            self.revert_tab_colour(sheet.google_page_id)])

        main_offset = self._header_shift(puzzle_data, puzzle_data.google_page_id)
        main_geom = self._header_geometry(puzzle_data, puzzle_data.google_page_id)
        requests.extend([*self._unsolve_header_requests(      # incl. un-greening the solution cell
                        puzzle_data, puzzle_data.google_page_id, main_offset, main_geom, own_tab=True),
                    *move(puzzle_data.google_page_id),
                    self.revert_tab_colour(puzzle_data.google_page_id)])
        updates = {
            'requests': requests
        }
        # Tabs: every tab above is back on the live spreadsheet now. Files: they
        # never left the puzzle's (or its minimeta's) file.
        await self.batch_update(updates, spreadsheet_id=self.spreadsheet_of(puzzle_data)
                                if files else self.get_spreadsheet_id())
        if own_file and hunt is not None:
            await self._move_puzzle_file(puzzle_data, hunt.drive_folder_id, hunt.solved_folder_id)


    async def update_solution(self, puzzle_data: PuzzleData):
        offset = self._header_shift(puzzle_data, puzzle_data.google_page_id)
        geom = self._header_geometry(puzzle_data, puzzle_data.google_page_id)
        requests = [self.update_cell(puzzle_data.solution, self.get_row(geom.status_a1),
                                     self.get_column(geom.status_a1) + offset, self.STRING_INPUT, puzzle_data.google_page_id)
                    ]
        if geom.layout != HEADER_COMPACT:
            await self.batch_update({'requests': requests},
                                    spreadsheet_id=self.spreadsheet_of(puzzle_data))
            return
        # Compact extras show the answer too (value only: the status cell's
        # colour and bold are already set). Each goes to the spreadsheet its
        # tab is in, which can differ from the main tab's.
        by_sheet = {self.spreadsheet_of(puzzle_data): requests}
        for sheet in puzzle_data.additional_sheets:
            if len(sheet.google_page_id) > 0:
                extra_geom = self._header_geometry(puzzle_data, sheet.google_page_id)
                by_sheet.setdefault(self.spreadsheet_of(sheet), []).append(
                    self.update_cell(puzzle_data.solution, self.get_row(extra_geom.status_a1),
                                     self.get_column(extra_geom.status_a1), self.STRING_INPUT,
                                     sheet.google_page_id))
        for spreadsheet_id, reqs in by_sheet.items():
            await self.batch_update({'requests': reqs}, spreadsheet_id=spreadsheet_id)

    async def move_puzzle_to_solved_sheet(self, puzzle_data: PuzzleData, *, hunt=None):
        """Solve placement (the dispatch point). Tabs: the in-place solve batch
        (solution, "Solved", green cell, tab to the end, green tab) runs on the
        live spreadsheet, then the tabs move to the solved spreadsheet. Files
        (`hunt` given in files mode): the same batch runs in the puzzle's own
        spreadsheet; a puzzle with its own file (targets and minimetas
        included) keeps its tabs in place and the FILE moves to the hunt's
        Solved folder -- same id, same link, on_solved_sheet stays False; a
        mini's tab goes to the end of its minimeta's file, with no Drive call."""
        files, own_file = self._files_placement(puzzle_data, hunt)
        live = self.spreadsheet_of(puzzle_data) if files else self.get_spreadsheet_id()
        offset = self._header_shift(puzzle_data, puzzle_data.google_page_id)
        requests = []
        # One tab count for every move request in this batch (M21): they are all
        # assembled before the single batch_update below, so nothing moves in
        # between and each re-fetch returned the same number. An own file needs
        # none: its tabs stay where they are.
        end_index = None if own_file else len((await self.sheet_list(live)).get('sheets', []))

        async def move_to_end(page):
            if end_index is None:
                return []
            return [await self.move_sheet_to_end(page, end_index=end_index)]
        main_move_req = await move_to_end(puzzle_data.google_page_id)
        main_geom = self._header_geometry(puzzle_data, puzzle_data.google_page_id)
        requests.extend([*self._solve_header_requests(
                puzzle_data, puzzle_data.google_page_id, offset, main_geom, own_tab=True),
            *main_move_req,
            self.change_tab_colour('green', puzzle_data.google_page_id)])
        for sheet in puzzle_data.additional_sheets:
            if len(sheet.google_page_id) > 0:
                sheet_move_req = await move_to_end(sheet.google_page_id)
                extra_geom = self._header_geometry(puzzle_data, sheet.google_page_id)
                requests.extend([*self._solve_header_requests(
                                puzzle_data, sheet.google_page_id, 0, extra_geom, own_tab=False),
                            *sheet_move_req,
                            self.change_tab_colour('green', sheet.google_page_id)])
        updates = {
            'requests': requests
        }
        await self.batch_update(updates, spreadsheet_id=live)
        if files:
            # Extras ride along inside the file; nothing crosses spreadsheets.
            if own_file and hunt is not None:
                await self._move_puzzle_file(puzzle_data, hunt.solved_folder_id,
                                             hunt.drive_folder_id)
            return
        for sheet in puzzle_data.additional_sheets:
            # Guarded on a real page id, as the in-place loop above already is --
            # an extra with no tab cannot be copied anywhere (H3).
            if self.get_solved_sheet_id() and len(sheet.google_page_id) > 0:
                current_name = await self.get_page_name_by_id(sheet.google_page_id, live)
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

    # --- Drive primitives for files-mode hunts (one spreadsheet per puzzle) ---

    async def create_folder(self, name: str, parent_id: str | None = None) -> str:
        """Create a Drive folder under `parent_id` (else the service account's root)."""
        body: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            body["parents"] = [parent_id]
        folder = await execute_with_retry(
            get_drive().files().create(body=body, fields="id"),
            on_retry=self._notify_retry,
        )
        return folder["id"]

    async def share_anyone_reader(self, file_id: str) -> None:
        """Let anyone with the link view `file_id` (a folder's children inherit it)."""
        await execute_with_retry(
            get_drive().permissions().create(
                fileId=file_id, body={"type": "anyone", "role": "reader"}),
            on_retry=self._notify_retry,
        )

    async def _share_anyone_writer(self, file_id: str) -> None:
        await execute_with_retry(
            get_drive().permissions().create(
                fileId=file_id, body={"type": "anyone", "role": "writer"}),
            on_retry=self._notify_retry,
        )

    async def copy_template(self, name: str, folder_id: str) -> str:
        """Copy the master spreadsheet into `folder_id`, shared anyone-with-link writer."""
        new_file = await execute_with_retry(
            get_drive().files().copy(
                fileId=config.master_spreadsheet,
                body={"name": name, "parents": [folder_id]},
                fields="id"),
            on_retry=self._notify_retry,
        )
        new_id = new_file["id"]
        try:
            await self._share_anyone_writer(new_id)
        except Exception:
            # The caller never learns the id, so nobody else could clean it up.
            try:
                await self.delete_file(new_id)
            except Exception:
                logger.exception("copy_template: could not delete unshared copy %s", new_id)
            raise
        return new_id

    async def move_file(self, file_id: str, to_folder: str,
                        from_folder: str | None = None) -> list[str]:
        """Move a file to `to_folder` (a comma-joined list is allowed). With no
        `from_folder` its actual current parents are looked up and all removed --
        Drive's "root" alias is unreliable for a file made by files().copy.
        Returns the parents removed, so a caller can move the file back."""
        if from_folder is None:
            meta = await execute_with_retry(
                get_drive().files().get(fileId=file_id, fields="parents"),
                on_retry=self._notify_retry,
            )
            removed = list(meta.get("parents", []))
        else:
            removed = [from_folder]
        await execute_with_retry(
            get_drive().files().update(
                fileId=file_id, addParents=to_folder, removeParents=",".join(removed),
                fields="id"),
            on_retry=self._notify_retry,
        )
        return removed

    async def get_file(self, file_id: str) -> dict | None:
        """One Drive file's {id, name, parents, trashed}, or None when it is gone
        (404). Any other error raises: a caller must not read an outage as
        "the file does not exist"."""
        try:
            return await execute_with_retry(
                get_drive().files().get(fileId=file_id, fields="id,name,parents,trashed"),
                on_retry=self._notify_retry,
            )
        except HttpError as e:
            if getattr(e.resp, "status", None) == 404:
                return None
            raise

    async def rename_file(self, file_id: str, name: str) -> None:
        await execute_with_retry(
            get_drive().files().update(fileId=file_id, body={"name": name}, fields="id"),
            on_retry=self._notify_retry,
        )

    async def delete_file(self, file_id: str) -> None:
        """Delete a Drive file; an already-gone file (404) is fine, anything else raises."""
        try:
            await execute_with_retry(
                get_drive().files().delete(fileId=file_id),
                on_retry=self._notify_retry,
            )
        except HttpError as e:
            if getattr(e.resp, "status", None) == 404:
                logger.info("delete_file: %s already gone", file_id)
                return
            raise

    async def list_folder(self, folder_id: str) -> list[dict]:
        """Non-trashed children of a folder as [{id, name, mimeType}], all pages."""
        items: list[dict] = []
        page_token = None
        while True:
            kwargs: dict[str, Any] = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": "nextPageToken, files(id, name, mimeType)",
                "pageSize": 1000,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            resp = await execute_with_retry(
                get_drive().files().list(**kwargs), on_retry=self._notify_retry)
            items.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return items

    async def list_modified_since(self, folder_id: str, since: datetime.datetime) -> list[dict]:
        """Non-trashed children of a folder modified after ``since`` (aware UTC), as
        [{id, mimeType, modifiedTime, lastModifyingUser: {me}}], all pages. Feeds the
        sheet-activity poller (bot/cogs/sheet_activity.py): one call per hunt folder."""
        stamp = since.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        items: list[dict] = []
        page_token = None
        while True:
            kwargs: dict[str, Any] = {
                "q": (f"'{folder_id}' in parents and trashed=false "
                      f"and modifiedTime > '{stamp}'"),
                "fields": ("nextPageToken, "
                           "files(id, mimeType, modifiedTime, lastModifyingUser(me))"),
                "pageSize": 1000,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            resp = await execute_with_retry(
                get_drive().files().list(**kwargs), on_retry=self._notify_retry)
            items.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return items

    async def template_tabs(self) -> list[dict]:
        """The master spreadsheet's tabs as [{sheetId, title, hidden}], read once per
        process (a Drive copy keeps sheet ids, so they identify tabs in a fresh copy)."""
        global _TEMPLATE_TABS
        if _TEMPLATE_TABS is None:
            sheets = (await self.sheet_list(config.master_spreadsheet))["sheets"]
            _TEMPLATE_TABS = [
                {"sheetId": s["properties"]["sheetId"],
                 "title": s["properties"]["title"],
                 "hidden": bool(s["properties"].get("hidden", False))}
                for s in sheets
            ]
        return [dict(t) for t in _TEMPLATE_TABS]

    async def create_reference_spreadsheet(self, hunt_name: str, folder_id: str) -> str:
        """A files-mode hunt's reference spreadsheet: a template copy named
        "<hunt> Reference" inside `folder_id`, trimmed to REFERENCE plus the hidden
        tabs (the hidden ones stay -- extras and a later switch back to tabs need
        them). One batchUpdate, skipped when the template has nothing to drop."""
        new_id = await self.copy_template(f"{hunt_name} Reference", folder_id)
        doomed = [t["sheetId"] for t in await self.template_tabs()
                  if not t["hidden"] and t["title"] != "REFERENCE"]
        if doomed:
            await self.batch_update(
                {"requests": [{"deleteSheet": {"sheetId": sid}} for sid in doomed]},
                spreadsheet_id=new_id)
        return new_id

    async def create_puzzle_spreadsheet(self, puzzle_data: PuzzleData, index=None,
                                        header_links=None, *, hunt=None):
        """Create a new puzzle's sheet: the dispatch point for sheet mode.

        Tabs (no hunt, or sheet_mode "tabs"): a tab in the spreadsheet the
        location rule names, exactly as always. Files: a puzzle gets its own
        file (create_puzzle_file); a mini (parent_puzzle_id) gets a tab in its
        minimeta's file, which the caller has set as its google_sheet_id --
        without one it is refused before any call (parent_sheet_missing)."""
        if hunt is not None and getattr(hunt, "sheet_mode", "tabs") == "files":
            if not puzzle_data.parent_puzzle_id:
                await self.create_puzzle_file(puzzle_data, hunt.drive_folder_id,
                                              header_links=header_links)
                return
            if not puzzle_data.google_sheet_id:
                raise RuntimeError("parent_sheet_missing")
        puzzle_index = index if index is not None else self.INITIAL_OFFSET
        # Forwarded only when given, so the no-links call keeps its old shape.
        extra = {"header_links": header_links} if header_links else {}
        page_before = puzzle_data.google_page_id
        try:
            await self.add_new_puzzle_sheet(puzzle_data, puzzle_index, **extra)
            await self.update_puzzle_info(puzzle_data)
        except Exception:
            # The tab is added first and filled in after; a failure there would
            # strand it, and a retry would get "<name> (2)". Delete it, as
            # create_puzzle_file deletes its copy.
            page_id = puzzle_data.google_page_id
            if page_id and page_id != page_before:
                try:
                    await self.delete_sheet_tab(page_id, self.spreadsheet_of(puzzle_data))
                except Exception:
                    logger.exception("create_puzzle_spreadsheet: could not delete tab %s "
                                     "after a failed create", page_id)
                puzzle_data.google_page_id = page_before
            raise

    async def create_puzzle_file(self, puzzle, folder_id: str, header_links=None) -> None:
        """Files mode: the puzzle's own spreadsheet, a template copy named after
        the puzzle in `folder_id`. Sets puzzle.google_sheet_id and
        google_page_id.

        Budget: 1 Drive copy + 1 permission (copy_template) and 2 Sheets
        batchUpdates -- the tab's addSheet, then ONE header batch that also
        deletes every template tab but the hidden "Tab template" (extras
        duplicate it) and allows IMAGE()/IMPORT* external URL access. No read of
        the new file: a copy keeps the template's sheet ids, which
        template_tabs() supplies (a target's contribution Table is one more
        best-effort batch, as in a tab hunt). A failed header batch is retried
        once with the template re-read (a stale cache would otherwise fail
        every create). Any failure after the copy deletes the copy, clears the
        ids and the template cache, and re-raises, so a failed create leaves
        no stray file."""
        global _TEMPLATE_TABS
        tabs = await self.template_tabs()
        new_id = await self.copy_template(puzzle.name, folder_id)
        puzzle.google_sheet_id = new_id
        puzzle.google_page_id = ""
        try:
            try:
                await self.generate_puzzle_tab(
                    puzzle, is_target=puzzle.is_target, index=0, header_links=header_links,
                    tab_name=self._unique_name(puzzle.name, [t["title"] for t in tabs]),
                    extra_requests=self._template_trim_requests(tabs))
            except HttpError:
                if not puzzle.google_page_id:
                    raise          # the addSheet failed: nothing to do with the trim
                # The header batch failed. The likeliest cause that a retry can
                # fix is a stale template cache (a tab removed from the master
                # since it was read): the batch is atomic, so one unknown
                # sheetId would fail every create until a restart. Re-read the
                # template and retry the header batch ONCE on the tab we have.
                logger.warning("create_puzzle_file: header batch failed for %s; "
                               "re-reading the template tabs and retrying once",
                               new_id, exc_info=True)
                _TEMPLATE_TABS = None
                tabs = await self.template_tabs()
                await self.generate_puzzle_tab(
                    puzzle, is_target=puzzle.is_target, header_links=header_links,
                    page_id=puzzle.google_page_id,
                    extra_requests=self._template_trim_requests(tabs))
        except Exception:
            # Next create re-reads the template rather than trusting the cache.
            _TEMPLATE_TABS = None
            try:
                await self.delete_file(new_id)
            except Exception:
                logger.exception("create_puzzle_file: could not delete %s after a "
                                 "failed create", new_id)
            puzzle.google_sheet_id = ""
            puzzle.google_page_id = ""
            raise

    def _template_trim_requests(self, tabs) -> list:
        """A puzzle file's trim: delete every template tab but the hidden
        "Tab template" (extras duplicate it), and allow IMAGE()/IMPORT*
        external URL access."""
        trim: list = [{"deleteSheet": {"sheetId": t["sheetId"]}}
                      for t in tabs if t["title"] != self.PUZZLE_SHEET]
        trim.append({"updateSpreadsheetProperties": {
            "properties": {"importFunctionsExternalUrlAccessAllowed": True},
            "fields": "importFunctionsExternalUrlAccessAllowed"}})
        return trim

    async def mini_insert_index(self, minimeta, minis) -> int:
        """Where a new mini's tab goes in its minimeta's file (files mode):
        after the minimeta's main tab and the unsolved minis, before the first
        solved mini -- e.g. main, m1, m2, solved m3 -> 3. Solved is the DB flag,
        not the tab colour. One read of the minimeta's file."""
        solved = {str(m.google_page_id) for m in minis if m.solved and m.google_page_id}
        open_ = {str(m.google_page_id) for m in minis
                 if not m.solved and m.google_page_id}
        if minimeta.google_page_id:
            open_.add(str(minimeta.google_page_id))
        sheets = (await self.sheet_list(self.spreadsheet_of(minimeta)))["sheets"]
        first_solved = last_open = None
        for sheet in sheets:
            props = sheet["properties"]
            sid, idx = str(props["sheetId"]), props["index"]
            if sid in solved and (first_solved is None or idx < first_solved):
                first_solved = idx
            elif sid in open_ and (last_open is None or idx > last_open):
                last_open = idx
        if first_solved is not None:
            return first_solved
        if last_open is not None:
            return last_open + 1
        return len(sheets)

    async def create_additional_spreadsheet(self, puzzle_data: PuzzleData, name = None, puzzle = False):
        # The extra is made beside its puzzle's tab, in the puzzle's spreadsheet.
        spreadsheet_id = self.spreadsheet_of(puzzle_data)
        index = await self.get_puzzle_sheet_index(puzzle_data, spreadsheet_id)
        if name:
            sheet_name = f"{name} ({puzzle_data.name})"
        else:
            sheet_name = f"Extra Sheet for {puzzle_data.name}"
        if getattr(puzzle_data, "header_layout", None) == HEADER_COMPACT:
            # A blank tab, not the template duplicate: the template carries the
            # full labelled header. Title + status only, in one batch.
            tab_name = await self.get_unique_tab_name(sheet_name, spreadsheet_id)
            sheet_id = await self.create_blank_tab(spreadsheet_id, tab_name, index + 1)
            await self.batch_update(
                {"requests": self._compact_header_requests(
                    puzzle_data, sheet_id, 0, COMPACT_EXTRA_GEOMETRY,
                    title=name or puzzle_data.name)},
                spreadsheet_id=spreadsheet_id)
            return sheet_id
        sheet_id = await self.add_new_sheet(sheet_name, index+1, spreadsheet_id=spreadsheet_id)
        await self.update_puzzle_info(puzzle_data, False, name, sheet_id,
                                      spreadsheet_id=spreadsheet_id)
        return sheet_id

    async def promote_tab_to_group(self, puzzle_data: PuzzleData, header_links=None):
        """Promote a puzzle's existing tab into a target (group) tab in place.

        Inserts the contribution columns + native Table and regenerates the header
        (including the name hyperlink) via promote_tab_to_target. The tab is NOT
        renamed — the puzzle name is unchanged, and renaming it to its own name
        makes get_unique_tab_name treat the tab as a collision ("Name (2)").
        Inverse of demote_tab_to_puzzle. `header_links` re-fills the Feeds line
        the header wipe clears."""
        extra = {"header_links": header_links} if header_links else {}
        await self.promote_tab_to_target(puzzle_data, **extra)

    async def demote_tab_to_puzzle(self, puzzle_data: PuzzleData, header_links=None):
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
        treat the tab as a collision and append "(2)". `header_links` re-fills
        the Feeds line the header wipe clears.

        A compact puzzle takes _demote_compact: one atomic batch (cut the header
        back, delete the columns), gated on its own Table being present, which
        for a compact tab is equivalent to its header being in column B."""
        from bot.cogs import sheet_layout
        page_id = puzzle_data.google_page_id
        spreadsheet_id = self.spreadsheet_of(puzzle_data)
        if getattr(puzzle_data, "header_layout", None) == HEADER_COMPACT:
            await self._demote_compact(puzzle_data, spreadsheet_id, header_links)
            return
        tab_name = await self.get_page_name_by_id(page_id, spreadsheet_id)
        safe_name = tab_name.replace("'", "''") if tab_name else None

        # 0. Only a TARGET tab has contribution columns to remove. A retry after
        #    a mid-operation failure finds the tab already back at offset 0, and
        #    deleting again takes four columns of real solver work -- silently,
        #    since Sheets does not error on it (C5). Step 1 is gated with it: it
        #    reads at offset+1, which is the wrong cells on a plain tab.
        layout = await self._tab_layout(page_id, tab_name, spreadsheet_id=spreadsheet_id)
        in_target_layout = layout == self.LAYOUT_TARGET

        # 1. Save flavour/progress/solution from the current target header (offset 1).
        saved = {}
        if in_target_layout and safe_name is not None:
            shift = self.TARGET_HEADER_SHIFT
            full = self._full_geometry()
            for label, ref in [("flavour", "B2"),
                               ("progress", full.progress_a1),
                               ("solution", full.status_a1)]:
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
            }]}, spreadsheet_id=spreadsheet_id)

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
            puzzle_data, page_id, 0, is_target=False, header_links=header_links)
        for label in ("flavour", "progress", "solution"):
            if label in saved:
                val, row, col = saved[label]
                requests.append(self.update_cell(
                    str(val), row, col, self.STRING_INPUT, page_id))
        await self.batch_update({"requests": requests}, spreadsheet_id=spreadsheet_id)

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


    async def stamp_rollup_metadata(self, puzzle: PuzzleData, key):
        """Best-effort: ensure developer metadata `key` is on this puzzle's tab,
        resolving the live-vs-archive spreadsheet. Returns (tab_page_id, metadata_id),
        or (None, None) if the puzzle has no sheet yet."""
        page_id = puzzle.google_page_id
        if page_id in (None, ""):
            return None, None
        spreadsheet_id = self.spreadsheet_of(puzzle)
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


    # Column layout for the Unassigned Puzzles tab. Bot owns A-D; column E
    # (Notes) belongs to solvers and is NEVER written by the reconcile.
    UNASSIGNED_HEADER = ["id", "Puzzle", "Answer", "Length", "Notes"]
    CONTRIBUTION_HEADER = ["key", "Source", "Type", "Content"]
    # Evidence tab columns — all bot-owned. Column A (id) is the hidden key the
    # reconcile diffs on (the evidence item id; Source Puzzle names are not unique).
    EVIDENCE_HEADER = ["id", "Source Puzzle", "Note", "Target Puzzle", "Logged At"]

    async def ensure_unassigned_table(self, page_id, show_round=False,
                                      spreadsheet_id: str | None = None):
        """Prepare the Unassigned Puzzles tab: write the header row and, best
        effort, wrap it in a native Table (hidden id column) for nicer styling.

        A hunt-level tab: `spreadsheet_id` None -> the hunt's live spreadsheet
        (the context-var id set by _gsheet_cog). Returns a table id to persist on the hunt (the native Table's id when one is created,
        otherwise the page id as a harmless sentinel). The service guards this
        behind a null table id, so it runs once per hunt; the data writes do not
        depend on the native Table succeeding.
        """
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
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

    async def ensure_evidence_table(self, page_id, spreadsheet_id: str | None = None):
        """Prepare the Evidence tab: pin it just after Unassigned Feeders, clear any
        old flat aggregate_to_tab render, write the header, and wrap it in a native
        Table (hidden id key column). Mirrors ensure_unassigned_table.

        A hunt-level tab: `spreadsheet_id` None -> the hunt's live spreadsheet
        (the context-var id set by _gsheet_cog). Returns the native Table's id (or the page id as a harmless sentinel if the Table API is
        unavailable). The service guards this behind a null table id, so it runs once
        per hunt; the data writes do not depend on the native Table succeeding.
        """
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
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

    async def _add_contribution_table(self, page_id, header_row=0,
                                      spreadsheet_id: str | None = None):
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
        body = {"requests": self._contribution_table_requests(page_id, header_row)}
        result = await self.batch_update(body, spreadsheet_id=spreadsheet_id)
        try:
            add_reply = next(r for r in result.get("replies", []) if "addTable" in r)
            table_id = add_reply["addTable"]["table"]["tableId"]
        except (StopIteration, KeyError, TypeError):
            return table_id
        # Hide the key column.
        await self.batch_update({"requests": [self._hide_key_column_request(page_id)]},
                                spreadsheet_id=spreadsheet_id)
        return table_id

    def _contribution_table_requests(self, page_id, header_row) -> list:
        """The [updateCells(CONTRIBUTION_HEADER), addTable] pair that creates the
        contribution Table at `header_row`."""
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
        return [
            {"updateCells": {
                "range": header_range,
                "fields": "userEnteredValue",
                "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": n}}
                    for n in self.CONTRIBUTION_HEADER]}],
            }},
            {"addTable": {"table": {"name": table_name, "range": header_range}}},
        ]

    def _hide_key_column_request(self, page_id) -> dict:
        """Hide the contribution Table's key column (A)."""
        return {
            "updateDimensionProperties": {
                "range": {"sheetId": page_id, "dimension": "COLUMNS",
                          "startIndex": 0, "endIndex": 1},
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        }

    # --- Task 7: generalized key-anchored reconciler ----------------------

    async def reconcile_table(self, page_id, desired_rows, data_col_count, numeric_cols=(),
                              grey_below=False, table_name=None, sheet_snapshot=None,
                              spreadsheet_id: str | None = None):
        """Incrementally reconcile the native Table on `page_id` against the FULL
        `desired_rows` (the single source of truth — callers pass the whole desired
        set, not a pre-diff).

        `sheet_snapshot`, when given, is the `sheets` list from an already-fetched
        `sheet_list(spreadsheet_id)` response for THIS spreadsheet, taken during
        THIS render — pass one only when the caller is certain nothing has created
        or promoted a tab since the fetch, or the snapshot's absence of the Table
        would be read as the Table not existing. When None (the default), this
        fetches its own snapshot exactly as before.

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

        Row planning is bot.core.table_reconcile.plan_rows. A row whose member left
        is removed with `deleteRange` over the Table's FULL width (`key_col`..
        `end_col`, so solver columns inside the Table go with it) and the cells
        below shift up — never `deleteDimension`, which is the whole sheet row and
        would move anything kept outside the Table. A member whose key changed
        (capstone -> meta turns `informs:12` into `feeds:12`) is re-keyed in its
        existing row, keeping its solver columns. Keyless rows are deleted when
        every in-Table cell is empty (blanks left by the old blank-in-place rule)
        and kept when a solver note survives in one."""
        from bot.core.table_reconcile import plan_rows
        # The spreadsheet holding `page_id`: None -> live (the hunt-level
        # Unassigned/Evidence tabs); a puzzle's render passes spreadsheet_of.
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()

        sheet = None
        sheets = (sheet_snapshot if sheet_snapshot is not None
                  else (await self.sheet_list(spreadsheet_id))["sheets"])
        for s in sheets:
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
            table_end = rng.get("endRowIndex")
            table_id = table.get("tableId")
        else:
            header_row, key_col, end_col, table_id = 0, 0, data_col_count, None
            table_end = None

        # Read the Table's full width (its solver-added columns included): a keyless
        # row is only deletable when every in-Table cell is empty.
        key_letter = self._col_letter(key_col)
        end_letter = self._col_letter(end_col - 1)
        resp = await self._values_get(spreadsheet_id, f"'{safe_name}'!{key_letter}:{end_letter}")
        vals = resp.get("values", []) or []
        # The rows the plan may touch: the Table's own extent, stretched over any
        # keyed row below it (never into unkeyed cells under the Table).
        keyed = [i for i in range(header_row + 1, len(vals)) if vals[i] and str(vals[i][0])]
        extent_end = table_end if table_end is not None else len(vals)
        extent_end = max(extent_end, (max(keyed) + 1) if keyed else header_row + 1)
        rows = [(i, [str(c) for c in (vals[i] if i < len(vals) else [])])
                for i in range(header_row + 1, extent_end)]

        # Stringify keys for the plan; keep the original cell values for writing.
        norm_rows = [[str(r[0])] + list(r[1:]) for r in desired_rows]
        plan = plan_rows(rows, norm_rows, header_row)

        numeric = set(numeric_cols)

        def _cell(col, v):
            if col in numeric:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return {"userEnteredValue": {"numberValue": v}}
                s = str(v).strip()
                if s and s.lstrip("-").isdigit():
                    return {"userEnteredValue": {"numberValue": int(s)}}
                return {}   # blank cell — valid in a number-typed Table column
            if isinstance(v, Link):
                cell: dict = {"userEnteredValue": {"stringValue": v.text}}
                # No run on empty text: a run at startIndex 0 of "" may be
                # rejected by the API, failing the whole batch.
                if v.uri and v.text:
                    cell["textFormatRuns"] = [
                        {"startIndex": 0, "format": {"link": {"uri": v.uri}}}]
                return cell
            return {"userEnteredValue": {"stringValue": str(v)}}

        def _row_cells(row):
            return [_cell(c, v) for c, v in enumerate(row)]

        requests = []
        # Updates first, at their pre-delete grid rows: rewrite the bot's columns
        # (key_col..key_col+data_col_count-1). A re-keyed member lands here too, so
        # it keeps its row and the solver columns beside it in the Table.
        for grid_row, row in plan.updates:
            requests.append({"updateCells": {
                "range": {"sheetId": page_id, "startRowIndex": grid_row,
                          "endRowIndex": grid_row + 1, "startColumnIndex": key_col,
                          "endColumnIndex": key_col + data_col_count},
                "fields": "userEnteredValue,textFormatRuns",
                "rows": [{"values": _row_cells(row)}],
            }})
        if table_id is not None:
            # Deletes, bottom-up so each index is still valid when it runs: remove
            # the row across the Table's FULL width (solver columns go with it) and
            # shift the cells below up. Never deleteDimension — that is the whole
            # sheet row and would move anything kept outside the Table.
            for grid_row in plan.deletes:
                requests.append({"deleteRange": {
                    "range": {"sheetId": page_id, "startRowIndex": grid_row,
                              "endRowIndex": grid_row + 1, "startColumnIndex": key_col,
                              "endColumnIndex": end_col},
                    "shiftDimension": "ROWS",
                }})
            # Appends: directly below the compacted table.
            next_row = plan.append_start
        else:
            # No Table on the page: nothing bounds a safe delete (the header row and
            # any solver columns are unknown), so keep the old rule — blank a
            # departed keyed row's bot columns in place and append below the last
            # keyed row, never reusing a blanked one.
            blank_cells = [{} for _ in range(data_col_count)]
            for grid_row in sorted(plan.deletes):
                if grid_row in keyed:
                    requests.append({"updateCells": {
                        "range": {"sheetId": page_id, "startRowIndex": grid_row,
                                  "endRowIndex": grid_row + 1, "startColumnIndex": key_col,
                                  "endColumnIndex": key_col + data_col_count},
                        "fields": "userEnteredValue,textFormatRuns",
                        "rows": [{"values": blank_cells}],
                    }})
            next_row = (max(keyed) + 1) if keyed else header_row + 1
        appends = plan.appends
        if appends:
            requests.append({"updateCells": {
                "range": {"sheetId": page_id, "startRowIndex": next_row,
                          "endRowIndex": next_row + len(appends), "startColumnIndex": key_col,
                          "endColumnIndex": key_col + data_col_count},
                "fields": "userEnteredValue,textFormatRuns",
                "rows": [{"values": _row_cells(row)} for row in appends],
            }})

        # Fold the data write, the Table resize and the grey-below formatting into
        # ONE batch_update: writes are the capped resource (60 req/min), and a
        # 50-round hunt's !rebuild issues this per target. Order matters — later
        # requests in the SAME batchUpdate still apply after earlier ones (Sheets
        # executes a batch's requests in list order), so keep the same relative
        # order the three separate calls had before: data writes, then the resize,
        # then the grey-below formatting.

        # Set the Table to exactly header + data rows. Sheets already shrinks a Table
        # on deleteRange (live-checked 2026-11-07); this makes the end explicit for
        # the append case and is consistent either way.
        if table_id is not None:
            end_row = plan.end_row
            requests.append({"updateTable": {
                "table": {
                    "tableId": table_id,
                    "range": {"sheetId": page_id,
                              "startRowIndex": header_row, "endRowIndex": end_row,
                              "startColumnIndex": key_col,
                              "endColumnIndex": end_col},
                },
                "fields": "range",
            }})

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
                if end_row > header_row + 1:
                    requests.append({"repeatCell": {
                        "range": {"sheetId": page_id, "startRowIndex": header_row + 1,
                                  "endRowIndex": end_row, "startColumnIndex": key_col,
                                  "endColumnIndex": end_col},
                        "cell": {"userEnteredFormat": {}},
                        "fields": "userEnteredFormat.backgroundColor"}})
                if row_count > end_row:
                    requests.append({"repeatCell": {
                        "range": {"sheetId": page_id, "startRowIndex": end_row,
                                  "endRowIndex": row_count, "startColumnIndex": key_col,
                                  "endColumnIndex": end_col},
                        "cell": {"userEnteredFormat": {"backgroundColor": self.SOCIAL_GREY}},
                        "fields": "userEnteredFormat.backgroundColor"}})

        if requests:
            deletes = any("deleteRange" in r for r in requests)
            if deletes:
                # Deleting by row index is not safe to replay on an ambiguous
                # failure: the rows would have shifted under the indices.
                await self.batch_update({"requests": requests},
                                        spreadsheet_id=spreadsheet_id,
                                        retry_ambiguous=False)
            else:
                await self.batch_update({"requests": requests}, spreadsheet_id=spreadsheet_id)

    # --- Task 8: code-generated puzzle tab ---------------------------------

    def _puzzle_tab_header_requests(self, puzzle, page_id, offset, is_target,
                                    header_links=None):
        """batchUpdate requests for a puzzle/target tab's header, shifted right by
        `offset`: the captured static cells + merges, the dynamic name/url
        hyperlink (always bot-written, not from the artifact), tab styling
        (JetBrains Mono, frozen + grey header rows, wide label column at the
        offset), and bold static labels. Shared by generate_puzzle_tab (fresh
        tab) and promote_tab_to_target (stamped over an inserted column band) so
        the two paths produce an identical header geometry.

        `header_links` (list[Link]) fills the Feeds line; None leaves it empty."""
        from bot.cogs import sheet_layout
        geom = self._header_geometry(puzzle, page_id)
        if geom.layout == HEADER_COMPACT:
            return self._compact_header_requests(
                puzzle, page_id, offset, geom, header_links=header_links)
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
        requests.append(self._title_request(puzzle, page_id, offset, geom))
        requests += self._tab_format_requests(page_id, label_col=offset, geom=geom)
        requests += self._label_bold_requests(page_id, cells, offset)
        requests += self._dynamic_bold_requests(page_id, offset, geom)
        requests.append(self._header_border_request(page_id, offset, geom))
        if header_links is not None:
            requests.append(self.header_links_request(page_id, offset, header_links, geom))
        return requests

    @staticmethod
    def _utf16_len(text: str) -> int:
        # Sheets counts textFormatRuns indices in UTF-16 code units.
        return len(text.encode("utf-16-le")) // 2

    def _link_runs(self, prefix: str, links: List[Link]) -> tuple[str, list]:
        """The feeds cell's text and its textFormatRuns. With a prefix, an
        unlinked run covers it and the link runs start after it (UTF-16)."""
        runs = []
        pos = 0
        if prefix:
            runs.append({"startIndex": 0, "format": {}})
            pos = self._utf16_len(prefix)
        for i, link in enumerate(links):
            if i:
                runs.append({"startIndex": pos, "format": {}})
                pos += self._utf16_len(", ")
            fmt = {"link": {"uri": link.uri}} if link.uri else {}
            runs.append({"startIndex": pos, "format": fmt})
            pos += self._utf16_len(link.text)
        return prefix + ", ".join(lk.text for lk in links), runs

    def header_links_request(self, page_id, offset, links: List[Link], geom=None) -> dict:
        """The Feeds line's links cell (I3, shifted by `offset`): the names joined
        by ", ", each name a rich-text link to its sheet and the separators left
        unlinked. Rich text, never a formula. No links clears the cell (an old
        line goes); the "Feeds:" label is not touched."""
        geom = geom or self._full_geometry()
        if geom.feeds_a1 is None:
            raise ValueError("this header layout has no feeds cell")
        row = self.get_row(geom.feeds_a1)
        col = self.get_column(geom.feeds_a1) + offset
        # A nameless link can't be shown, and this rides inside tab generation /
        # promote / demote, where a TypeError would fail the user's action.
        links = [lk for lk in links if lk.text]
        cell: dict[str, Any] = {}
        compact = geom.layout == HEADER_COMPACT
        if links:
            text, runs = self._link_runs("Feeds: " if compact else "", links)
            cell = {"userEnteredValue": {"stringValue": text},
                    "textFormatRuns": runs}
        elif compact:
            cell = {"userEnteredValue": {"stringValue": "No links"}}
        return {"updateCells": {
            "rows": [{"values": [cell]}],
            "fields": "userEnteredValue,textFormatRuns",
            "range": {"sheetId": page_id,
                      "startRowIndex": row, "endRowIndex": row + 1,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
        }}

    def _feeds_line_requests(self, page_id, offset, links: List[Link]) -> list:
        """Re-stamp row 3 into the Feeds layout on a tab that may predate it, then
        write the links. Unmerge the whole row-3 band (an old B3:Q3 merge keeps
        its progress value in B3), merge B3:G3 + I3:Q3 (both empty after the
        unmerge apart from B3), stamp the bold label, then the links cell. The
        same requests are harmless on a tab already in the new layout."""
        from bot.cogs import sheet_layout
        row = self.get_row(sheet_layout.FEEDS_LINKS_A1)
        row_merges = [m for m in sheet_layout.PUZZLE_TAB_MERGES if m[0] == row]
        label = next(c for c in sheet_layout.PUZZLE_TAB_CELLS
                     if c["a1"] == sheet_layout.FEEDS_LABEL_A1)
        requests: list = [{"unmergeCells": {"range": {
            "sheetId": page_id, "startRowIndex": row, "endRowIndex": row + 1,
            "startColumnIndex": min(m[2] for m in row_merges) + offset,
            "endColumnIndex": max(m[3] for m in row_merges) + offset}}}]
        for start_r, end_r, start_c, end_c in row_merges:
            requests.append({"mergeCells": {
                "range": {"sheetId": page_id,
                          "startRowIndex": start_r, "endRowIndex": end_r,
                          "startColumnIndex": start_c + offset,
                          "endColumnIndex": end_c + offset},
                "mergeType": "MERGE_ALL"}})
        requests.append(self.update_cell(
            label["value"], self.get_row(label["a1"]),
            self.get_column(label["a1"]) + offset, label["type"], page_id))
        requests += self._label_bold_requests(page_id, [label], offset)
        requests.append(self.header_links_request(page_id, offset, links))
        requests.append(self._header_border_request(page_id, offset))
        return requests

    async def write_header_links(
            self, tabs, layouts: Mapping[tuple[str, str], str] | None = None) -> list[str]:
        """Write the Feeds line on existing tabs. `tabs` is a list of
        (SheetLocation, offset, list[Link]); one batch_update per spreadsheet,
        never per tab, written to the spreadsheet each location names. A
        location with no spreadsheet id or no page id is skipped.

        A batchUpdate is all-or-nothing, so one bad tab (stale page id, a solver
        merge across row 3, a tab mid-promote) would cancel every tab's line on
        that spreadsheet. If a spreadsheet's batch fails, each of its tabs is
        retried alone -- extra writes on the failure path only. A retryable
        failure (429/5xx, already retried by batch_update) is not fanned out:
        per-tab writes would only add load to a saturated quota or a down
        service, so every tab on that spreadsheet is marked failed. Best-effort:
        never raises; returns the page ids whose line was not written.

        `layouts` maps (spreadsheet_id, str(page_id)) to a header layout; a tab
        keyed HEADER_COMPACT gets only its A3 cell. Keyed by spreadsheet too,
        because files-mode copies share page ids across spreadsheets."""
        layouts = layouts or {}
        by_sheet: dict[str, list[tuple[str, list]]] = {}
        for loc, offset, links in tabs:
            if loc.page_id is None or loc.page_id == "":
                continue
            sid: str = loc.spreadsheet_id
            if not sid:
                logger.warning("write_header_links: page %s has no spreadsheet id; "
                               "skipped", loc.page_id)
                continue
            if layouts.get((sid, str(loc.page_id))) == HEADER_COMPACT:
                reqs = [self.header_links_request(
                    int(loc.page_id), offset, links, COMPACT_GEOMETRY)]
            else:
                reqs = self._feeds_line_requests(int(loc.page_id), offset, links)
            by_sheet.setdefault(sid, []).append((str(loc.page_id), reqs))
        failed: list[str] = []
        for spreadsheet_id, per_tab in by_sheet.items():
            try:
                await self.batch_update(
                    {"requests": [r for _page, reqs in per_tab for r in reqs]},
                    spreadsheet_id=spreadsheet_id)
                continue
            except Exception as exc:  # noqa: BLE001
                if _is_retryable(exc):
                    logger.exception("write_header_links: batch on spreadsheet %s "
                                     "failed (quota or outage); %d tabs not written",
                                     spreadsheet_id, len(per_tab))
                    failed.extend(page_id for page_id, _reqs in per_tab)
                    continue
                if len(per_tab) > 1:
                    logger.warning("write_header_links: batch on spreadsheet %s "
                                   "failed; retrying its %d tabs one at a time",
                                   spreadsheet_id, len(per_tab), exc_info=True)
                else:
                    page_id = per_tab[0][0]
                    logger.exception("write_header_links: Feeds line for page %s "
                                     "on spreadsheet %s failed",
                                     page_id, spreadsheet_id)
                    failed.append(page_id)
                    continue
            for page_id, reqs in per_tab:
                try:
                    await self.batch_update({"requests": reqs}, spreadsheet_id=spreadsheet_id)
                except Exception:  # noqa: BLE001
                    logger.exception("write_header_links: Feeds line for page %s "
                                     "on spreadsheet %s failed",
                                     page_id, spreadsheet_id)
                    failed.append(page_id)
        return failed

    async def generate_puzzle_tab(self, puzzle, is_target, index=None, header_links=None,
                                  *, tab_name=None, extra_requests=None, page_id=None):
        """Create a puzzle tab entirely in code (no template duplicateSheet).

        Adds a blank tab, writes the captured header layout + the dynamic
        name/url hyperlink, and (when is_target) the contribution Table.
        Sets puzzle.google_page_id. Returns the new page id. `header_links`
        (list[Link]) fills the Feeds line in the same batch.

        create_puzzle_file passes `tab_name` (already unique, so no read) and
        `extra_requests` (its template trim), appended to the header batch;
        its one retry passes `page_id`, the tab already added, so no new tab."""
        from bot.cogs import sheet_layout
        import inspect as insp

        spreadsheet_id = self.spreadsheet_of(puzzle)
        assert spreadsheet_id  # sheet ops run with a hunt spreadsheet in context
        if page_id is None:
            name = tab_name or await self.get_unique_tab_name(puzzle.name, spreadsheet_id)
            _result = self.create_blank_tab(spreadsheet_id, name, index)
            page_id = await _result if insp.iscoroutine(_result) else _result
        puzzle.google_page_id = page_id

        # Target tabs shift the header one column right so column A is free for the
        # contribution Table's hidden key column (labels in B, values in C+).
        offset = self.TARGET_HEADER_SHIFT if is_target else 0

        # Static header (captured cells + merges), dynamic name hyperlink, tab
        # styling and bold labels — all shifted right by `offset`. Shared with
        # promote_tab_to_target so a generated and a promoted target end identical.
        requests = self._puzzle_tab_header_requests(
            puzzle, page_id, offset, is_target,
            header_links=header_links or None)
        # A compact target's Table and key-column hide ride in the same atomic
        # batch (no deferral): a failed batch leaves nothing half-made.
        compact_target = is_target \
            and self._header_geometry(puzzle, page_id).layout == HEADER_COMPACT
        if compact_target:
            requests += self._contribution_table_requests(page_id, sheet_layout.FROZEN_ROWS)
            requests.append(self._hide_key_column_request(page_id))
        requests += extra_requests or []
        if requests:
            await self.batch_update({"requests": requests}, spreadsheet_id=spreadsheet_id)

        # Contribution Table below the frozen header (row 4), so it never overlaps
        # the header merges in rows 1-3. Best effort: Google's Sheets write path
        # returns transient 500s ("Internal error encountered") on a busy spreadsheet,
        # and a flicker here must NOT abort meta creation (the user just sees a 500 and
        # a half-made meta). If it fails, the tab is left without the Table; it is
        # created lazily on the first contribution render (see _ensure_contribution_table).
        if is_target and not compact_target:
            try:
                await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS,
                                                   spreadsheet_id=spreadsheet_id)
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

    async def _tab_layout(self, page_id, tab_name=None, spreadsheet_id: str | None = None):
        """Which header layout this tab is currently in. One A1:B1 read.

        The guard for every geometry-shifting mutation on a contribution tab.
        Both M18 (promote inserts 4 more columns per retry) and C5 (demote
        deletes 4 more) are retries that assumed a layout instead of checking
        it, and neither raises when wrong -- a second deleteDimension of the
        same range succeeds silently, eating real solver columns.
        """
        from bot.cogs import sheet_layout
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        if tab_name is None:
            tab_name = await self.get_page_name_by_id(page_id, spreadsheet_id)
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

    async def _own_table_present(self, page_id, spreadsheet_id) -> bool:
        """Whether `page_id` carries its own contribution Table. One sheet_list read."""
        sheets = (await self.sheet_list(spreadsheet_id))["sheets"]
        for s in sheets:
            if s["properties"]["sheetId"] == int(page_id):
                return bool(select_table(s.get("tables"), contribution_table_name(page_id)))
        return False

    def _header_cut(self, page_id, from_col, to_col) -> dict:
        """cutPaste of the three header cells (rows 1-3) between two columns."""
        return {"cutPaste": {
            "source": {"sheetId": page_id, "startRowIndex": 0, "endRowIndex": 3,
                       "startColumnIndex": from_col, "endColumnIndex": from_col + 1},
            "destination": {"sheetId": page_id, "rowIndex": 0, "columnIndex": to_col},
            "pasteType": "PASTE_NORMAL"}}

    async def _promote_compact(self, puzzle, spreadsheet_id, header_links=None, *,
                               known_tableless=False) -> None:
        """Compact promote: ONE batch that opens CONTRIBUTION_WIDTH columns at A,
        moves the three header cells from the pushed-right column to
        TARGET_HEADER_SHIFT, and adds the Table. Idempotent: a tab that already
        has its own Table is left alone (unless the caller just proved it has none)."""
        from bot.cogs import sheet_layout
        page_id = puzzle.google_page_id
        # Relies on the atomic invariant (header in B <=> own Table); a Table deleted
        # by hand while the header is in B would be re-promoted.
        if not known_tableless and await self._own_table_present(page_id, spreadsheet_id):
            return
        w, shift = self.CONTRIBUTION_WIDTH, self.TARGET_HEADER_SHIFT
        reqs = [
            {"insertDimension": {
                "range": {"sheetId": page_id, "dimension": "COLUMNS",
                          "startIndex": 0, "endIndex": w},
                "inheritFromBefore": False}},
            # The insert copies the old column A's header format into A-D: clear it.
            {"repeatCell": {
                "range": {"sheetId": page_id, "startRowIndex": 0, "endRowIndex": 3,
                          "startColumnIndex": 0, "endColumnIndex": w},
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat.backgroundColor,"
                          "userEnteredFormat.borders,userEnteredFormat.textFormat.bold"}},
            self._header_cut(page_id, w, shift),
            {"updateDimensionProperties": {
                "range": {"sheetId": page_id, "dimension": "COLUMNS",
                          "startIndex": shift, "endIndex": shift + 1},
                "properties": {"pixelSize": self.LABEL_COL_WIDTH},
                "fields": "pixelSize"}},
        ]
        if header_links is not None:
            reqs.append(self.header_links_request(
                page_id, shift, header_links, sheet_layout.COMPACT_GEOMETRY))
        reqs += self._contribution_table_requests(page_id, sheet_layout.FROZEN_ROWS)
        reqs.append(self._hide_key_column_request(page_id))
        await self.batch_update({"requests": reqs}, spreadsheet_id=spreadsheet_id)

    async def _demote_compact(self, puzzle, spreadsheet_id, header_links=None) -> None:
        """Compact demote: ONE batch that moves the header cells back to where
        promote's cut left the originals, then deletes the CONTRIBUTION_WIDTH
        columns. A tab with no own Table is already demoted: nothing to do."""
        from bot.cogs import sheet_layout
        page_id = puzzle.google_page_id
        if not await self._own_table_present(page_id, spreadsheet_id):
            logger.warning("demote_tab_to_puzzle: compact tab %s has no contribution "
                           "Table; already demoted, nothing to do", page_id)
            return
        w, shift = self.CONTRIBUTION_WIDTH, self.TARGET_HEADER_SHIFT
        reqs = [
            self._header_cut(page_id, shift, w),
            {"deleteDimension": {"range": {
                "sheetId": page_id, "dimension": "COLUMNS",
                "startIndex": 0, "endIndex": w}}},
            # A target created compact never widened column E; give the new A its width.
            {"updateDimensionProperties": {
                "range": {"sheetId": page_id, "dimension": "COLUMNS",
                          "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": self.LABEL_COL_WIDTH},
                "fields": "pixelSize"}},
        ]
        if header_links is not None:
            reqs.append(self.header_links_request(
                page_id, 0, header_links, sheet_layout.COMPACT_GEOMETRY))
        await self.batch_update({"requests": reqs}, spreadsheet_id=spreadsheet_id)

    async def promote_tab_to_target(self, puzzle, header_links=None):
        """Transform an existing puzzle tab into a target tab in place, ending in
        the SAME geometry as a freshly generated target: header at offset 1, the
        contribution Table at A4.

        Insert CONTRIBUTION_WIDTH blank columns at the left so the solver grid
        rides clear to the right (cols E+), then unmerge + clear the frozen
        header rows and re-stamp the canonical target header at offset 1 (the
        insert pushes the old header to offset 4, whose merges would otherwise
        collide with the new ones). Finally add the Table at A4. The user's
        flavour text and the bot-written progress/solution values are preserved
        across the insert.

        A compact puzzle takes _promote_compact instead: one atomic batch, with no
        probe. That relies on the invariant that a compact target is created with
        its header in column B AND its own Table in one batch, so "own Table
        present" is equivalent to "header is in column B"."""
        from bot.cogs import sheet_layout
        page_id = puzzle.google_page_id
        spreadsheet_id = self.spreadsheet_of(puzzle)
        if getattr(puzzle, "header_layout", None) == HEADER_COMPACT:
            await self._promote_compact(puzzle, spreadsheet_id, header_links)
            return
        tab_name = await self.get_page_name_by_id(page_id, spreadsheet_id)
        if tab_name is None:
            return
        safe_name = tab_name.replace("'", "''")
        offset = self.TARGET_HEADER_SHIFT

        # 0. Only a PLAIN tab needs the column insert. Promote is re-entered
        #    whenever step 4 failed (M18), and re-inserting walks the solver grid
        #    four columns right every time. Steps 1-3 are skipped too, not just
        #    the insert: step 1 reads the at-risk values at offset-0 positions,
        #    which on an already-shifted tab are the wrong cells.
        layout = await self._tab_layout(page_id, tab_name, spreadsheet_id=spreadsheet_id)
        if layout != self.LAYOUT_PLAIN:
            if layout == self.LAYOUT_UNKNOWN:
                # Columns in, header never stamped (a step-3 failure) -- or not
                # our tab at all. Either way, shifting it again is the damage.
                logger.warning(
                    "promote_tab_to_target: tab %s (page %s) is in neither plain nor "
                    "target layout; adding the Table without shifting it. Its header "
                    "may need stamping by hand.", tab_name, page_id)
            await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS,
                                               spreadsheet_id=spreadsheet_id)
            return

        # 1. Save at-risk values BEFORE the insert: flavour is user-owned; progress
        #    and solution are bot-written but carry solve state worth preserving.
        full = self._full_geometry()
        preserved = [("flavour", "B2"),                       # captured layout: B2
                     ("progress", full.progress_a1),
                     ("solution", full.status_a1)]
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
        }]}, spreadsheet_id=spreadsheet_id)

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
        requests += self._puzzle_tab_header_requests(
            puzzle, page_id, offset, is_target=True, header_links=header_links)
        for label in ("flavour", "progress", "solution"):
            if label in saved:
                val, row, col = saved[label]
                requests.append(self.update_cell(
                    str(val), row, col + offset, self.STRING_INPUT, page_id))
        await self.batch_update({"requests": requests}, spreadsheet_id=spreadsheet_id)

        # 4. Add the contribution Table at A4 (below the frozen header).
        await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS,
                                           spreadsheet_id=spreadsheet_id)

    # --- Task 10: render contribution table from service -------------------

    async def _ensure_contribution_table(self, puzzle, spreadsheet_id: str | None = None):
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
        writing the rollup.

        Returns the `sheets` snapshot fetched here (the list from `sheet_list`'s
        response) when the Table was already present and nothing was created or
        promoted — that snapshot is still accurate and a caller may hand it to
        `reconcile_table` as `sheet_snapshot` to skip its own fetch. Returns None
        on every path that creates or promotes a tab: a snapshot taken before such
        a mutation must not be reused to conclude the tab still lacks the Table.

        `spreadsheet_id` is the spreadsheet holding the tab (the render passes
        spreadsheet_of(puzzle)); None -> live."""
        from bot.cogs import sheet_layout
        page_id = puzzle.google_page_id
        spreadsheet_id = spreadsheet_id or self.get_spreadsheet_id()
        sheets = (await self.sheet_list(spreadsheet_id))["sheets"]
        for s in sheets:
            if s["properties"]["sheetId"] == int(page_id):
                # Ours specifically, not "a Table exists": select_table matches
                # strictly on the name the bot gave its own Table, so a solver's
                # table on the tab cannot stand in for the contribution Table.
                # It used to — generate_puzzle_tab defers Table creation past a
                # transient Sheets 500, and in that window a solver's table was
                # the lone one on the tab, so creation was skipped forever and
                # the reconciler wrote the rollup into their data instead.
                if select_table(s.get("tables"), contribution_table_name(page_id)):
                    return sheets
                break
        tab_name = await self.get_page_name_by_id(page_id, spreadsheet_id)
        if tab_name is None:
            await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS,
                                               spreadsheet_id=spreadsheet_id)
            return None
        if getattr(puzzle, "header_layout", None) == HEADER_COMPACT:
            # Tableless compact tab: by the atomic-create invariant its header is
            # still in column A, so promote without probing.
            await self._promote_compact(puzzle, spreadsheet_id, known_tableless=True)
            return None
        # One shared geometry probe rather than this function's own A1 read --
        # see _tab_layout for why A1 alone is not sufficient (D7).
        if await self._tab_layout(page_id, tab_name,
                                  spreadsheet_id=spreadsheet_id) == self.LAYOUT_PLAIN:
            await self.promote_tab_to_target(puzzle)
            return None
        await self._add_contribution_table(page_id, header_row=sheet_layout.FROZEN_ROWS,
                                           spreadsheet_id=spreadsheet_id)
        return None

    async def render_minimeta_contribution(self, minimeta, members, link_for=None):
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
        # Its own spreadsheet by the location rule: a solved tab-hunt minimeta's
        # tab is on the solved spreadsheet (code map complication 9).
        spreadsheet_id = self.spreadsheet_of(minimeta)
        snapshot = await self._ensure_contribution_table(minimeta, spreadsheet_id=spreadsheet_id)
        rows = [
            [f"feeds:{m.id}", Link(m.name, link_for(m)) if link_for is not None else m.name, "Feeder", m.solution.upper() if m.solution else ""]
            for m in members
            if m.id != minimeta.id
        ]
        await self.reconcile_table(
            page_id=page_id,
            desired_rows=rows,
            data_col_count=len(self.CONTRIBUTION_HEADER),
            grey_below=True,
            table_name=contribution_table_name(page_id),
            sheet_snapshot=snapshot,
            spreadsheet_id=spreadsheet_id,
        )

    async def render_contribution(self, target_puzzle, tag_group_id, members, evidence,
                                  link_for=None):
        """Render the unified contribution table on the target's tab.

        Excludes the target's own row, builds desired rows from edges/evidence,
        and reconciles the native Table on the target's page."""
        from bot.core.contribution import contribution_rows
        # Self-heal: creates/promotes the Table if it's missing (see
        # _ensure_contribution_table); raises if it still can't be made, which
        # refresh_target/!rebuild badge and report rather than silently no-op.
        spreadsheet_id = self.spreadsheet_of(target_puzzle)
        snapshot = await self._ensure_contribution_table(target_puzzle,
                                                         spreadsheet_id=spreadsheet_id)
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
        rows = contribution_rows(tag_group_id, contributing, evidence, link_for=link_for)
        await self.reconcile_table(
            page_id=target_puzzle.google_page_id,
            desired_rows=rows,
            data_col_count=len(self.CONTRIBUTION_HEADER),
            grey_below=True,
            table_name=contribution_table_name(target_puzzle.google_page_id),
            sheet_snapshot=snapshot,
            spreadsheet_id=spreadsheet_id,
        )


async def setup(bot):
    # Comment this out if google-drive-related package are not installed!
    await bot.add_cog(GoogleSheets(bot))