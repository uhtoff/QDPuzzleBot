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
  LoadingOverlay,
  Menu,
  Paper,
  ScrollArea,
  Stack,
  Text,
} from "@mantine/core";
import { IconDotsVertical } from "@tabler/icons-react";
import type { BoardGroup, HuntDetail, Puzzle } from "../types";
import { useRetagPuzzle, type RetagMode } from "../api";
import { statusColor } from "../util";

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
}

function PuzzleCard({ puzzle, dragId, columnMetaCode, columnName, metas, onRetag }: CardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: dragId,
    data: { puzzleId: puzzle.id },
  });

  // Metas this puzzle is not already in (candidates for "add").
  const inCodes = new Set(puzzle.rounds.map((r) => r.meta_code).filter(Boolean));
  const addable = metas.filter((m) => !inCodes.has(m.code));
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
        opacity: isDragging ? 0.4 : 1,
        cursor: "grab",
        backgroundColor: puzzle.solved ? "var(--mantine-color-green-light)" : undefined,
      }}
      {...listeners}
      {...attributes}
    >
      <Group gap={6} wrap="nowrap" justify="space-between">
        <Group gap={4} wrap="nowrap" style={{ minWidth: 0 }}>
          {puzzle.is_meta && <Badge size="xs" color="grape">META</Badge>}
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
    </Card>
  );
}

function Column({ group, children }: { group: BoardGroup; children: ReactNode }) {
  const droppable = group.meta_code != null;
  const { setNodeRef, isOver } = useDroppable({
    id: `meta:${group.meta_code}`,
    disabled: !droppable,
    data: { metaCode: group.meta_code },
  });
  return (
    <Paper
      ref={droppable ? setNodeRef : undefined}
      withBorder
      p="xs"
      radius="md"
      w={260}
      style={{
        flex: "0 0 auto",
        outline: isOver ? "2px solid var(--mantine-color-blue-5)" : undefined,
        opacity: droppable ? 1 : 0.85,
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
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  const [active, setActive] = useState<Puzzle | null>(null);

  const byId = new Map<number, Puzzle>();
  detail.board.forEach((g) => g.puzzles.forEach((p) => byId.set(p.id, p)));

  const metas: MetaOption[] = detail.board
    .filter((g) => g.meta_code != null)
    .map((g) => ({ code: g.meta_code as string, name: g.name }));

  const runRetag = (puzzleId: number, metaCode: string, mode: RetagMode) =>
    retag.mutate({ puzzleId, metaCode, mode });

  function onDragStart(e: DragStartEvent) {
    const puzzleId = e.active.data.current?.puzzleId as number | undefined;
    setActive(puzzleId ? byId.get(puzzleId) ?? null : null);
  }

  function onDragEnd(e: DragEndEvent) {
    setActive(null);
    const puzzleId = e.active.data.current?.puzzleId as number | undefined;
    const metaCode = e.over?.data.current?.metaCode as string | null | undefined;
    if (puzzleId && metaCode) runRetag(puzzleId, metaCode, "move");
  }

  return (
    <Box pos="relative">
      <LoadingOverlay visible={retag.isPending} zIndex={5} />
      <Text size="sm" c="dimmed" mb="xs">
        Drag a puzzle onto a meta to reassign it (moves its Discord channel too). Use the ⋮ menu on a card to
        add it to an additional meta or remove it from one.
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
    </Box>
  );
}
