import { beforeAll, describe, expect, it, vi } from "vitest";

// dashboard.js is a content script with top-level side effects: load it once
// against stubbed page globals, then inspect what it published and relays.
const dataset = {};
let onMessage = null;
let onRuntime = null;
const sendMessage = vi.fn(() => Promise.resolve());
const win = {
  postMessage: vi.fn(),
  addEventListener: (type, fn) => {
    if (type === "message") onMessage = fn;
  },
};

beforeAll(async () => {
  vi.stubGlobal("document", { documentElement: { dataset } });
  vi.stubGlobal("window", win);
  vi.stubGlobal("location", { origin: "https://dash.test" });
  vi.stubGlobal("chrome", {
    runtime: {
      sendMessage,
      getManifest: () => ({ version: "9.8.7" }),
      onMessage: { addListener: (fn) => (onRuntime = fn) },
    },
  });
  await import("../src/dashboard.js");
});

const post = (action) =>
  onMessage({ source: win, origin: "https://dash.test", data: { source: "puzzbot-dashboard", action, payload: {} } });

describe("dashboard content script", () => {
  it("marks the page and lists exactly the actions it relays", () => {
    expect(dataset.puzzbotExtension).toBe("1");
    expect(dataset.puzzbotVersion).toBe("9.8.7"); // the dashboard compares it with versions.json
    const listed = dataset.puzzbotActions.split(" ");
    expect(listed.sort()).toEqual(["closeSolved", "openPage", "openPuzzle", "openRound", "work"]);
    // Every listed action is relayed...
    for (const action of listed) {
      sendMessage.mockClear();
      post(action);
      expect(sendMessage).toHaveBeenCalledWith({ action, payload: {} });
    }
    // ...and one not listed is not, so the list cannot claim more than the gate allows.
    sendMessage.mockClear();
    post("notAnAction");
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("relays a board link when the page supports it", () => {
    win.postMessage.mockClear();
    dataset.puzzbotLinks = "1";
    const respond = vi.fn();
    onRuntime({ type: "openBoardLink", search: "?image=5" }, {}, respond);
    expect(win.postMessage).toHaveBeenCalledWith(
      { source: "puzzbot-extension", action: "openBoardLink", search: "?image=5" },
      "https://dash.test",
    );
    expect(respond).toHaveBeenCalledWith(true);
  });

  it("declines when the page has not published support", () => {
    win.postMessage.mockClear();
    delete dataset.puzzbotLinks;
    const respond = vi.fn();
    onRuntime({ type: "openBoardLink", search: "?image=5" }, {}, respond);
    expect(win.postMessage).not.toHaveBeenCalled();
    expect(respond).toHaveBeenCalledWith(false);
  });

  it("ignores other messages and a non-string search", () => {
    win.postMessage.mockClear();
    dataset.puzzbotLinks = "1";
    const respond = vi.fn();
    onRuntime({ type: "other" }, {}, respond);
    onRuntime({ type: "openBoardLink", search: 5 }, {}, respond);
    expect(respond).not.toHaveBeenCalledWith(true);
    expect(win.postMessage).not.toHaveBeenCalled();
  });
});
