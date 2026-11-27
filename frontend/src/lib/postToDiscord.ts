/** Pure helpers for the "Post to Discord" flow (no DOM, so they are unit tested). */
import { ApiError } from "../api";
import type { PostTarget } from "../types";

/** The server's cap on the optional message. */
export const MAX_POST_NOTE = 1500;

/** The multipart body the post route expects: the snapshot, the (trimmed)
 *  message, and exactly one of puzzle_id / evidence_id. */
export function buildPostForm(
  blob: Blob, ext: "png" | "jpg", content: string, target: PostTarget,
): FormData {
  const form = new FormData();
  form.append("file", blob, `markup.${ext}`);
  form.append("content", content.trim());
  if (target.kind === "puzzle_image") form.append("puzzle_id", String(target.puzzleId));
  else form.append("evidence_id", String(target.evidenceId));
  return form;
}

/** Where the post will land, for "Posting to …". The board payload carries no
 *  channel name, so this names it generically. */
export function postDestination(target: PostTarget, hasPuzzleChannel: boolean): string {
  if (target.kind === "puzzle_image" || hasPuzzleChannel) return "the puzzle's channel";
  return "the hunt channel";
}

const MESSAGES: Record<string, string> = {
  not_found_or_forbidden: "That image can't be found, or you don't have access to it.",
  forbidden: "You don't have permission to post to that channel.",
  archived: "This hunt is archived (read-only), so nothing can be posted.",
  send_failed: "Discord refused the post. Try again in a moment.",
  command: "The message can't start with \"!\" (it would run as a bot command).",
  too_long: `The message is too long (max ${MAX_POST_NOTE} characters).`,
  bad_image: "The rendered image was not accepted (it must be a PNG or JPEG under 10 MB).",
  not_on_puzzle: "That image is no longer on this puzzle.",
  bad_target: "Couldn't tell where to post this image. Close the editor and try again.",
};

/** A human message for whatever the post attempt threw or returned. */
export function postErrorMessage(e: unknown): string {
  if (e && typeof e === "object" && (e as { error?: string }).error === "too_large") {
    return "The marked-up image is too large to post, even compressed. Try a smaller image.";
  }
  if (e instanceof ApiError) {
    return (e.slug && MESSAGES[e.slug]) || e.message;
  }
  return "Couldn't post the image.";
}
