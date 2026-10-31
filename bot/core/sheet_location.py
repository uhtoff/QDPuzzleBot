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


def _spreadsheet_for(hunt, on_solved_sheet: bool) -> str:
    # A hunt with no solved sheet has solved_sheet_id == "" -- fall back to the
    # live spreadsheet rather than build a '/d//' dead link (M15).
    if on_solved_sheet and hunt.solved_sheet_id:
        return hunt.solved_sheet_id
    return hunt.google_sheet_id


def puzzle_location(hunt, puzzle) -> SheetLocation:
    return SheetLocation(_spreadsheet_for(hunt, puzzle.on_solved_sheet), puzzle.google_page_id)


def additional_sheet_location(hunt, sheet) -> SheetLocation:
    """An extra sheet uses its OWN on_solved_sheet, not its puzzle's."""
    return SheetLocation(_spreadsheet_for(hunt, sheet.on_solved_sheet), sheet.google_page_id)


def hunt_sheet_location(hunt, page_id) -> SheetLocation:
    """A hunt's own working sheet is always on the hunt's main spreadsheet."""
    return SheetLocation(hunt.google_sheet_id, page_id)
