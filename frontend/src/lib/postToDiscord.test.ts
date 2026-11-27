import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { buildPostForm, postDestination, postErrorMessage, MAX_POST_NOTE } from "./postToDiscord";

describe("buildPostForm", () => {
  const blob = new Blob(["x"], { type: "image/png" });
  it("sends a puzzle image as puzzle_id with the file", () => {
    const f = buildPostForm(blob, "png", " hi ", { kind: "puzzle_image", puzzleId: 7 });
    expect(f.get("puzzle_id")).toBe("7");
    expect(f.has("evidence_id")).toBe(false);
    expect((f.get("file") as File).name).toBe("markup.png");
    expect(f.get("content")).toBe("hi");
  });
  it("sends evidence as evidence_id, and a jpg name for a jpg", () => {
    const f = buildPostForm(blob, "jpg", "", { kind: "evidence", evidenceId: 3 });
    expect(f.get("evidence_id")).toBe("3");
    expect(f.has("puzzle_id")).toBe(false);
    expect((f.get("file") as File).name).toBe("markup.jpg");
    expect(f.get("content")).toBe("");
  });
});

describe("postDestination", () => {
  it("names the puzzle's channel or the hunt channel", () => {
    expect(postDestination({ kind: "puzzle_image", puzzleId: 1 }, true)).toBe("the puzzle's channel");
    expect(postDestination({ kind: "evidence", evidenceId: 1 }, true)).toBe("the puzzle's channel");
    expect(postDestination({ kind: "evidence", evidenceId: 1 }, false)).toBe("the hunt channel");
  });
});

describe("postErrorMessage", () => {
  const err = (slug: string) => new ApiError("raw", slug, 400);
  it("maps each server code to its own message", () => {
    const codes = ["not_found_or_forbidden", "forbidden", "archived", "send_failed",
      "command", "too_long", "bad_image", "not_on_puzzle", "bad_target"];
    const msgs = codes.map((c) => postErrorMessage(err(c)));
    expect(new Set(msgs).size).toBe(codes.length);
    expect(msgs.every((m) => m !== "raw")).toBe(true);
    expect(postErrorMessage(err("command"))).toContain("!");
    expect(postErrorMessage(err("too_long"))).toContain(String(MAX_POST_NOTE));
  });
  it("falls back to the error's message, then a generic one", () => {
    expect(postErrorMessage(err("mystery"))).toBe("raw");
    expect(postErrorMessage("nope")).toBe("Couldn't post the image.");
  });
  it("explains a snapshot that is too large", () => {
    expect(postErrorMessage({ error: "too_large" })).toContain("too large");
  });
});
