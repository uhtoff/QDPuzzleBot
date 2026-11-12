import { describe, expect, it } from "vitest";
import { boardViews } from "./boardViews";

const values = (o: Parameters<typeof boardViews>[0]) => boardViews(o).map((v) => v.value);
const width = (o: Parameters<typeof boardViews>[0]) => boardViews(o).reduce((s, v) => s + v.width, 0);

const on = { canvas: true };

describe("boardViews", () => {
  it("shows the shared views to everyone, in order", () => {
    expect(values({ canManage: false, canAdminister: false, ...on })).toEqual(["board", "all", "evidence", "canvas"]);
  });

  it("leaves Canvas out when the hunt has it off, for managers too", () => {
    expect(values({ canManage: false, canAdminister: false, canvas: false })).toEqual(["board", "all", "evidence"]);
    expect(values({ canManage: true, canAdminister: true, canvas: false })).toEqual([
      "board", "all", "evidence", "arrange", "manage",
    ]);
    expect(width({ canManage: true, canAdminister: true, canvas: false })).toBe(570);
  });

  it("adds Arrange and Manage for managers, Manage alone for an admin of an archived hunt", () => {
    expect(values({ canManage: true, canAdminister: false, ...on })).toEqual([
      "board", "all", "evidence", "canvas", "arrange", "manage",
    ]);
    expect(values({ canManage: false, canAdminister: true, ...on })).toEqual(["board", "all", "evidence", "canvas", "manage"]);
  });

  // The previous hard-coded widths: 450 base, +105 Arrange, +105 Manage.
  it("keeps the desktop widths the control was tuned to", () => {
    expect(width({ canManage: false, canAdminister: false, ...on })).toBe(450);
    expect(width({ canManage: false, canAdminister: true, ...on })).toBe(555);
    expect(width({ canManage: true, canAdminister: true, ...on })).toBe(660);
  });
});
