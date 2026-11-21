import { Fragment, memo, useCallback, useEffect, useMemo, useState } from "react";
import { useMiniExpansion } from "../hooks/useMiniExpansion";
import { useLingering } from "../hooks/useLingering";
import { useClipboard, useDisclosure } from "@mantine/hooks";
import {
  Accordion,
  ActionIcon,
  Badge,
  Box,
  Group,
  Table,
  Text,
  Tooltip,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconBrowserPlus,
  IconCopy,
  IconRefresh,
  IconTrash,
} from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import type { BoardGroup as BoardGroupType, HuntDetail, Puzzle } from "../types";
import type { LiveActivity } from "../hooks/useActivityFeed";
import {
  useDeleteRound,
  useRebuildRollup,
  useRollupAnswers,
  useUpdatePuzzle,
} from "../api";
import { DeleteRoundModal } from "./DeleteRoundModal";
import { PuzzleLinks } from "./PuzzleLinks";
import { VoiceBadge } from "./VoiceBadge";
import { puzzleVoiceCount, voiceRowBg, type LiveVoice } from "../lib/voice";
import { MetaBadge, getKind, kindColorVar } from "./MetaBadge";
import { MiniCountBadge } from "./MiniCountBadge";
import { MiniPuzzlePanel } from "./MiniPuzzlePanel";
import { StatusCell } from "./PuzzleEditCells";
import { PuzzleRowActions } from "./PuzzleRowActions";
import { formatStatus, fullDeletePuzzleCount, notifyError, relativeTime, sortPuzzles, statusColor } from "../util";
import { extensionSupports, openRound } from "../lib/extension";
import { groupTarget } from "../lib/groupTarget";
import { latestActivity } from "../lib/sparkline";
import { ActivitySparkline } from "./ActivitySparkline";

interface Props {
  group: BoardGroupType;
  detail: HuntDetail;
  live: Record<string, LiveActivity>;
  voice: LiveVoice;
  isOpen: boolean;
  isNarrow: boolean;
  onSolve: (puzzle: Puzzle) => void;
  onOpenNotes: (puzzle: Puzzle) => void;
  onOpenChat: (puzzle: Puzzle) => void;
  /** The rows to show while filtering (default: all). Only the rows: the
   *  header's counts and "Open round as a tab group" still cover the whole group. */
  rows?: Puzzle[];
}

interface RowProps {
  puzzle: Puzzle;
  isNarrow: boolean;
  editable: boolean;
  huntUid: string;
  canManage: boolean;
  liveAt: string | undefined;
  voiceCount: number;
  minisExpanded: boolean;
  onToggleMinis: (puzzleId: number) => void;
  onStatusChange: (puzzleId: number, status: string) => void;
  onSolve: (puzzle: Puzzle) => void;
  onOpenNotes: (puzzle: Puzzle) => void;
  onOpenChat: (puzzle: Puzzle) => void;
}

const BoardPuzzleRow = memo(function BoardPuzzleRow({
  puzzle: p,
  isNarrow,
  editable,
  huntUid,
  canManage,
  liveAt,
  voiceCount,
  minisExpanded,
  onToggleMinis,
  onStatusChange,
  onSolve,
  onOpenNotes,
  onOpenChat,
}: RowProps) {
  const handleStatusChange = useCallback(
    (status: string) => onStatusChange(p.id, status),
    [onStatusChange, p.id],
  );
  const bandProps = p.is_minimeta
    ? { onClick: () => onToggleMinis(p.id), style: { cursor: "pointer" as const } }
    : {};
  const rowKind = getKind(p);
  const activityText = relativeTime(
    latestActivity(liveAt, p.last_activity?.at, p.activity?.last_sheet_edit_at),
  );

  return (
    <Fragment>
      <Table.Tr bg={voiceRowBg(p.solved, voiceCount)}>
        {/* Kind strip — both layouts */}
        <Table.Td w={4} p={0} style={{ backgroundColor: rowKind ? kindColorVar(rowKind.color) : undefined }} />
        {isNarrow ? (
          <>
            {/* Compact layout: links | title+solution | actions */}
            <Table.Td w={92}>
              <PuzzleLinks puzzle={p} onOpenChat={onOpenChat} />
            </Table.Td>
            <Table.Td {...bandProps}>
              <Group gap={6} wrap="nowrap">
                <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
                <MiniCountBadge puzzle={p} />
                <VoiceBadge count={voiceCount} />
              </Group>
              <Text ff="monospace" size="xs" c={p.solved ? "green" : "dimmed"} mt={2}>
                {p.solution || "—"}
              </Text>
            </Table.Td>
            <PuzzleRowActions
              puzzle={p}
              editable={editable}
              onSolve={onSolve}
              onUnsolve={onSolve}
              onOpenNotes={onOpenNotes}
              huntUid={huntUid}
              tdWidth={80}
            />
          </>
        ) : (
          <>
            {/* Wide layout: all original columns */}
            <Table.Td w={110}>
              <PuzzleLinks puzzle={p} onOpenChat={onOpenChat} />
            </Table.Td>
            <Table.Td {...bandProps}>
              <Group gap={6}>
                <MetaBadge puzzle={p} />
                <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
                <MiniCountBadge puzzle={p} />
                <VoiceBadge count={voiceCount} />
              </Group>
            </Table.Td>
            <Table.Td {...bandProps}>
              <Text ff="monospace" size="sm">
                {p.solution || ""}
              </Text>
            </Table.Td>
            <Table.Td w={130}>
              {editable ? (
                <StatusCell
                  value={p.status}
                  solved={p.solved}
                  onChange={handleStatusChange}
                />
              ) : (
                p.status && (
                  <Badge variant="light" color={statusColor(p.status)}>
                    {formatStatus(p.status)}
                  </Badge>
                )
              )}
            </Table.Td>
            <Table.Td w={160}>
              <Group gap={6} wrap="nowrap">
                <ActivitySparkline activity={p.activity} />
                <Text size="xs" c="dimmed">
                  {activityText}
                </Text>
              </Group>
            </Table.Td>
            <PuzzleRowActions
              puzzle={p}
              editable={editable}
              onSolve={onSolve}
              onUnsolve={onSolve}
              onOpenNotes={onOpenNotes}
              huntUid={huntUid}
              tdWidth={76}
            />
          </>
        )}
      </Table.Tr>
      {p.is_minimeta && minisExpanded && (
        <Table.Tr>
          <Table.Td colSpan={99} p={0}>
            <MiniPuzzlePanel
              parentId={p.id}
              minis={p.mini_puzzles}
              uid={huntUid}
              canManage={canManage}
            />
          </Table.Td>
        </Table.Tr>
      )}
    </Fragment>
  );
});

export const BoardGroup = memo(function BoardGroup({
  group,
  detail,
  live,
  voice,
  isOpen,
  isNarrow,
  onSolve,
  onOpenNotes,
  onOpenChat,
  rows,
}: Props) {
  const solvedAll = group.target_solved;
  const editable = !detail.hunt.archived;
  const isOrganiser = detail.hunt.can_manage;
  const { expandedMinis, toggleMinis } = useMiniExpansion();
  // Aggregated answers reveal
  const [answersOpened, { open: openAnswers, close }] = useDisclosure(false);
  const clipboard = useClipboard();
  const isMeta = !!group.group_code;
  const answersQuery = useRollupAnswers(group.tag_group_id, answersOpened && isMeta);
  const updateMut = useUpdatePuzzle(detail.hunt.uid);
  const rebuildMut = useRebuildRollup(detail.hunt.uid);
  const deleteRoundMut = useDeleteRound(detail.hunt.uid);
  const [deleteRoundOpen, setDeleteRoundOpen] = useState(false);
  const [deleteAllPuzzles, setDeleteAllPuzzles] = useState(false);
  // Mounted only while in use, but kept through its close fade.
  const deleteModal = useLingering(deleteRoundOpen ? true : null);
  const deleteMounted = deleteModal.value != null;
  // Rows are skipped while the group is collapsed, but must outlive the
  // Accordion's collapse animation or it shrinks an already-empty panel.
  const showRows = useLingering(isOpen ? true : null, 250).value != null;
  // A plain group / Unassigned Feeders has no target, so no kind bar — only a
  // group with a target (meta/capstone/metameta/runaround) carries one, in the
  // target puzzle's own kind colour. Mirrors the Arrange board's group lines.
  const targetPuzzle = groupTarget(group);
  const groupKind = targetPuzzle ? getKind(targetPuzzle) : null;
  // Puzzles a full delete actually removes (excludes shared feeders and foreign
  // targets, which are kept & relocated) — the warning must not over-promise.
  const deleteCount = useMemo(
    () => (deleteMounted ? fullDeletePuzzleCount(group) : 0),
    [deleteMounted, group],
  );

  // Reset the scope choice once the modal has gone, so a reopen (this round
  // or another) starts from "keep" rather than a stale "all" selection.
  useEffect(() => {
    if (!deleteMounted) setDeleteAllPuzzles(false);
  }, [deleteMounted]);

  const { mutate: updateMutate } = updateMut;
  const onStatusChange = useCallback(
    (puzzleId: number, status: string) =>
      updateMutate({ puzzleId, patch: { status } }, { onError: notifyError }),
    [updateMutate],
  );

  const sortedRows = useMemo(
    () => (showRows ? sortPuzzles(rows ?? group.puzzles, group.group_code) : []),
    [showRows, rows, group.puzzles, group.group_code],
  );

  function rebuild() {
    rebuildMut.mutate(group.tag_group_id, {
      onSuccess: (data) =>
        notifications.show(
          data.ok
            ? { message: `Rebuilt "${group.name}" sheet`, color: "green" }
            : { message: data.error || "Rebuild failed", color: "red" }
        ),
      onError: notifyError,
    });
  }

  function runDeleteRound() {
    deleteRoundMut.mutate(
      { roundId: group.tag_group_id, keepPuzzles: !deleteAllPuzzles },
      {
        onSuccess: (data) => {
          if (data.failed_channels && data.failed_channels.length > 0) {
            notifications.show({
              message: `Round "${group.name}" deleted. Could not delete/relocate channels for: ${data.failed_channels.join(", ")}.`,
              color: "yellow",
              autoClose: 10000,
            });
          } else {
            notifications.show({ message: `Round "${group.name}" permanently deleted.`, color: "green" });
          }
          setDeleteRoundOpen(false);
        },
        onError: notifyError,
      },
    );
  }

  function copyAnswers() {
    if (!answersQuery.data) return;
    const answerRows = answersQuery.data.answers
      .filter((a) => a.solution && a.solution.trim().length > 0)
      .map((a) => `${a.name}\t${a.solution}`);
    if (answerRows.length === 0) {
      notifications.show({ message: "No solved answers to copy", color: "yellow" });
      return;
    }
    clipboard.copy(answerRows.join("\n"));
    notifications.show({ message: `Copied ${answerRows.length} answers`, color: "green" });
  }

  return (
    <Accordion.Item
      value={String(group.tag_group_id)}
      style={{
        // Same full-height kind line as an Arrange group Paper: on the whole
        // accordion item, so it runs down the expanded puzzle list too — not
        // just a header-row stub.
        borderLeft: groupKind ? `4px solid ${kindColorVar(groupKind.color)}` : undefined,
      }}
    >
      <Box style={{ display: "flex", alignItems: "center" }}>
        <Accordion.Control
          style={{ flex: 1 }}
          styles={solvedAll ? { control: { backgroundColor: "var(--mantine-color-green-light)" } } : undefined}
        >
          <Group gap="sm">
            <Text fw={600}>{group.name}</Text>
            {group.group_code && (
              <Badge variant="light" color="grape" size="sm">
                {group.group_code}
              </Badge>
            )}
            {group.target_solved && group.target_solution && (
              <Badge color="green" variant="filled" size="sm">
                {group.target_solution}
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
              {group.num_solved_feeders}/{group.num_feeders} feeders solved
            </Text>
          </Group>
        </Accordion.Control>
        {group.rollup_stale && editable && detail.hunt.can_manage && (
          <ActionIcon
            variant="subtle"
            color="red"
            mr="sm"
            loading={rebuildMut.isPending}
            onClick={(e) => {
              e.stopPropagation();
              rebuild();
            }}
            data-tip="Rebuild this meta sheet"
            aria-label="Rebuild meta sheet"
          >
            <IconRefresh size={16} />
          </ActionIcon>
        )}
        {/* Extension: open every puzzle sheet of this round as a named tab group */}
        {extensionSupports("openRound") && detail.hunt.sheet_mode === "files" && group.puzzles.some((p) => p.sheet_url) && (
          <ActionIcon
            variant="subtle"
            color="blue"
            mr="sm"
            onClick={(e) => {
              e.stopPropagation();
              openRound(
                group.name,
                group.puzzles.flatMap((p) => (p.sheet_url ? [p.sheet_url] : [])),
              );
            }}
            data-tip="Open round as a tab group"
            aria-label="Open round as a tab group"
          >
            <IconBrowserPlus size={16} />
          </ActionIcon>
        )}
        {/* M7.5 aggregated answers toggle (only for meta rounds) */}
        {isMeta && (
          <ActionIcon
            variant="subtle"
            color="blue"
            mr="sm"
            onClick={(e) => {
              e.stopPropagation();
              answersOpened ? close() : openAnswers();
            }}
            data-tip={answersOpened ? "Hide aggregated answers" : "View aggregated answers"}
            aria-label={answersOpened ? "Hide aggregated answers" : "View aggregated answers"}
          >
            <IconCopy size={16} />
          </ActionIcon>
        )}
        {/* Delete round button — organiser only, non-archived, real rounds only */}
        {group.tag_group_id !== 0 && isOrganiser && editable && (
          <ActionIcon
            variant="subtle"
            color="red"
            mr="sm"
            onClick={(e) => {
              e.stopPropagation();
              setDeleteRoundOpen(true);
            }}
            data-tip="Delete this round…"
            aria-label="Delete round"
          >
            <IconTrash size={16} />
          </ActionIcon>
        )}
      </Box>
      <Accordion.Panel>
        {showRows && (
          <>
            <Table>
              <Table.Tbody>
                {sortedRows.map((p) => (
                  <BoardPuzzleRow
                    key={`${group.tag_group_id}-${p.id}`}
                    puzzle={p}
                    isNarrow={isNarrow}
                    editable={editable}
                    huntUid={detail.hunt.uid}
                    canManage={detail.hunt.can_manage}
                    liveAt={p.channel_id ? live[p.channel_id]?.at : undefined}
                    voiceCount={puzzleVoiceCount(p, voice)}
                    minisExpanded={expandedMinis.has(p.id)}
                    onToggleMinis={toggleMinis}
                    onStatusChange={onStatusChange}
                    onSolve={onSolve}
                    onOpenNotes={onOpenNotes}
                    onOpenChat={onOpenChat}
                  />
                ))}
              </Table.Tbody>
            </Table>

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
                  <ActionIcon
                    variant="light"
                    color="blue"
                    onClick={copyAnswers}
                    disabled={!answersQuery.data}
                    data-tip="Copy to clipboard"
                    aria-label="Copy answers"
                  >
                    {clipboard.copied ? <IconCircleCheck size={14} /> : <IconCopy size={14} />}
                  </ActionIcon>
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
          </>
        )}
      </Accordion.Panel>

      {deleteMounted && (
        <DeleteRoundModal
          opened={deleteModal.opened}
          onClose={() => setDeleteRoundOpen(false)}
          onConfirm={runDeleteRound}
          name={group.name}
          noun="round"
          puzzleCount={deleteCount}
          loading={deleteRoundMut.isPending}
          deleteAllPuzzles={deleteAllPuzzles}
          onDeleteAllPuzzlesChange={setDeleteAllPuzzles}
        />
      )}
    </Accordion.Item>
  );
});
