import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import type { UseMutationResult } from "@tanstack/react-query";
import { InvitesOffNotice, SetupFormView } from "./ServerSetup";
import type { SetupResult, SetupStatus } from "../types";

const wrap = (node: ReactNode) =>
  renderToStaticMarkup(<MantineProvider>{node}</MantineProvider>);

const NOTICE = "nobody will be DM&#x27;d an invite";

// The "Send invites" switch starts off for every server, and a team set up with
// it off got no invites with only a closing "run !send_invites" line to say so.
describe("InvitesOffNotice", () => {
  it("warns, naming the command and the hunt, when invites are off", () => {
    const html = wrap(<InvitesOffNotice sendInvites={false} huntName="  Puzzle Boat 13 " />);
    expect(html).toContain(NOTICE);
    expect(html).toContain("!send_invites Puzzle Boat 13");
    expect(html).toContain("#bot-commands");
  });

  it("says nothing when invites are on", () => {
    // MantineProvider emits its own <style>, so check the notice is absent.
    const html = wrap(<InvitesOffNotice sendInvites={true} huntName="Puzzle Boat 13" />);
    expect(html).not.toContain(NOTICE);
    expect(html).not.toContain("send_invites");
  });

  it("falls back to a placeholder before a hunt name is typed", () => {
    const html = wrap(<InvitesOffNotice sendInvites={false} huntName="" />);
    expect(html).toContain("!send_invites &lt;hunt name&gt;");
  });
});

describe("SetupFormView", () => {
  it("shows the invites-off warning on a fresh form", () => {
    const status: SetupStatus = {
      template_problems: [],
      already_set_up: null,
      existing: null,
      hunt_uid: null,
      hubs: [],
    };
    const mut = { error: null, isPending: false, mutate: () => {} } as unknown as
      UseMutationResult<SetupResult, Error, unknown>;
    expect(wrap(<SetupFormView status={status} mut={mut} />)).toContain(NOTICE);
  });
});
