"""The "Images" field of a puzzle channel's pinned info message.

Lists dashboard deep links to a puzzle's own images and the source-evidence
images that point at it, trimmed to whatever room the embed budget leaves.
"""
import re
from dataclasses import dataclass

from bot.utils.urls import image_link

NOTE_LIMIT = 40
_MD_SPECIAL = re.compile(r"([\[\]()\\*_~`|])")


@dataclass(frozen=True)
class PinImage:
    media_id: int
    note: str | None
    is_evidence: bool


def _clean_note(note: str) -> str:
    one_line = " ".join(note.split())
    if len(one_line) > NOTE_LIMIT:
        one_line = one_line[:NOTE_LIMIT] + "…"
    return _MD_SPECIAL.sub(r"\\\1", one_line)


def images_field_value(images: list[PinImage], base: str, hunt_uid: str,
                       puzzle_id: int, budget: int) -> str | None:
    """Link lines for *images* in the given order, or None if nothing fits."""
    if not images or not base:
        return None
    lines = []
    counts = {False: 0, True: 0}
    for img in images:
        counts[img.is_evidence] += 1
        kind = "Evidence" if img.is_evidence else "Image"
        note = _clean_note(img.note) if img.note else ""
        label = f"{kind}: {note}" if note else f"{kind} {counts[img.is_evidence]}"
        url = image_link(base, hunt_uid, puzzle_id, img.media_id)
        lines.append(f"• [{label}]({url})")
    if len("\n".join(lines)) <= budget:
        return "\n".join(lines)
    puzzle_url = image_link(base, hunt_uid, puzzle_id)

    def more(n: int) -> str:
        return f"• [+{n} more on the dashboard]({puzzle_url})"

    shown = 0
    used = 0
    for i, line in enumerate(lines):
        added = used + len(line) + (1 if i else 0)
        if added + 1 + len(more(len(lines) - i - 1)) > budget:
            break
        used = added
        shown = i + 1
    if shown == 0:
        only = more(len(lines))
        return only if len(only) <= budget else None
    return "\n".join(lines[:shown] + [more(len(lines) - shown)])


def pin_images_for(puzzle_id: int) -> list[PinImage]:
    """The puzzle's own images (by position) then its image evidence (by created_at)."""
    from bot.store import EvidenceJsonDb, PuzzleImageJsonDb

    own = [PinImage(p.media_id, None, False)
           for p in PuzzleImageJsonDb.get_by_puzzle(puzzle_id)]
    evidence = [PinImage(e.media_id, e.note or None, True)
                for e in EvidenceJsonDb.get_image_evidence_for_puzzle(puzzle_id)
                if e.media_id is not None]
    return own + evidence
