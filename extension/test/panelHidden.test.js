import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

// A CSS `display` rule on an element beats the browser's own [hidden] { display: none },
// so a panel element that starts hidden and is given a display needs its own [hidden]
// rule, or it shows as an empty bar (the update notice did, before 1.2.1).
const html = readFileSync(new URL("../src/panel.html", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/panel.css", import.meta.url), "utf8");

const hiddenIds = [...html.matchAll(/<\w+[^>]*\bid="([\w-]+)"[^>]*\bhidden\b[^>]*>/g)].map((m) => m[1]);

function setsDisplay(id) {
  const rule = new RegExp(`(^|[},\\s])#${id}\\s*(,[^{]*)?\\{[^}]*\\bdisplay\\s*:`, "m");
  return rule.test(css);
}

function hidesWhenHidden(id) {
  const rule = new RegExp(`#${id}\\[hidden\\][^{]*\\{[^}]*display\\s*:\\s*none`);
  return rule.test(css);
}

describe("panel elements that start hidden", () => {
  it("finds the hidden elements", () => {
    expect(hiddenIds).toEqual(expect.arrayContaining(["update", "peek", "unread", "notice", "composer"]));
  });
  it.each(hiddenIds)("#%s stays hidden when its CSS sets a display", (id) => {
    if (setsDisplay(id)) expect(hidesWhenHidden(id)).toBe(true);
  });
});
