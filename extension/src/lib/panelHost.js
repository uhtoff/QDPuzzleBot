// Where the chat panel lives: Chrome's side panel or Firefox's sidebar. The browser
// APIs are passed in so both paths can be tested.
//
// Firefox only lets an extension open its sidebar inside a user action (toolbar click,
// menu, shortcut), not from a dashboard button or a notification click, so open() does
// nothing there; the toolbar button toggles the sidebar instead.

const swallow = (fn) => {
  try {
    const r = fn();
    if (r && typeof r.catch === "function") r.catch(() => {});
  } catch {
    /* the panel is best-effort */
  }
};

export function createPanelHost({ sidePanel, sidebarAction, action, path }) {
  if (sidePanel) {
    return {
      setup() {
        swallow(() => sidePanel.setPanelBehavior({ openPanelOnActionClick: true }));
        swallow(() => sidePanel.setOptions({ path, enabled: true }));
      },
      open(windowId) {
        swallow(() => sidePanel.open({ windowId }));
      },
    };
  }
  if (sidebarAction && action) {
    let registered = false;
    return {
      // Idempotent: setup() runs at startup and again on install/update, and two
      // listeners would toggle the sidebar open and shut on every click.
      setup() {
        if (registered) return;
        registered = true;
        // No await before toggle(): it must run inside the click's user action.
        action.onClicked.addListener(() => {
          swallow(() => sidebarAction.toggle());
        });
      },
      open() {},
    };
  }
  return { setup() {}, open() {} };
}
