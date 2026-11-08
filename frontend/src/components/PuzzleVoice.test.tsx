import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { VoiceBadge } from "./VoiceBadge";
import { PuzzleVoiceControl } from "./PuzzleVoiceControl";

const wrap = (el: React.ReactElement) =>
  renderToStaticMarkup(<MantineProvider>{el}</MantineProvider>).replace(/<style[^>]*>.*?<\/style>/g, "");

describe("VoiceBadge", () => {
  it("marks a puzzle with people in voice: headphones label and the count", () => {
    const html = wrap(<VoiceBadge count={3} />);
    expect(html).toContain("In voice: 3");
    expect(html).toContain(">3<");
  });
  it("renders nothing at 0", () => {
    expect(wrap(<VoiceBadge count={0} />)).toBe("");
  });
});

describe("PuzzleVoiceControl", () => {
  const base = { solved: false, archived: false, pending: false, onCreate: () => {} };
  it("offers Create when there is no voice channel", () => {
    const html = wrap(<PuzzleVoiceControl {...base} voiceUrl={null} count={0} />);
    expect(html).toContain("Create voice channel");
    expect(html).not.toContain("Join voice");
  });
  it("offers Join (new tab) when a voice channel exists, with the live count", () => {
    const html = wrap(<PuzzleVoiceControl {...base} voiceUrl="https://discord.com/channels/1/2" count={2} />);
    expect(html).toContain("Join voice");
    expect(html).toContain('href="https://discord.com/channels/1/2"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain("2 in voice");
    expect(html).not.toContain("Create voice channel");
  });
  it("hides Join's count at 0", () => {
    expect(wrap(<PuzzleVoiceControl {...base} voiceUrl="https://x/y" count={0} />)).not.toMatch(/\d in voice/);
  });
  it("offers nothing to create for a solved or archived puzzle without voice", () => {
    expect(wrap(<PuzzleVoiceControl {...base} solved voiceUrl={null} count={0} />)).toBe("");
    expect(wrap(<PuzzleVoiceControl {...base} archived voiceUrl={null} count={0} />)).toBe("");
  });
  it("still offers Join on a solved puzzle that has voice", () => {
    expect(wrap(<PuzzleVoiceControl {...base} solved voiceUrl="https://x/y" count={0} />)).toContain("Join voice");
  });
  it("shows a loading state while creating", () => {
    const html = wrap(<PuzzleVoiceControl {...base} pending voiceUrl={null} count={0} />);
    expect(html).toContain("data-loading");
  });
});
