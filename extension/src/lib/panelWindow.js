// Which window should the side panel open in for "Work on this puzzle"?
// `entry` is the registry entry of the already-open files-mode tab for the
// payload's sheet (or null/undefined); otherwise the sender's window.
export function panelWindow(entry, senderWindowId) {
  return typeof entry?.windowId === "number" ? entry.windowId : senderWindowId;
}
