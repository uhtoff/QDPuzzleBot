// Notes are typed by any team member and shown to everyone on the dashboard:
// whatever they type must render as text, never as markup, and only http(s)
// links may become anchors.
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { DiscordMarkdown } from "./DiscordMarkdown";

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
});
