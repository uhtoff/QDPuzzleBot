import { localToTs, tsToLocal } from "../util";

export interface SetupForm {
  hubId: string | null;
  roleId: string | null;
  huntName: string;
  huntUrl: string;
  startLocal: string;
  startOffsetMinutes: number;
  username: string;
  password: string;
  createSolvedSheet: boolean;
  filesMode: boolean;
  sendInvites: boolean;
  multiTeam: boolean;
  /** Hub category for a new multi-team hunt's channels; blank = none. */
  hubCategory: string;
}

export interface SetupPayload {
  hub_id: string;
  roster_role_id: string;
  hunt_name: string;
  hunt_url: string;
  start_timestamp?: number;
  username?: string;
  password?: string;
  create_solved_sheet?: boolean;
  sheet_mode?: "files";
  send_invites: boolean;
  multi_team?: true;
  hub_category?: string;
}

export const emptySetupForm = (offsetMinutes: number): SetupForm => ({
  hubId: null,
  roleId: null,
  huntName: "",
  huntUrl: "",
  startLocal: "",
  startOffsetMinutes: offsetMinutes,
  username: "",
  password: "",
  createSolvedSheet: false,
  filesMode: false,
  sendInvites: false,
  multiTeam: false,
  hubCategory: "general",
});

/** The POST body: strings trimmed, empty optionals left out, the start time
 *  converted from wall-clock-in-offset to a UTC timestamp, invites off unless set. */
export function buildPayload(form: SetupForm): SetupPayload {
  const payload: SetupPayload = {
    hub_id: form.hubId ?? "",
    roster_role_id: form.roleId ?? "",
    hunt_name: form.huntName.trim(),
    hunt_url: form.huntUrl.trim(),
    send_invites: form.sendInvites === true,
  };
  const ts = form.startLocal ? localToTs(form.startLocal, form.startOffsetMinutes) : null;
  if (ts != null) payload.start_timestamp = ts;
  const username = form.username.trim();
  if (username) payload.username = username;
  const password = form.password.trim();
  if (password) payload.password = password;
  // A files hunt has no solved spreadsheet, so the two never go out together.
  if (form.filesMode) payload.sheet_mode = "files";
  else if (form.createSolvedSheet) payload.create_solved_sheet = true;
  if (form.multiTeam) {
    payload.multi_team = true;
    payload.hub_category = form.hubCategory.trim();
  }
  return payload;
}

/** Whether the Set up button is enabled. */
export function canSubmit(form: SetupForm, templateProblems: string[]): boolean {
  if (templateProblems.length > 0) return false;
  return !!(form.hubId && form.roleId && form.huntName.trim() && form.huntUrl.trim());
}

export interface RunningMultiTeam {
  id: number;
  name: string;
  team_count: number;
  /** The first team's hunt website ("" if none) and start, to fill in for a joiner. */
  url: string;
  start_timestamp: number | null;
}

/** Picking a running multi-team hunt fills in its name, website and start time
 *  (shown in the form's selected offset); the login stays as typed. A blank
 *  website or start on the running hunt leaves the form's value alone. */
export function pickRunningHunt(form: SetupForm, m: RunningMultiTeam): SetupForm {
  const startLocal = tsToLocal(m.start_timestamp, form.startOffsetMinutes);
  return {
    ...form,
    huntName: m.name,
    huntUrl: m.url || form.huntUrl,
    startLocal: startLocal || form.startLocal,
  };
}

/** The running multi-team hunt this hunt name would join (the server matches
 *  on trimmed, case-insensitive name), or null if it would start a new one. */
export function multiTeamMatch(
  huntName: string,
  running: RunningMultiTeam[],
): RunningMultiTeam | null {
  const wanted = huntName.trim().toLowerCase();
  if (!wanted) return null;
  return running.find((m) => m.name.trim().toLowerCase() === wanted) ?? null;
}
