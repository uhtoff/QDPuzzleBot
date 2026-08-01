import { useState } from "react";
import { ActionIcon, Badge, Box, Button, Group, Stack, Text, TextInput, Tooltip } from "@mantine/core";
import { IconCheck, IconTrash, IconX } from "@tabler/icons-react";
import type { MiniPuzzle } from "../types";
import { useCreateMini, useDeleteMini, useSolvePuzzle } from "../api";
import { notifyError, showSheetWarning } from "../util";
import { ConfirmModal } from "./ConfirmModal";
import { SolveModal } from "./PuzzleEditCells";

interface Props {
  parentId: number;
  minis: MiniPuzzle[];
  uid: string;
  canManage?: boolean;
}

export function MiniPuzzlePanel({ parentId, minis, uid, canManage = false }: Props) {
  const [newName, setNewName] = useState("");
  const [solveInputs, setSolveInputs] = useState<Record<number, string>>({});
  const createMini = useCreateMini(uid);
  const deleteMini = useDeleteMini(uid);
  const solvePuzzle = useSolvePuzzle(uid);
  const [solveMini, setSolveMini] = useState<MiniPuzzle | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<MiniPuzzle | null>(null);

  function setSolveInput(id: number, val: string) {
    setSolveInputs((prev) => ({ ...prev, [id]: val }));
  }

  function runDeleteMini() {
    if (!deleteTarget) return;
    deleteMini.mutate(
      { parentId, miniId: deleteTarget.id },
      {
        onSuccess: (data) => {
          showSheetWarning(data);
          setDeleteTarget(null);
        },
        onError: notifyError,
      }
    );
  }

  return (
    <Box p="xs" bg="var(--mantine-color-gray-0)" style={{ borderTop: "1px solid var(--mantine-color-gray-3)" }}>
      <Stack gap="xs">
        {minis.length === 0 && (
          <Text size="sm" c="dimmed">No mini-puzzles yet.</Text>
        )}
        {minis.map((mini, i) => (
          <Group key={mini.id} gap="xs" wrap="nowrap">
            <Text size="sm" fw={500} style={{ minWidth: 24 }}>{i + 1}.</Text>
            <Text size="sm" style={{ flex: 1 }}>{mini.name}</Text>
            {mini.solved ? (
              <>
                <Badge color="green" variant="light">{mini.solution}</Badge>
                <Tooltip label="Unsolve">
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="red"
                    onClick={() => setSolveMini(mini)}
                  >
                    <IconX size={14} />
                  </ActionIcon>
                </Tooltip>
              </>
            ) : (
              <>
                <TextInput
                  size="xs"
                  placeholder="Answer…"
                  value={solveInputs[mini.id] ?? ""}
                  onChange={(e) => setSolveInput(mini.id, e.currentTarget.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && solveInputs[mini.id]) {
                      solvePuzzle.mutate(
                        { puzzleId: mini.id, solution: solveInputs[mini.id] },
                        { onSuccess: () => setSolveInput(mini.id, ""), onError: notifyError }
                      );
                    }
                  }}
                  style={{ width: 160 }}
                />
                <Tooltip label="Mark solved">
                  <ActionIcon
                    size="sm"
                    variant="subtle"
                    color="green"
                    disabled={!solveInputs[mini.id]}
                    onClick={() => {
                      solvePuzzle.mutate(
                        { puzzleId: mini.id, solution: solveInputs[mini.id] },
                        { onSuccess: () => setSolveInput(mini.id, ""), onError: notifyError }
                      );
                    }}
                  >
                    <IconCheck size={14} />
                  </ActionIcon>
                </Tooltip>
              </>
            )}
            {canManage && (
              <Tooltip label="Delete mini-puzzle">
                <ActionIcon
                  size="sm"
                  variant="subtle"
                  color="red"
                  onClick={() => setDeleteTarget(mini)}
                >
                  <IconTrash size={14} />
                </ActionIcon>
              </Tooltip>
            )}
          </Group>
        ))}

        <Group gap="xs">
          <TextInput
            size="xs"
            placeholder="New mini-puzzle name…"
            value={newName}
            onChange={(e) => setNewName(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newName.trim()) {
                createMini.mutate(
                  { puzzleId: parentId, name: newName.trim() },
                  { onSuccess: () => setNewName(""), onError: notifyError }
                );
              }
            }}
            style={{ flex: 1 }}
          />
          <Button
            size="xs"
            disabled={!newName.trim()}
            loading={createMini.isPending}
            onClick={() => {
              createMini.mutate(
                { puzzleId: parentId, name: newName.trim() },
                { onSuccess: () => setNewName(""), onError: notifyError }
              );
            }}
          >
            Add Mini
          </Button>
        </Group>
      </Stack>
      <SolveModal puzzle={solveMini} uid={uid} onClose={() => setSolveMini(null)} />
      <ConfirmModal
        opened={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={runDeleteMini}
        title="Delete mini-puzzle"
        message={`Permanently delete "${deleteTarget?.name ?? ""}", its Discord thread and its sheet tab? This cannot be undone.`}
        confirmLabel="Delete mini"
        confirmColor="red"
      />
    </Box>
  );
}
