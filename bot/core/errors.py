"""Shared service-layer exceptions (M5)."""


class SheetRefreshError(RuntimeError):
    """Raised when a sheet-side step failed after the DB change was committed.

    Two id spaces, deliberately separate (D9):

      failed_group_ids  -- tag_group ids whose meta/rollup sheet failed to
                           refresh. !rebuild re-renders those, so retrying is
                           the right advice.
      failed_puzzle_ids -- puzzle ids whose move to the solved spreadsheet
                           failed. !rebuild never touches tab location, so it
                           cannot retry this one; the tab is simply still on
                           the main spreadsheet.

    This replaces one combined list whose message told every caller to rebuild
    -- advice that was wrong for the puzzle case and named the wrong id space
    for it too.
    """

    def __init__(self, failed_group_ids=(), failed_puzzle_ids=()):
        self.failed_group_ids = list(failed_group_ids)
        self.failed_puzzle_ids = list(failed_puzzle_ids)
        super().__init__(self._describe())

    def _describe(self):
        parts = []
        if self.failed_group_ids:
            n = len(self.failed_group_ids)
            parts.append(
                f"{n} meta sheet{'s' if n != 1 else ''} failed to refresh "
                f"(group ids: {self.failed_group_ids}); the change is saved -- rebuild to retry."
            )
        if self.failed_puzzle_ids:
            n = len(self.failed_puzzle_ids)
            parts.append(
                f"{n} puzzle sheet{'s' if n != 1 else ''} failed to move to the solved "
                f"spreadsheet (puzzle ids: {self.failed_puzzle_ids}); the change is saved "
                f"and the tab is still on the main spreadsheet."
            )
        if not parts:
            return "A sheet update failed; the change is saved."
        return " ".join(parts)
