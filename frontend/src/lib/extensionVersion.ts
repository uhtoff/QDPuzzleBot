import { extensionPresent } from "./extension";

// "Your Puzzle Chat copy is behind": compared with public/extension/versions.json,
// which names the newest RELEASED version and what each version adds. The same
// rule as the extension's own src/lib/updateStatus.js (a separate package).

/** Copies up to and including this one publish no version (data-puzzbot-version
 *  arrived in 1.2.1), so a copy that publishes none is taken as this. */
export const IMPLIED_VERSION = "1.2.0";

export interface VersionsInfo {
  latest: string;
  versions?: { version: string; adds: string }[];
}

export interface UpdateNotice {
  latest: string;
  adds: { version: string; adds: string }[];
}

const VERSION = /^\d+(\.\d+)*$/;

export function compareVersions(a: string, b: string): number {
  const pa = a.split(".").map(Number);
  const pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d > 0 ? 1 : -1;
  }
  return 0;
}

/** The installed copy's version, or null when the extension isn't installed. */
export function installedExtensionVersion(): string | null {
  if (!extensionPresent()) return null;
  const v = document.documentElement.dataset.puzzbotVersion;
  return v && VERSION.test(v) ? v : IMPLIED_VERSION;
}

export function extensionUpdateNotice(
  installed: string | null,
  info: VersionsInfo | null | undefined,
  dismissed: string | null,
): UpdateNotice | null {
  const latest = info?.latest;
  if (!installed || typeof latest !== "string" || !VERSION.test(latest)) return null;
  if (compareVersions(installed, latest) >= 0 || dismissed === latest) return null;
  const adds = (info?.versions ?? [])
    .filter((v) => VERSION.test(v.version) && typeof v.adds === "string")
    .filter((v) => compareVersions(v.version, installed) > 0 && compareVersions(v.version, latest) <= 0)
    .sort((a, b) => compareVersions(b.version, a.version))
    .map(({ version, adds }) => ({ version, adds }));
  return { latest, adds };
}
