import { Anchor, Blockquote, Code, List, Stack, Text } from "@mantine/core";
import { createContext, Fragment, useContext, type ReactNode } from "react";

import { parseBlocks, type Block, type Inline } from "../lib/discordMarkdown";
import { discordLinkLabel, parseDiscordLink } from "../lib/discordLinks";
import { safeHref } from "../lib/safeHref";

// Notes as Discord shows them (see lib/discordMarkdown.ts). Everything is built
// as React elements: typed text never becomes markup, and links pass safeHref.

type ChannelName = (id: string) => string | null;
const noNames: ChannelName = () => null;

/** Channel id -> name for the hunt on screen; the hunt page provides it, so every
 *  note below names its Discord links without passing names down. */
export const ChannelNamesContext = createContext<ReadonlyMap<string, string> | null>(null);

// Discord's own chip for a channel/message link, so a bare link reads as it does there.
const CHIP = {
  padding: "0 4px",
  borderRadius: 4,
  background: "var(--mantine-color-indigo-light)",
  color: "var(--mantine-color-indigo-light-color)",
  textDecoration: "none",
} as const;

function inline(spans: Inline[], names: ChannelName = noNames): ReactNode[] {
  return spans.map((s, i) => {
    switch (s.kind) {
      case "text":
        return <Fragment key={i}>{s.text}</Fragment>;
      case "code":
        return <Code key={i}>{s.text}</Code>;
      case "bold":
        return <strong key={i}>{inline(s.children, names)}</strong>;
      case "italic":
        return <em key={i}>{inline(s.children, names)}</em>;
      case "underline":
        return <u key={i}>{inline(s.children, names)}</u>;
      case "strike":
        return <s key={i}>{inline(s.children, names)}</s>;
      case "link": {
        const href = safeHref(s.href);
        // A bare Discord link (its text is the URL itself) shows as a chip naming
        // the puzzle; a [text](url) link keeps the text it was given.
        const bare = s.children.length === 1 && s.children[0].kind === "text" && s.children[0].text === s.href;
        const discord = href && bare ? parseDiscordLink(href) : null;
        if (href && discord) {
          return (
            <Anchor key={i} href={href} target="_blank" rel="noopener noreferrer" inherit title={href} style={CHIP}>
              {discordLinkLabel(discord, names)}
            </Anchor>
          );
        }
        return href ? (
          <Anchor key={i} href={href} target="_blank" rel="noopener noreferrer" inherit title={href}>
            {inline(s.children, names)}
          </Anchor>
        ) : (
          <Fragment key={i}>{inline(s.children, names)}</Fragment>
        );
      }
    }
  });
}

const lines = (ls: Inline[][], names: ChannelName) =>
  ls.map((l, i) => (
    <Fragment key={i}>
      {i > 0 && <br />}
      {inline(l, names)}
    </Fragment>
  ));

const HEADING_SIZE = { 1: "lg", 2: "md", 3: "sm" } as const;

function block(b: Block, i: number, names: ChannelName): ReactNode {
  switch (b.kind) {
    case "paragraph":
      return <Text key={i} size="sm" style={{ overflowWrap: "anywhere" }}>{lines(b.lines, names)}</Text>;
    case "heading":
      return <Text key={i} size={HEADING_SIZE[b.level]} fw={700}>{inline(b.content, names)}</Text>;
    case "subtext":
      return <Text key={i} size="xs" c="dimmed">{inline(b.content, names)}</Text>;
    case "quote":
      return (
        <Blockquote key={i} p="xs" fz="sm" style={{ overflowWrap: "anywhere" }}>
          {lines(b.lines, names)}
        </Blockquote>
      );
    case "list":
      return (
        <List key={i} size="sm" type={b.ordered ? "ordered" : "unordered"} start={b.ordered ? b.start : undefined}>
          {b.items.map((item, j) => <List.Item key={j}>{inline(item, names)}</List.Item>)}
        </List>
      );
    case "codeblock":
      return <Code key={i} block>{b.text}</Code>;
  }
}

/** A note (or any member-typed text) rendered as Discord markdown. `channelName`
 *  names the puzzle behind a Discord channel id, for link chips. */
export function DiscordMarkdown({ text, channelName }: { text: string; channelName?: ChannelName }) {
  const fromPage = useContext(ChannelNamesContext);
  const names: ChannelName = channelName ?? (fromPage ? (id) => fromPage.get(id) ?? null : noNames);
  return <Stack gap={4} style={{ minWidth: 0 }}>{parseBlocks(text).map((b, i) => block(b, i, names))}</Stack>;
}
