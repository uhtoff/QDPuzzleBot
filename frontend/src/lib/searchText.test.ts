// The board search is a filter: only letters and digits take part in the match,
// so a straight apostrophe typed on a keyboard finds a name stored with a curly
// one (iOS smart punctuation, titles pasted from hunt sites). Each case pairs a
// query with a haystack whose punctuation, spacing, case or accents differ.
import { describe, expect, it } from "vitest";
import { foldForSearch, matchesSearch } from "./searchText";

describe("matchesSearch", () => {
  it.each([
    ["world's best", "World’s Best Crossword"], // straight vs curly apostrophe
    ["world’s best", "World's Best Crossword"], // curly vs straight
    ["worlds best", "World’s Best Crossword"], // apostrophe left out
    ['"quoted"', "The “Quoted” One"], // straight vs curly double quotes
    ["crossword-ish", "Crossword–ish"], // hyphen vs en dash
    ["best crossword", "Best  Crossword."], // double space, trailing punctuation
    ["cafe", "Café Society"], // accent folded
    ["CAFÉ", "cafe society"], // case and accent the other way
    ["2nd", "The 2nd Round"], // digits kept
  ])("%j finds %j", (query, hay) => {
    expect(matchesSearch(hay, query)).toBe(true);
  });

  it.each([
    ["world's worst", "World’s Best Crossword"],
    ["cafes", "Café Life"], // folds to "cafelife": no "cafes" (spaces go, so "Café Society" WOULD match)
    ["3rd", "The 2nd Round"],
  ])("%j does not find %j", (query, hay) => {
    expect(matchesSearch(hay, query)).toBe(false);
  });

  it("treats a query with no letters or digits as no filter", () => {
    expect(matchesSearch("Anything", "?!")).toBe(true);
    expect(matchesSearch("Anything", "   ")).toBe(true);
  });

  it("keeps non-Latin letters rather than discarding them", () => {
    expect(foldForSearch("Ωmega Δelta")).toBe("ωmegaδelta");
    expect(matchesSearch("Ωmega Δelta", "Δelta")).toBe(true);
    expect(matchesSearch("Ωmega Δelta", "Σelta")).toBe(false);
  });
});
