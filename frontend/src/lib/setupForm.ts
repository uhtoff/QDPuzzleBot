import { localToTs } from "../util";

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
  return payload;
}

/** Whether the Set up button is enabled. */
export function canSubmit(form: SetupForm, templateProblems: string[]): boolean {
  if (templateProblems.length > 0) return false;
  return !!(form.hubId && form.roleId && form.huntName.trim() && form.huntUrl.trim());
}
