// Derive the Firefox manifest from the Chrome one (manifest.json stays the single
// source). Firefox has a sidebar instead of the side panel, runs the background as a
// module event page rather than a service worker, and needs a gecko block.

const GECKO = {
  id: "puzzle-chat@puzzbot.com",
  strict_min_version: "140.0", // ESR; tabs.group (138), tabGroups (139), data_collection_permissions (140)
  // Mirrors the Chrome Store disclosure in store/listing.md ("Data use disclosure").
  data_collection_permissions: {
    required: ["authenticationInfo", "websiteContent", "personalCommunications"],
  },
};

export function firefoxManifest(chromeManifest) {
  const m = structuredClone(chromeManifest);
  delete m.side_panel;
  delete m.minimum_chrome_version;
  delete m.options_page;
  m.permissions = (m.permissions ?? []).filter((p) => p !== "sidePanel");
  m.background = { scripts: [chromeManifest.background.service_worker], type: "module" };
  m.sidebar_action = {
    default_panel: chromeManifest.side_panel.default_path,
    default_title: chromeManifest.name,
    default_icon: structuredClone(chromeManifest.action.default_icon),
    open_at_install: false,
  };
  m.options_ui = { page: chromeManifest.options_page, open_in_tab: true };
  m.browser_specific_settings = { gecko: structuredClone(GECKO) };
  return m;
}
