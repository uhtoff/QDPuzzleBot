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
  useConvertToFeeder,
  useConvertToFeederPreview,
  useRetagPuzzle,
  type RetagMode,
} from "../api";
import { statusColor } from "../util";
import { TypedConfirmModal } from "./TypedConfirmModal";

interface MetaOption {
  code: string;
  name: string;
}

interface CardProps {
  puzzle: Puzzle;
  dragId: string;
  columnMetaCode: string | null;
  columnName: string;
  metas: MetaOption[];
  onRetag: (puzzleId: number, metaCode: string, mode: RetagMode) => void;
  onConvertToFeeder: (puzzle: Puzzle) => void;
  isPending: boolean;
}

function PuzzleCard({ puzzle, dragId, columnMetaCode, columnName, metas, onRetag, onConvertToFeeder, isPending }: CardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: dragId,
    disabled: isPending,
    data: { puzzleId: puzzle.id, sourceMetaCode: columnMetaCode },
  });

  // Metas this puzzle is not already in (candidates for "add").
  const inCodes = new Set(puzzle.rounds.map((r) => r.meta_code).filter(Boolean));
  const addable = metas.filter((m) => !inCodes.has(m.code));
  // A feeder that belongs to more than one meta is at risk: a plain drag is a
  // "move", which detaches it from all of its other metas. Flag it on the card.
  const metaCount = puzzle.rounds.filter((r) => r.meta_code).length;
  const multiMeta = !puzzle.is_meta && metaCount > 1;
  // Allow removing from the current column unless this puzzle IS that meta.
  const here = puzzle.rounds.find((r) => r.meta_code === columnMetaCode);
  const canRemove = columnMetaCode != null && here != null && !here.is_meta_of;

  return (
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
          {puzzle.is_meta && <Badge size="xs" color="grape">META</Badge>}
          {multiMeta && (
            <Badge
              size="xs"
              color="orange"
              variant="light"
              title={`In ${metaCount} metas — dragging moves it and removes it from the others`}
            >
              {metaCount} metas
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
            <Menu.Label>Add to meta</Menu.Label>
            {addable.length === 0 ? (
              <Menu.Item disabled>Already in all metas</Menu.Item>
            ) : (
              addable.map((m) => (
                <Menu.Item key={m.code} onClick={() => onRetag(puzzle.id, m.code, "add")}>
                  {m.name}
                </Menu.Item>
              ))
            )}
            {canRemove && (
              <>
                <Menu.Divider />
                <Menu.Item color="red" onClick={() => onRetag(puzzle.id, columnMetaCode!, "remove")}>
                  Remove from {columnName}
                </Menu.Item>
              </>
            )}
            {puzzle.is_meta && (
              <>
                <Menu.Divider />
                <Menu.Item color="red" onClick={() => onConvertToFeeder(puzzle)}>
                  Convert to feeder…
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
  );
}

function Column({ group, children }: { group: BoardGroup; children: ReactNode }) {
  // The Unassigned column (meta_code === null) is a drop target too: dropping a
  // puzzle there clears it from all metas. Its metaCode in the drop data is null.
  const isUnassigned = group.meta_code == null;
  const { setNodeRef, isOver } = useDroppable({
    id: `meta:${group.meta_code}`,
    data: { metaCode: group.meta_code },
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
          {group.meta_code && (
            <Badge size="xs" color="grape" variant="light">
              {group.meta_code}
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
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  const [active, setActive] = useState<Puzzle | null>(null);
  // The metapuzzle awaiting a convert-to-feeder confirm (null = modal closed).
  const [convertTarget, setConvertTarget] = useState<Puzzle | null>(null);
  // Preview the orphan impact only while the confirm modal is open for a target.
  const convertPreview = useConvertToFeederPreview(convertTarget?.id ?? null, convertTarget != null);
  // Track every in-flight retag ourselves: React Query's single mutation only
  // exposes the latest call's variables, so concurrent retags would otherwise
  // grey out just one card.
  const [pendingIds, setPendingIds] = useState<Set<number>>(new Set());

  const byId = new Map<number, Puzzle>();
  detail.board.forEach((g) => g.puzzles.forEach((p) => byId.set(p.id, p)));

  const metas: MetaOption[] = detail.board
    .filter((g) => g.meta_code != null)
    .map((g) => ({ code: g.meta_code as string, name: g.name }));

  const runRetag = (puzzleId: number, metaCode: string, mode: RetagMode) => {
    setPendingIds((s) => new Set(s).add(puzzleId));
    retag.mutate(
      { puzzleId, metaCode, mode },
      {
        onSuccess: (data) => {
          if (data.sheet_warning) {
            notifications.show({ message: data.sheet_warning, color: "yellow", autoClose: 8000 });
          }
        },
        onSettled: () =>
          setPendingIds((s) => {
            const next = new Set(s);
            next.delete(puzzleId);
            return next;
          }),
      }
    );
  };

  function runConvertToFeeder() {
    if (!convertTarget) return;
    const name = convertTarget.name;
    convert.mutate(convertTarget.id, {
      onSuccess: (data) => {
        if (data.sheet_warning) {
          notifications.show({ message: data.sheet_warning, color: "yellow", autoClose: 8000 });
        }
        const orphans = data.orphaned ?? 0;
        notifications.show({
          message:
            orphans > 0
              ? `Converted “${name}” to a feeder — ${orphans} feeder${orphans === 1 ? "" : "s"} orphaned.`
              : `Converted “${name}” to a feeder.`,
          color: "green",
        });
        setConvertTarget(null);
      },
      onError: (e) => notifications.show({ message: (e as Error).message, color: "red" }),
    });
  }

  function onDragStart(e: DragStartEvent) {
    const puzzleId = e.active.data.current?.puzzleId as number | undefined;
    setActive(puzzleId ? byId.get(puzzleId) ?? null : null);
  }

  function onDragEnd(e: DragEndEvent) {
    setActive(null);
    const puzzleId = e.active.data.current?.puzzleId as number | undefined;
    const sourceMetaCode = e.active.data.current?.sourceMetaCode as string | null | undefined;
    if (!puzzleId || e.over == null) return;
    const metaCode = e.over.data.current?.metaCode as string | null | undefined;
    const p = byId.get(puzzleId);

    // Dropped on the Unassigned column (metaCode === null): clear all metas.
    if (metaCode == null) {
      if (!p) return;
      const currentMetas = p.rounds.filter((r) => r.meta_code);
      if (currentMetas.length === 0) return; // already unassigned — no-op
      if (p.is_meta) {
        notifications.show({ message: "A meta can't be unassigned.", color: "yellow" });
        return;
      }
      const names = currentMetas.map((r) => r.name).join(", ");
      if (!window.confirm(`Remove "${p.name}" from all metas: ${names}?`)) return;
      runRetag(puzzleId, "", "clear");
      return;
    }

    // Skip a no-op drop back onto the puzzle's current meta — otherwise every
    // drop fires a retag that moves the Discord channel (a PATCH /channels write).
    if (metaCode === sourceMetaCode) return;
    // A plain drag is a "move": the server clears all of this puzzle's meta tags
    // and re-adds only the target, silently detaching it from any other metas.
    // Warn first when that would actually lose memberships.
    if (p && !p.is_meta && p.rounds.filter((r) => r.meta_code).length > 1) {
      const lost = p.rounds.filter((r) => r.meta_code && r.meta_code !== metaCode);
      const names = lost.map((r) => r.name).join(", ");
      const ok = window.confirm(
        `"${p.name}" is also in: ${names}.\n\n` +
          `Dragging onto a meta MOVES it — it will be removed from those other metas. ` +
          `To keep it in multiple metas, use the ⋮ menu's "Add to meta" instead.\n\n` +
          `Move it anyway?`,
      );
      if (!ok) return;
    }
    runRetag(puzzleId, metaCode, "move");
  }

  return (
    <Box>
      <Text size="sm" c="dimmed" mb="xs">
        Drag a puzzle onto a meta to reassign it (moves its Discord channel too), or onto Unassigned to remove it
        from all metas. Use the ⋮ menu on a card to add it to an additional meta or remove it from just one.
      </Text>
      <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
        <ScrollArea type="auto" offsetScrollbars>
          <Group align="flex-start" wrap="nowrap" gap="sm" pb="sm">
            {detail.board.map((group) => (
              <Column key={group.round_id} group={group}>
                {group.puzzles.map((p) => (
                  <PuzzleCard
                    key={`${group.round_id}-${p.id}`}
                    puzzle={p}
                    dragId={`${group.round_id}:${p.id}`}
                    columnMetaCode={group.meta_code}
                    columnName={group.name}
                    metas={metas}
                    onRetag={runRetag}
                    onConvertToFeeder={setConvertTarget}
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
        title={`Convert “${convertTarget?.name ?? ""}” to a feeder`}
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
    </Box>
  );
}
