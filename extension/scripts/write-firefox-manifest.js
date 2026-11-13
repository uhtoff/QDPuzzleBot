// Usage: node scripts/write-firefox-manifest.js <output path>
import { readFileSync, writeFileSync } from "node:fs";
import { firefoxManifest } from "./firefoxManifest.js";

const out = process.argv[2];
if (!out) {
  console.error("usage: node scripts/write-firefox-manifest.js <output path>");
  process.exit(2);
}
const chrome = JSON.parse(readFileSync(new URL("../manifest.json", import.meta.url), "utf8"));
writeFileSync(out, JSON.stringify(firefoxManifest(chrome), null, 2) + "\n");
