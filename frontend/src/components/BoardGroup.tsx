import { useState } from "react";
import { Accordion, ActionIcon, Badge, Box, Group, Indicator, Table, Text, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconCircleCheck, IconNote, IconRefresh } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import type { BoardGroup as BoardGroupType, HuntDetail, Puzzle } from "../types";
import type { LiveActivity } from "../hooks/useActivityFeed";
import { useRebuildRollup, useUpdatePuzzle } from "../api";
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
  const rebuildMut = useRebuildRollup(detail.hunt.uid);

  function rebuild() {
    rebuildMut.mutate(group.round_id, {
      onSuccess: (data) =>
        notifications.show(
          data.ok
            ? { message: `Rebuilt “${group.name}” sheet`, color: "green" }
            : { message: data.error || "Rebuild failed", color: "red" }
        ),
      onError: (e) => notifications.show({ message: (e as Error).message, color: "red" }),
    });
  }

  function activityFor(p: Puzzle): string {
    const at = (p.channel_id && live[p.channel_id]?.at) || p.last_activity?.at || null;
    return relativeTime(at);
  }

  return (
    <Accordion.Item value={String(group.round_id)}>
      <Box style={{ display: "flex", alignItems: "center" }}>
        <Accordion.Control
          style={{ flex: 1 }}
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
            {group.rollup_stale && (
              <Tooltip label={group.rollup_error || "This meta's sheet is out of date"} multiline maw={320}>
                <Badge color="red" variant="light" size="sm" leftSection={<IconAlertTriangle size={12} />}>
                  sheet stale
                </Badge>
              </Tooltip>
            )}
            <Text c="dimmed" size="sm">
              {group.num_solved_feeders}/{group.num_feeders} solved
            </Text>
          </Group>
        </Accordion.Control>
        {group.rollup_stale && editable && detail.hunt.can_manage && (
          <Tooltip label="Rebuild this meta sheet">
            <ActionIcon
              variant="subtle"
              color="red"
              mr="sm"
              loading={rebuildMut.isPending}
              onClick={(e) => {
                e.stopPropagation();
                rebuild();
              }}
              aria-label="Rebuild meta sheet"
            >
              <IconRefresh size={16} />
            </ActionIcon>
          </Tooltip>
        )}
      </Box>
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
