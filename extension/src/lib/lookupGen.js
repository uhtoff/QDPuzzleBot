// Per-tab lookup generations: a newer lookup for the same tab invalidates older
// ones, so a slow earlier response can never overwrite a newer registry entry.
export function createLookupGen() {
  const latest = new Map();
  return {
    // Start a lookup for `tabId`; the returned token is valid until the next begin/drop.
    begin(tabId) {
      const token = (latest.get(tabId) ?? 0) + 1;
      latest.set(tabId, token);
      return token;
    },
    isCurrent: (tabId, token) => latest.get(tabId) === token,
    // Tab closed: any in-flight lookup is now stale.
    drop(tabId) {
      latest.set(tabId, (latest.get(tabId) ?? 0) + 1);
    },
  };
}
