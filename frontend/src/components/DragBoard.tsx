import { useState, type ReactNode } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  ActionIcon,
  Badge,
  Box,
  Card,
  Group,
  Loader,
  Menu,
  Paper,
  ScrollArea,
  Stack,
  Text,
} from "@mantine/core";
import { IconDotsVertical } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import type { BoardGroup, HuntDetail, Puzzle } from "../types";
import {
  useConvertToCapstone,
  useConvertToFeeder,
  useConvertToFeederPreview,
  useConvertToGroup,
  useConvertToMeta,
  useConvertToMetapuzzle,
  useConvertToRunaround,
  useDeletePuzzle,
  useMakeMinimeta,
  useRetagPuzzle,
  type RetagMode,
} from "../api";
import { notifyError, showSheetWarning, statusColor } from "../util";
import { ConfirmModal } from "./ConfirmModal";
import { MetaBadge } from "./MetaBadge";
import { TypedConfirmModal } from "./TypedConfirmModal";

interface MetaOption {
  code: string;
  name: string;
}

interface CardProps {
  puzzle: Puzzle;
  dragId: string;
  columnGroupCode: string | null;
  columnName: string;
  metas: MetaOption[];
  onRetag: (puzzleId: number, groupCode: string, mode: RetagMode) => void;
  onConvertToFeeder: (puzzle: Puzzle) => void;
  onDeletePuzzle: (puzzle: Puzzle) => void;
  onMakeMinimeta: (puzzleId: number) => void;
  onConvertToMeta: (puzzleId: number) => void;
  onConvertToMetapuzzle: (puzzleId: number) => void;
  onConvertToCapstone: (puzzleId: number) => void;
  onConvertToRunaround: (puzzleId: number) => void;
  onConvertToGroup: (puzzleId: number) => void;
  isOrganiser: boolean;
  isPending: boolean;
}

function PuzzleCard({ puzzle, dragId, columnGroupCode, columnName, metas, onRetag, onConvertToFeeder, onDeletePuzzle, onMakeMinimeta, onConvertToMeta, onConvertToMetapuzzle, onConvertToCapstone, onConvertToRunaround, onConvertToGroup, isOrganiser, isPending }: CardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: dragId,
    disabled: isPending,
    data: { puzzleId: puzzle.id, sourceGroupCode: columnGroupCode },
  });
  // Pending action for the ConfirmModal (minimeta / convert-to-meta).
  const [pendingAction, setPendingAction] = useState<{ title: string; message: string; onConfirm: () => void } | null>(null);

  // Groups this puzzle is not already in (candidates for "add"). A meta may also
  // be added to its OWN group so it feeds itself in a loop — that group is in
  // inCodes (the meta is its target with a 'none' self-edge) but stays addable
  // until the self-edge is actually feeding.
  const inCodes = new Set(puzzle.rounds.map((r) => r.group_code).filter(Boolean));
  const ownTarget = puzzle.rounds.find((r) => r.is_target_of) ?? null;
  const ownTargetCode = ownTarget?.group_code ?? null;
  const selfFeeds = ownTarget?.edges.includes("feeds") ?? false;
  const addable = metas.filter(
    (m) => !inCodes.has(m.code) || (m.code === ownTargetCode && !selfFeeds),
  );
  // A feeder that belongs to more than one meta is at risk: a plain drag is a
  // "move", which detaches it from all of its other metas. Flag it on the card.
  const metaCount = puzzle.rounds.filter((r) => r.group_code).length;
  const multiMeta = !puzzle.is_meta && metaCount > 1;
  // Allow removing from the current column unless this puzzle IS that meta.
  const here = puzzle.rounds.find((r) => r.group_code === columnGroupCode);
  const canRemove = columnGroupCode != null && here != null && !here.is_target_of;
  // The stored type of the group this puzzle is the target of (Metapuzzle /
  // Capstone / Runaround / Group). Drives the convert menu so it never offers
  // the type the puzzle already is.
  const currentType = puzzle.rounds.find((r) => r.is_target_of)?.group_type ?? null;

  return (
    <>
    <Card
      ref={setNodeRef}
      withBorder
      padding="xs"
      radius="sm"
      style={{
        transform: transform ? `translate3d(${transform.x}px, ${transform.y}px, 0)` : undefined,
        opacity: isDragging ? 0.4 : isPending ? 0.5 : 1,
        cursor: "grab",
        backgroundColor: puzzle.solved ? "var(--mantine-color-green-light)" : undefined,
      }}
      {...listeners}
      {...attributes}
    >
      <Group gap={6} wrap="nowrap" justify="space-between">
        <Group gap={4} wrap="nowrap" style={{ minWidth: 0 }}>
          <MetaBadge puzzle={puzzle} />
          {multiMeta && (
            <Badge
              size="xs"
              color="orange"
              variant="light"
              title={`In ${metaCount} groups — dragging moves it and removes it from the others`}
            >
              {metaCount} groups
            </Badge>
          )}
          <Text size="sm" fw={puzzle.is_meta ? 600 : 400} lineClamp={1}>
            {puzzle.name}
          </Text>
        </Group>
        <Menu position="bottom-end" withinPortal shadow="md" width={220}>
          <Menu.Target>
            {/* stopPropagation so opening the menu doesn't start a drag */}
            <ActionIcon
              variant="subtle"
              color="gray"
              size="sm"
              onPointerDown={(e) => e.stopPropagation()}
              aria-label="Puzzle actions"
            >
              <IconDotsVertical size={15} />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown onPointerDown={(e) => e.stopPropagation()}>
            <Menu.Label>Add to group</Menu.Label>
            {addable.length === 0 ? (
              <Menu.Item disabled>Already in all groups</Menu.Item>
            ) : (
              addable.map((m) => (
                <Menu.Item key={m.code} onClick={() => onRetag(puzzle.id, m.code, "add")}>
                  {m.code === ownTargetCode ? `${m.name} (feed itself)` : m.name}
                </Menu.Item>
              ))
            )}
            {canRemove && (
              <>
                <Menu.Divider />
                <Menu.Item color="red" onClick={() => onRetag(puzzle.id, columnGroupCode!, "remove")}>
                  Remove from {columnName}
                </Menu.Item>
              </>
            )}
            {puzzle.is_meta && (
              <>
                <Menu.Divider />
                {currentType !== "Metapuzzle" && (
                  <Menu.Item
                    color="grape"
                    onClick={() =>
                      setPendingAction({
                        title: "Convert to metapuzzle",
                        message: `Convert "${puzzle.name}" to a metapuzzle?`,
                        onConfirm: () => onConvertToMetapuzzle(puzzle.id),
                      })
                    }
                  >
                    Convert to metapuzzle
                  </Menu.Item>
                )}
                {currentType !== "Capstone" && (
                  <Menu.Item
                    color="grape"
                    onClick={() =>
                      setPendingAction({
                        title: "Convert to capstone",
                        message: `Convert "${puzzle.name}" to a capstone?`,
                        onConfirm: () => onConvertToCapstone(puzzle.id),
                      })
                    }
                  >
                    Convert to capstone
                  </Menu.Item>
                )}
                {currentType !== "Runaround" && (
                  <Menu.Item
                    color="grape"
                    onClick={() =>
                      setPendingAction({
                        title: "Convert to runaround",
                        message: `Convert "${puzzle.name}" to a runaround?`,
                        onConfirm: () => onConvertToRunaround(puzzle.id),
                      })
                    }
                  >
                    Convert to runaround
                  </Menu.Item>
                )}
                {currentType !== "Group" && (
                  <Menu.Item
                    color="grape"
                    onClick={() =>
                      setPendingAction({
                        title: "Convert to group",
                        message: `Convert "${puzzle.name}" to a group?`,
                        onConfirm: () => onConvertToGroup(puzzle.id),
                      })
                    }
                  >
                    Convert to group
                  </Menu.Item>
                )}
                <Menu.Item color="red" onClick={() => onConvertToFeeder(puzzle)}>
                  Convert to feeder…
                </Menu.Item>
              </>
            )}
            {!puzzle.is_meta && !puzzle.solved && (
              <>
                <Menu.Divider />
                <Menu.Item
                  color="grape"
                  onClick={() =>
                    setPendingAction({
                      title: "Make minimeta",
                      message: `Mark "${puzzle.name}" as a minimeta?`,
                      onConfirm: () => onMakeMinimeta(puzzle.id),
                    })
                  }
                >
                  Make minimeta
                </Menu.Item>
                <Menu.Item
                  color="grape"
                  onClick={() =>
                    setPendingAction({
                      title: "Convert to meta",
                      message: `Convert "${puzzle.name}" to a meta? This creates a new meta round around it.`,
                      onConfirm: () => onConvertToMeta(puzzle.id),
                    })
                  }
                >
                  Convert to meta
                </Menu.Item>
              </>
            )}
            {isOrganiser && (
              <>
                <Menu.Divider />
                <Menu.Item color="red" onClick={() => onDeletePuzzle(puzzle)}>
                  Delete puzzle…
                </Menu.Item>
              </>
            )}
          </Menu.Dropdown>
        </Menu>
      </Group>
      {puzzle.solved && puzzle.solution ? (
        <Text size="xs" ff="monospace" c="green" mt={2}>
          {puzzle.solution}
        </Text>
      ) : (
        puzzle.status && (
          <Badge size="xs" variant="light" color={statusColor(puzzle.status)} mt={4}>
            {puzzle.status}
          </Badge>
        )
      )}
      {isPending && (
        <Group gap={4} mt={4}>
          <Loader size="xs" color="blue" />
          <Text size="xs" c="dimmed">Retagging...</Text>
        </Group>
      )}
    </Card>
    {pendingAction && (
      <ConfirmModal
        opened={pendingAction != null}
        onClose={() => setPendingAction(null)}
        onConfirm={() => {
          pendingAction.onConfirm();
          setPendingAction(null);
        }}
        title={pendingAction.title}
        message={pendingAction.message}
        confirmLabel="Confirm"
        confirmColor="grape"
      />
    )}
    </>
  );
}

function Column({ group, children }: { group: BoardGroup; children: ReactNode }) {
  // The Unassigned column (group_code === null) is a drop target too: dropping a
  // puzzle there clears it from all metas. Its groupCode in the drop data is null.
  const isUnassigned = group.group_code == null;
  const { setNodeRef, isOver } = useDroppable({
    id: `meta:${group.group_code}`,
    data: { groupCode: group.group_code },
  });
  return (
    <Paper
      ref={setNodeRef}
      withBorder
      p="xs"
      radius="md"
      w={260}
      style={{
        flex: "0 0 auto",
        outline: isOver ? "2px solid var(--mantine-color-blue-5)" : undefined,
        opacity: isUnassigned && !isOver ? 0.85 : 1,
      }}
    >
      <Group justify="space-between" mb="xs">
        <Group gap={6}>
          <Text fw={600} size="sm" lineClamp={1}>
            {group.name}
          </Text>
          {group.group_code && (
            <Badge size="xs" color="grape" variant="light">
              {group.group_code}
            </Badge>
          )}
        </Group>
        <Text size="xs" c="dimmed">
          {group.num_solved_feeders}/{group.num_feeders}
        </Text>
      </Group>
      <Stack gap={6}>{children}</Stack>
    </Paper>
  );
}

export function DragBoard({ detail, uid }: { detail: HuntDetail; uid: string }) {
  const retag = useRetagPuzzle(uid);
  const convert = useConvertToFeeder(uid);
  const deletePuzzleMut = useDeletePuzzle(uid);
  const makeMinimeta = useMakeMinimeta(uid);
  const convertToMeta = useConvertToMeta(uid);
  const convertToCapstone = useConvertToCapstone(uid);
  const convertToRunaround = useConvertToRunaround(uid);
  const convertToGroup = useConvertToGroup(uid);
  const convertToMetapuzzle = useConvertToMetapuzzle(uid);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  const [active, setActive] = useState<Puzzle | null>(null);
  // The metapuzzle awaiting a convert-to-feeder confirm (null = modal closed).
  const [convertTarget, setConvertTarget] = useState<Puzzle | null>(null);
  // Preview the orphan impact only while the confirm modal is open for a target.
  const convertPreview = useConvertToFeederPreview(convertTarget?.id ?? null, convertTarget != null);
  // Puzzle awaiting a delete confirm (null = modal closed).
  const [deleteTarget, setDeleteTarget] = useState<Puzzle | null>(null);
  const isOrganiser = detail.hunt.can_manage;
  // Track every in-flight retag ourselves: React Query's single mutation only
  // exposes the latest call's variables, so concurrent retags would otherwise
  // grey out just one card.
  const [pendingIds, setPendingIds] = useState<Set<number>>(new Set());
  // Deferred drag action awaiting confirmation via ConfirmModal.
  const [dragConfirm, setDragConfirm] = useState<{ title: string; message: string; puzzleId: number; groupCode: string; mode: RetagMode } | null>(null);

  const byId = new Map<number, Puzzle>();
  detail.board.forEach((g) => g.puzzles.forEach((p) => byId.set(p.id, p)));

  const metas: MetaOption[] = detail.board
    .filter((g) => g.group_code != null)
    .map((g) => ({ code: g.group_code as string, name: g.name }));

  const runRetag = (puzzleId: number, groupCode: string, mode: RetagMode) => {
    setPendingIds((s) => new Set(s).add(puzzleId));
    retag.mutate(
      { puzzleId, metaCode: groupCode, mode },
      {
        onSuccess: (data) => {
          showSheetWarning(data);
        },
        onSettled: () => {
          setPendingIds((s) => {
            const next = new Set(s);
            next.delete(puzzleId);
            return next;
          });
        },
      }
    );
  };

  function runConvertToFeeder() {
    if (!convertTarget) return;
    const name = convertTarget.name;
    convert.mutate(convertTarget.id, {
      onSuccess: (data) => {
        showSheetWarning(data);
        const orphans = data.orphaned ?? 0;
        notifications.show({
          message:
            orphans > 0
              ? `Converted "${name}" to a feeder — ${orphans} feeder${orphans === 1 ? "" : "s"} orphaned.`
              : `Converted "${name}" to a feeder.`,
          color: "green",
        });
        setConvertTarget(null);
      },
      onError: notifyError,
    });
  }

  function runDeletePuzzle() {
    if (!deleteTarget) return;
    const name = deleteTarget.name;
    deletePuzzleMut.mutate(deleteTarget.id, {
      onSuccess: (data) => {
        showSheetWarning(data);
        notifications.show({ message: `"${name}" permanently deleted.`, color: "green" });
        setDeleteTarget(null);
      },
      onError: notifyError,
    });
  }

  function onMakeMinimeta(puzzleId: number) {
    makeMinimeta.mutate(puzzleId, {
      onError: notifyError,
    });
  }

  function onConvertToMeta(puzzleId: number) {
    convertToMeta.mutate(puzzleId, {
      onSuccess: (data) => {
        showSheetWarning(data);
        notifications.show({ message: "Converted to a metapuzzle.", color: "green" });
      },
      onError: notifyError,
    });
  }

  function onConvertToCapstone(puzzleId: number) {
    convertToCapstone.mutate(puzzleId, {
      onSuccess: (data) => {
        showSheetWarning(data);
        notifications.show({ message: "Converted to a capstone.", color: "green" });
      },
      onError: notifyError,
    });
  }

  function onConvertToRunaround(puzzleId: number) {
    convertToRunaround.mutate(puzzleId, {
      onSuccess: (data) => {
        showSheetWarning(data);
        notifications.show({ message: "Converted to a runaround.", color: "green" });
      },
      onError: notifyError,
    });
  }

  function onConvertToGroup(puzzleId: number) {
    convertToGroup.mutate(puzzleId, {
      onSuccess: (data) => showSheetWarning(data),
      onError: notifyError,
    });
  }

  function onConvertToMetapuzzle(puzzleId: number) {
    convertToMetapuzzle.mutate(puzzleId, {
      onSuccess: (data) => {
        showSheetWarning(data);
        notifications.show({ message: "Converted to a metapuzzle.", color: "green" });
      },
      onError: notifyError,
    });
  }

  function onDragStart(e: DragStartEvent) {
    const puzzleId = e.active.data.current?.puzzleId as number | undefined;
    setActive(puzzleId ? byId.get(puzzleId) ?? null : null);
  }

  function onDragEnd(e: DragEndEvent) {
    setActive(null);
    const puzzleId = e.active.data.current?.puzzleId as number | undefined;
    const sourceGroupCode = e.active.data.current?.sourceGroupCode as string | null | undefined;
    if (!puzzleId || e.over == null) return;
    const groupCode = e.over.data.current?.groupCode as string | null | undefined;
    const p = byId.get(puzzleId);

    // Dropped on the Unassigned column (groupCode === null): clear all metas.
    if (groupCode == null) {
      if (!p) return;
      const currentMetas = p.rounds.filter((r) => r.group_code);
      if (currentMetas.length === 0) return; // already unassigned — no-op
      if (p.is_meta) {
        notifications.show({ message: "A meta can't be unassigned.", color: "yellow" });
        return;
      }
      const names = currentMetas.map((r) => r.name).join(", ");
      setDragConfirm({
        title: "Remove from all groups",
        message: `Remove "${p.name}" from all groups: ${names}?`,
        puzzleId,
        groupCode: "",
        mode: "clear",
      });
      return;
    }

    // Skip a no-op drop back onto the puzzle's current meta — otherwise every
    // drop fires a retag that moves the Discord channel (a PATCH /channels write).
    if (groupCode === sourceGroupCode) return;
    // A plain drag is a "move": the server clears all of this puzzle's meta tags
    // and re-adds only the target, silently detaching it from any other metas.
    // Warn first when that would actually lose memberships.
    if (p && !p.is_meta && p.rounds.filter((r) => r.group_code).length > 1) {
      const lost = p.rounds.filter((r) => r.group_code && r.group_code !== groupCode);
      const names = lost.map((r) => r.name).join(", ");
      setDragConfirm({
        title: "Move puzzle",
        message:
          `"${p.name}" is also in: ${names}. ` +
          `Dragging onto a group MOVES it — it will be removed from those other groups. ` +
          `To keep it in multiple groups, use the ⋮ menu's "Add to group" instead. ` +
          `Move it anyway?`,
        puzzleId,
        groupCode,
        mode: "move",
      });
      return;
    }
    runRetag(puzzleId, groupCode, "move");
  }

  return (
    <Box>
      <Text size="sm" c="dimmed" mb="xs">
        Drag a puzzle onto a group to reassign it (moves its Discord channel too), or onto Unassigned Feeders to
        remove it from all groups. Use the ⋮ menu on a card to add it to an additional group or remove it from just one.
      </Text>
      <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
        <ScrollArea type="auto" offsetScrollbars>
          <Group align="flex-start" wrap="nowrap" gap="sm" pb="sm">
            {detail.board.map((group) => (
              <Column key={group.tag_group_id} group={group}>
                {group.puzzles.map((p) => (
                  <PuzzleCard
                    key={`${group.tag_group_id}-${p.id}`}
                    puzzle={p}
                    dragId={`${group.tag_group_id}:${p.id}`}
                    columnGroupCode={group.group_code}
                    columnName={group.name}
                    metas={metas}
                    onRetag={runRetag}
                    onConvertToFeeder={setConvertTarget}
                    onDeletePuzzle={setDeleteTarget}
                    onMakeMinimeta={onMakeMinimeta}
                    onConvertToMeta={onConvertToMeta}
                    onConvertToMetapuzzle={onConvertToMetapuzzle}
                    onConvertToCapstone={onConvertToCapstone}
                    onConvertToRunaround={onConvertToRunaround}
                    onConvertToGroup={onConvertToGroup}
                    isOrganiser={isOrganiser}
                    isPending={pendingIds.has(p.id)}
                  />
                ))}
              </Column>
            ))}
          </Group>
        </ScrollArea>
        <DragOverlay>
          {active ? (
            <Card withBorder padding="xs" radius="sm" style={{ cursor: "grabbing" }}>
              <Text size="sm" fw={active.is_meta ? 600 : 400}>
                {active.name}
              </Text>
            </Card>
          ) : null}
        </DragOverlay>
      </DndContext>
      {retag.isError && (
        <Text c="red" size="sm" mt="xs">
          {(retag.error as Error).message}
        </Text>
      )}

      <TypedConfirmModal
        opened={convertTarget != null}
        onClose={() => setConvertTarget(null)}
        onConfirm={runConvertToFeeder}
        title={`Convert "${convertTarget?.name ?? ""}" to a feeder`}
        confirmPhrase={convertTarget?.name ?? ""}
        confirmLabel="Convert to feeder"
        loading={convert.isPending}
      >
        <Text size="sm">
          This deletes the metapuzzle&rsquo;s round and demotes it to an ordinary feeder.
          Its Google sheet is restructured back to a plain puzzle.
        </Text>
        {convertPreview.isLoading ? (
          <Group gap={6}>
            <Loader size="xs" />
            <Text size="sm" c="dimmed">
              Checking which feeders would be orphaned&hellip;
            </Text>
          </Group>
        ) : convertPreview.data && convertPreview.data.orphan_count > 0 ? (
          <Text size="sm" c="red">
            ⚠️ {convertPreview.data.orphan_count} feeder
            {convertPreview.data.orphan_count === 1 ? "" : "s"} tagged to this meta will be{" "}
            <b>orphaned</b> (unassigned from any meta):{" "}
            {convertPreview.data.orphan_feeders.map((f) => f.name).join(", ")}.
          </Text>
        ) : (
          <Text size="sm" c="dimmed">
            No feeders are tagged to this meta — nothing will be orphaned.
          </Text>
        )}
      </TypedConfirmModal>

      <TypedConfirmModal
        opened={deleteTarget != null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={runDeletePuzzle}
        title={`Delete "${deleteTarget?.name ?? ""}"`}
        confirmPhrase={deleteTarget?.name ?? ""}
        confirmLabel="Delete puzzle"
        loading={deletePuzzleMut.isPending}
      >
        <Text size="sm" c="red">
          ⚠️ This permanently deletes the puzzle&rsquo;s Discord channel and Google sheet tab.
          This cannot be undone.
        </Text>
      </TypedConfirmModal>

      <ConfirmModal
        opened={dragConfirm != null}
        onClose={() => setDragConfirm(null)}
        onConfirm={() => {
          if (dragConfirm) runRetag(dragConfirm.puzzleId, dragConfirm.groupCode, dragConfirm.mode);
          setDragConfirm(null);
        }}
        title={dragConfirm?.title ?? ""}
        message={dragConfirm?.message ?? ""}
        confirmLabel="Move"
        confirmColor="blue"
      />
    </Box>
  );
}
