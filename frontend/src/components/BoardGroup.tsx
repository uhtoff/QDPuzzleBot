import { useState } from "react";
import {
  Accordion,
  ActionIcon,
  Badge,
  Box,
  Group,
  Indicator,
  Modal,
  Select,
  Table,
  Text,
  Tooltip,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconNote,
  IconRefresh,
  IconTree,
} from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import type { BoardGroup as BoardGroupType, HuntDetail, Puzzle } from "../types";
import type { LiveActivity } from "../hooks/useActivityFeed";
import { useRebuildRollup, useSetSourceMode, useSetNesting, useUpdatePuzzle, type SourceMode } from "../api";
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
  const isOrganiser = detail.hunt.can_manage;
  const [solvePuzzle, setSolvePuzzle] = useState<Puzzle | null>(null);
  const [nestModalOpen, setNestModalOpen] = useState(false);
  const [nestParentId, setNestParentId] = useState<string|null>(null);
  // Collect other meta rounds for the nesting dropdown
  const allMetaRounds = detail.board.filter((g) => g.round_id !== 0 && g.round_id !== group.round_id);
  const updateMut = useUpdatePuzzle(detail.hunt.uid);
  const rebuildMut = useRebuildRollup(detail.hunt.uid);
  const sourceModeMut = useSetSourceMode(detail.hunt.uid);
  const nestingMut = useSetNesting(detail.hunt.uid);

  function rebuild() {
    rebuildMut.mutate(group.round_id, {
      onSuccess: (data) =>
        notifications.show(
          data.ok
            ? { message: `Rebuilt "${group.name}" sheet`, color: "green" }
            : { message: data.error || "Rebuild failed", color: "red" }
        ),
      onError: (e) => notifications.show({ message: (e as Error).message, color: "red" }),
    });
  }

  function setSourceMode(mode: string) {
    sourceModeMut.mutate({ roundId: group.round_id, mode: mode as SourceMode }, {
      onSuccess: (data) =>
        notifications.show(
          data.ok
            ? { message: `"${group.name}" → ${mode}`, color: "green" }
            : { message: "Failed to update source_mode", color: "red" }
        ),
      onError: (e) => notifications.show({ message: (e as Error).message, color: "red" }),
    });
  }

  function applyNesting() {
    const parentId = nestParentId ? Number(nestParentId) : null;
    nestingMut.mutate({ roundId: group.round_id, parentId }, {
      onSuccess: () => {
        notifications.show({
          message: parentId
            ? `“${group.name}” nested under “${allMetaRounds.find(g => g.round_id === parentId)?.name || parentId}”`
            : `“${group.name}” nesting cleared`,
          color: "green",
        });
        setNestModalOpen(false);
        setNestParentId(null);
      },
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
        {/* M7 source_mode selector */}
        {group.round_id !== 0 && editable && isOrganiser && (
          <Select
            data={[
              { value: "round", label: "round" },
              { value: "subtree", label: "subtree" },
              { value: "explicit", label: "explicit" },
            ]}
            value={group.source_mode ?? "round"}
            onChange={(v) => setSourceMode(v ?? "round")}
            mr="sm"
            size="xs"
            comboboxProps={{ withinPortal: false }}
            style={{ minWidth: 80 }}
          />
        )}
        {/* M7 nesting button (only for meta rounds) */}
        {group.round_id !== 0 && isOrganiser && editable && allMetaRounds.length > 0 && (
          <Tooltip label="Nest under another meta">
            <ActionIcon
              variant="subtle"
              color="grape"
              mr="sm"
              onClick={(e) => {
                e.stopPropagation();
                setNestModalOpen(true);
              }}
              aria-label="Nest this meta"
            >
              <IconTree size={16} />
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

        {/* Nesting modal */}
        <Modal
          opened={nestModalOpen}
          onClose={() => { setNestModalOpen(false); setNestParentId(null); }}
          title={`Nest "${group.name}" under…`}
        >
          <Select
            data={allMetaRounds.map((g) => ({
              value: String(g.round_id),
              label: `${g.name} (${g.meta_code ?? '—'})`,
            }))}
            placeholder="Pick parent round (or leave blank to clear)"
            value={nestParentId}
            onChange={setNestParentId}
            mr="md"
          />
          <Group mt="md" justify="right">
            <ActionIcon variant="subtle" color="red" onClick={() => { setNestParentId(null); applyNesting(); }}>
              Clear
            </ActionIcon>
            <ActionIcon variant="light" color="green" onClick={applyNesting} disabled={!nestParentId}>
              Apply
            </ActionIcon>
          </Group>
        </Modal>
      </Accordion.Panel>
    </Accordion.Item>
  );
}
