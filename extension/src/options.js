import { DEFAULT_ORIGIN, SHEETS_ORIGINS, parseOrigin } from "./lib/origin.js";

// Live: https://quarantinedecrypters.com (default). Test instance: https://puzzbot.com
const input = document.getElementById("origin");
const status = document.getElementById("status");

chrome.storage.sync.get({ dashboardOrigin: DEFAULT_ORIGIN }).then(({ dashboardOrigin }) => {
  input.value = dashboardOrigin;
});

document.getElementById("save").addEventListener("click", async () => {
  const origin = parseOrigin(input.value);
  if (!origin) {
    status.textContent = "Enter an https origin with no path, e.g. " + DEFAULT_ORIGIN;
    return;
  }
  // Must be the first await so the click gesture is still valid.
  const granted = await chrome.permissions.request({ origins: [origin + "/*"] });
  if (!granted) {
    status.textContent = "Permission denied; origin not saved.";
    return;
  }
  const { dashboardOrigin: previous } = await chrome.storage.sync.get({ dashboardOrigin: DEFAULT_ORIGIN });
  await chrome.storage.sync.set({ dashboardOrigin: origin });
  // Drop the old dashboard's host permission now the new one is granted and saved.
  const old = parseOrigin(previous);
  if (old && old !== origin) await chrome.permissions.remove({ origins: [old + "/*"] }).catch(() => {});
  input.value = origin;
  status.textContent = "Saved.";
});

const notify = document.getElementById("notify");
chrome.storage.sync.get({ notify: false }).then((v) => {
  notify.checked = v.notify;
  // A stored "on" without the permission (revoked in chrome://extensions) must not look enabled.
  if (v.notify) {
    chrome.permissions.contains({ permissions: ["notifications"] }).then((ok) => {
      if (!ok) notify.checked = false;
    });
  }
});
notify.addEventListener("change", async () => {
  status.textContent = "";
  if (notify.checked) {
    // Optional permission: must be requested first, inside the user gesture.
    const granted = await chrome.permissions.request({ permissions: ["notifications"] }).catch(() => false);
    if (!granted) {
      notify.checked = false;
      status.textContent = "Notification permission denied.";
      return;
    }
    await chrome.storage.sync.set({ notify: true });
  } else {
    await chrome.storage.sync.set({ notify: false });
    await chrome.permissions.remove({ permissions: ["notifications"] }).catch(() => {});
  }
});

// Firefox lets the user revoke the Sheets permission; without it every tab is "not a puzzle".
const sheets = document.getElementById("sheets");
const checkSheets = () =>
  chrome.permissions
    .contains({ origins: SHEETS_ORIGINS })
    .then((ok) => {
      sheets.hidden = ok;
    })
    .catch(() => {});
checkSheets();
chrome.permissions.onAdded.addListener(checkSheets);
chrome.permissions.onRemoved.addListener(checkSheets);
document.getElementById("sheets-grant").addEventListener("click", async () => {
  // First await, so the click still counts as the user action Firefox requires.
  const granted = await chrome.permissions.request({ origins: SHEETS_ORIGINS }).catch(() => false);
  status.textContent = granted ? "Google Sheets access allowed." : "Google Sheets access was not allowed.";
  checkSheets();
});
