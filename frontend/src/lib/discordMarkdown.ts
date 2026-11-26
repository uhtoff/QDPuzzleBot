import { emojify } from "../emoji";

// The markdown Discord renders, parsed into plain data for React to render.
// Notes are typed by any team member and also read in Discord (`!notes`), so the
// dashboard shows them the way Discord does. Nothing here produces HTML: the
// renderer (components/DiscordMarkdown.tsx) builds elements, so typed text can
// never become markup, and only http(s) links are recognised at all.

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "code"; text: string }
  | { kind: "link"; href: string; children: Inline[] }
  | { kind: "bold" | "italic" | "underline" | "strike"; children: Inline[] };

export type Block =
  | { kind: "paragraph"; lines: Inline[][] }
  | { kind: "heading"; level: 1 | 2 | 3; content: Inline[] }
  | { kind: "subtext"; content: Inline[] }
  | { kind: "quote"; lines: Inline[][] }
  | { kind: "list"; ordered: boolean; start: number; items: Inline[][] }
  | { kind: "codeblock"; text: string };

type Rule = {
  re: RegExp;
  build: (m: RegExpExecArray) => { node: Inline; end?: number };
};

// Trailing punctuation is the sentence's, not the URL's.
const URL_TAIL = /[.,:;!?'")\]]+$/;

// Earlier rules win a tie on where they start. Code and links come first, so
// markers inside them are left alone; bold before italic so `***x***` is both.
// A closing `**` / `__` must not be followed by another of its character, which
// makes `**a *b***` close on the last two (as Discord does).
const RULES: Rule[] = [
  { re: /`([^`\n]+)`/g, build: (m) => ({ node: { kind: "code", text: m[1] } }) },
  {
    re: /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g,
    build: (m) => ({ node: { kind: "link", href: m[2], children: parseInline(m[1]) } }),
  },
  {
    re: /https?:\/\/[^\s<]+/g,
    build: (m) => {
      const url = m[0].replace(URL_TAIL, "");
      return { node: { kind: "link", href: url, children: [{ kind: "text", text: url }] }, end: m.index + url.length };
    },
  },
  { re: /\*\*(.+?)\*\*(?!\*)/g, build: (m) => ({ node: { kind: "bold", children: parseInline(m[1]) } }) },
  { re: /__(.+?)__(?!_)/g, build: (m) => ({ node: { kind: "underline", children: parseInline(m[1]) } }) },
  { re: /~~(.+?)~~/g, build: (m) => ({ node: { kind: "strike", children: parseInline(m[1]) } }) },
  // Not next to a space on the inside: `2 * 3 * 4` stays as typed.
  { re: /\*(?![\s*])(.+?)(?<!\s)\*/g, build: (m) => ({ node: { kind: "italic", children: parseInline(m[1]) } }) },
  // Not inside a word: snake_case stays as typed.
  {
    re: /(?<![A-Za-z0-9_])_(?![\s_])(.+?)(?<!\s)_(?![A-Za-z0-9_])/g,
    build: (m) => ({ node: { kind: "italic", children: parseInline(m[1]) } }),
  },
];

/** One line's spans. Unmatched markers stay as typed. */
export function parseInline(src: string): Inline[] {
  const out: Inline[] = [];
  const text = (s: string) => {
    if (!s) return;
    const prev = out[out.length - 1];
    if (prev?.kind === "text") prev.text += emojify(s);
    else out.push({ kind: "text", text: emojify(s) });
  };
  let pos = 0;
  while (pos < src.length) {
    let best: { m: RegExpExecArray; rule: Rule } | null = null;
    for (const rule of RULES) {
      rule.re.lastIndex = pos;
      const m = rule.re.exec(src);
      if (m && (!best || m.index < best.m.index)) best = { m, rule };
    }
    if (!best) break;
    text(src.slice(pos, best.m.index));
    const { node, end } = best.rule.build(best.m);
    out.push(node);
    pos = end ?? best.m.index + best.m[0].length;
  }
  text(src.slice(pos));
  return out;
}

const HEADING = /^(#{1,3}) (.+)$/;
const SUBTEXT = /^-# (.+)$/;
const QUOTE = /^> ?(.*)$/;
const BULLET = /^[-*] (.+)$/;
const NUMBERED = /^(\d+)\. (.+)$/;
const FENCE = /^```/;

/** A note's blocks: paragraphs (line breaks kept), headings, small text, quotes,
 *  lists and fenced code. */
export function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  const last = () => blocks[blocks.length - 1];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (FENCE.test(line)) {
      const close = lines.findIndex((l, j) => j > i && l.trim() === "```");
      if (close > i) {
        // A first line of just a word (```py) names the language; drop it.
        const opening = line.slice(3).trim();
        const body = lines.slice(i + 1, close);
        if (opening && !/^\w+$/.test(opening)) body.unshift(opening);
        blocks.push({ kind: "codeblock", text: body.join("\n") });
        i = close + 1;
        continue;
      }
    }
    let m: RegExpExecArray | null;
    if (!line.trim()) {
      blocks.push({ kind: "paragraph", lines: [] }); // a break; empty ones are dropped below
    } else if ((m = HEADING.exec(line))) {
      blocks.push({ kind: "heading", level: m[1].length as 1 | 2 | 3, content: parseInline(m[2]) });
    } else if ((m = SUBTEXT.exec(line))) {
      blocks.push({ kind: "subtext", content: parseInline(m[1]) });
    } else if ((m = QUOTE.exec(line))) {
      const b = last();
      if (b?.kind === "quote") b.lines.push(parseInline(m[1]));
      else blocks.push({ kind: "quote", lines: [parseInline(m[1])] });
    } else if ((m = BULLET.exec(line)) || (m = NUMBERED.exec(line))) {
      const ordered = m.length === 3 && /^\d+$/.test(m[1]);
      const content = parseInline(ordered ? m[2] : m[1]);
      const b = last();
      if (b?.kind === "list" && b.ordered === ordered) b.items.push(content);
      else blocks.push({ kind: "list", ordered, start: ordered ? Number(m[1]) : 1, items: [content] });
    } else {
      const b = last();
      if (b?.kind === "paragraph" && b.lines.length) b.lines.push(parseInline(line));
      else blocks.push({ kind: "paragraph", lines: [parseInline(line)] });
    }
    i++;
  }
  return blocks.filter((b) => b.kind !== "paragraph" || b.lines.length);
}

/** Whether the text renders any differently from plain text (line breaks aside):
 *  an editor shows a preview only then. */
export function hasFormatting(src: string): boolean {
  return parseBlocks(src).some(
    (b) => b.kind !== "paragraph" || b.lines.some((l) => l.some((s) => s.kind !== "text")),
  );
}
