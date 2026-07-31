"""Small text helpers for user-facing message bodies."""


def flush_left(text: str) -> str:
    """Strip leading whitespace from every line.

    Multi-line message bodies in the cogs are written as triple-quoted literals
    indented to match the surrounding code::

        links = (f\"\"\"    • [Puzzle link]({url})
                                • [Google Sheet]({sheet})
                                \"\"\")

    Python keeps that indentation in the *value*, so Discord renders the
    bullets pushed 4 and 32 columns right - ragged, and nothing to do with
    embeds. ``textwrap.dedent`` does not help: it removes only the common
    leading prefix, and the first line (sitting next to the opening quote) has
    none, so the common prefix is empty and nothing is stripped.

    These bodies are flat bullet lists with no intentional nesting, so
    stripping each line is both correct and sufficient. Blank lines are
    preserved as blank rather than dropped, since they are paragraph breaks.
    """
    if not text:
        return text
    return "\n".join(line.strip() for line in text.splitlines())
