import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { HuntDetail, HuntList, RollupAnswers, User } from "./types";

/** Wrap fetch with credentials so the session cookie is always sent. */
async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "include" });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    credentials: "include",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as {
      error?: string;
      retry_after?: number;
      detail?: string;
    };
    throw new Error(friendlyError(detail, res.status));
  }
  return res.json() as Promise<T>;
}

function friendlyError(d: { error?: string; retry_after?: number; detail?: string }, status: number): string {
  switch (d.error) {
    case "discord_rate_limited": {
      const secs = d.retry_after ?? 0;
      if (secs >= 3600) {
        const hrs = Math.round(secs / 3600);
        return `Discord has temporarily blocked channel changes for this bot (~${hrs}h). This is a server-side cooldown that clears on its own — retrying won't help and can extend it.`;
      }
      const mins = secs ? Math.ceil(secs / 60) : null;
      return `Discord is rate-limiting channel changes${mins ? ` — try again in ~${mins} min` : ""}.`;
    }
    case "duplicate_name":
      return "A puzzle with that name already exists.";
    case "round_exists":
    case "category_exists":
      return "A meta/category with that name already exists.";
    case "puzzle_name_clash":
      return "That name clashes with an existing puzzle.";
    case "guild_full":
    case "category_full":
    case "no_category_space":
      return "No channel/category space left on the Discord server.";
    case "insufficient_role":
    case "forbidden":
      return "You don't have permission for that.";
    case "hunt_archived":
      return "This hunt is archived (read-only).";
    case "solved_no_sheets":
      return "You can't add sheets to a solved puzzle.";
    case "sheet_not_found":
      return "That sheet no longer exists — refresh and try again.";
    case "bad_meta_code":
      return "That meta no longer exists — refresh and try again.";
    case "discord_error":
      return `Discord error: ${d.detail ?? "please try again"}.`;
    default:
      return d.error || `${status}`;
  }
}

export class AuthError extends Error {
  constructor() {
    super("auth_required");
    this.name = "AuthError";
  }
}

export function loginUrl() {
  return "/api/auth/login";
}

export async function logout() {
  await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const data = await apiGet<{ authenticated: boolean; user: User }>("/api/auth/me");
      return data.user;
    },
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}

export function useHunts() {
  return useQuery({
    queryKey: ["hunts"],
    queryFn: () => apiGet<HuntList>("/api/hunts"),
  });
}

export function useHunt(uid: string | undefined) {
  return useQuery({
    queryKey: ["hunt", uid],
    queryFn: () => apiGet<HuntDetail>(`/api/hunts/${uid}`),
    enabled: !!uid,
    refetchInterval: 30_000,
  });
}

export function useAddNote(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, note }: { puzzleId: number; note: string }) =>
      apiSend<{ notes: string[] }>(`/api/puzzles/${puzzleId}/notes`, "POST", { note }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useEraseNote(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, index }: { puzzleId: number; index: number }) =>
      apiSend<{ notes: string[] }>(`/api/puzzles/${puzzleId}/notes/${index}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useAddSheet(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, name }: { puzzleId: number; name: string }) =>
      apiSend<{ ok: boolean }>(`/api/puzzles/${puzzleId}/sheets`, "POST", { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useRemoveSheet(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, sheetId }: { puzzleId: number; sheetId: number }) =>
      apiSend<{ ok: boolean }>(`/api/puzzles/${puzzleId}/sheets/${sheetId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export interface PuzzlePatch {
  status?: string;
  priority?: string;
  puzzle_type?: string;
}

export function useUpdatePuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, patch }: { puzzleId: number; patch: PuzzlePatch }) =>
      apiSend<{ ok: boolean }>(`/api/puzzles/${puzzleId}`, "PATCH", patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useSolvePuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, solution }: { puzzleId: number; solution: string }) =>
      apiSend<{ ok: boolean; solution: string; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/solve`,
        "POST",
        { solution }
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useCreatePuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, metaCode }: { name: string; metaCode: string }) =>
      apiSend<{ ok: boolean; puzzle_id: number }>(`/api/hunts/${uid}/puzzles`, "POST", {
        name,
        meta_code: metaCode,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useCreateMeta(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name }: { name: string }) =>
      apiSend<{ ok: boolean; meta_code: string }>(`/api/hunts/${uid}/metas`, "POST", { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export type RetagMode = "move" | "add" | "remove";

export function useRetagPuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      puzzleId,
      metaCode,
      mode,
    }: {
      puzzleId: number;
      metaCode: string;
      mode: RetagMode;
    }) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(`/api/puzzles/${puzzleId}/meta`, "POST", {
        meta_code: metaCode,
        mode,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useRebuildRollup(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (roundId: number) =>
      apiSend<{ ok: boolean; error?: string }>(`/api/rollups/${roundId}/rebuild`, "POST"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export type SourceMode = "round" | "subtree" | "explicit";

export function useSetSourceMode(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ roundId, mode }: { roundId: number; mode: SourceMode }) =>
      apiSend<{ ok: boolean; source_mode: string }>(`/api/rollups/${roundId}`, "PATCH", {
        source_mode: mode,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useSetNesting(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ roundId, parentId }: { roundId: number; parentId: number | null }) =>
      apiSend<{ ok: boolean }>(`/api/rounds/${roundId}/nest`, "POST", {
        parent_round_id: parentId,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useRollupAnswers(roundId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["rollup-answers", roundId],
    queryFn: () => apiGet<RollupAnswers>(`/api/rollups/${roundId}/answers`),
    enabled,
  });
}
