import { describe, it, expect } from "vitest";
import {
  PER_PAGE,
  SOLVED_GRACE_MS,
  isListed,
  nextGraceEnd,
  listRows,
  paginate,
  totals,
  listHuntName,
  applyMessage,
  replayMessages,
  clearChannel,
  clearAll,
  badgeCount,
  badgeText,
  rowText,
  rowTime,
  rowMessage,
  rowBadge,
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
    const now = Date.parse("2026-11-24T15:01:00+00:00");
    const solvedAt = "2026-11-24T15:00:00+00:00";
    expect(isListed(ch({ unread: 2 }), now)).toBe(true);
    expect(isListed(ch({ unread: 0 }), now)).toBe(false);
    expect(isListed(ch({ solved: true, unread: 3 }), now)).toBe(false);
    expect(isListed(ch({ solved: true, solved_at: solvedAt, newly_solved: true, unread: 0 }), now)).toBe(true);
  });
});

describe("solved grace window", () => {
  const solvedAt = "2026-11-24T15:00:00+00:00";
  const at = (min) => Date.parse(solvedAt) + min * 60000;

  it("keeps a solved chat's unread listed for ten minutes after the solve", () => {
    expect(SOLVED_GRACE_MS).toBe(10 * 60000);
    const c = ch({ solved: true, solved_at: solvedAt, unread: 2 });
    expect(isListed(c, at(0))).toBe(true);
    expect(isListed(c, at(9.9))).toBe(true);
    expect(isListed(c, at(10))).toBe(false);
    expect(isListed(c, at(30))).toBe(false);
  });

  it("a Solved row also clears ten minutes after the solve, seen or not", () => {
    const c = ch({ solved: true, solved_at: solvedAt, newly_solved: true, unread: 0 });
    expect(isListed(c, at(9.9))).toBe(true);
    expect(isListed(c, at(10))).toBe(false);
    expect(isListed({ ...c, unread: 4 }, at(10))).toBe(false);
  });

  it("needs unread messages, and a known solve time", () => {
    expect(isListed(ch({ solved: true, solved_at: solvedAt, unread: 0 }), at(1))).toBe(false);
    expect(isListed(ch({ solved: true, solved_at: null, unread: 2 }), at(1))).toBe(false);
    expect(isListed(ch({ solved: true, solved_at: "not a time", unread: 2 }), at(1))).toBe(false);
  });

  it("listRows applies the window at the time it is given", () => {
    const chans = [
      ch({ channel_id: "a", solved: true, solved_at: solvedAt, unread: 1, last: last("5") }),
      ch({ channel_id: "b", unread: 1, last: last("4") }),
    ];
    expect(listRows(chans, null, at(5)).map((r) => r.channel_id)).toEqual(["a", "b"]);
    expect(listRows(chans, null, at(11)).map((r) => r.channel_id)).toEqual(["b"]);
  });

  it("nextGraceEnd is the soonest end among chats listed only by the window", () => {
    const later = "2026-11-24T15:04:00+00:00";
    const chans = [
      ch({ channel_id: "a", solved: true, solved_at: later, unread: 1 }),
      ch({ channel_id: "b", solved: true, solved_at: solvedAt, unread: 1 }),
      ch({ channel_id: "c", solved: true, solved_at: "2026-11-24T14:00:00+00:00", unread: 1 }), // already over
      ch({ channel_id: "d", solved: true, solved_at: solvedAt, unread: 0 }), // nothing to show
      ch({ channel_id: "e", unread: 3 }),
    ];
    expect(nextGraceEnd(chans, at(1))).toBe(at(10));
    // A Solved row with nothing unread is timed too.
    const solvedRow = ch({ channel_id: "f", solved: true, solved_at: "2026-11-24T14:58:00+00:00", newly_solved: true, unread: 0 });
    expect(nextGraceEnd([...chans, solvedRow], at(1))).toBe(at(8));
    expect(nextGraceEnd(chans, at(10))).toBe(Date.parse(later) + SOLVED_GRACE_MS);
    expect(nextGraceEnd(chans, at(20))).toBeNull();
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
        ch({ channel_id: "s", solved: true, newly_solved: true, solution: "X", last: last("5"), solved_seq: "40",
             solved_at: "2026-11-24T15:00:00+00:00" }),
        ch({ channel_id: "b", unread: 1, last: last("20") }),
      ],
      null,
      Date.parse("2026-11-24T15:01:00+00:00"), // inside the solved chat's window
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
  it("names the solver in brackets when known", () => {
    expect(rowText(ch({ solved: true, newly_solved: true, solution: "MINE", solved_by: "Uhtoff" }))).toBe("Solved: MINE (Uhtoff)");
    expect(rowText(ch({ solved: true, newly_solved: true, solution: null, solved_by: "Uhtoff" }))).toBe("Solved (Uhtoff)");
    expect(rowText(ch({ solved: true, newly_solved: true, solution: "MINE", solved_by: null }))).toBe("Solved: MINE");
  });
  it("otherwise author and message", () => {
    expect(rowText(ch({ unread: 1, last: last("5") }))).toBe("Mina: grid refs are OS");
  });
  it("nothing to show", () => {
    expect(rowText(ch({ unread: 1, last: null }))).toBe("");
  });
});

describe("a Solved row with new messages", () => {
  const solved = (over) => ch({ solved: true, newly_solved: true, solution: "SOLVED", solved_by: "Uhtoff",
                                solved_at: "2026-11-24T15:30:00+00:00", ...over });

  it("keeps the Solved line and adds the latest message as its own line", () => {
    const c = solved({ unread: 1, last: last("9", "Beta Test", "🥳") });
    expect(rowText(c)).toBe("Solved: SOLVED (Uhtoff)");
    expect(rowMessage(c)).toBe("Beta Test: 🥳");
  });

  it("has no message line with nothing unread, or on any other row", () => {
    expect(rowMessage(solved({ unread: 0, last: last("9", "Beta Test", "🥳") }))).toBe("");
    expect(rowMessage(ch({ unread: 2, last: last("9") }))).toBe(""); // its line 2 already is the message
  });

  it("badge: the unread count once there is one, else the tick", () => {
    expect(rowBadge(solved({ unread: 0 }))).toBe("✓");
    expect(rowBadge(solved({ unread: 3 }))).toBe("3");
    expect(rowBadge(solved({ unread: 120 }))).toBe("99+");
    expect(rowBadge(ch({ unread: 7 }))).toBe("7");
  });
});

describe("rowTime", () => {
  it("a Solved row is timed from the solve, not its last message", () => {
    const c = ch({ solved: true, newly_solved: true, solved_at: "2026-11-24T15:30:00+00:00", last: last("5") });
    expect(rowTime(c)).toBe("2026-11-24T15:30:00+00:00");
    // a message after the solve moves the row up, so its time follows the message
    expect(rowTime({ ...c, unread: 1 })).toBe("2026-11-24T14:10:00+00:00");
  });
  it("any other row is timed from its last message", () => {
    expect(rowTime(ch({ unread: 1, last: last("5") }))).toBe("2026-11-24T14:10:00+00:00");
    // late chatter after a solve you have seen: the message, not the solve
    expect(rowTime(ch({ solved: true, solved_at: "2026-11-24T13:00:00+00:00", unread: 1, last: last("5") })))
      .toBe("2026-11-24T14:10:00+00:00");
    expect(rowTime(ch({ unread: 1, last: null }))).toBeNull();
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

describe("listHuntName", () => {
  it("names the list's hunt on a tab outside any hunt", () => {
    expect(listHuntName(undefined, "H1", "Saltmarsh")).toBe("Saltmarsh");
  });
  it("names it on a tab from another hunt (a peek)", () => {
    expect(listHuntName("H2", "H1", "Saltmarsh")).toBe("Saltmarsh");
  });
  it("leaves it out on the hunt's own tab", () => {
    expect(listHuntName("H1", "H1", "Saltmarsh")).toBeNull();
  });
  it("is null when the server sent no name (an older dashboard)", () => {
    expect(listHuntName(undefined, "H1", undefined)).toBeNull();
  });
});

describe("replayMessages", () => {
  // A refetch replaces the cache with the server's counts. A message that arrived
  // live after the server counted, but before its answer, must not be lost.
  const server = () => [ch({ channel_id: "900", unread: 1, last: last("100") }), ch({ channel_id: "901", unread: 0, last: last("200") })];
  const opts = { visible: [], markers: null, selfId: null };

  it("adds a message the server had not counted yet", () => {
    const out = replayMessages(server(), [msgEv("900", { id: "150" })], opts);
    expect(out.find((c) => c.channel_id === "900").unread).toBe(2);
    expect(out.find((c) => c.channel_id === "900").last.id).toBe("150");
  });
  it("skips a message the server already counted", () => {
    const out = replayMessages(server(), [msgEv("900", { id: "100" }), msgEv("901", { id: "180" })], opts);
    expect(out.find((c) => c.channel_id === "900").unread).toBe(1);
    expect(out.find((c) => c.channel_id === "901").unread).toBe(0);
  });
  it("replays several, in order, across chats", () => {
    const out = replayMessages(server(), [msgEv("900", { id: "150" }), msgEv("901", { id: "250" }), msgEv("900", { id: "160" })], opts);
    expect(out.find((c) => c.channel_id === "900").unread).toBe(3);
    expect(out.find((c) => c.channel_id === "900").last.id).toBe("160");
    expect(out.find((c) => c.channel_id === "901").unread).toBe(1);
  });
  it("nothing buffered leaves the server's answer as it is", () => {
    const s = server();
    expect(replayMessages(s, [], opts)).toEqual(s);
  });
});
