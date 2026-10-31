import { describe, expect, it } from "vitest";
import { discordSpans } from "./discordText";

describe("discordSpans", () => {
  it("turns shortcodes into emoji and keeps the rest of the text", () => {
    expect(discordSpans(":white_check_mark: Setup complete; :incoming_envelope: sent")).toEqual([
      { kind: "text", text: "✅ Setup complete; 📨 sent" },
    ]);
  });

  it("splits bold and code spans in order", () => {
    expect(discordSpans("Created hunt **Test Hunt**, admin pinned to `mod`.")).toEqual([
      { kind: "text", text: "Created hunt " },
      { kind: "bold", text: "Test Hunt" },
      { kind: "text", text: ", admin pinned to " },
      { kind: "code", text: "mod" },
      { kind: "text", text: "." },
    ]);
  });

  it("leaves shortcodes inside code spans alone", () => {
    expect(discordSpans("type `:x:` here")).toEqual([
      { kind: "text", text: "type " },
      { kind: "code", text: ":x:" },
      { kind: "text", text: " here" },
    ]);
  });

  it("leaves unpaired markers as plain text", () => {
    expect(discordSpans("a ** b ` c")).toEqual([{ kind: "text", text: "a ** b ` c" }]);
  });
});
