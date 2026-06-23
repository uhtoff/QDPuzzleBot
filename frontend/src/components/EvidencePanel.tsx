import { useMemo, useState } from "react";
import {
  Box,
  Group,
  Select,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import type { EvidenceItem, HuntDetail } from "../types";
import { useEvidence, useUpdateEvidence } from "../api";

interface Props {
  uid: string;
  detail: HuntDetail;
}

export function EvidencePanel({ uid, detail }: Props) {
  const { data: items = [], isLoading, error } = useEvidence(uid);
  const updateEvidence = useUpdateEvidence(uid);
  const [filterSource, setFilterSource] = useState<string | null>(null);

  const allPuzzles = useMemo(() => {
    const seen = new Map<number, string>();
    detail.board.forEach((g) => g.puzzles.forEach((p) => seen.set(p.id, p.name)));
    return Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name }));
  }, [detail]);

  const sourceOptions = useMemo(() => {
    const seen = new Map<number, string>();
    items.forEach((i) => seen.set(i.source_puzzle_id, i.source_puzzle_name));
    return [{ value: "", label: "All sources" }, ...Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name }))];
  }, [items]);

  const filtered = filterSource
    ? items.filter((i) => String(i.source_puzzle_id) === filterSource)
    : items;

  if (isLoading) return <Text size="sm" c="dimmed">Loading evidence…</Text>;
  if (error) return <Text size="sm" c="red">Failed to load evidence.</Text>;

  return (
    <Stack gap="sm">
      <Group gap="sm">
        <Select
          size="xs"
          placeholder="Filter by source puzzle"
          data={sourceOptions}
          value={filterSource ?? ""}
          onChange={(v) => setFilterSource(v || null)}
          clearable
          w={240}
        />
        <Text size="xs" c="dimmed">{filtered.length} item{filtered.length !== 1 ? "s" : ""}</Text>
      </Group>

      {filtered.length === 0 ? (
        <Box p="md">
          <Text size="sm" c="dimmed">No clues logged yet. Use <code>!clue</code> or <code>!clues</code> in any puzzle channel.</Text>
        </Box>
      ) : (
        <Table striped highlightOnHover withTableBorder withColumnBorders fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Source Puzzle</Table.Th>
              <Table.Th>Note</Table.Th>
              <Table.Th>Target Puzzle</Table.Th>
              <Table.Th>Logged At</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.map((item) => (
              <EvidenceRow
                key={item.id}
                item={item}
                puzzleOptions={allPuzzles}
                onSetTarget={(targetId) =>
                  updateEvidence.mutate({ itemId: item.id, targetPuzzleId: targetId })
                }
              />
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

function EvidenceRow({
  item,
  puzzleOptions,
  onSetTarget,
}: {
  item: EvidenceItem;
  puzzleOptions: { value: string; label: string }[];
  onSetTarget: (targetId: number | null) => void;
}) {
  const loggedAt = item.created_at ? item.created_at.slice(0, 16).replace("T", " ") : "";

  return (
    <Table.Tr>
      <Table.Td>{item.source_puzzle_name}</Table.Td>
      <Table.Td>{item.note}</Table.Td>
      <Table.Td>
        <Select
          size="xs"
          placeholder="Assign target…"
          data={puzzleOptions}
          value={item.target_puzzle_id ? String(item.target_puzzle_id) : null}
          onChange={(v) => onSetTarget(v ? Number(v) : null)}
          searchable
          clearable
          w={200}
        />
      </Table.Td>
      <Table.Td>{loggedAt}</Table.Td>
    </Table.Tr>
  );
}
