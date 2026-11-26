import { Anchor, Blockquote, Code, List, Stack, Text } from "@mantine/core";
import { Fragment, type ReactNode } from "react";

import { parseBlocks, type Block, type Inline } from "../lib/discordMarkdown";
import { safeHref } from "../lib/safeHref";

// Notes as Discord shows them (see lib/discordMarkdown.ts). Everything is built
// as React elements: typed text never becomes markup, and links pass safeHref.

function inline(spans: Inline[]): ReactNode[] {
  return spans.map((s, i) => {
    switch (s.kind) {
      case "text":
        return <Fragment key={i}>{s.text}</Fragment>;
      case "code":
        return <Code key={i}>{s.text}</Code>;
      case "bold":
        return <strong key={i}>{inline(s.children)}</strong>;
      case "italic":
        return <em key={i}>{inline(s.children)}</em>;
      case "underline":
        return <u key={i}>{inline(s.children)}</u>;
      case "strike":
        return <s key={i}>{inline(s.children)}</s>;
      case "link": {
        const href = safeHref(s.href);
        return href ? (
          <Anchor key={i} href={href} target="_blank" rel="noopener noreferrer" inherit>
            {inline(s.children)}
          </Anchor>
        ) : (
          <Fragment key={i}>{inline(s.children)}</Fragment>
        );
      }
    }
  });
}

const lines = (ls: Inline[][]) =>
  ls.map((l, i) => (
    <Fragment key={i}>
      {i > 0 && <br />}
      {inline(l)}
    </Fragment>
  ));

const HEADING_SIZE = { 1: "lg", 2: "md", 3: "sm" } as const;

function block(b: Block, i: number): ReactNode {
  switch (b.kind) {
    case "paragraph":
      return <Text key={i} size="sm" style={{ overflowWrap: "anywhere" }}>{lines(b.lines)}</Text>;
    case "heading":
      return <Text key={i} size={HEADING_SIZE[b.level]} fw={700}>{inline(b.content)}</Text>;
    case "subtext":
      return <Text key={i} size="xs" c="dimmed">{inline(b.content)}</Text>;
    case "quote":
      return (
        <Blockquote key={i} p="xs" fz="sm" style={{ overflowWrap: "anywhere" }}>
          {lines(b.lines)}
        </Blockquote>
      );
    case "list":
      return (
        <List key={i} size="sm" type={b.ordered ? "ordered" : "unordered"} start={b.ordered ? b.start : undefined}>
          {b.items.map((item, j) => <List.Item key={j}>{inline(item)}</List.Item>)}
        </List>
      );
    case "codeblock":
      return <Code key={i} block>{b.text}</Code>;
  }
}

/** A note (or any member-typed text) rendered as Discord markdown. */
export function DiscordMarkdown({ text }: { text: string }) {
  return <Stack gap={4} style={{ minWidth: 0 }}>{parseBlocks(text).map(block)}</Stack>;
}
