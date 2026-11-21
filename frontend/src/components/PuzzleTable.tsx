import { Fragment, memo, useCallback, useMemo, useState } from "react";
import { useMiniExpansion } from "../hooks/useMiniExpansion";
import {
  Badge,
  Checkbox,
  Group,
  Menu,
  Table,
  Text,
} from "@mantine/core";
import type { HuntDetail, Puzzle, PuzzleTagGroup } from "../types";
import type { LiveActivity } from "../hooks/useActivityFeed";
import { latestActivity } from "../lib/sparkline";
import { ActivitySparkline } from "./ActivitySparkline";
import { useRetagPuzzle, useUpdatePuzzle, type EdgeType } from "../api";
import { PuzzleLinks } from "./PuzzleLinks";
import { VoiceBadge } from "./VoiceBadge";
import { puzzleVoiceCount, voiceRowBg, type LiveVoice } from "../lib/voice";
import { MetaBadge } from "./MetaBadge";
import { MiniCountBadge } from "./MiniCountBadge";
import { PriorityCell, StatusCell, TypeCell } from "./PuzzleEditCells";
import { MiniPuzzlePanel } from "./MiniPuzzlePanel";
import { PuzzleRowActions } from "./PuzzleRowActions";
import { answerLength, formatPriority, formatStatus, notifyError, priorityColor, relativeTime, sortPuzzles, statusColor } from "../util";
import { puzzleMatches, type PuzzleFilter } from "../lib/puzzleFilter";
import { PuzzleFilterBar } from "./PuzzleFilterBar";
import "./PuzzleTable.css";

interface Props {
  detail: HuntDetail;
  puzzles: Puzzle[];
  live: Record<string, LiveActivity>;
  voice: LiveVoice;
  onSolve: (puzzle: Puzzle) => void;
  onOpenNotes: (puzzle: Puzzle) => void;
  onOpenChat: (puzzle: Puzzle) => void;
  /** Shared with the "By group" view, so a search survives switching views. */
  filter: PuzzleFilter;
  onFilterChange: (next: PuzzleFilter) => void;
}

// Debug edge annotation: how this puzzle is tagged into a given group.
// T = target (this puzzle is the group's meta/capstone answer), F = feeds,
// I = informs, N = none (grab-bag member), ? = unknown/missing edge.
const EDGE_CODE: Record<string, string> = { feeds: "F", informs: "I", none: "N", target: "T" };
const EDGE_COLOR: Record<string, string> = {
  T: "grape",
  F: "blue",
  I: "teal",
  N: "gray",
  "?": "red",
};

function edgeCodes(r: PuzzleTagGroup): string[] {
  const codes = r.edges.map((e) => EDGE_CODE[e] ?? "?");
  // is_target_of is authoritative for target-ness even if the self-edge self-feeds.
  if (r.is_target_of && !codes.includes("T")) codes.unshift("T");
  return codes;
}

function edgeCode(r: PuzzleTagGroup): string {
  return edgeCodes(r).join("+") || "?";
}

function edgeColor(r: PuzzleTagGroup): string {
  const codes = edgeCodes(r);
  if (codes.length > 1) return "grape";
  return EDGE_COLOR[codes[0]] ?? "red";
}

function edgeTitle(r: PuzzleTagGroup): string {
  const title = r.edges.join(", ") || "no edge";
  return r.is_target_of ? `target · ${title}` : title;
}

// Edge choices offered in the debug-edges edit menu. A feeder picks how it
// contributes; a target toggles its own self-feed loop ('target' = no self-feed).
const FEEDER_TOGGLES: { edge: EdgeType; label: string }[] = [
  { edge: "feeds", label: "Feeds" },
  { edge: "informs", label: "Informs" },
];
const TARGET_EDGE_OPTS: { edge: EdgeType; label: string }[] = [
  { edge: "feeds", label: "Self-feeds" },
  { edge: "informs", label: "Self-informs" },
  { edge: "target", label: "No self-feed" },
];

// One debug-edge badge. Read-only unless `editable` (organiser) and the group has a
// code to retag against; then clicking opens a menu that re-pins this puzzle's edge.
function EdgeBadge({
  r,
  editable,
  onPick,
  onRemove,
}: {
  r: PuzzleTagGroup;
  editable: boolean;
  onPick: (edge: EdgeType) => void;
  onRemove: (edge?: EdgeType) => void;
}) {
  const [opened, setOpened] = useState(false);
  const canEdit = editable && !!r.group_code;
  const code = edgeCode(r);
  const badge = (
    <Badge
      size="xs"
      variant="light"
      color={edgeColor(r)}
      title={`group ${r.id} (${r.group_type ?? "?"}) · ${edgeTitle(r)}`}
      style={canEdit ? { cursor: "pointer" } : undefined}
      onClick={canEdit && !opened ? () => setOpened(true) : undefined}
    >
      {r.name} [{code}]
    </Badge>
  );
  if (!canEdit || !opened) return badge;
  return (
    <Menu opened={opened} onChange={setOpened} shadow="md" position="bottom-start" withinPortal>
      <Menu.Target>{badge}</Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>{r.name} tag</Menu.Label>
        {r.is_target_of
          ? TARGET_EDGE_OPTS.map((o) => (
              <Menu.Item
                key={o.edge}
                disabled={r.edges.includes(o.edge)}
                onClick={() => {
                  setOpened(false);
                  onPick(o.edge);
                }}
              >
                {o.label}
              </Menu.Item>
            ))
          : [
              ...FEEDER_TOGGLES.map((o) => {
                const on = r.edges.includes(o.edge);
                return (
                  <Menu.Item
                    key={o.edge}
                    leftSection={on ? "✓" : ""}
                    onClick={() => {
                      setOpened(false);
                      on ? onRemove(o.edge) : onPick(o.edge);
                    }}
                  >
                    {o.label}
                  </Menu.Item>
                );
              }),
              // 'none' is mutually exclusive with feeds/informs, so it's a mode you
              // switch to (not a toggle): picking it clears the contributing edges
              // and leaves the puzzle grouped-only. Switch back by picking Feeds/Informs.
              <Menu.Item
                key="none"
                leftSection={r.edges.includes("none") ? "✓" : ""}
                disabled={r.edges.includes("none")}
                onClick={() => {
                  setOpened(false);
                  onPick("none");
                }}
              >
                Group only
              </Menu.Item>,
            ]}
        {/* A target can't be removed from its own group (backend rejects it with
            cannot_remove_own_meta) — only offer removal for feeder memberships. */}
        {!r.is_target_of && (
          <>
            <Menu.Divider />
            <Menu.Item
              color="red"
              onClick={() => {
                setOpened(false);
                onRemove();
              }}
            >
              Remove from group
            </Menu.Item>
          </>
        )}
      </Menu.Dropdown>
    </Menu>
  );
}

interface TableRowProps {
  puzzle: Puzzle;
  /** Alternates per puzzle (see PuzzleTable.css). */
  striped: boolean;
  editable: boolean;
  canManage: boolean;
  debugEdges: boolean;
  huntUid: string;
  liveAt: string | undefined;
  voiceCount: number;
  minisExpanded: boolean;
  onToggleMinis: (puzzleId: number) => void;
  onUpdateField: (puzzleId: number, patch: Record<string, string>) => void;
  onRetagEdge: (puzzleId: number, metaCode: string, mode: "add" | "remove", edge?: EdgeType) => void;
  onSolve: (puzzle: Puzzle) => void;
  onOpenNotes: (puzzle: Puzzle) => void;
  onOpenChat: (puzzle: Puzzle) => void;
}

const PuzzleTableRow = memo(function PuzzleTableRow({
  puzzle: p,
  striped,
  editable,
  canManage,
  debugEdges,
  huntUid,
  liveAt,
  voiceCount,
  minisExpanded,
  onToggleMinis,
  onUpdateField,
  onRetagEdge,
  onSolve,
  onOpenNotes,
  onOpenChat,
}: TableRowProps) {
  const onStatusChange = useCallback(
    (status: string) => onUpdateField(p.id, { status }),
    [onUpdateField, p.id],
  );
  const onPriorityChange = useCallback(
    (priority: string) => onUpdateField(p.id, { priority }),
    [onUpdateField, p.id],
  );
  const onTypeSave = useCallback(
    (puzzle_type: string) => onUpdateField(p.id, { puzzle_type }),
    [onUpdateField, p.id],
  );
  const bandProps = p.is_minimeta
    ? { onClick: () => onToggleMinis(p.id), style: { cursor: "pointer" as const } }
    : {};
  const activityText = relativeTime(
    latestActivity(liveAt, p.last_activity?.at, p.activity?.last_sheet_edit_at),
  );

  return (
    <Fragment>
      <Table.Tr
        bg={voiceRowBg(p.solved, voiceCount)}
        className={striped ? "puzzle-row-striped" : undefined}
      >
        <Table.Td>
          <PuzzleLinks puzzle={p} onOpenChat={onOpenChat} />
        </Table.Td>
        <Table.Td {...bandProps}>
          <Group gap={6} wrap="nowrap">
            <MetaBadge puzzle={p} />
            <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
            <MiniCountBadge puzzle={p} />
            <VoiceBadge count={voiceCount} />
          </Group>
        </Table.Td>
        {debugEdges && (
          <Table.Td {...bandProps}>
            {p.rounds.length ? (
              <Group gap={4} wrap="wrap" onClick={(e) => e.stopPropagation()}>
                {p.rounds.map((r) => (
                  <EdgeBadge
                    key={r.id}
                    r={r}
                    editable={canManage}
                    onPick={(edge) => onRetagEdge(p.id, r.group_code as string, "add", edge)}
                    onRemove={(edge) => onRetagEdge(p.id, r.group_code as string, "remove", edge)}
                  />
                ))}
              </Group>
            ) : (
              <Text size="sm" c="dimmed">—</Text>
            )}
          </Table.Td>
        )}
        <Table.Td {...bandProps}>
          <Text ff="monospace" size="sm">
            {p.solution || ""}
          </Text>
        </Table.Td>
        <Table.Td ta="right" {...bandProps}>
          <Text size="sm" c="dimmed">
            {p.solution ? answerLength(p.solution) : ""}
          </Text>
        </Table.Td>
        <Table.Td>
          {editable ? (
            <StatusCell
              value={p.status}
              solved={p.solved}
              onChange={onStatusChange}
            />
          ) : p.status ? (
            <Badge variant="light" color={statusColor(p.status)}>
              {formatStatus(p.status)}
            </Badge>
          ) : (
            "—"
          )}
        </Table.Td>
        <Table.Td>
          {editable ? (
            <PriorityCell
              value={p.priority}
              onChange={onPriorityChange}
            />
          ) : p.priority ? (
            <Badge variant="dot" color={priorityColor(p.priority)}>
              {formatPriority(p.priority)}
            </Badge>
          ) : (
            "—"
          )}
        </Table.Td>
        <Table.Td>
          {editable ? (
            <TypeCell
              value={p.puzzle_type}
              onSave={onTypeSave}
            />
          ) : (
            <Text size="sm">{p.puzzle_type || "—"}</Text>
          )}
        </Table.Td>
        <Table.Td>
          <Group gap={6} wrap="nowrap">
            <ActivitySparkline activity={p.activity} />
            <Text size="sm" c="dimmed">
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
        />
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

export const PuzzleTable = memo(function PuzzleTable({
  detail,
  puzzles,
  live,
  voice,
  onSolve,
  onOpenNotes,
  onOpenChat,
  filter,
  onFilterChange,
}: Props) {
  const [debugEdges, setDebugEdges] = useState(false);
  const { expandedMinis, toggleMinis } = useMiniExpansion();
  const updateMut = useUpdatePuzzle(detail.hunt.uid);
  const retag = useRetagPuzzle(detail.hunt.uid);
  const editable = !detail.hunt.archived;
  // Debug edges (and the click-to-edit menu) are organiser-only; can_manage is
  // already false for archived hunts and matches the backend's require_organiser gate.
  const canManage = detail.hunt.can_manage;

  const { mutate: updateMutate } = updateMut;
  const onUpdateField = useCallback(
    (puzzleId: number, patch: Record<string, string>) =>
      updateMutate({ puzzleId, patch }, { onError: notifyError }),
    [updateMutate],
  );

  const { mutate: retagMutate } = retag;
  const onRetagEdge = useCallback(
    (puzzleId: number, metaCode: string, mode: "add" | "remove", edge?: EdgeType) =>
      retagMutate({ puzzleId, metaCode, mode, edge }, { onError: notifyError }),
    [retagMutate],
  );

  const filtered = useMemo(
    () => sortPuzzles(puzzles.filter((p) => puzzleMatches(p, filter))),
    [puzzles, filter],
  );

  return (
    <>
      <PuzzleFilterBar
        puzzles={puzzles}
        filter={filter}
        onChange={onFilterChange}
        shown={filtered.length}
        total={puzzles.length}
      >
        {canManage && (
          <Checkbox
            label="Debug tags"
            checked={debugEdges}
            onChange={(e) => setDebugEdges(e.currentTarget.checked)}
            mb={8}
          />
        )}
      </PuzzleFilterBar>

      <Table.ScrollContainer minWidth={900}>
        <Table highlightOnHover stickyHeader>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={110}>Links</Table.Th>
              <Table.Th>Puzzle</Table.Th>
              {debugEdges && <Table.Th>Group</Table.Th>}
              <Table.Th>Solution</Table.Th>
              <Table.Th w={50} ta="right">Len</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Priority</Table.Th>
              <Table.Th>Type</Table.Th>
              <Table.Th>Activity</Table.Th>
              <Table.Th w={90}>Actions</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.map((p, i) => (
              <PuzzleTableRow
                key={p.id}
                puzzle={p}
                striped={i % 2 === 0}
                editable={editable}
                canManage={canManage}
                debugEdges={debugEdges}
                huntUid={detail.hunt.uid}
                liveAt={p.channel_id ? live[p.channel_id]?.at : undefined}
                voiceCount={puzzleVoiceCount(p, voice)}
                minisExpanded={expandedMinis.has(p.id)}
                onToggleMinis={toggleMinis}
                onUpdateField={onUpdateField}
                onRetagEdge={onRetagEdge}
                onSolve={onSolve}
                onOpenNotes={onOpenNotes}
                onOpenChat={onOpenChat}
              />
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </>
  );
});
