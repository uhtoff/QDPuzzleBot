// A duplicate tab sitting in a tab group (e.g. a restored saved group) keeps
// its place: the existing tab moves into that group where the duplicate is.
// Returns null when no move is needed or possible.
const NONE = -1; // chrome.tabGroups.TAB_GROUP_ID_NONE

export function groupMove(newTab, existingTab) {
  if (!newTab || !existingTab) return null;
  if (newTab.groupId === undefined || newTab.groupId === NONE) return null;
  if (existingTab.groupId === newTab.groupId) return null;
  if (existingTab.pinned) return null;
  return {
    groupId: newTab.groupId,
    windowId: newTab.windowId,
    index: newTab.index,
    crossWindow: existingTab.windowId !== newTab.windowId,
  };
}
