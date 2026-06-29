import { useState } from "react";
import { ActionIcon, Badge, Box, Button, Group, Stack, Text, TextInput, Tooltip } from "@mantine/core";
import { IconCheck, IconX } from "@tabler/icons-react";
import type { MiniPuzzle } from "../types";
import { useCreateMini, useSolvePuzzle } from "../api";
import { useUnsolveConfirm } from "./useUnsolveConfirm";

interface Props {
  parentId: number;
  minis: MiniPuzzle[];
  uid: string;
}

export function MiniPuzzlePanel({ parentId, minis, uid }: Props) {
  const [newName, setNewName] = useState("");
  const [solveInputs, setSolveInputs] = useState<Record<number, string>>({});
  const createMini = useCreateMini(uid);
  const solvePuzzle = useSolvePuzzle(uid);
  const { request: requestUnsolve, modal: unsolveModal } = useUnsolveConfirm(uid);

  function setSolveInput(id: number, val: string) {
    setSolveInputs((prev) => ({ ...prev, [id]: val }));
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
                    onClick={() => requestUnsolve({ id: mini.id, name: mini.name })}
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
                      solvePuzzle.mutate({ puzzleId: mini.id, solution: solveInputs[mini.id] });
                      setSolveInput(mini.id, "");
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
                      solvePuzzle.mutate({ puzzleId: mini.id, solution: solveInputs[mini.id] });
                      setSolveInput(mini.id, "");
                    }}
                  >
                    <IconCheck size={14} />
                  </ActionIcon>
                </Tooltip>
              </>
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
                createMini.mutate({ puzzleId: parentId, name: newName.trim() });
                setNewName("");
              }
            }}
            style={{ flex: 1 }}
          />
          <Button
            size="xs"
            disabled={!newName.trim()}
            loading={createMini.isPending}
            onClick={() => {
              createMini.mutate({ puzzleId: parentId, name: newName.trim() });
              setNewName("");
            }}
          >
            Add Mini
          </Button>
        </Group>
      </Stack>
      {unsolveModal}
    </Box>
  );
}
