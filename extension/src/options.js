import { DEFAULT_ORIGIN, parseOrigin } from "./lib/origin.js";

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
  await chrome.storage.sync.set({ dashboardOrigin: origin });
  input.value = origin;
  status.textContent = "Saved.";
});
