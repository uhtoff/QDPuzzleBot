import { useState } from "react";
import { Accordion, ActionIcon, Badge, Group, Indicator, Table, Text, Tooltip } from "@mantine/core";
import { IconCircleCheck, IconNote } from "@tabler/icons-react";
import type { BoardGroup as BoardGroupType, HuntDetail, Puzzle } from "../types";
import type { LiveActivity } from "../hooks/useActivityFeed";
import { useUpdatePuzzle } from "../api";
import { PuzzleLinks } from "./PuzzleLinks";
import { SolveModal, StatusCell } from "./PuzzleEditCells";
import { relativeTime, statusColor } from "../util";

interface Props {
  group: BoardGroupType;
  detail: HuntDetail;
  live: Record<string, LiveActivity>;
  onOpenNotes: (puzzle: Puzzle) => void;
}

export function BoardGroup({ group, detail, live, onOpenNotes }: Props) {
  const solvedAll = group.meta_solved || (group.num_feeders > 0 && group.num_solved_feeders === group.num_feeders);
  const editable = !detail.hunt.archived;
  const [solvePuzzle, setSolvePuzzle] = useState<Puzzle | null>(null);
  const updateMut = useUpdatePuzzle(detail.hunt.uid);

  function activityFor(p: Puzzle): string {
    const at = (p.channel_id && live[p.channel_id]?.at) || p.last_activity?.at || null;
    return relativeTime(at);
  }

  return (
    <Accordion.Item value={String(group.round_id)}>
      <Accordion.Control
        styles={solvedAll ? { control: { backgroundColor: "var(--mantine-color-green-light)" } } : undefined}
      >
        <Group gap="sm">
          <Text fw={600}>{group.name}</Text>
          {group.meta_code && (
            <Badge variant="light" color="grape" size="sm">
              {group.meta_code}
            </Badge>
          )}
          {group.meta_solved && group.meta_solution && (
            <Badge color="green" variant="filled" size="sm">
              {group.meta_solution}
            </Badge>
          )}
          <Text c="dimmed" size="sm">
            {group.num_solved_feeders}/{group.num_feeders} solved
          </Text>
        </Group>
      </Accordion.Control>
      <Accordion.Panel>
        <Table>
          <Table.Tbody>
            {group.puzzles.map((p) => (
              <Table.Tr key={`${group.round_id}-${p.id}`} bg={p.solved ? "var(--mantine-color-green-light)" : undefined}>
                <Table.Td w={110}>
                  <PuzzleLinks
                    puzzle={p}
                    sheetBase={detail.hunt.sheet_base_url}
                    archiveSheetBase={detail.hunt.archive_sheet_base_url}
                  />
                </Table.Td>
                <Table.Td>
                  <Group gap={6}>
                    {p.is_meta && <Badge size="xs" color="grape">META</Badge>}
                    <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Text ff="monospace" size="sm">
                    {p.solution || ""}
                  </Text>
                </Table.Td>
                <Table.Td w={130}>
                  {editable ? (
                    <StatusCell
                      value={p.status}
                      onChange={(status) => updateMut.mutate({ puzzleId: p.id, patch: { status } })}
                    />
                  ) : (
                    p.status && (
                      <Badge variant="light" color={statusColor(p.status)}>
                        {p.status}
                      </Badge>
                    )
                  )}
                </Table.Td>
                <Table.Td w={90}>
                  <Text size="xs" c="dimmed">
                    {activityFor(p)}
                  </Text>
                </Table.Td>
                <Table.Td w={76}>
                  <Group gap={2} wrap="nowrap">
                    {editable && !p.solved && (
                      <Tooltip label="Mark solved">
                        <ActionIcon variant="subtle" color="green" onClick={() => setSolvePuzzle(p)} aria-label="Solve">
                          <IconCircleCheck size={16} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                    <Indicator label={p.notes.length} size={16} disabled={p.notes.length === 0} color="blue">
                      <ActionIcon variant="subtle" color="gray" onClick={() => onOpenNotes(p)} aria-label="Details">
                        <IconNote size={15} />
                      </ActionIcon>
                    </Indicator>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>

        <SolveModal puzzle={solvePuzzle} uid={detail.hunt.uid} onClose={() => setSolvePuzzle(null)} />
      </Accordion.Panel>
    </Accordion.Item>
  );
}
