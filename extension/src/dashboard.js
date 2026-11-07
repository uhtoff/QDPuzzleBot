// Content script on the dashboard origin: marks the page as extension-equipped
// and relays the SPA's window messages to the background worker.
document.documentElement.dataset.puzzbotExtension = "1";

const ACTIONS = new Set(["work", "openRound", "closeSolved"]);

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
