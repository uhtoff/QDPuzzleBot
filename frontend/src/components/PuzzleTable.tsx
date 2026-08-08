import { useMemo, useState } from "react";
import { useMiniExpansion } from "../hooks/useMiniExpansion";
import {
  Badge,
  Checkbox,
  Group,
  Menu,
  MultiSelect,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import type { HuntDetail, Puzzle, PuzzleTagGroup } from "../types";
import type { LiveActivity } from "../hooks/useActivityFeed";
import { useRetagPuzzle, useUpdatePuzzle, type EdgeType } from "../api";
import { PuzzleLinks } from "./PuzzleLinks";
import { MetaBadge } from "./MetaBadge";
import { MiniCountBadge } from "./MiniCountBadge";
import { PriorityCell, SolveModal, StatusCell, TypeCell } from "./PuzzleEditCells";
import { MiniPuzzlePanel } from "./MiniPuzzlePanel";
import { PuzzleRowActions } from "./PuzzleRowActions";
import { answerLength, formatPriority, formatStatus, notifyError, priorityColor, relativeTime, sortPuzzles, statusColor } from "../util";

interface Props {
  detail: HuntDetail;
  puzzles: Puzzle[];
  live: Record<string, LiveActivity>;
  onOpenNotes: (puzzle: Puzzle) => void;
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
  const code = edgeCode(r);
  const badge = (
    <Badge
      size="xs"
      variant="light"
      color={edgeColor(r)}
      title={`group ${r.id} (${r.group_type ?? "?"}) · ${edgeTitle(r)}`}
      style={editable && r.group_code ? { cursor: "pointer" } : undefined}
    >
      {r.name} [{code}]
    </Badge>
  );
  if (!editable || !r.group_code) return badge;
  return (
    <Menu shadow="md" position="bottom-start" withinPortal>
      <Menu.Target>{badge}</Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>{r.name} tag</Menu.Label>
        {r.is_target_of
          ? TARGET_EDGE_OPTS.map((o) => (
              <Menu.Item
                key={o.edge}
                disabled={r.edges.includes(o.edge)}
                onClick={() => onPick(o.edge)}
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
                    onClick={() => (on ? onRemove(o.edge) : onPick(o.edge))}
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
                onClick={() => onPick("none")}
              >
                Group only
              </Menu.Item>,
            ]}
        {/* A target can't be removed from its own group (backend rejects it with
            cannot_remove_own_meta) — only offer removal for feeder memberships. */}
        {!r.is_target_of && (
          <>
            <Menu.Divider />
            <Menu.Item color="red" onClick={() => onRemove()}>
              Remove from group
            </Menu.Item>
          </>
        )}
      </Menu.Dropdown>
    </Menu>
  );
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
  const [debugEdges, setDebugEdges] = useState(false);
  const [solvePuzzle, setSolvePuzzle] = useState<Puzzle | null>(null);
  const { expandedMinis, toggleMinis } = useMiniExpansion();
  const updateMut = useUpdatePuzzle(detail.hunt.uid);
  const retag = useRetagPuzzle(detail.hunt.uid);
  const editable = !detail.hunt.archived;
  // Debug edges (and the click-to-edit menu) are organiser-only; can_manage is
  // already false for archived hunts and matches the backend's require_organiser gate.
  const canManage = detail.hunt.can_manage;

  // Filter VALUES stay the stored strings (that is what the rows are matched
  // against); only the label is title-cased.
  const statusOptions = useMemo(
    () => uniqueValues(puzzles, "status").map((v) => ({ value: v, label: formatStatus(v) })),
    [puzzles],
  );
  const priorityOptions = useMemo(
    () => uniqueValues(puzzles, "priority").map((v) => ({ value: v, label: formatPriority(v) })),
    [puzzles],
  );
  const typeOptions = useMemo(() => uniqueValues(puzzles, "puzzle_type"), [puzzles]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const result = puzzles.filter((p) => {
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
    return sortPuzzles(result);
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
        {canManage && (
          <Checkbox
            label="Debug tags"
            checked={debugEdges}
            onChange={(e) => setDebugEdges(e.currentTarget.checked)}
            mb={8}
          />
        )}
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
          {filtered.map((p) => {
              const bandProps = p.is_minimeta
                ? { onClick: () => toggleMinis(p.id), style: { cursor: "pointer" as const } }
                : {};
              return (
              <Table.Tbody key={p.id}>
              <Table.Tr bg={p.solved ? "var(--mantine-color-green-light)" : undefined}>
                <Table.Td>
                  <PuzzleLinks
                    puzzle={p}
                    sheetBase={detail.hunt.sheet_base_url}
                    solvedSheetBase={detail.hunt.solved_sheet_base_url}
                    huntArchived={detail.hunt.archived}
                  />
                </Table.Td>
                <Table.Td {...bandProps}>
                  <Group gap={6} wrap="nowrap">
                    <MetaBadge puzzle={p} />
                    <Text fw={p.is_meta ? 600 : 400}>{p.name}</Text>
                    <MiniCountBadge puzzle={p} />
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
                            onPick={(edge) =>
                              retag.mutate(
                                {
                                  puzzleId: p.id,
                                  metaCode: r.group_code as string,
                                  mode: "add",
                                  edge,
                                },
                                { onError: notifyError }
                              )
                            }
                            onRemove={(edge) =>
                              retag.mutate(
                                {
                                  puzzleId: p.id,
                                  metaCode: r.group_code as string,
                                  mode: "remove",
                                  edge,
                                },
                                { onError: notifyError }
                              )
                            }
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
                      onChange={(status) =>
                        updateMut.mutate({ puzzleId: p.id, patch: { status } }, { onError: notifyError })
                      }
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
                      onChange={(priority) =>
                        updateMut.mutate({ puzzleId: p.id, patch: { priority } }, { onError: notifyError })
                      }
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
                      onSave={(puzzle_type) =>
                        updateMut.mutate({ puzzleId: p.id, patch: { puzzle_type } }, { onError: notifyError })
                      }
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
                <PuzzleRowActions
                  puzzle={p}
                  editable={editable}
                  onSolve={setSolvePuzzle}
                  onUnsolve={setSolvePuzzle}
                  onOpenNotes={onOpenNotes}
                />
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
              </Table.Tbody>
            );
            })}
        </Table>
      </Table.ScrollContainer>

      <SolveModal puzzle={solvePuzzle} uid={detail.hunt.uid} onClose={() => setSolvePuzzle(null)} />
    </>
  );
}
