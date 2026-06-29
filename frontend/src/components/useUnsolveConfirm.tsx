import { useState, type ReactNode } from "react";
import { useUnsolvePuzzle } from "../api";
import { notifyError } from "../util";
import { ConfirmModal } from "./ConfirmModal";

interface UnsolveTarget {
  id: number;
  name?: string;
}

/**
 * Shared "Are you sure?" flow for unsolving a puzzle. Owns the unsolve mutation
 * and the confirm modal so every entry point (board, table, mini panel) prompts
 * with identical copy. Call `request(puzzle)` where you would previously have
 * mutated directly, and render `modal` once in the component.
 */
export function useUnsolveConfirm(uid: string): {
  request: (puzzle: UnsolveTarget) => void;
  modal: ReactNode;
} {
  const unsolveMut = useUnsolvePuzzle(uid);
  const [target, setTarget] = useState<UnsolveTarget | null>(null);

  function confirm() {
    if (!target) return;
    unsolveMut.mutate({ puzzleId: target.id }, { onError: notifyError });
    setTarget(null);
  }

  const modal = (
    <ConfirmModal
      opened={target !== null}
      onClose={() => setTarget(null)}
      onConfirm={confirm}
      title="Unsolve puzzle?"
      message={
        `This clears the solution${target?.name ? ` for “${target.name}”` : ""} and moves the ` +
        `channel back to unsolved. You can re-solve it later.`
      }
      confirmLabel="Unsolve"
      confirmColor="red"
    />
  );

  return { request: (puzzle) => setTarget(puzzle), modal };
}
