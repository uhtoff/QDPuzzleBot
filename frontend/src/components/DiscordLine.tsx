import { Code, Text } from "@mantine/core";

import { discordSpans } from "../lib/discordText";

/** One of the bot's Discord-formatted replies (`:shortcode:` emoji, **bold**,
 *  `code`), rendered as it reads in Discord rather than as raw markup. */
export function DiscordLine({ line }: { line: string }) {
  return (
    <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
      {discordSpans(line).map((span, i) =>
        span.kind === "bold" ? (
          <strong key={i}>{span.text}</strong>
        ) : span.kind === "code" ? (
          <Code key={i}>{span.text}</Code>
        ) : (
          <span key={i}>{span.text}</span>
        ),
      )}
    </Text>
  );
}
