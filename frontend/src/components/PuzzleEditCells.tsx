import { useState, useEffect } from "react";
import {
  Badge,
  Button,
  Group,
  Menu,
  Modal,
  Popover,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import type { Puzzle } from "../types";
import { useSolvePuzzle, type SolveMode } from "../api";
import { notifyError, showSheetWarning, priorityColor, statusColor } from "../util";

const STATUS_OPTIONS = [
  "Unstarted",
  "In Progress",
  "Stuck",
  "Needs Extraction",
  "Needs Submission",
  "Solved",
  "Backsolved",
];
const PRIORITY_OPTIONS = ["Low", "Medium", "High", "Very High"];

/** Click a status badge to pick a new one. */
export function StatusCell({ value, onChange }: { value: string | null; onChange: (v: string) => void }) {
  return (
    <Menu position="bottom-start" withinPortal shadow="md">
      <Menu.Target>
        <Badge variant="light" color={statusColor(value)} style={{ cursor: "pointer" }}>
          {value || "set status"}
        </Badge>
      </Menu.Target>
      <Menu.Dropdown>
        {STATUS_OPTIONS.map((s) => (
          <Menu.Item key={s} onClick={() => onChange(s)}>
            {s}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}

export function PriorityCell({ value, onChange }: { value: string | null; onChange: (v: string) => void }) {
  return (
    <Menu position="bottom-start" withinPortal shadow="md">
      <Menu.Target>
        <Badge variant="dot" color={priorityColor(value)} style={{ cursor: "pointer" }}>
          {value || "set"}
        </Badge>
      </Menu.Target>
      <Menu.Dropdown>
        {PRIORITY_OPTIONS.map((p) => (
          <Menu.Item key={p} onClick={() => onChange(p)}>
            {p}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}

/** Click the type text to edit it inline. */
export function TypeCell({ value, onSave }: { value: string | null; onSave: (v: string) => void }) {
  const [opened, { open, close }] = useDisclosure(false);
  const [text, setText] = useState(value || "");

  function save() {
    onSave(text.trim());
    close();
  }

  return (
    <Popover opened={opened} onClose={close} position="bottom-start" withArrow shadow="md">
      <Popover.Target>
        <Text
          size="sm"
          c={value ? undefined : "dimmed"}
          style={{ cursor: "pointer" }}
          onClick={() => {
            setText(value || "");
            open();
          }}
        >
          {value || "set type"}
        </Text>
      </Popover.Target>
      <Popover.Dropdown>
        <Group gap="xs">
          <TextInput
            size="xs"
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
            data-autofocus
          />
          <Button size="xs" onClick={save}>
            Save
          </Button>
        </Group>
      </Popover.Dropdown>
    </Popover>
  );
}

/**
 * Modal to mark a puzzle solved or change its solution.
 *
 * Unsolved puzzles: **Mark partial** and **Mark complete** buttons.
 * Solved puzzles: **Change solution** and **Add solution** buttons.
 */
export function SolveModal({ puzzle, uid, onClose }: { puzzle: Puzzle | null; uid: string; onClose: () => void }) {
  const solve = useSolvePuzzle(uid);
  // One input per action button.
  const [partialText, setPartialText] = useState("");
  const [fullText, setFullText] = useState("");
  const [changeText, setChangeText] = useState("");
  const [addText, setAddText] = useState("");

  function submit(mode: SolveMode) {
    if (!puzzle) return;
    const value =
      mode === "partial" ? partialText : mode === "full" ? fullText : mode === "change" ? changeText : addText;

    // Guards mirror cog behaviour.
    if (mode !== "partial" && !value.trim()) return; // optional for partial only
    if ((mode === "full" || mode === "partial") && puzzle.solved) return; // already solved
    if ((mode === "change" || mode === "add") && !puzzle.solved) return; // not yet solved

    solve.mutate(
      { puzzleId: puzzle.id, solution: value.trim(), mode },
      {
        onSuccess: (data) => {
          const msg =
            mode === "full"
              ? `Marked \"${puzzle.name}\" solved`
              : mode === "partial"
                ? `Marked \"${puzzle.name}\" partially solved`
                : mode === "change"
                  ? `Changed solution for \"${puzzle.name}\"`
                  : `Added solution to \"${puzzle.name}\"`;
          notifications.show({ message: msg, color: mode === "partial" ? "blue" : "green" });
          showSheetWarning(data);
          setPartialText("");
          setFullText("");
          setChangeText("");
          setAddText("");
          onClose();
        },
        onError: notifyError,
      },
    );
  }

  function handleKey(e: React.KeyboardEvent, mode: SolveMode) {
    if (e.key === "Enter") submit(mode);
  }

  // When the puzzle changes, prefill change/add with current solution.
  useEffect(() => {
    if (puzzle?.solution && puzzle.solved) {
      setChangeText(puzzle.solution);
    }
  }, [puzzle?.id]);

  const isSolved = puzzle?.solved ?? false;

  return (
    <Modal opened={puzzle !== null} onClose={onClose} title={puzzle ? `Solve — ${puzzle.name}` : "Solve"} centered>
      {puzzle && (
        <Stack>
          {isSolved && (
            <Text size="sm" c="dimmed">
              Current solution: {puzzle.solution}
            </Text>
          )}

          {!isSolved ? (
            /* --- unsolved: partial + complete --- */
            <Stack gap="sm">
              <TextInput
                label="Partial solution (optional)"
                description="Records a partial answer without fully solving the puzzle."
                value={partialText}
                onChange={(e) => setPartialText(e.currentTarget.value)}
                onKeyDown={(e) => handleKey(e, "partial")}
              />
              <Button
                color="blue"
                variant="light"
                onClick={() => submit("partial")}
                loading={solve.isPending}
              >
                Mark partial
              </Button>

              <TextInput
                label="Solution"
                description="Posts the SOLVED message to the channel and archives the sheet."
                value={fullText}
                onChange={(e) => setFullText(e.currentTarget.value)}
                onKeyDown={(e) => handleKey(e, "full")}
              />
              <Button
                color="green"
                onClick={() => submit("full")}
                loading={solve.isPending}
                disabled={!fullText.trim()}
              >
                Mark complete
              </Button>
            </Stack>
          ) :
            /* --- solved: change + add --- */
            <Stack gap="sm">
              <TextInput
                label="Change solution"
                description="Replaces the current solution."
                value={changeText}
                onChange={(e) => setChangeText(e.currentTarget.value)}
                onKeyDown={(e) => handleKey(e, "change")}
              />
              <Button
                color="orange"
                variant="light"
                onClick={() => submit("change")}
                loading={solve.isPending}
                disabled={!changeText.trim()}
              >
                Change solution
              </Button>

              <TextInput
                label="Add solution"
                description="Appends to the existing solution."
                value={addText}
                onChange={(e) => setAddText(e.currentTarget.value)}
                onKeyDown={(e) => handleKey(e, "add")}
              />
              <Button
                color="teal"
                variant="light"
                onClick={() => submit("add")}
                loading={solve.isPending}
                disabled={!addText.trim()}
              >
                Add solution
              </Button>
            </Stack>
          }
        </Stack>
      )}
    </Modal>
  );
}
