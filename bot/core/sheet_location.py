"""One place that answers "where does this sheet live?".

Pure: no Discord or Google imports. A location is a spreadsheet id plus a page
(tab) id; ``url`` is the full link form, built only here.
"""
from dataclasses import dataclass

from bot.utils.urls import spreadsheet_url


@dataclass(frozen=True)
class SheetLocation:
    spreadsheet_id: str
    page_id: str | int | None

    @property
    def url(self) -> str | None:
        """Full URL, or None when the spreadsheet or page is unknown."""
        if not self.spreadsheet_id or self.page_id is None or self.page_id == "":
            return None
        return spreadsheet_url(self.spreadsheet_id, self.page_id)


def spreadsheet_for(obj, live_id: str, solved_id: str) -> str:
    """The location rule: the spreadsheet an object's sheet lives in.

    Its own ``google_sheet_id`` wins (files mode); else the solved spreadsheet
    iff ``on_solved_sheet`` is set and the solved id is non-empty; else live.
    A hunt with no solved sheet has solved_id == "" -- fall back to the live
    spreadsheet rather than build a '/d//' dead link (M15).
    """
    if obj.google_sheet_id:
        return obj.google_sheet_id
    if obj.on_solved_sheet and solved_id:
        return solved_id
    return live_id


def puzzle_location(hunt, puzzle) -> SheetLocation:
    return SheetLocation(
        spreadsheet_for(puzzle, hunt.google_sheet_id, hunt.solved_sheet_id), puzzle.google_page_id)


def additional_sheet_location(hunt, sheet) -> SheetLocation:
    """An extra sheet uses its OWN on_solved_sheet, not its puzzle's."""
    return SheetLocation(
        spreadsheet_for(sheet, hunt.google_sheet_id, hunt.solved_sheet_id), sheet.google_page_id)


def hunt_sheet_location(hunt, page_id) -> SheetLocation:
    """A hunt's own working sheet is always on the hunt's main spreadsheet."""
    return SheetLocation(hunt.google_sheet_id, page_id)


@dataclass(frozen=True)
class Link:
    """A table cell's text plus an optional link target.

    ``uri=None`` renders as plain text. Never used as a row key.
    """
    text: str
    uri: str | None
