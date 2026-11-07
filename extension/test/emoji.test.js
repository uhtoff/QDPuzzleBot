import { describe, it, expect } from "vitest";
import { replaceShortcodes } from "../src/lib/emoji.js";
import { tokenise, embedLines, emojiUrl, rewriteMentions, notificationText } from "../src/lib/discordText.js";
import { isBlank } from "../src/lib/format.js";

describe("shortcodes", () => {
  it("known replaced, unknown literal, adjacent", () => {
    expect(replaceShortcodes(":pencil: Clue :nope: :x::tada:")).toBe("📝 Clue :nope: ❌🎉");
  });
  it("tokenise: not inside code spans, but in plain and bold", () => {
    expect(tokenise(":x: `:x:` **:tada:**")).toEqual([
      { text: "❌ " }, { text: ":x:", code: true }, { text: " " }, { text: "🎉", bold: true },
    ]);
  });
  it("notification text replaces them", () => {
    expect(notificationText({ author_name: "Bot", content: ":pencil: Clue logged from **Cap**." })).toBe("Bot: 📝 Clue logged from Cap.");
  });
});

describe("custom emoji", () => {
  it("static and animated segments", () => {
    const segs = tokenise("a <:foo:123> <a:bar:456>", undefined);
    expect(segs[1]).toEqual({ text: ":foo:", emoji: { name: "foo", id: "123", animated: false } });
    expect(segs[3]).toEqual({ text: ":bar:", emoji: { name: "bar", id: "456", animated: true } });
    expect(emojiUrl(segs[1].emoji)).toBe("https://cdn.discordapp.com/emojis/123.png?size=32");
    expect(emojiUrl(segs[3].emoji)).toBe("https://cdn.discordapp.com/emojis/456.gif?size=32");
  });
  it("non-digit id stays text", () => {
    expect(tokenise("<:foo:abc>")).toEqual([{ text: "<:foo:abc>" }]);
  });
  it("rewriteMentions keeps them only on request", () => {
    expect(rewriteMentions("<:foo:1>")).toBe(":foo:");
    expect(rewriteMentions("<:foo:1>", undefined, { keepCustomEmoji: true })).toBe("<:foo:1>");
  });
});

describe("embedLines", () => {
  it("title, description, fields, footer in order", () => {
    expect(embedLines({ title: "T", description: "D", fields: [{ name: "n", value: "v" }, { name: "", value: "" }], footer: "F" })).toEqual([
      { kind: "title", text: "T" }, { kind: "description", text: "D" }, { kind: "field", text: "n: v" }, { kind: "footer", text: "F" },
    ]);
  });
  it("empty embed has no lines", () => expect(embedLines({ color: 5 })).toEqual([]));
});

describe("isBlank", () => {
  it("blank only with no text, embed lines or attachments", () => {
    expect(isBlank({ content: "  ", embeds: [], attachments: [] })).toBe(true);
    expect(isBlank({ content: "", embeds: [{ color: 1 }] })).toBe(true);
    expect(isBlank({ content: "", embeds: [{ title: "x" }] })).toBe(false);
    expect(isBlank({ content: "hi" })).toBe(false);
    expect(isBlank({ content: "", attachments: [{}] })).toBe(false);
  });
});
