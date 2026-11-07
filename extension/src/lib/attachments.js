// Map a server attachment (bot/web/data.py: {id, filename, content_type, size, url})
// to a display line. href is set only for a downloaded attachment whose url is a
// dashboard attachment path, joined to the configured origin.
const PREFIX = "/api/attachments/";

export function attachmentView(att, origin) {
  const text = typeof att?.filename === "string" && att.filename ? att.filename : "attachment";
  const url = att?.url;
  if (typeof url === "string" && url.startsWith(PREFIX) && origin) return { text, href: origin + url };
  return { text, href: null };
}
