/** Public facts about this instance, from /api/auth/site (config `web.site_name`,
 *  `web.test_instance`, `web.contact`). Shown on the logged-out landing page so it
 *  explains itself instead of being a bare "Log in with Discord" card. */
export interface SiteInfo {
  name: string;
  test_instance: boolean;
  contact: string;
}

/** Used when /api/auth/site can't be reached: a neutral, non-test description. */
export const DEFAULT_SITE: SiteInfo = { name: "Puzzbot", test_instance: false, contact: "" };

/** The browser-tab title for the landing page. */
export function pageTitle(site: SiteInfo): string {
  const name = site.name.trim() || DEFAULT_SITE.name;
  return `${name}${site.test_instance ? " (test)" : ""} — puzzle-hunt team dashboard`;
}
