"""Swap Discord user/role mention markup for readable names.

The bridge stores message text for the dashboard and the chat extension, which
have no member list of their own: a raw ``<@123>`` there can only be shown as
"@someone". Resolving at capture time, while the bot still holds the message's
mention objects and the guild cache, puts the name into the stored text once,
for every reader. Channel mentions (``<#id>``) are left alone: readers already
turn those into puzzle names. A mention that cannot be resolved stays raw, so a
reader falls back to its generic label rather than showing a wrong name.
"""
import re
from collections.abc import Callable

Namer = Callable[[int], "str | None"]

_USER = re.compile(r"<@!?(\d+)>")
_ROLE = re.compile(r"<@&(\d+)>")


def resolve_mentions(text: str, user_name: Namer, role_name: Namer) -> str:
    def swap(namer: Namer):
        def repl(m: re.Match) -> str:
            name = namer(int(m.group(1)))
            return f"@{name}" if isinstance(name, str) and name else m.group(0)
        return repl

    if not text or "<@" not in text:
        return text
    return _USER.sub(swap(user_name), _ROLE.sub(swap(role_name), text))
