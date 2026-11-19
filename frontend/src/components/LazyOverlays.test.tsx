import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { AddToGroupPicker } from "./AddToGroupPicker";
import { PriorityCell, StatusCell, TypeCell } from "./PuzzleEditCells";
import { PuzzleLinks } from "./PuzzleLinks";
import type { Puzzle } from "../types";

const wrap = (el: React.ReactElement) =>
  renderToStaticMarkup(<MantineProvider>{el}</MantineProvider>).replace(/<style[^>]*>.*?<\/style>/g, "");

const SAMPLE_PUZZLE: Puzzle = {
  id: 1,
  name: "Test Puzzle",
  url: "https://example.invalid/p/1",
  channel_id: "12345",
  discord_url: "https://discord.com/channels/1/12345",
  discord_deep_url: "discord://-/channels/1/12345",
  sheet_url: "https://docs.google.com/spreadsheets/d/abc/edit",
  is_meta: false,
  meta_kind: null,
  is_minimeta: false,
  status: "in progress",
  solved: false,
  on_solved_sheet: false,
  solution: null,
  priority: "high",
  puzzle_type: "Wordplay",
  notes: [],
  start_time: null,
  solve_time: null,
  last_activity: null,
  activity: null,
  rounds: [],
  sheets: [],
  mini_puzzles: [],
  voice_channel_id: null,
  voice_url: null,
  voice_count: 0,
};

describe("lazy row/card overlays", () => {
  it("AddToGroupPicker renders nothing when closed", () => {
    const html = wrap(
      <AddToGroupPicker
        opened={false}
        onClose={() => {}}
        puzzleId={1}
        items={[{ code: "A", name: "Round A" }]}
        ownTargetCode={null}
        selfFeeds={false}
        selfInforms={false}
        onRetag={() => {}}
      />,
    );
    expect(html).toBe("");
  });

  it("StatusCell, PriorityCell, and TypeCell do not mount menus/popovers while closed", () => {
    const statusHtml = wrap(<StatusCell value="in progress" solved={false} onChange={() => {}} />);
    expect(statusHtml).toContain("In Progress");
    expect(statusHtml).not.toContain("aria-haspopup");

    const prioHtml = wrap(<PriorityCell value="high" onChange={() => {}} />);
    expect(prioHtml).toContain("High");
    expect(prioHtml).not.toContain("aria-haspopup");

    const typeHtml = wrap(<TypeCell value="Wordplay" onSave={() => {}} />);
    expect(typeHtml).toContain("Wordplay");
    expect(typeHtml).not.toContain("aria-haspopup");
  });

  it("StatusCell, PriorityCell, and TypeCell mount their Menu/Popover when opened", () => {
    const statusOpen = wrap(<StatusCell value="in progress" solved={false} onChange={() => {}} defaultOpened />);
    expect(statusOpen).toContain('aria-haspopup="menu"');
    expect(statusOpen).toContain('aria-expanded="true"');

    const prioOpen = wrap(<PriorityCell value="high" onChange={() => {}} defaultOpened />);
    expect(prioOpen).toContain('aria-haspopup="menu"');
    expect(prioOpen).toContain('aria-expanded="true"');

    const typeOpen = wrap(<TypeCell value="Wordplay" onSave={() => {}} defaultOpened />);
    expect(typeOpen).toContain('aria-haspopup="dialog"');
    expect(typeOpen).toContain('aria-expanded="true"');
  });

  it("PuzzleLinks renders native titles without mounting ChatViewer or Floating-UI tooltips", () => {
    const html = wrap(<PuzzleLinks puzzle={SAMPLE_PUZZLE} onOpenChat={() => {}} />);
    expect(html).toContain('title="Spreadsheet"');
    expect(html).toContain('title="Chat"');
    expect(html).toContain('title="Puzzle page"');
    expect(html).not.toContain("mantine-Modal");
  });
});
