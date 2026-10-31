"""The hub's Discord server-template link: validation and canonical forms."""
import re

ACCEPTED_FORMS = ("`https://discord.new/<code>`, `discord.new/<code>` or "
                  "`https://discord.com/template/<code>`")

_PATTERN = re.compile(
    r"(?:https?://)?(?:discord\.new/|(?:www\.)?discord\.com/template/)([A-Za-z0-9]+)")


def normalise(text):
    """Canonical `https://discord.new/<code>` for an accepted form, else None."""
    match = _PATTERN.fullmatch((text or "").strip())
    if match is None:
        return None
    return f"https://discord.new/{match.group(1)}"


def code(link):
    """The template code from a canonical link."""
    return link.rsplit("/", 1)[-1]


def browser_url(link):
    """Fallback for when the Discord app ignores a discord.new link."""
    return f"https://discord.com/template/{code(link)}"
