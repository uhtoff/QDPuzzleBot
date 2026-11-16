import { describe, it, expect, vi } from "vitest";
import { createPanelHost } from "../src/lib/panelHost.js";

const PATH = "src/panel.html";
const tick = () => new Promise((r) => setTimeout(r, 0));

function chromeEnv({ openRejects = false } = {}) {
  return {
    sidePanel: {
      setPanelBehavior: vi.fn(() => Promise.resolve()),
      setOptions: vi.fn(() => Promise.resolve()),
      open: vi.fn(() => (openRejects ? Promise.reject(new Error("no gesture")) : Promise.resolve())),
    },
    action: { onClicked: { addListener: vi.fn() } },
    path: PATH,
  };
}

function firefoxEnv() {
  return {
    sidebarAction: { toggle: vi.fn(() => Promise.resolve()), open: vi.fn(() => Promise.resolve()) },
    action: { onClicked: { addListener: vi.fn() } },
    path: PATH,
  };
}

describe("createPanelHost (Chrome side panel)", () => {
  it("setup sets the action-click behaviour and the panel path", () => {
    const env = chromeEnv();
    createPanelHost(env).setup();
    expect(env.sidePanel.setPanelBehavior).toHaveBeenCalledWith({ openPanelOnActionClick: true });
    expect(env.sidePanel.setOptions).toHaveBeenCalledWith({ path: PATH, enabled: true });
    expect(env.action.onClicked.addListener).not.toHaveBeenCalled();
  });
  it("open targets the window and swallows a rejection", async () => {
    const env = chromeEnv({ openRejects: true });
    const unhandled = vi.fn();
    process.on("unhandledRejection", unhandled);
    createPanelHost(env).open(7);
    await tick();
    process.off("unhandledRejection", unhandled);
    expect(env.sidePanel.open).toHaveBeenCalledWith({ windowId: 7 });
    expect(unhandled).not.toHaveBeenCalled();
  });
});

describe("createPanelHost (Firefox sidebar)", () => {
  it("setup twice registers one toolbar listener that toggles once", () => {
    const env = firefoxEnv();
    const host = createPanelHost(env);
    host.setup();
    host.setup();
    expect(env.action.onClicked.addListener).toHaveBeenCalledTimes(1);
    env.action.onClicked.addListener.mock.calls[0][0]();
    expect(env.sidebarAction.toggle).toHaveBeenCalledTimes(1);
  });
  it("the listener toggles synchronously, before returning", () => {
    const env = firefoxEnv();
    createPanelHost(env).setup();
    const listener = env.action.onClicked.addListener.mock.calls[0][0];
    const ret = listener();
    expect(env.sidebarAction.toggle).toHaveBeenCalledTimes(1);
    expect(ret instanceof Promise).toBe(false);
  });
  it("open is a no-op and never throws", () => {
    const env = firefoxEnv();
    expect(() => createPanelHost(env).open(3)).not.toThrow();
    expect(env.sidebarAction.open).not.toHaveBeenCalled();
    expect(env.sidebarAction.toggle).not.toHaveBeenCalled();
  });
  it("a synchronously throwing toggle does not escape the listener", () => {
    const env = firefoxEnv();
    env.sidebarAction.toggle = vi.fn(() => {
      throw new Error("boom");
    });
    createPanelHost(env).setup();
    expect(() => env.action.onClicked.addListener.mock.calls[0][0]()).not.toThrow();
  });
});

describe("createPanelHost (neither API)", () => {
  it("setup and open are no-ops", () => {
    const host = createPanelHost({ path: PATH });
    expect(() => {
      host.setup();
      host.open(1);
    }).not.toThrow();
  });
});
