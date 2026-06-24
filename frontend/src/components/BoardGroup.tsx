import { Fragment, useState } from "react";
import { useClipboard, useDisclosure } from "@mantine/hooks";
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
  IconCopy,
  IconNote,
  IconRefresh,
  IconTrash,
  IconTree,
} from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import type { BoardGroup as BoardGroupType, HuntDetail, Puzzle } from "../types";
import type { LiveActivity } from "../hooks/useActivityFeed";
import {
  useDeleteRound,
  useRebuildRollup,
  useRollupAnswers,
  useSetNesting,
  useSetSourceMode,
  useUpdatePuzzle,
  type SourceMode,
} from "../api";
import { TypedConfirmModal } from "./TypedConfirmModal";
import { PuzzleLinks } from "./PuzzleLinks";
import { MiniPuzzlePanel } from "./MiniPuzzlePanel";
import { SolveModal, StatusCell } from "./PuzzleEditCells";
import { notifyError, relativeTime, statusColor } from "../util";

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
  const [expandedMinis, setExpandedMinis] = useState<Set<number>>(new Set());
  const [nestModalOpen, setNestModalOpen] = useState(false);
  const [nestParentId, setNestParentId] = useState<string|null>(null);
  // Aggregated answers reveal
  const [answersOpened, { open: openAnswers, close }] = useDisclosure(false);
  const clipboard = useClipboard();
  const isMeta = !!group.meta_code;
  const answersQuery = useRollupAnswers(group.round_id, answersOpened && isMeta);
  // Collect other meta rounds for the nesting dropdown
  const allMetaRounds = detail.board.filter((g) => g.round_id !== 0 && g.round_id !== group.round_id);
  const updateMut = useUpdatePuzzle(detail.hunt.uid);
  const rebuildMut = useRebuildRollup(detail.hunt.uid);
  const sourceModeMut = useSetSourceMode(detail.hunt.uid);
  const nestingMut = useSetNesting(detail.hunt.uid);
  const deleteRoundMut = useDeleteRound(detail.hunt.uid);
  const [deleteRoundOpen, setDeleteRoundOpen] = useState(false);

  function rebuild() {
    rebuildMut.mutate(group.round_id, {
      onSuccess: (data) =>
        notifications.show(
          data.ok
            ? { message: `Rebuilt "${group.name}" sheet`, color: "green" }
            : { message: data.error || "Rebuild failed", color: "red" }
        ),
      onError: notifyError,
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
      onError: notifyError,
    });
  }

  function applyNesting() {
    const parentId = nestParentId ? Number(nestParentId) : null;
    nestingMut.mutate({ roundId: group.round_id, parentId }, {
      onSuccess: () => {
        notifications.show({
          message: parentId
            ? `"${group.name}" nested under "${allMetaRounds.find(g => g.round_id === parentId)?.name || parentId}"`
            : `"${group.name}" nesting cleared`,
          color: "green",
        });
        setNestModalOpen(false);
        setNestParentId(null);
      },
      onError: notifyError,
    });
  }

  function runDeleteRound() {
    deleteRoundMut.mutate(group.round_id, {
      onSuccess: (data) => {
        if (data.failed_channels && data.failed_channels.length > 0) {
          notifications.show({
            message: `Round "${group.name}" deleted. Could not delete channels for: ${data.failed_channels.join(", ")}.`,
            color: "yellow",
            autoClose: 10000,
          });
        } else {
          notifications.show({ message: `Round "${group.name}" permanently deleted.`, color: "green" });
        }
        setDeleteRoundOpen(false);
      },
      onError: notifyError,
    });
  }

  function copyAnswers() {
    if (!answersQuery.data) return;
    const rows = answersQuery.data.answers
      .filter((a) => a.solution && a.solution.trim().length > 0)
      .map((a) => `${a.name}\t${a.solution}`);
    if (rows.length === 0) {
      notifications.show({ message: "No solved answers to copy", color: "yellow" });
      return;
    }
    clipboard.copy(rows.join("\n"));
    notifications.show({ message: `Copied ${rows.length} answers`, color: "green" });
  }

  function toggleMinis(id: number) {
    setExpandedMinis((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
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
        {/* M7.5 aggregated answers toggle (only for meta rounds) */}
        {isMeta && (
          <Tooltip label={answersOpened ? "Hide aggregated answers" : "View aggregated answers"}>
            <ActionIcon
              variant="subtle"
              color="blue"
              mr="sm"
              onClick={(e) => {
                e.stopPropagation();
                answersOpened ? close() : openAnswers();
              }}
              aria-label={answersOpened ? "Hide aggregated answers" : "View aggregated answers"}
            >
              <IconCopy size={16} />
            </ActionIcon>
          </Tooltip>
        )}
        {/* Delete round button — organiser only, non-archived, real rounds only */}
        {group.round_id !== 0 && isOrganiser && editable && (
          <Tooltip label="Delete this round and all its puzzles">
            <ActionIcon
              variant="subtle"
              color="red"
              mr="sm"
              onClick={(e) => {
                e.stopPropagation();
                setDeleteRoundOpen(true);
              }}
              aria-label="Delete round"
            >
              <IconTrash size={16} />
            </ActionIcon>
          </Tooltip>
        )}
      </Box>
      <Accordion.Panel>
        <Table>
          <Table.Tbody>
            {group.puzzles.map((p) => (
              <Fragment key={`${group.round_id}-${p.id}`}>
              <Table.Tr bg={p.solved ? "var(--mantine-color-green-light)" : undefined}>
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
                    {p.puzzle_type === "Minimeta" && (
                      <Tooltip label={`${p.mini_puzzles.filter((m) => m.solved).length}/${p.mini_puzzles.length} minis solved`}>
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          onClick={() => toggleMinis(p.id)}
                          aria-label="Toggle minis"
                        >
                          <Badge color="violet" variant="light">
                            {p.mini_puzzles.filter((m) => m.solved).length}/{p.mini_puzzles.length}
                          </Badge>
                        </ActionIcon>
                      </Tooltip>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
              {p.puzzle_type === "Minimeta" && expandedMinis.has(p.id) && (
                <Table.Tr>
                  <Table.Td colSpan={99} p={0}>
                    <MiniPuzzlePanel parentId={p.id} minis={p.mini_puzzles} uid={detail.hunt.uid} />
                  </Table.Td>
                </Table.Tr>
              )}
              </Fragment>
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

        {/* M7.5 aggregated answers panel (only for meta rounds) */}
        {isMeta && answersOpened && (
          <Box mt="sm" p="sm" style={{ backgroundColor: "var(--mantine-color-gray-light)", borderRadius: "md" }}>
            <Group justify="space-between" mb="xs">
              <Text size="sm" fw={600}>
                Aggregated answers
                {answersQuery.data && (
                  <Text component="span" c="dimmed" fw={400} ml="xs">
                    mode: {answersQuery.data.mode} · {answersQuery.data.answers.length} answers
                  </Text>
                )}
              </Text>
              <Tooltip label="Copy to clipboard">
                <ActionIcon
                  variant="light"
                  color="blue"
                  onClick={copyAnswers}
                  disabled={!answersQuery.data}
                  aria-label="Copy answers"
                >
                  {clipboard.copied ? <IconCircleCheck size={14} /> : <IconCopy size={14} />}
                </ActionIcon>
              </Tooltip>
            </Group>
            {answersQuery.data && (
              <Table>
                <Table.Tbody>
                  {answersQuery.data.answers.map((a) => (
                    <Table.Tr key={a.name}>
                      <Table.Td><Text size="xs" fw={500}>{a.name}</Text></Table.Td>
                      <Table.Td><Text ff="monospace" size="xs">{a.solution || ""}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            )}
          </Box>
        )}
      </Accordion.Panel>

      <TypedConfirmModal
        opened={deleteRoundOpen}
        onClose={() => setDeleteRoundOpen(false)}
        onConfirm={runDeleteRound}
        title={`Delete round "${group.name}"`}
        confirmPhrase={group.name}
        confirmLabel="Delete round"
        loading={deleteRoundMut.isPending}
      >
        <Text size="sm" c="red">
          ⚠️ This permanently deletes <b>{group.num_feeders} puzzle{group.num_feeders === 1 ? "" : "s"}</b>,
          their Discord channels and Google sheet tabs, and the round&rsquo;s Discord category.
          This cannot be undone.
        </Text>
      </TypedConfirmModal>
    </Accordion.Item>
  );
}
