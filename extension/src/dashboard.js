// Content script on the dashboard origin: marks the page as extension-equipped
// and relays the SPA's window messages to the background worker.
document.documentElement.dataset.puzzbotExtension = "1";

const ACTIONS = new Set(["work", "openPuzzle", "openPage", "openRound", "closeSolved"]);
// Published so the dashboard shows only the buttons this copy can act on. It is
// the relay gate itself, so it can never list an action that would be dropped.
// (1.0.0 published nothing; the dashboard treats that as its three actions.)
document.documentElement.dataset.puzzbotActions = [...ACTIONS].join(" ");
// So the dashboard can say when this copy is behind (copies up to 1.2.0 publish none).
document.documentElement.dataset.puzzbotVersion = chrome.runtime.getManifest().version;

window.addEventListener("message", (event) => {
  if (event.source !== window || event.origin !== location.origin) return;
  const data = event.data;
  if (!data || data.source !== "puzzbot-dashboard" || !ACTIONS.has(data.action)) return;
  try {
    chrome.runtime.sendMessage({ action: data.action, payload: data.payload }).catch(() => {});
  } catch {
    /* "Extension context invalidated" after an extension update: reload the dashboard tab */
  }
});

// A board link the extension folded in from a duplicate tab: hand its query to the SPA,
// which applies it without reloading. Answers whether the page took it; `false` (a
// dashboard too old to publish data-puzzbot-links) makes the extension navigate instead.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "openBoardLink") return;
  if (typeof msg.search !== "string" || document.documentElement.dataset.puzzbotLinks !== "1") {
    sendResponse(false);
    return;
  }
  window.postMessage({ source: "puzzbot-extension", action: "openBoardLink", search: msg.search }, location.origin);
  sendResponse(true);
});
