import { beforeAll, describe, expect, it, vi } from "vitest";

// dashboard.js is a content script with top-level side effects: load it once
// against stubbed page globals, then inspect what it published and relays.
const dataset = {};
let onMessage = null;
const sendMessage = vi.fn(() => Promise.resolve());
const win = {
  addEventListener: (type, fn) => {
    if (type === "message") onMessage = fn;
  },
};

beforeAll(async () => {
  vi.stubGlobal("document", { documentElement: { dataset } });
  vi.stubGlobal("window", win);
  vi.stubGlobal("location", { origin: "https://dash.test" });
  vi.stubGlobal("chrome", { runtime: { sendMessage } });
  await import("../src/dashboard.js");
});

const post = (action) =>
  onMessage({ source: win, origin: "https://dash.test", data: { source: "puzzbot-dashboard", action, payload: {} } });

describe("dashboard content script", () => {
  it("marks the page and lists exactly the actions it relays", () => {
    expect(dataset.puzzbotExtension).toBe("1");
    const listed = dataset.puzzbotActions.split(" ");
    expect(listed.sort()).toEqual(["closeSolved", "openRound", "work"]);
    // Every listed action is relayed...
    for (const action of listed) {
      sendMessage.mockClear();
      post(action);
      expect(sendMessage).toHaveBeenCalledWith({ action, payload: {} });
    }
    // ...and one not listed is not, so the list cannot claim more than the gate allows.
    sendMessage.mockClear();
    post("openPuzzle");
    expect(sendMessage).not.toHaveBeenCalled();
  });
});
