import { createContext, useContext, useState, type ReactNode } from "react";
import type { HuntDetail } from "../types";

export type ViewAs = "admin" | "organiser" | "regular";

interface ViewAsState {
  viewAs: ViewAs;
  setViewAs: (v: ViewAs) => void;
}

const ViewAsCtx = createContext<ViewAsState>({ viewAs: "admin", setViewAs: () => {} });

export function ViewAsProvider({ children }: { children: ReactNode }) {
  // In-memory only: a full page reload resets to "admin" (the safety property).
  const [viewAs, setViewAs] = useState<ViewAs>("admin");
  return <ViewAsCtx.Provider value={{ viewAs, setViewAs }}>{children}</ViewAsCtx.Provider>;
}

export function useViewAs() {
  return useContext(ViewAsCtx);
}

/**
 * Cap the role the UI renders as. Effective role per hunt = min(real role, ceiling).
 *   admin     -> unchanged
 *   organiser -> drop admin, keep organiser (can_manage) if real
 *   regular   -> drop can_administer always; drop can_manage only when the
 *                Organiser gate is actually active for this guild (a real
 *                regular user keeps can_manage when it isn't — see
 *                organiser_gate_active)
 * Every gated site reads detail.hunt.can_manage / can_administer, so overriding
 * those two fields cascades to all of them with no per-component edits.
 */
export function applyViewAs(detail: HuntDetail, viewAs: ViewAs): HuntDetail {
  if (viewAs === "admin") return detail;
  const can_manage =
    viewAs === "organiser"
      ? detail.hunt.can_manage
      : detail.hunt.organiser_gate_active
        ? false
        : detail.hunt.can_manage;
  return { ...detail, hunt: { ...detail.hunt, can_manage, can_administer: false } };
}
