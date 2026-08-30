import { Fragment, useEffect, useState } from "react";
import { useMiniExpansion } from "../hooks/useMiniExpansion";
import { useClipboard, useDisclosure, useMediaQuery } from "@mantine/hooks";
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
import { MetaBadge, getKind, kindColorVar } from "./MetaBadge";
import { MiniCountBadge } from "./MiniCountBadge";
import { MiniPuzzlePanel } from "./MiniPuzzlePanel";
import { SolveModal, StatusCell } from "./PuzzleEditCells";
import { PuzzleRowActions } from "./PuzzleRowActions";
import { formatStatus, fullDeletePuzzleCount, notifyError, relativeTime, sortPuzzles, statusColor } from "../util";
import { groupTarget } from "../lib/groupTarget";

interface Props {
  group: BoardGroupType;
  detail: HuntDetail;
  live: Record<string, LiveActivity>;
  onOpenNotes: (puzzle: Puzzle) => void;
}

export function BoardGroup({ group, detail, live, onOpenNotes }: Props) {
  const solvedAll = group.target_solved;
  const editable = !detail.hunt.archived;
  const isOrganiser = detail.hunt.can_manage;
  const [solvePuzzle, setSolvePuzzle] = useState<Puzzle | null>(null);
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
  const isNarrow = useMediaQuery("(max-width: 768px)") ?? false;
  // A plain group / Unassigned Feeders has no target, so no kind bar — only a
  // group with a target (meta/capstone/metameta/runaround) carries one, in the
  // target puzzle's own kind colour. Mirrors the Arrange board's group lines.
  const targetPuzzle = groupTarget(group);
  const groupKind = targetPuzzle ? getKind(targetPuzzle) : null;
  // Puzzles a full delete actually removes (excludes shared feeders and foreign
  // targets, which are kept & relocated) — the warning must not over-promise.
  const deleteCount = fullDeletePuzzleCount(group);

  // Reset the scope choice whenever the modal closes, so a reopen (this round
  // or another) starts from "keep" rather than a stale "all" selection.
  useEffect(() => {
    if (!deleteRoundOpen) setDeleteAllPuzzles(false);
  }, [deleteRoundOpen]);

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

  function activityFor(p: Puzzle): string {
    const at = (p.channel_id && live[p.channel_id]?.at) || p.last_activity?.at || null;
    return relativeTime(at);
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
        {group.tag_group_id !== 0 && isOrganiser && editable && (
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
            {sortPuzzles(group.puzzles, group.group_code).map((p) => {
              const bandProps = p.is_minimeta
                ? { onClick: () => toggleMinis(p.id), style: { cursor: "pointer" as const } }
                : {};
              const rowKind = getKind(p);
              return (
              <Fragment key={`${group.tag_group_id}-${p.id}`}>
                <Table.Tr bg={p.solved ? "var(--mantine-color-green-light)" : undefined}>
                  {/* Kind strip — both layouts */}
                  <Table.Td w={4} p={0} style={{ backgroundColor: rowKind ? kindColorVar(rowKind.color) : undefined }} />
                  {isNarrow ? (
                    <>
                      {/* Compact layout: links | title+solution | actions */}
                      <Table.Td w={92}>
                        <PuzzleLinks
                          puzzle={p}
                          sheetBase={detail.hunt.sheet_base_url}
                          solvedSheetBase={detail.hunt.solved_sheet_base_url}
                          huntArchived={detail.hunt.archived}
                        />
                      </Table.Td>
                      <Table.Td {...bandProps}>
                        <Group gap={6} wrap="nowrap">
                          <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
                          <MiniCountBadge puzzle={p} />
                        </Group>
                        <Text ff="monospace" size="xs" c={p.solved ? "green" : "dimmed"} mt={2}>
                          {p.solution || "—"}
                        </Text>
                      </Table.Td>
                      <PuzzleRowActions
                        puzzle={p}
                        editable={editable}
                        onSolve={setSolvePuzzle}
                        onUnsolve={setSolvePuzzle}
                        onOpenNotes={onOpenNotes}
                        tdWidth={80}
                      />
                    </>
                  ) : (
                    <>
                      {/* Wide layout: all original columns */}
                      <Table.Td w={110}>
                        <PuzzleLinks
                          puzzle={p}
                          sheetBase={detail.hunt.sheet_base_url}
                          solvedSheetBase={detail.hunt.solved_sheet_base_url}
                          huntArchived={detail.hunt.archived}
                        />
                      </Table.Td>
                      <Table.Td {...bandProps}>
                        <Group gap={6}>
                          <MetaBadge puzzle={p} />
                          <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
                          <MiniCountBadge puzzle={p} />
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
                            onChange={(status) =>
                              updateMut.mutate({ puzzleId: p.id, patch: { status } }, { onError: notifyError })
                            }
                          />
                        ) : (
                          p.status && (
                            <Badge variant="light" color={statusColor(p.status)}>
                              {formatStatus(p.status)}
                            </Badge>
                          )
                        )}
                      </Table.Td>
                      <Table.Td w={90}>
                        <Text size="xs" c="dimmed">
                          {activityFor(p)}
                        </Text>
                      </Table.Td>
                      <PuzzleRowActions
                        puzzle={p}
                        editable={editable}
                        onSolve={setSolvePuzzle}
                        onUnsolve={setSolvePuzzle}
                        onOpenNotes={onOpenNotes}
                        tdWidth={76}
                      />
                    </>
                  )}
                </Table.Tr>
                {p.is_minimeta && expandedMinis.has(p.id) && (
                  <Table.Tr>
                    <Table.Td colSpan={99} p={0}>
                      <MiniPuzzlePanel
                        parentId={p.id}
                        minis={p.mini_puzzles}
                        uid={detail.hunt.uid}
                        canManage={detail.hunt.can_manage}
                      />
                    </Table.Td>
                  </Table.Tr>
                )}
              </Fragment>
            );
            })}
          </Table.Tbody>
        </Table>

        <SolveModal puzzle={solvePuzzle} uid={detail.hunt.uid} onClose={() => setSolvePuzzle(null)} />

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

      <DeleteRoundModal
        opened={deleteRoundOpen}
        onClose={() => setDeleteRoundOpen(false)}
        onConfirm={runDeleteRound}
        name={group.name}
        noun="round"
        puzzleCount={deleteCount}
        loading={deleteRoundMut.isPending}
        deleteAllPuzzles={deleteAllPuzzles}
        onDeleteAllPuzzlesChange={setDeleteAllPuzzles}
      />
    </Accordion.Item>
  );
}
