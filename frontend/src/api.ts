import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { EvidenceItem, Guild, HuntDetail, HuntList, RollupAnswers, User } from "./types";

function csrfToken(): string {
  return document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)?.[1] ?? "";
}

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
    headers: {
      "X-CSRF-Token": csrfToken(),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
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
    case "csrf":
      return "Your session expired — please refresh the page.";
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
    case "bad_group_code":
      return "That meta no longer exists — refresh and try again.";
    case "discord_error":
      return `Discord error — please try again.`;
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
  await fetch("/api/auth/logout", {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken() },
  });
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

export function useLogClue(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, note }: { puzzleId: number; note: string }) =>
      apiSend<{ ok: boolean }>(`/api/puzzles/${puzzleId}/clues`, "POST", { note }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt", uid] });
      qc.invalidateQueries({ queryKey: ["evidence", uid] });
    },
  });
}

export function useSetPuzzleLink(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, url }: { puzzleId: number; url: string }) =>
      apiSend<{ ok: boolean }>(`/api/puzzles/${puzzleId}/link`, "POST", { url }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useMakeMinimeta(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean }>(`/api/puzzles/${puzzleId}/make-minimeta`, "POST"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useConvertToMeta(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/convert-to-meta`, "POST",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useRenamePuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, name }: { puzzleId: number; name: string }) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/rename`, "POST", { name },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

/** Modes for solve-variant actions (mirrors web/actions.py). */
export type SolveMode = "full" | "partial" | "change" | "add";

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
    mutationFn: ({ puzzleId, solution, mode }: { puzzleId: number; solution: string; mode?: SolveMode }) =>
      apiSend<{ ok: boolean; solution: string; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/solve`,
        "POST",
        { solution, mode: mode ?? "full" }
      ),
    onMutate: async ({ puzzleId, solution, mode }) => {
      await qc.cancelQueries({ queryKey: ["hunt", uid] });
      const previous = qc.getQueryData(["hunt", uid]) as
        | { board?: { tag_group_id: number; puzzles: Array<{ id: number; solved?: boolean; solution?: string | null; status?: string } & Record<string, unknown>> }[] }
        | undefined;
      if (previous?.board) {
        qc.setQueryData(["hunt", uid], {
          ...previous,
          board: previous.board.map((g) => ({
            ...g,
            puzzles: g.puzzles.map(
              (p) => p.id === puzzleId ? { ...p, solved: mode === "full" || p.solved, solution, status: mode === "full" ? "Solved" : p.status } : p
            ),
          })),
        });
      }
      return { previous };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(["hunt", uid], ctx.previous);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useUnsolvePuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId }: { puzzleId: number }) =>
      apiSend<{ ok: boolean }>(`/api/puzzles/${puzzleId}/unsolve`, "POST"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt", uid] });
    },
  });
}

export function useCreateMini(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, name }: { puzzleId: number; name: string }) =>
      apiSend<{ ok: boolean; mini_id: number; name: string; channel_id: string }>(
        `/api/puzzles/${puzzleId}/minis`,
        "POST",
        { name }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt", uid] });
    },
  });
}

export function useCreatePuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, metaCode }: { name: string; metaCode: string }) =>
      apiSend<{ ok: boolean; puzzle_id: number }>(`/api/hunts/${uid}/puzzles`, "POST", {
        name,
        group_code: metaCode,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

/** Meta/round types accepted by create_meta (mirrors TYPE_DEFAULTS in puzzle_service.py). */
export type MetaType = "Round" | "Metapuzzle" | "Capstone" | "Runaround" | "Group";

export function useCreateMeta(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, type }: { name: string; type?: MetaType }) =>
      apiSend<{ ok: boolean; group_code: string }>(`/api/hunts/${uid}/metas`, "POST", {
        name,
        ...(type ? { type } : {}),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export type RetagMode = "move" | "add" | "remove" | "clear";

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
        group_code: metaCode,
        mode,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export interface ConvertPreview {
  orphan_count: number;
  orphan_feeders: { id: number; name: string }[];
}

/** Read-only preview for the convert-to-feeder confirm dialog: how many feeders
 * (and which) demoting this metapuzzle would orphan. Only fetched while enabled. */
export function useConvertToFeederPreview(puzzleId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ["convert-preview", puzzleId],
    queryFn: () =>
      apiGet<ConvertPreview>(`/api/puzzles/${puzzleId}/convert-to-feeder/preview`),
    enabled: enabled && puzzleId != null,
  });
}

export function useConvertToFeeder(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean; orphaned?: number; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/convert-to-feeder`,
        "POST"
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useConvertToCapstone(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/convert-to-capstone`, "POST",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useConvertToRunaround(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/convert-to-runaround`, "POST",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useConvertToGroup(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/convert-to-group`, "POST",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useDeletePuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(`/api/puzzles/${puzzleId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export interface DeleteRoundResult {
  ok: boolean;
  failed_channels?: string[];
}

export function useDeleteRound(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (roundId: number) =>
      apiSend<DeleteRoundResult>(`/api/tag_groups/${roundId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export interface ReconcileAction {
  kind: string;
  target: string;
  detail: string;
}

export interface ReconcileResult {
  ok: boolean;
  actions: ReconcileAction[];
  orphan_channels: { id: number; name: string }[];
  orphan_tabs: { id: number; title: string }[];
  failures: { target: string; error: string }[];
  deleted: number;
}

export function useReconcile(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mode, confirmedOrphans }: { mode: string; confirmedOrphans?: string[] }) =>
      apiSend<ReconcileResult>(
        `/api/hunts/${uid}/reconcile?mode=${encodeURIComponent(mode)}`,
        "POST",
        confirmedOrphans ? { confirmed_orphans: confirmedOrphans } : {}
      ),
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

export function useRollupAnswers(roundId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["rollup-answers", roundId],
    queryFn: () => apiGet<RollupAnswers>(`/api/rollups/${roundId}/answers`),
    enabled,
  });
}

export function useEvidence(huntUid: string) {
  return useQuery({
    queryKey: ["evidence", huntUid],
    queryFn: () => apiGet<EvidenceItem[]>(`/api/evidence?hunt_uid=${huntUid}`),
    staleTime: 10_000,
  });
}

export function useUpdateEvidence(huntUid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, targetPuzzleId }: { itemId: number; targetPuzzleId: number | null }) =>
      apiSend<EvidenceItem>(`/api/evidence/${itemId}`, "PATCH", { target_puzzle_id: targetPuzzleId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["evidence", huntUid] }),
  });
}

export interface HuntSettingsPatch {
  url?: string;
  start_timestamp?: number;
  username?: string;
  password?: string;
  url_sep?: string;
  puzzle_prefix?: string;
}

export function useUpdateHuntSettings(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: HuntSettingsPatch) =>
      apiSend<{ ok: boolean }>(`/api/hunts/${uid}/settings`, "PATCH", patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useArchiveHunt(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (archived: boolean) =>
      apiSend<{ ok: boolean; archived: boolean }>(`/api/hunts/${uid}/archive`, "POST", { archived }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt", uid] });
      qc.invalidateQueries({ queryKey: ["hunts"] });
    },
  });
}

export function useCreateArchiveSheet(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiSend<{ ok: boolean; archive_google_sheet_id: string }>(`/api/hunts/${uid}/archive-sheet`, "POST"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useAdminGuilds() {
  return useQuery({
    queryKey: ["guilds", "admin"],
    queryFn: () => apiGet<{ guilds: Guild[] }>("/api/guilds?admin=1").then((d) => d.guilds),
    staleTime: 60_000,
  });
}

export interface CreateHuntPayload {
  guild_id: number;
  name: string;
  url: string;
  start_timestamp: number;
  role_name?: string;
  username?: string;
  password?: string;
  create_archive_sheet?: boolean;
  layout_mode?: "channel" | "forum";
}

export function useCreateHunt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateHuntPayload) =>
      apiSend<{ uid: string }>("/api/hunts", "POST", payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunts"] }),
  });
}

export function useDeleteHunt(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<{ ok: boolean; failed_channels?: string[] }>(`/api/hunts/${uid}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunts"] }),
  });
}
