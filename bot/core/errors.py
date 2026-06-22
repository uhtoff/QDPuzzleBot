"""Shared service-layer exceptions (M5)."""


class SheetRefreshError(RuntimeError):
    """Raised by a fan-out service method (solve / retag) after it has fully
    completed its DB + archive work, to signal that one or more roll-up sheets
    failed to refresh. Each failed round is already badged (last_ok=0) on its
    rollup row; this exception lets the edge surface a non-fatal "sheet stale"
    toast without rolling back the committed mutation.

    `failed_round_ids` is the list of meta round ids whose refresh failed.
    """

    def __init__(self, failed_round_ids):
        self.failed_round_ids = list(failed_round_ids)
        n = len(self.failed_round_ids)
        super().__init__(
            f"{n} meta sheet{'s' if n != 1 else ''} failed to refresh "
            f"(round ids: {self.failed_round_ids}); the change is saved — rebuild to retry."
        )
