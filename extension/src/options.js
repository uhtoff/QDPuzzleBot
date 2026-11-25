import { DEFAULT_ORIGIN, SHEETS_ORIGINS, parseOrigin } from "./lib/origin.js";
import { notifyPrefs } from "./lib/notify.js";

// Live: https://quarantinedecrypters.com (default). Test instance: https://puzzbot.com
const input = document.getElementById("origin");
const status = document.getElementById("status");

chrome.storage.sync.get({ dashboardOrigin: DEFAULT_ORIGIN }).then(({ dashboardOrigin }) => {
  input.value = dashboardOrigin;
});

// The saved origin syncs across installs but its host permission does not, so a fresh
// install can show the right origin with no access to it. Say so; Save grants it.
const access = document.getElementById("access");
const checkAccess = async () => {
  const { dashboardOrigin } = await chrome.storage.sync.get({ dashboardOrigin: DEFAULT_ORIGIN });
  const origin = parseOrigin(dashboardOrigin);
  const ok = origin ? await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => true) : true;
  access.textContent = ok ? "" : "Puzzle Chat does not have access to " + origin + " yet. Click Save to allow it.";
  access.hidden = ok;
};
checkAccess();
chrome.permissions.onAdded.addListener(checkAccess);
chrome.permissions.onRemoved.addListener(checkAccess);

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
  checkAccess();
});

// Two switches, one optional permission: requested when either goes on, dropped when
// both are off.
const switches = { notifySolves: document.getElementById("notify-solves"), notifyMessages: document.getElementById("notify-messages") };
chrome.storage.sync.get(["notify", "notifyMessages", "notifySolves"]).then(async (stored) => {
  const prefs = notifyPrefs(stored);
  // A stored "on" without the permission (revoked in the browser) must not look enabled.
  const ok = await chrome.permissions.contains({ permissions: ["notifications"] }).catch(() => false);
  switches.notifySolves.checked = ok && prefs.solves;
  switches.notifyMessages.checked = ok && prefs.messages;
});
for (const box of Object.values(switches)) {
  box.addEventListener("change", async () => {
    status.textContent = "";
    if (box.checked) {
      // Optional permission: must be requested first, inside the user gesture.
      const granted = await chrome.permissions.request({ permissions: ["notifications"] }).catch(() => false);
      if (!granted) {
        box.checked = false;
        status.textContent = "Notification permission denied.";
        return;
      }
    }
    // Both written, so the old single switch no longer decides either.
    await chrome.storage.sync.set({ notifySolves: switches.notifySolves.checked, notifyMessages: switches.notifyMessages.checked });
    if (!switches.notifySolves.checked && !switches.notifyMessages.checked) {
      await chrome.permissions.remove({ permissions: ["notifications"] }).catch(() => {});
    }
  });
}

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
