import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { onAuthLost } from "./queryClient";
import type { CanvasRegion, ChatMessage, EvidenceItem, Guild, GuildSettingsPatch, GuildSettingsResponse, HuntCanvas, HuntDetail, HuntList, MarkKind, MarkStyle, MediaMark, PuzzleImage, RegionRole, RegionShape, RollupAnswers, User } from "./types";

function csrfToken(): string {
  return document.cookie.match(/(?:^|; )csrf_token=([^;]*)/)?.[1] ?? "";
}

/** Wrap fetch with credentials so the session cookie is always sent. */
async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "include" });
  if (res.status === 401) throw authLost();
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
  if (res.status === 401) throw authLost();
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

/** Multipart upload with the session cookie and CSRF header attached.
 *  Not apiSend: that sets a JSON Content-Type, and multipart needs the
 *  browser to set its own boundary. Match apiSend's credentials/CSRF
 *  handling otherwise, since the CSRF guard applies to these routes too. */
async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken() },
    body: form,
  });
  if (res.status === 401) throw authLost();
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
    case "ocr_failed":
      return "Couldn't extract text from that image — try typing the note by hand.";
    case "already_gated":
      return "This hunt already has a team role.";
    case "role_not_applied":
      return "Couldn't gate the hunt — the role couldn't be made visible (are you addable to it?).";
    case "missing_role_name":
      return "Enter a team role name.";
    default:
      return d.error || `${status}`;
  }
}

export class AuthError extends Error {
  constructor() {
    // The message reaches the user directly: notifyError shows err.message in a
    // toast, so the raw "auth_required" slug used to be what they saw.
    super("You have been signed out — please sign in again.");
    this.name = "AuthError";
  }
}

/** Thrown on every 401. Also drops the cached user, so App re-renders into the
 *  login screen instead of leaving a stale session on screen. */
function authLost(): AuthError {
  onAuthLost();
  return new AuthError();
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

/** A working sheet belonging to the hunt rather than to a puzzle — the web
 *  equivalent of `!add_sheet` run in a hunt channel. */
export function useAddHuntSheet(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name }: { name: string }) =>
      apiSend<{ ok: boolean }>(`/api/hunts/${uid}/sheets`, "POST", { name }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useRemoveHuntSheet(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sheetId }: { sheetId: number }) =>
      apiSend<{ ok: boolean }>(`/api/hunts/${uid}/sheets/${sheetId}`, "DELETE"),
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

export function useUploadMedia(huntUid: string) {
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      // width/height/tile_status come back too (B1): a caller creating a
      // canvas needs them to know whether to wait for tiling.
      return apiUpload<{
        id: number; url: string; width: number; height: number;
        tile_status: "none" | "pending" | "ready" | "failed";
      }>(`/api/hunts/${huntUid}/media`, form);
    },
  });
}

export function useAddEvidence(huntUid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ puzzleId, sheetId, note, mediaId }:
      { puzzleId: number | null; sheetId: number | null; note: string; mediaId: number | null }) =>
      apiSend<{ id: number; note: string }[]>("/api/evidence", "POST", {
        hunt_uid: huntUid,
        source_puzzle_id: puzzleId,
        source_sheet_id: sheetId,
        notes: note ? [note] : [],
        media_id: mediaId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hunt", huntUid] });
      qc.invalidateQueries({ queryKey: ["evidence", huntUid] });
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

export function useRemoveMinimeta(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/remove-minimeta`, "POST",
      ),
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

export function useRenameGroup(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tagGroupId, name }: { tagGroupId: number; name: string }) =>
      apiSend<{ ok: boolean }>(
        `/api/tag_groups/${tagGroupId}/rename`, "POST", { name },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useChangeGroupType(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tagGroupId, type }: { tagGroupId: number; type: string }) =>
      apiSend<{ ok: boolean; summary?: string; sheet_warning?: string }>(
        `/api/tag_groups/${tagGroupId}/change-type`, "POST", { type },
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

export function useDeleteMini(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    // Nested under the parent: the server checks the mini really is its child.
    mutationFn: ({ parentId, miniId }: { parentId: number; miniId: number }) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(
        `/api/puzzles/${parentId}/minis/${miniId}`,
        "DELETE"
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

/**
 * Meta/round types accepted by create_meta. The dropdown's runtime list comes
 * from the hunt-detail API (CREATE_META_TYPES in puzzle_service.py); this union
 * is a compile-time guard on the create-meta payload. Keep them in sync.
 */
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
// User-selectable edge types. 'target' is system-owned but allowed when clearing a
// target's deliberate self-feed; the backend guards it to the group's own target.
export type EdgeType = "feeds" | "informs" | "none" | "target";

export function useRetagPuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      puzzleId,
      metaCode,
      mode,
      edge,
    }: {
      puzzleId: number;
      metaCode: string;
      mode: RetagMode;
      edge?: EdgeType;
    }) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(`/api/puzzles/${puzzleId}/meta`, "POST", {
        group_code: metaCode,
        mode,
        ...(edge ? { edge } : {}),
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

export function useConvertToMetapuzzle(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (puzzleId: number) =>
      apiSend<{ ok: boolean; sheet_warning?: string }>(
        `/api/puzzles/${puzzleId}/convert-to-metapuzzle`, "POST",
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
    mutationFn: ({ roundId, keepPuzzles }: { roundId: number; keepPuzzles: boolean }) =>
      apiSend<DeleteRoundResult>(`/api/tag_groups/${roundId}`, "DELETE", {
        keep_puzzles: keepPuzzles,
      }),
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

export interface MediaGcResult {
  /** Orphaned images: referenced by nothing, older than the 24h grace period. */
  count: number;
  /** Originals only — tile pyramids aren't counted, so this is a floor. */
  bytes: number;
}

export function useMediaStats(uid: string) {
  return useMutation({
    mutationFn: () => apiGet<MediaGcResult>(`/api/hunts/${uid}/media/stats`),
  });
}

export function useMediaSweep(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<MediaGcResult & { ok: boolean }>(
      `/api/hunts/${uid}/media/sweep`, "POST"),
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

export function fetchChannelMessages(channelId: string, before?: string): Promise<ChatMessage[]> {
  const qs = before ? `?before=${before}` : "";
  return apiGet<{ messages: ChatMessage[] }>(`/api/channels/${channelId}/messages${qs}`).then(
    (d) => d.messages,
  );
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

/** Swap the image on an existing evidence row, keeping its note and its place.
 *  The previous media row is left alone server-side — see
 *  MySQLEvidenceDb.update_media. */
export function useReplaceClueImage(huntUid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, mediaId }: { itemId: number; mediaId: number | null }) =>
      apiSend<{ id: number; media_id: number | null }>(
        `/api/evidence/${itemId}`,
        "PATCH",
        { media_id: mediaId }
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["evidence", huntUid] }),
  });
}

export function useDeleteEvidence(huntUid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: number) =>
      apiSend<{ ok: boolean }>(`/api/evidence/${itemId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["evidence", huntUid] }),
  });
}

export function useEditClueNote(huntUid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, note }: { itemId: number; note: string }) =>
      apiSend<{ id: number; note: string; target_puzzle_id: number | null }>(
        `/api/evidence/${itemId}`,
        "PATCH",
        { note }
      ),
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

export function useCreateSolvedSheet(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiSend<{ ok: boolean; solved_sheet_id: string }>(`/api/hunts/${uid}/solved-sheet`, "POST"),
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

export function useGuildSettings(guildId: number | null) {
  return useQuery({
    queryKey: ["guild-settings", guildId],
    queryFn: () =>
      apiGet<GuildSettingsResponse>(`/api/guilds/${guildId}/settings`),
    enabled: guildId !== null,
  });
}

export function useUpdateGuildSettings(guildId: number | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: GuildSettingsPatch) =>
      apiSend<{ ok: boolean }>(`/api/guilds/${guildId}/settings`, "PATCH", patch),
    // The tiers block is derived server-side from what we just wrote, so refetch
    // rather than patching the cache by hand.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["guild-settings", guildId] }),
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
  create_solved_sheet?: boolean;
  layout_mode?: "channel" | "forum";
  // Set on the second attempt after the backend asks to confirm reusing an
  // existing role (needs_confirm: "existing_role").
  confirm_existing_role?: boolean;
}

// A successful create returns { uid }, optionally with warning: "role_not_applied"
// (hunt created but couldn't be gated). A 200 with needs_confirm: "existing_role"
// means the named role already exists and the caller must re-POST with
// confirm_existing_role: true. (apiSend only treats non-2xx as errors, so this
// confirm signal arrives here as a normal result.)
export interface CreateHuntResult {
  uid?: string;
  warning?: string;
  needs_confirm?: string;
  message?: string;
}

export function useCreateHunt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateHuntPayload) =>
      apiSend<CreateHuntResult>("/api/hunts", "POST", payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunts"] }),
  });
}

// Gate an open hunt behind a team role. Mirrors useReconcile's "resubmit with a
// confirm flag" shape: on needs_confirm the caller re-runs with confirmExisting.
export interface SetHuntRoleResult {
  ok?: boolean;
  failed_categories?: string[];
  needs_confirm?: string;
  message?: string;
}

export function useSetHuntRole(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ roleName, confirmExisting }: { roleName: string; confirmExisting?: boolean }) =>
      apiSend<SetHuntRoleResult>(
        `/api/hunts/${uid}/role`,
        "POST",
        confirmExisting
          ? { role_name: roleName, confirm_existing_role: true }
          : { role_name: roleName }
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunt", uid] }),
  });
}

export function useDeleteHunt(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend<{ ok: boolean; failed_channels?: string[] }>(`/api/hunts/${uid}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hunts"] }),
  });
}

// --- Canvases and regions ---------------------------------------------------

export function useCanvases(uid: string | undefined) {
  return useQuery({
    queryKey: ["canvases", uid],
    queryFn: () => apiGet<{ canvases: HuntCanvas[] }>(`/api/hunts/${uid}/canvases`),
    enabled: !!uid,
  });
}

export function useCreateCanvas(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mediaId, name, note }: { mediaId: number; name: string; note?: string }) =>
      apiSend<HuntCanvas>(`/api/hunts/${uid}/canvases`, "POST", {
        media_id: mediaId,
        name,
        note: note ?? null,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["canvases", uid] }),
  });
}

export function useUpdateCanvas(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ canvasId, name, note }: { canvasId: number; name?: string; note?: string | null }) =>
      apiSend<HuntCanvas>(`/api/canvases/${canvasId}`, "PATCH", { name, note }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["canvases", uid] }),
  });
}

export function useDeleteCanvas(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (canvasId: number) =>
      apiSend<{ ok: boolean }>(`/api/canvases/${canvasId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["canvases", uid] }),
  });
}

/** Rebuild the tile pyramid for a canvas whose tiling failed, or which is
 *  stuck 'pending' because the process died mid-build. Returns 409 if the
 *  canvas is not in one of those two states. */
export function useRetileCanvas(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (canvasId: number) =>
      apiSend<{ ok: boolean; tile_status: string }>(`/api/canvases/${canvasId}/retile`, "POST"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["canvases", uid] }),
  });
}

/** puzzle_id and role always travel together — the API rejects a half-set
 *  link with a 400. */
type RegionLink = {
  puzzle_id?: number | null;
  role?: RegionRole | null;
};

/** Everything a region write accepts: its own fields plus the puzzle link. */
type RegionWrite = RegionLink & {
  shape?: RegionShape;
  label?: string;
  note?: string | null;
};

export function useCreateRegion(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ canvasId, ...body }: { canvasId: number } & RegionWrite) =>
      apiSend<CanvasRegion>(`/api/canvases/${canvasId}/regions`, "POST", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["canvases", uid] }),
  });
}

export function useUpdateRegion(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ canvasId, regionId, ...body }:
      { canvasId: number; regionId: number } & RegionWrite) =>
      apiSend<CanvasRegion>(`/api/canvases/${canvasId}/regions/${regionId}`, "PATCH", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["canvases", uid] });
      // An informs link creates or drops an evidence row, so the evidence views
      // are stale too.
      qc.invalidateQueries({ queryKey: ["evidence", uid] });
    },
  });
}

export function useDeleteRegion(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ canvasId, regionId }: { canvasId: number; regionId: number }) =>
      apiSend<{ ok: boolean }>(`/api/canvases/${canvasId}/regions/${regionId}`, "DELETE"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["canvases", uid] });
      qc.invalidateQueries({ queryKey: ["evidence", uid] });
    },
  });
}

export function useMarkup(mediaId: number | null) {
  return useQuery({
    queryKey: ["markup", mediaId],
    queryFn: () => apiGet<MediaMark[]>(`/api/media/${mediaId}/markup`),
    enabled: mediaId != null,
  });
}

/** The geometry half of a write. Style travels separately because it is the
 *  only part an existing mark can change. */
type MarkPayload = {
  kind: MarkKind;
  points: [number, number][];
  text?: string;
  size?: number;
};

export function useCreateMark(mediaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ mark, style }: { mark: MarkPayload; style: Partial<MarkStyle> }) =>
      apiSend<MediaMark>(`/api/media/${mediaId}/markup`, "POST", { mark, style }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["markup", mediaId] }),
  });
}

export function useUpdateMark(mediaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ markId, style, text }:
      { markId: number; style: Partial<MarkStyle>; text?: string }) =>
      apiSend<MediaMark>(`/api/media/${mediaId}/markup/${markId}`, "PATCH", { style, text }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["markup", mediaId] }),
  });
}

export function useDeleteMark(mediaId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (markId: number) =>
      apiSend<{ ok: boolean }>(`/api/media/${mediaId}/markup/${markId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["markup", mediaId] }),
  });
}

// --- Puzzle images -------------------------------------------------------

export function usePuzzleImages(puzzleId: number | null) {
  return useQuery({
    queryKey: ["puzzle-images", puzzleId],
    queryFn: () => apiGet<PuzzleImage[]>(`/api/puzzles/${puzzleId}/images`),
    enabled: puzzleId != null,
  });
}

export function useAttachPuzzleImage(puzzleId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (mediaId: number) =>
      apiSend<PuzzleImage>(`/api/puzzles/${puzzleId}/images`, "POST", { media_id: mediaId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["puzzle-images", puzzleId] }),
  });
}

export function useReorderPuzzleImages(puzzleId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (orderedIds: number[]) =>
      apiSend<PuzzleImage[]>(`/api/puzzles/${puzzleId}/images`, "PATCH", { ordered_ids: orderedIds }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["puzzle-images", puzzleId] }),
  });
}

export function useDetachPuzzleImage(puzzleId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (imageId: number) =>
      apiSend<{ ok: boolean }>(`/api/puzzles/${puzzleId}/images/${imageId}`, "DELETE"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["puzzle-images", puzzleId] }),
  });
}

export function useCreatePuzzleFromRegion(uid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ canvasId, regionId, name, roundId, role }: {
      canvasId: number; regionId: number; name: string; roundId: number | null;
      role: RegionRole;
    }) =>
      apiSend<CanvasRegion>(
        `/api/canvases/${canvasId}/regions/${regionId}/create-puzzle`, "POST",
        // Omitting round_id (rather than sending null) is what the API reads as
        // "no round" — an unattached, hunt-level puzzle.
        roundId ? { name, round_id: roundId, role } : { name, role }),
    onSuccess: () => {
      // A new puzzle with a channel and a sheet tab: the board is stale too.
      qc.invalidateQueries({ queryKey: ["canvases", uid] });
      qc.invalidateQueries({ queryKey: ["hunt", uid] });
    },
  });
}

export function useOcrCanvasRegion() {
  return useMutation({
    mutationFn: ({ canvasId, shape }: { canvasId: number; shape: RegionShape }) =>
      apiSend<{ text: string }>(`/api/canvases/${canvasId}/ocr-preview`, "POST", { shape }),
  });
}

export function useOcrEvidenceImage(huntUid: string) {
  return useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return apiUpload<{ text: string }>(`/api/hunts/${huntUid}/evidence/ocr-preview`, form);
    },
  });
}
