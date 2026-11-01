"""Pure data model for the !reconcile feature (no Discord/Sheets imports).

A ReconcileReport accumulates the actions reconcile took (or would take, in
report-only mode), plus orphan candidates and per-item failures, so the cog edge
can render a summary and gate destructive deletes behind a confirmation.
"""
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class ReconcileAction:
    kind: str          # e.g. "channel_create", "channel_relink", "channel_move",
                       # "channel_rename", "tab_create", "tab_rename",
                       # "round_category_broken"; files mode: "file_create",
                       # "file_move", "file_rename"
    target: str        # human label (puzzle/round name)
    detail: str = ""   # extra context for the report line


@dataclass
class ReconcileReport:
    actions: List[ReconcileAction] = field(default_factory=list)
    orphan_channels: List[Tuple[int, str]] = field(default_factory=list)  # (channel_id, name)
    orphan_tabs: List[Tuple[int, str]] = field(default_factory=list)      # (sheet_id, title)
    failures: List[Tuple[str, str]] = field(default_factory=list)         # (target, error)
    # Files mode: files in the hunt's Drive folders that match no puzzle (and
    # are not the reference spreadsheet). Reported only -- never deleted, and
    # not orphans (has_orphans ignores them), so `apply orphans` leaves them.
    stray_files: List[Tuple[str, str]] = field(default_factory=list)      # (file_id, name)

    def add(self, kind, target, detail=""):
        self.actions.append(ReconcileAction(kind, target, detail))

    def add_orphan_channel(self, channel_id, name):
        self.orphan_channels.append((channel_id, name))

    def add_orphan_tab(self, sheet_id, title):
        self.orphan_tabs.append((sheet_id, title))

    def add_stray_file(self, file_id, name):
        self.stray_files.append((file_id, name))

    def add_failure(self, target, error):
        self.failures.append((target, str(error)))

    def counts(self):
        return dict(Counter(a.kind for a in self.actions))

    def has_orphans(self):
        return bool(self.orphan_channels or self.orphan_tabs)
