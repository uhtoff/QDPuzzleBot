import discord
import re
from typing import List, Optional

FIELD_VALUE_LIMIT = 1024
FIELD_NAME_LIMIT = 256
EMBED_TOTAL_LIMIT = 6000
MAX_FIELDS = 25

def _chunk(text: str, size: int) -> List[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] if text else [""]

def chunk_note_pretty(text: str, limit: int = 1024) -> List[str]:
    """
    Split a long note into <=limit chunks, preferring paragraph boundaries.
    Order of preference:
      1) blank-line paragraphs
      2) single-line breaks
      3) sentence-ish breaks
      4) word breaks
      5) hard character splits (last resort)
    """
    if not text:
        return [""]

    def flush(acc: List[str], buf: str):
        if buf:
            acc.append(buf)
        return ""

    chunks: List[str] = []
    buf = ""

    # 1) Split into paragraphs by blank lines
    paragraphs = re.split(r"\n{2,}", text.strip())

    def add_piece(piece: str):
        nonlocal buf, chunks
        if not piece:
            return

        # If it fits in the current buffer (with separator), add it
        sep = "\n\n" if buf else ""
        if len(buf) + len(sep) + len(piece) <= limit:
            buf = f"{buf}{sep}{piece}" if buf else piece
            return

        # Otherwise, flush current buffer and handle the piece
        buf = flush(chunks, buf)

        if len(piece) <= limit:
            buf = piece
            return

        # 2) Paragraph itself too big -> split on single newlines
        lines = piece.splitlines()
        if len(lines) > 1:
            tmp = ""
            for line in lines:
                sep2 = "\n" if tmp else ""
                if len(tmp) + len(sep2) + len(line) <= limit:
                    tmp = f"{tmp}{sep2}{line}" if tmp else line
                else:
                    tmp = flush(chunks, tmp)
                    if len(line) <= limit:
                        tmp = line
                    else:
                        # 3) Line too big -> split on sentence-ish boundaries
                        _split_long_text(line, limit, chunks)
            buf = tmp
            return

        # 3/4/5) Single giant line/paragraph -> split further
        _split_long_text(piece, limit, chunks)

    def _split_long_text(s: str, limit: int, out: List[str]):
        """
        Split very long text into <=limit chunks, preferring sentence-ish boundaries,
        then words, then hard split.
        """
        # Try sentence-ish splits (keeps delimiters)
        parts = re.split(r"(?<=[.!?])\s+", s)
        if len(parts) > 1:
            tmp = ""
            for part in parts:
                sep = " " if tmp else ""
                if len(tmp) + len(sep) + len(part) <= limit:
                    tmp = f"{tmp}{sep}{part}" if tmp else part
                else:
                    if tmp:
                        out.append(tmp)
                        tmp = ""
                    if len(part) <= limit:
                        tmp = part
                    else:
                        _split_by_words_or_hard(part, limit, out)
            if tmp:
                out.append(tmp)
            return

        _split_by_words_or_hard(s, limit, out)

    def _split_by_words_or_hard(s: str, limit: int, out: List[str]):
        words = s.split(" ")
        if len(words) > 1:
            tmp = ""
            for w in words:
                sep = " " if tmp else ""
                if len(tmp) + len(sep) + len(w) <= limit:
                    tmp = f"{tmp}{sep}{w}" if tmp else w
                else:
                    if tmp:
                        out.append(tmp)
                    # single word longer than limit -> hard split
                    if len(w) > limit:
                        for i in range(0, len(w), limit):
                            out.append(w[i:i+limit])
                        tmp = ""
                    else:
                        tmp = w
            if tmp:
                out.append(tmp)
            return

        # Hard split last resort
        for i in range(0, len(s), limit):
            out.append(s[i:i+limit])

    # Make helper visible inside add_piece
    globals()["_split_long_text"] = _split_long_text
    globals()["_split_by_words_or_hard"] = _split_by_words_or_hard

    for p in paragraphs:
        add_piece(p)

    if buf:
        chunks.append(buf)

    # Clean up accidental empty chunks
    return [c if c else "\u200b" for c in chunks]



def build_note_embeds(
    *,
    message: Optional[str],
    notes: List[str],
    title: Optional[str] = None,
    note_embed: Optional[bool] = True
) -> List[discord.Embed]:
    """
    Builds 1 to N embeds containing notes, splitting long notes across multiple fields
    and spilling over to new embeds when field count / total chars would exceed limits.
    """
    embeds: List[discord.Embed] = []
    embed = discord.Embed(title=title, description=message or "")

    def embed_total_chars(e: discord.Embed) -> int:
        total = 0
        if e.title:
            total += len(e.title)
        if e.description:
            total += len(e.description)
        if e.footer and e.footer.text:
            total += len(e.footer.text)
        for f in e.fields:
            total += len(f.name) + len(f.value)
        return total

    def start_new_embed():
        nonlocal embed
        embeds.append(embed)
        embed = discord.Embed(title=title)  # keep title; omit description after first for cleanliness

    note_number = 1

    for note in notes:
        # split long note into 1024-sized chunks (field value limit)
        chunks = chunk_note_pretty(note, FIELD_VALUE_LIMIT)

        for ci, chunk in enumerate(chunks):
            if note_embed is True:
                base_name = f"Note {note_number}"
            else:
                base_name = ""
            name = base_name if ci == 0 else f"{base_name} (cont. {ci})"
            name = name[:FIELD_NAME_LIMIT]

            # If adding this field would exceed field count or embed total chars, spill to a new embed.
            # +2 is a tiny buffer for safety.
            projected = embed_total_chars(embed) + len(name) + len(chunk) + 2

            if len(embed.fields) >= MAX_FIELDS or projected > EMBED_TOTAL_LIMIT:
                start_new_embed()

            embed.add_field(name=name, value=chunk or "\u200b", inline=False)

        note_number += 1

    embeds.append(embed)

    # Add page footers if multiple embeds
    if len(embeds) > 1:
        for idx, e in enumerate(embeds, start=1):
            e.set_footer(text=f"Page {idx}/{len(embeds)}")

    return embeds
