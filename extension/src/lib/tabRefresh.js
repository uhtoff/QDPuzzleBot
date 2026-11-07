import { parseSheetUrl } from "./sheetUrl.js";
import { cacheValid, shouldDeliver, loadingSnapshot } from "./panelState.js";

// Called synchronously when a tab reports a URL, before any await. If the URL is a
// sheet other than the one the registry entry (or a recorded failure) is for, the
// old entry is dropped, every in-flight lookup for the tab is invalidated, and a
// "loading" snapshot goes to the panels (no reply target) before the new lookup starts.
// `failedFor` remembers the sheet a failure was recorded for, so a retry of the same
// failing URL does not flash "loading".
export function invalidateOnNavigate({ tab, registry, failures, failedFor, lookupGen, ports, post }) {
  const parsed = parseSheetUrl(tab.url ?? "");
  if (!parsed || cacheValid(registry.get(tab.id), parsed) || cacheValid(failedFor.get(tab.id), parsed)) return false;
  lookupGen.begin(tab.id); // a pending lookup for the old sheet can no longer register it
  registry.delete(tab.id);
  failures.delete(tab.id);
  failedFor.delete(tab.id);
  const snap = loadingSnapshot(tab.id);
  for (const [p, windowId] of ports) if (shouldDeliver(windowId, tab)) post(p, snap);
  return true;
}
