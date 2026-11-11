import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { closeSolved, extensionPresent, openPuzzle, openRound, workOn } from "./extension";

// vitest runs in node here: stub the two globals the module touches.
const dataset: Record<string, string> = {};
const postMessage = vi.fn();

beforeEach(() => {
  for (const k of Object.keys(dataset)) delete dataset[k];
  postMessage.mockReset();
  vi.stubGlobal("document", { documentElement: { dataset } });
  vi.stubGlobal("window", { postMessage, location: { origin: "https://dash.test" } });
});
afterEach(() => vi.unstubAllGlobals());

describe("extension bridge", () => {
  it("is absent without the attribute and present with it", () => {
    expect(extensionPresent()).toBe(false);
    dataset.puzzbotExtension = "1";
    expect(extensionPresent()).toBe(true);
    dataset.puzzbotExtension = "0";
    expect(extensionPresent()).toBe(false);
  });

  it("workOn posts the work message to the page's own origin", () => {
    workOn({ id: 7, sheet_url: "https://docs.google.com/spreadsheets/d/abc", hunt_uid: "h1" });
    expect(postMessage).toHaveBeenCalledWith(
      {
        source: "puzzbot-dashboard",
        action: "work",
        payload: { puzzleId: 7, sheetUrl: "https://docs.google.com/spreadsheets/d/abc", huntUid: "h1" },
      },
      "https://dash.test",
    );
  });

  it("workOn does nothing without a sheet url", () => {
    workOn({ id: 7, sheet_url: null, hunt_uid: "h1" });
    expect(postMessage).not.toHaveBeenCalled();
  });

  it("openPuzzle, openRound and closeSolved post their shapes", () => {
    openPuzzle("Puzzle A", "https://docs.google.com/spreadsheets/d/s", "https://hunt.test/p/a");
    openRound("Round A", ["u1", "u2"]);
    closeSolved();
    expect(postMessage).toHaveBeenNthCalledWith(
      1,
      {
        source: "puzzbot-dashboard",
        action: "openPuzzle",
        payload: {
          name: "Puzzle A",
          sheetUrl: "https://docs.google.com/spreadsheets/d/s",
          puzzleUrl: "https://hunt.test/p/a",
        },
      },
      "https://dash.test",
    );
    expect(postMessage).toHaveBeenNthCalledWith(
      2,
      { source: "puzzbot-dashboard", action: "openRound", payload: { name: "Round A", sheetUrls: ["u1", "u2"] } },
      "https://dash.test",
    );
    expect(postMessage).toHaveBeenNthCalledWith(
      3,
      { source: "puzzbot-dashboard", action: "closeSolved", payload: {} },
      "https://dash.test",
    );
  });
});
