import { describe, it, expect } from "vitest";
import {
  PER_PAGE,
  isListed,
  listRows,
  paginate,
  totals,
  applyMessage,
  clearChannel,
  clearAll,
  badgeCount,
  badgeText,
  rowText,
  peekPuzzle,
} from "../src/lib/unreadList.js";

const last = (id, author_name = "Mina", content = "grid refs are OS") => ({ id, author_name, content, created_at: "2026-11-24T14:10:00+00:00" });

function ch(over = {}) {
  return {
    channel_id: "900",
    kind: "puzzle",
    puzzle_id: 1,
    name: "Tidal Lock",
    rounds: ["Harbour"],
    solved: false,
    solution: null,
    newly_solved: false,
    sheet_url: "https://docs.google.com/spreadsheets/d/x/edit#gid=1",
    discord_url: "https://discord.com/channels/777/900",
    unread: 0,
    last: null,
    ...over,
  };
}

const msgEv = (channel_id, over = {}) => ({
  type: "message",
  channel_id,
  message: { id: "500", author_id: "7", author_name: "Dev", kind: 0, content: "new thing", created_at: "2026-11-24T15:00:00+00:00", ...over },
});

describe("isListed", () => {
  it("lists unsolved chats with unread, and newly solved ones", () => {
    expect(isListed(ch({ unread: 2 }))).toBe(true);
    expect(isListed(ch({ unread: 0 }))).toBe(false);
    expect(isListed(ch({ solved: true, unread: 3 }))).toBe(false);
    expect(isListed(ch({ solved: true, newly_solved: true, unread: 0 }))).toBe(true);
  });
});

describe("listRows", () => {
  it("orders newest activity first, numerically, and skips the viewing chat", () => {
    const rows = listRows(
      [
        ch({ channel_id: "a", unread: 1, last: last("9") }),
        ch({ channel_id: "b", unread: 1, last: last("30") }),
        ch({ channel_id: "c", unread: 1, last: last("10") }),
        ch({ channel_id: "d", unread: 1, last: last("20") }),
        ch({ channel_id: "e", unread: 0, last: last("99") }),
      ],
      "c",
    );
    expect(rows.map((r) => r.channel_id)).toEqual(["b", "d", "a"]);
  });
  it("sorts a newly solved chat by when it was solved", () => {
    const rows = listRows(
      [
        ch({ channel_id: "a", unread: 1, last: last("30") }),
        ch({ channel_id: "s", solved: true, newly_solved: true, solution: "X", last: last("5"), solved_seq: "40" }),
        ch({ channel_id: "b", unread: 1, last: last("20") }),
      ],
      null,
    );
    expect(rows.map((r) => r.channel_id)).toEqual(["s", "a", "b"]);
  });
});

describe("paginate", () => {
  const rows = Array.from({ length: 12 }, (_, i) => ch({ channel_id: String(i) }));
  it("five to a page", () => {
    expect(PER_PAGE).toBe(5);
    const p0 = paginate(rows, 0);
    expect(p0).toMatchObject({ page: 0, pages: 3, from: 1, to: 5, total: 12, pad: 0 });
    expect(p0.slice.map((r) => r.channel_id)).toEqual(["0", "1", "2", "3", "4"]);
  });
  it("pads a short last page so the chat below does not jump", () => {
    const p2 = paginate(rows, 2);
    expect(p2).toMatchObject({ page: 2, from: 11, to: 12, pad: 3 });
    expect(p2.slice.map((r) => r.channel_id)).toEqual(["10", "11"]);
  });
  it("clamps an out-of-range page", () => {
    expect(paginate(rows, 9).page).toBe(2);
    expect(paginate(rows, -1).page).toBe(0);
  });
  it("one page needs no padding", () => {
    expect(paginate(rows.slice(0, 3), 0)).toMatchObject({ pages: 1, pad: 0, from: 1, to: 3 });
  });
});

describe("totals", () => {
  it("counts puzzles, messages (solved rows included) and solves", () => {
    const t = totals([
      ch({ channel_id: "a", unread: 3 }),
      ch({ channel_id: "b", unread: 2 }),
      ch({ channel_id: "s", solved: true, newly_solved: true, unread: 2 }),
    ]);
    expect(t).toEqual({ puzzles: 3, messages: 7, solved: 1 });
  });
});

describe("applyMessage", () => {
  const base = () => [ch({ channel_id: "900", unread: 1, last: last("100") }), ch({ channel_id: "901", unread: 2, last: last("200", "Jon", "stuck") })];

  it("counts a person's message in a hidden chat and replaces its last line", () => {
    const before = base();
    const { channels, known } = applyMessage(before, msgEv("900"), { visible: [], markers: null, selfId: null });
    expect(known).toBe(true);
    const a = channels.find((c) => c.channel_id === "900");
    expect(a.unread).toBe(2);
    expect(a.last).toEqual({ id: "500", author_name: "Dev", content: "new thing", created_at: "2026-11-24T15:00:00+00:00" });
    expect(channels.find((c) => c.channel_id === "901")).toEqual(before[1]);
  });
  it("ignores bot posts and commands", () => {
    for (const kind of [1, 2, 3]) {
      const { channels } = applyMessage(base(), msgEv("900", { kind }), { visible: [], markers: null, selfId: null });
      expect(channels.find((c) => c.channel_id === "900").unread).toBe(1);
      expect(channels.find((c) => c.channel_id === "900").last.id).toBe("100");
    }
  });
  it("a visible chat is read", () => {
    const { channels } = applyMessage(base(), msgEv("900"), { visible: ["900"], markers: null, selfId: null });
    expect(channels.find((c) => c.channel_id === "900").unread).toBe(0);
    expect(channels.find((c) => c.channel_id === "901").unread).toBe(2);
  });
  it("your own message marks the chat read", () => {
    const { channels } = applyMessage(base(), msgEv("901", { author_id: "7" }), { visible: [], markers: null, selfId: "7" });
    expect(channels.find((c) => c.channel_id === "901").unread).toBe(0);
    expect(channels.find((c) => c.channel_id === "900").unread).toBe(1);
  });
  it("an unknown chat is reported, not added", () => {
    const before = base();
    const { channels, known } = applyMessage(before, msgEv("999"), { visible: [], markers: null, selfId: null });
    expect(known).toBe(false);
    expect(channels).toEqual(before);
  });
});

describe("clearing", () => {
  it("clearChannel clears one chat only", () => {
    const out = clearChannel([ch({ channel_id: "a", unread: 3, solved: true, newly_solved: true }), ch({ channel_id: "b", unread: 4 })], "a");
    expect(out.find((c) => c.channel_id === "a")).toMatchObject({ unread: 0, newly_solved: false });
    expect(out.find((c) => c.channel_id === "b").unread).toBe(4);
  });
  it("clearAll clears every chat and every solve", () => {
    const out = clearAll([ch({ channel_id: "a", unread: 3 }), ch({ channel_id: "b", unread: 4, solved: true, newly_solved: true })]);
    expect(out.map((c) => [c.unread, c.newly_solved])).toEqual([[0, false], [0, false]]);
  });
});

describe("badge", () => {
  it("badgeCount sums unread over chats with an open tab", () => {
    const chans = [ch({ channel_id: "a", unread: 2 }), ch({ channel_id: "b", unread: 5 }), ch({ channel_id: "c", unread: 1 })];
    expect(badgeCount(chans, ["a", "c"])).toBe(3);
  });
  it("badgeText is empty at 0, the number, and capped at 99+", () => {
    expect(badgeText(0)).toBe("");
    expect(badgeText(7)).toBe("7");
    expect(badgeText(99)).toBe("99");
    expect(badgeText(100)).toBe("99+");
  });
});

describe("rowText", () => {
  it("a solved row shows the answer, not the last message", () => {
    const t = rowText(ch({ solved: true, newly_solved: true, solution: "PARADIDDLE", last: last("5", "Tom", "rudiments!") }));
    expect(t).toBe("Solved: PARADIDDLE");
    expect(t).not.toContain("Tom");
  });
  it("otherwise author and message", () => {
    expect(rowText(ch({ unread: 1, last: last("5") }))).toBe("Mina: grid refs are OS");
  });
  it("nothing to show", () => {
    expect(rowText(ch({ unread: 1, last: null }))).toBe("");
  });
});

describe("peekPuzzle", () => {
  it("a puzzle chat in the by-sheet lookup's shape", () => {
    const p = peekPuzzle(ch({ channel_id: "900", puzzle_id: 4, name: "Tidal Lock", solved: true, solution: "EBB" }), "H1");
    expect(p).toEqual({
      id: 4,
      name: "Tidal Lock",
      solved: true,
      solution: "EBB",
      channel_id: "900",
      discord_url: "https://discord.com/channels/777/900",
      sheet_url: "https://docs.google.com/spreadsheets/d/x/edit#gid=1",
      hunt_uid: "H1",
      hunt_archived: false,
      sheet_mode: null,
      hunt_chat: false,
      bridge_enabled: true,
    });
  });
  it("the general chat is a hunt chat with no puzzle id", () => {
    const p = peekPuzzle(ch({ kind: "general", puzzle_id: null, channel_id: "800", sheet_url: null }), "H1");
    expect(p).toMatchObject({ id: null, hunt_chat: true, channel_id: "800", hunt_uid: "H1" });
  });
});
