from dataclasses import dataclass
from typing import Optional


@dataclass
class FeederPoolItemData:
    """One given feeder — a finished answer string the puzzle handed the solvers.

    It is never a puzzle: it has no channel, no thread and no sheet tab. `text` is
    the identity a solver sees; `id` is the identity the sheets diff on.
    """
    id: int = 0
    minimeta_puzzle_id: int = 0
    text: str = ""
    note: Optional[str] = None
    position: int = 0

    @classmethod
    def import_dict(cls, row: dict):
        item = cls()
        for attr in item.__dict__:
            if attr in row:
                setattr(item, attr, row[attr])
        return item


@dataclass
class FeederClaimData:
    """A claim that `item_id` belongs to `target_id` (a Mini, or the Minimeta itself).

    `confirmed` is set when the claiming mini is solved — solving is the proof. A
    claim is never deleted to resolve a contest: a losing claim is information, and
    deleting it would make unsolve lossy.
    """
    id: int = 0
    item_id: int = 0
    target_id: int = 0
    confirmed: bool = False

    @classmethod
    def import_dict(cls, row: dict):
        claim = cls()
        for attr in claim.__dict__:
            if attr in row:
                setattr(claim, attr, row[attr])
        claim.confirmed = bool(claim.confirmed)   # MySQL hands back 0/1
        return claim
