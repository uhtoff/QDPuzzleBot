import { useMemo, useState } from "react";
import {
  ActionIcon,
  Badge,
  Checkbox,
  Group,
  Indicator,
  MultiSelect,
  Table,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { IconCircleCheck, IconNote, IconSearch } from "@tabler/icons-react";
import type { HuntDetail, Puzzle } from "../types";
import type { LiveActivity } from "../hooks/useActivityFeed";
import { useUpdatePuzzle } from "../api";
import { PuzzleLinks } from "./PuzzleLinks";
import { PriorityCell, SolveModal, StatusCell, TypeCell } from "./PuzzleEditCells";
import { priorityColor, relativeTime, statusColor } from "../util";

interface Props {
  detail: HuntDetail;
  puzzles: Puzzle[];
  live: Record<string, LiveActivity>;
  onOpenNotes: (puzzle: Puzzle) => void;
}

function uniqueValues(puzzles: Puzzle[], key: "status" | "priority" | "puzzle_type") {
  const set = new Set<string>();
  puzzles.forEach((p) => {
    if (p[key]) set.add(p[key] as string);
  });
  return Array.from(set).sort();
}

export function PuzzleTable({ detail, puzzles, live, onOpenNotes }: Props) {
  const [search, setSearch] = useState("");
  const [statuses, setStatuses] = useState<string[]>([]);
  const [priorities, setPriorities] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [hideSolved, setHideSolved] = useState(false);
  const [solvePuzzle, setSolvePuzzle] = useState<Puzzle | null>(null);
  const updateMut = useUpdatePuzzle(detail.hunt.uid);
  const editable = !detail.hunt.archived;

  const statusOptions = useMemo(() => uniqueValues(puzzles, "status"), [puzzles]);
  const priorityOptions = useMemo(() => uniqueValues(puzzles, "priority"), [puzzles]);
  const typeOptions = useMemo(() => uniqueValues(puzzles, "puzzle_type"), [puzzles]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return puzzles.filter((p) => {
      if (hideSolved && p.solved) return false;
      if (statuses.length && !statuses.includes(p.status || "")) return false;
      if (priorities.length && !priorities.includes(p.priority || "")) return false;
      if (types.length && !types.includes(p.puzzle_type || "")) return false;
      if (q) {
        const hay = `${p.name} ${p.solution ?? ""} ${p.rounds.map((r) => r.name).join(" ")}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [puzzles, search, statuses, priorities, types, hideSolved]);

  function activityFor(p: Puzzle): string {
    const liveHit = p.channel_id ? live[p.channel_id] : undefined;
    const at = liveHit?.at ?? p.last_activity?.at ?? null;
    return relativeTime(at);
  }

  return (
    <>
      <Group mb="md" align="flex-end" wrap="wrap">
        <TextInput
          label="Search"
          placeholder="Name, solution, round…"
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          w={240}
        />
        <MultiSelect
          label="Status"
          data={statusOptions}
          value={statuses}
          onChange={setStatuses}
          clearable
          w={180}
        />
        <MultiSelect
          label="Priority"
          data={priorityOptions}
          value={priorities}
          onChange={setPriorities}
          clearable
          w={150}
        />
        <MultiSelect
          label="Type"
          data={typeOptions}
          value={types}
          onChange={setTypes}
          clearable
          w={180}
        />
        <Checkbox
          label="Hide solved"
          checked={hideSolved}
          onChange={(e) => setHideSolved(e.currentTarget.checked)}
          mb={8}
        />
        <Text c="dimmed" size="sm" mb={8}>
          {filtered.length} / {puzzles.length}
        </Text>
      </Group>

      <Table.ScrollContainer minWidth={900}>
        <Table striped highlightOnHover stickyHeader>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={110}>Links</Table.Th>
              <Table.Th>Puzzle</Table.Th>
              <Table.Th>Round</Table.Th>
              <Table.Th>Solution</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Priority</Table.Th>
              <Table.Th>Type</Table.Th>
              <Table.Th>Activity</Table.Th>
              <Table.Th w={90}>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.map((p) => (
              <Table.Tr key={p.id} bg={p.solved ? "var(--mantine-color-green-light)" : undefined}>
                <Table.Td>
                  <PuzzleLinks
                    puzzle={p}
                    sheetBase={detail.hunt.sheet_base_url}
                    archiveSheetBase={detail.hunt.archive_sheet_base_url}
                  />
                </Table.Td>
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    {p.is_meta && <Badge size="xs" color="grape">META</Badge>}
                    <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c="dimmed">
                    {p.rounds.map((r) => r.name).join(", ") || "—"}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text ff="monospace" size="sm">
                    {p.solution || ""}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {editable ? (
                    <StatusCell
                      value={p.status}
                      onChange={(status) => updateMut.mutate({ puzzleId: p.id, patch: { status } })}
                    />
                  ) : p.status ? (
                    <Badge variant="light" color={statusColor(p.status)}>
                      {p.status}
                    </Badge>
                  ) : (
                    "—"
                  )}
                </Table.Td>
                <Table.Td>
                  {editable ? (
                    <PriorityCell
                      value={p.priority}
                      onChange={(priority) => updateMut.mutate({ puzzleId: p.id, patch: { priority } })}
                    />
                  ) : p.priority ? (
                    <Badge variant="dot" color={priorityColor(p.priority)}>
                      {p.priority}
                    </Badge>
                  ) : (
                    "—"
                  )}
                </Table.Td>
                <Table.Td>
                  {editable ? (
                    <TypeCell
                      value={p.puzzle_type}
                      onSave={(puzzle_type) => updateMut.mutate({ puzzleId: p.id, patch: { puzzle_type } })}
                    />
                  ) : (
                    <Text size="sm">{p.puzzle_type || "—"}</Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c="dimmed">
                    {activityFor(p)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    {editable && !p.solved && (
                      <Tooltip label="Mark solved">
                        <ActionIcon variant="subtle" color="green" onClick={() => setSolvePuzzle(p)} aria-label="Solve">
                          <IconCircleCheck size={17} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                    <Indicator label={p.notes.length} size={16} disabled={p.notes.length === 0} color="blue">
                      <ActionIcon variant="subtle" color="gray" onClick={() => onOpenNotes(p)} aria-label="Notes">
                        <IconNote size={16} />
                      </ActionIcon>
                    </Indicator>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      <SolveModal puzzle={solvePuzzle} uid={detail.hunt.uid} onClose={() => setSolvePuzzle(null)} />
    </>
  );
}
