/** Fold text for the board search: only letters and digits take part, so
 *  punctuation, spacing, case and accents never stop a match (a straight
 *  apostrophe finds a curly one; "cafe" finds "Café"). Letters in any script
 *  are kept, not just A–Z. */
export function foldForSearch(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/\p{M}/gu, "") // accents split off by NFKD
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]/gu, "");
}

/** Does `haystack` contain `query`, comparing letters and digits only? A query
 *  with no letters or digits (e.g. "?") filters nothing. */
export function matchesSearch(haystack: string, query: string): boolean {
  const q = foldForSearch(query);
  return !q || foldForSearch(haystack).includes(q);
}
