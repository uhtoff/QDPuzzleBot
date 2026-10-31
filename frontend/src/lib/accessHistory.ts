// Plain-words labels for the team-server access history. The API sends raw
// codes (`action`, `via`); the page shows these sentences instead.

const VIA: Record<string, string> = {
  button: "by the Join/Leave button",
  arrival: "on arrival",
  hub_role_lost: "lost the hub role",
  hub_admin_lost: "lost hub admin",
  self: "left on their own",
};

const ACTION: Record<string, string> = {
  grant: "got the team role",
  revoke: "lost the team role",
  elevate: "got mod",
  demote: "lost mod",
  arrive: "arrived",
  depart: "left",
};

export function viaLabel(via: string, actorName: string | null): string {
  if (via === "hand") return actorName ? `by hand (by ${actorName})` : "by hand";
  return VIA[via] ?? via;
}

export function actionLabel(action: string): string {
  return ACTION[action] ?? action;
}
