import { useState } from "react";
import { Badge, Button, Group, Menu, Modal, Popover, Stack, Text, TextInput } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import type { Puzzle } from "../types";
import { useSolvePuzzle } from "../api";
import { priorityColor, statusColor } from "../util";

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

/** Modal to enter a solution and mark a puzzle solved (posts the SOLVED message to Discord). */
export function SolveModal({ puzzle, uid, onClose }: { puzzle: Puzzle | null; uid: string; onClose: () => void }) {
  const [solution, setSolution] = useState("");
  const solve = useSolvePuzzle(uid);

  function submit() {
    const value = solution.trim();
    if (!value || !puzzle) return;
    solve.mutate(
      { puzzleId: puzzle.id, solution: value },
      {
        onSuccess: () => {
          notifications.show({ message: `Marked “${puzzle.name}” solved`, color: "green" });
          setSolution("");
          onClose();
        },
        onError: (e) => notifications.show({ message: (e as Error).message, color: "red" }),
      }
    );
  }

  return (
    <Modal opened={puzzle !== null} onClose={onClose} title={puzzle ? `Solve — ${puzzle.name}` : "Solve"} centered>
      {puzzle && (
        <Stack>
          <TextInput
            label="Solution"
            description="Posts the SOLVED message to the channel and archives the sheet."
            value={solution}
            onChange={(e) => setSolution(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            data-autofocus
          />
          <Button color="green" onClick={submit} loading={solve.isPending} disabled={!solution.trim()}>
            Mark solved
          </Button>
        </Stack>
      )}
    </Modal>
  );
}
