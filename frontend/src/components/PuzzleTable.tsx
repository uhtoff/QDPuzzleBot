import { useMemo, useState } from "react";
import {
  Badge,
  Checkbox,
  Group,
  MultiSelect,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import type { HuntDetail, Puzzle } from "../types";
import type { LiveActivity } from "../hooks/useActivityFeed";
import { PuzzleLinks } from "./PuzzleLinks";
import { priorityColor, relativeTime, statusColor } from "../util";

interface Props {
  detail: HuntDetail;
  puzzles: Puzzle[];
  live: Record<string, LiveActivity>;
}

function uniqueValues(puzzles: Puzzle[], key: "status" | "priority" | "puzzle_type") {
  const set = new Set<string>();
  puzzles.forEach((p) => {
    if (p[key]) set.add(p[key] as string);
  });
  return Array.from(set).sort();
}

export function PuzzleTable({ detail, puzzles, live }: Props) {
  const [search, setSearch] = useState("");
  const [statuses, setStatuses] = useState<string[]>([]);
  const [priorities, setPriorities] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [hideSolved, setHideSolved] = useState(false);

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
                  {p.status ? (
                    <Badge variant="light" color={statusColor(p.status)}>
                      {p.status}
                    </Badge>
                  ) : (
                    "—"
                  )}
                </Table.Td>
                <Table.Td>
                  {p.priority ? (
                    <Badge variant="dot" color={priorityColor(p.priority)}>
                      {p.priority}
                    </Badge>
                  ) : (
                    "—"
                  )}
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{p.puzzle_type || "—"}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c="dimmed">
                    {activityFor(p)}
                  </Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </>
  );
}
