// Notes are typed by any team member and shown to everyone on the dashboard:
// whatever they type must render as text, never as markup, and only http(s)
// links may become anchors.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { ChannelNamesContext, DiscordMarkdown } from "./DiscordMarkdown";

const render = (text: string) =>
  renderToStaticMarkup(
    <MantineProvider>
      <DiscordMarkdown text={text} />
    </MantineProvider>,
  );

describe("DiscordMarkdown", () => {
  it("renders the formatting", () => {
    const html = render("**bold** *it* __u__ ~~s~~ `c`");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<em>it</em>");
    expect(html).toContain("<u>u</u>");
    expect(html).toContain("<s>s</s>");
    expect(html).toMatch(/<code[^>]*>c<\/code>/);
  });

  it("typed HTML stays text", () => {
    const html = render('<img src=x onerror="alert(1)"> **<b>x</b>**');
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<b>");
    expect(html).toContain("&lt;img");
  });

  it("http(s) links open in a new tab; nothing else becomes a link", () => {
    const ok = render("[map](https://ex.com/m)");
    expect(ok).toMatch(/<a[^>]*href="https:\/\/ex.com\/m"[^>]*target="_blank"/);
    expect(ok).toContain('rel="noopener noreferrer"');
    const bad = render("[x](javascript:alert(1)) and data:text/html,hi");
    expect(bad).not.toContain("<a");
  });

  it("keeps line breaks within a paragraph", () => {
    expect(render("one\ntwo")).toMatch(/one<br\/>two/);
  });

  it("a bare Discord link is a chip naming its puzzle, from the page's channel names", () => {
    const url = "https://discord.com/channels/1227/1574/1575";
    const html = renderToStaticMarkup(
      <MantineProvider>
        <ChannelNamesContext.Provider value={new Map([["1574", "Cap ONe"]])}>
          <DiscordMarkdown text={`see ${url}`} />
        </ChannelNamesContext.Provider>
      </MantineProvider>,
    );
    expect(html).toContain("#Cap ONe › 💬");
    expect(html).toContain(`href="${url}"`);
    expect(html).toContain(`title="${url}"`);
  });

  it("a [text](discord link) keeps its own text", () => {
    const html = render("[the clue](https://discord.com/channels/1/2/3)");
    expect(html).toContain(">the clue</a>");
    expect(html).not.toContain("💬");
  });
});
