import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { channelClashOf } from "./ChannelClashPrompt";

describe("channelClashOf", () => {
  it("reads the taken and suggested channel names from a 409 clash", () => {
    const err = new ApiError("msg", "channel_name_clash", 409,
      { error: "channel_name_clash", channel: "general", suggested: "general-1" });
    expect(channelClashOf(err)).toEqual({ channel: "general", suggested: "general-1" });
  });

  it("is null for any other error, or a clash missing its names", () => {
    expect(channelClashOf(new ApiError("m", "duplicate_name", 409, {}))).toBeNull();
    expect(channelClashOf(new ApiError("m", "channel_name_clash", 409, { channel: "x" }))).toBeNull();
    expect(channelClashOf(new Error("boom"))).toBeNull();
  });
});
