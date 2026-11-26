// "Your copy is behind": the dashboard serves versions.json (frontend/public/extension/)
// naming the newest RELEASED version and what each version adds. Bumping it is a
// release step, so nobody is told to update before the stores have the version.

const VERSION = /^\d+(\.\d+)*$/;

/** -1, 0 or 1, comparing dotted numbers part by part ("1.2" equals "1.2.0"). */
export function compareVersions(a, b) {
  const pa = String(a).split(".").map(Number);
  const pb = String(b).split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d > 0 ? 1 : -1;
  }
  return 0;
}

/** {latest, adds: [{version, adds}] newest first} when `installed` is behind and
 *  this release has not been dismissed; else null. */
export function updateNotice(installed, info, dismissed) {
  const latest = info?.latest;
  if (typeof latest !== "string" || !VERSION.test(latest)) return null;
  if (compareVersions(installed, latest) >= 0 || dismissed === latest) return null;
  const adds = (Array.isArray(info.versions) ? info.versions : [])
    .filter((v) => typeof v?.version === "string" && VERSION.test(v.version) && typeof v.adds === "string")
    .filter((v) => compareVersions(v.version, installed) > 0 && compareVersions(v.version, latest) <= 0)
    .sort((a, b) => compareVersions(b.version, a.version))
    .map(({ version, adds }) => ({ version, adds }));
  return { latest, adds };
}

/** How to update now, for the browser this copy runs in. */
export const updateHowTo = (firefox) =>
  firefox
    ? "Firefox updates add-ons about once a day. To update now: about:addons → ⚙ → Check for Updates."
    : "Chrome updates extensions within a few hours. To update now: chrome://extensions → Developer mode → Update.";
