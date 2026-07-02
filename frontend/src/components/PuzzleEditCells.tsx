import { useState, useEffect } from "react";
import {
  Badge,
  Button,
  Collapse,
  Divider,
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
import { IconChevronDown, IconChevronUp } from "@tabler/icons-react";
import type { Puzzle } from "../types";
import { useSolvePuzzle, useUnsolvePuzzle, type SolveMode } from "../api";
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

export type SolveTarget = Pick<Puzzle, "id" | "name" | "solved" | "solution">;

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
 * Modal to solve, unsolve, or amend a puzzle's solution.
 *
 * Unsolved puzzles: primary **Solve** (with an answer), plus **Mark complete
 * (no answer)** and **Mark partial** under Advanced.
 * Solved puzzles: primary **Mark unsolved**, plus **Change solution** and
 * **Add solution** under Advanced.
 */
export function SolveModal({ puzzle, uid, onClose }: { puzzle: SolveTarget | null; uid: string; onClose: () => void }) {
  const solve = useSolvePuzzle(uid);
  const unsolve = useUnsolvePuzzle(uid);
  // One input per action button.
  const [partialText, setPartialText] = useState("");
  const [fullText, setFullText] = useState("");
  const [changeText, setChangeText] = useState("");
  const [addText, setAddText] = useState("");
  // Secondary actions live behind an "Advanced" collapse (mirrors NewHuntModal).
  const [advancedOpen, setAdvancedOpen] = useState(false);

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

  // Solve a puzzle that has no text answer (interactions, etc.): record a tick as
  // the solution. Mirrors the bot's !mark_as_complete (full solve with "✅").
  function markComplete() {
    if (!puzzle || puzzle.solved) return;
    solve.mutate(
      { puzzleId: puzzle.id, solution: "✅", mode: "full" },
      {
        onSuccess: (data) => {
          notifications.show({ message: `Marked \"${puzzle.name}\" solved`, color: "green" });
          showSheetWarning(data);
          onClose();
        },
        onError: notifyError,
      },
    );
  }

  // Clear the solution and move the channel back to unsolved. Opening this modal
  // is the deliberate step (no confirm dialog). Close immediately and rely on the
  // error toast; a failed unsolve self-corrects on the next query invalidation.
  function unsolvePuzzle() {
    if (!puzzle) return;
    unsolve.mutate({ puzzleId: puzzle.id }, { onError: notifyError });
    onClose();
  }

  // When the puzzle changes: prefill change with current solution, collapse Advanced.
  useEffect(() => {
    if (puzzle?.solution && puzzle.solved) {
      setChangeText(puzzle.solution);
    }
    setAdvancedOpen(false);
  }, [puzzle?.id]);

  const isSolved = puzzle?.solved ?? false;

  const advancedToggle = (
    <>
      <Divider />
      <Group onClick={() => setAdvancedOpen((o) => !o)} style={{ cursor: "pointer" }}>
        {advancedOpen ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
        <Text size="sm" fw={600}>
          Advanced
        </Text>
      </Group>
    </>
  );

  return (
    <Modal
      opened={puzzle !== null}
      onClose={onClose}
      title={puzzle ? (isSolved ? puzzle.name : `Solve — ${puzzle.name}`) : "Solve"}
      centered
    >
      {puzzle && (
        <Stack>
          {isSolved && (
            <Text size="sm" c="dimmed">
              Current solution: {puzzle.solution}
            </Text>
          )}

          {!isSolved ? (
            /* --- unsolved: Answer/Solve primary, rest behind Advanced --- */
            <Stack gap="sm">
              <TextInput
                label="Answer"
                description="Posts the SOLVED message to the channel and archives the sheet."
                value={fullText}
                onChange={(e) => setFullText(e.currentTarget.value)}
                onKeyDown={(e) => handleKey(e, "full")}
              />
              <Button color="green" onClick={() => submit("full")} loading={solve.isPending} disabled={!fullText.trim()}>
                Solve
              </Button>

              {advancedToggle}
              <Collapse in={advancedOpen}>
                <Stack gap="sm" pb="sm">
                  <Button color="green" variant="outline" onClick={markComplete} loading={solve.isPending}>
                    Mark complete (no answer)
                  </Button>

                  <TextInput
                    label="Partial solution (optional)"
                    description="Records a partial answer without fully solving the puzzle."
                    value={partialText}
                    onChange={(e) => setPartialText(e.currentTarget.value)}
                    onKeyDown={(e) => handleKey(e, "partial")}
                  />
                  <Button color="blue" variant="light" onClick={() => submit("partial")} loading={solve.isPending}>
                    Mark partial
                  </Button>
                </Stack>
              </Collapse>
            </Stack>
          ) : (
            /* --- solved: Mark unsolved primary, change/add behind Advanced --- */
            <Stack gap="sm">
              <Button color="red" onClick={unsolvePuzzle} loading={unsolve.isPending}>
                Mark unsolved
              </Button>
              <Text size="sm" c="dimmed">
                Clears the solution and moves the channel back to unsolved. You can re-solve it later.
              </Text>

              {advancedToggle}
              <Collapse in={advancedOpen}>
                <Stack gap="sm" pb="sm">
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
              </Collapse>
            </Stack>
          )}
        </Stack>
      )}
    </Modal>
  );
}
