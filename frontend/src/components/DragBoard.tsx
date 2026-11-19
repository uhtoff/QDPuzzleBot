import { memo, useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
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
  TextInput,
} from "@mantine/core";
import { IconCheck, IconDotsVertical, IconX } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import type { BoardGroup, HuntDetail, Puzzle } from "../types";
import {
  useConvertToFeeder,
  useConvertToFeederPreview,
  useConvertToMeta,
  useConvertToCapstone,
  useConvertToRunaround,
  useChangeGroupType,
  useDeletePuzzle,
  useDeleteRound,
  useMakeMinimeta,
  useRemoveMinimeta,
  useRenameGroup,
  useRetagPuzzle,
  type EdgeType,
  type RetagMode,
} from "../api";
import { formatStatus, fullDeletePuzzleCount, notifyError, showSheetWarning, sortPuzzles, statusColor } from "../util";
import { puzzlesById } from "../lib/boardPuzzles";
import { groupTarget } from "../lib/groupTarget";
import { AddToGroupPicker } from "./AddToGroupPicker";
import { ConfirmModal } from "./ConfirmModal";
import { getKind, kindColorVar } from "./MetaBadge";
import { TypedConfirmModal } from "./TypedConfirmModal";
import { DeleteRoundModal } from "./DeleteRoundModal";
import { ExtractCategoryModal, FoldCategoryModal } from "./CategoryModals";

interface MetaOption {
  code: string;
  name: string;
}

interface PendingCardAction {
  title: string;
  message: string;
  onConfirm: () => void;
}

/**
 * Compute addable/moveable candidate groups for a puzzle.
 *
 * Groups this puzzle is not already in (candidates for "add"). A meta may also
 * be added to its OWN group so it feeds itself in a loop — that group is in
 * inCodes (the meta is its target with a 'none' self-edge) but stays addable
 * until the self-edge is actually feeding.
 *
 * Candidates for "Move to group…" — any group this puzzle isn't already in
 * (a plain move replaces its only membership, so no self-target rows here).
 */
function pickerCandidates(puzzle: Puzzle, metas: MetaOption[]) {
  const inCodes = new Set(puzzle.rounds.map((r) => r.group_code).filter(Boolean));
  const ownTarget = puzzle.rounds.find((r) => r.is_target_of) ?? null;
  const ownTargetCode = ownTarget?.group_code ?? null;
  const selfFeeds = ownTarget?.edges.includes("feeds") ?? false;
  const selfInforms = ownTarget?.edges.includes("informs") ?? false;
  const addable = metas.filter(
    (m) => !inCodes.has(m.code) || (m.code === ownTargetCode && (!selfFeeds || !selfInforms)),
  );
  const moveable = metas.filter((m) => !inCodes.has(m.code));
  return { ownTargetCode, selfFeeds, selfInforms, addable, moveable };
}

interface CardProps {
  puzzle: Puzzle;
  dragId: string;
  columnGroupCode: string | null;
  columnName: string;
  metas: MetaOption[];
  onRetag: (puzzleId: number, groupCode: string, mode: RetagMode, edge?: EdgeType) => void;
  onOpenPicker: (puzzleId: number, mode: "add" | "move") => void;
  onPendingAction: (action: PendingCardAction) => void;
  onDeletePuzzle: (puzzle: Puzzle) => void;
  onMakeMinimeta: (puzzleId: number) => void;
  onRemoveMinimeta: (puzzle: Puzzle) => void;
  onConvertToMeta: (puzzleId: number) => void;
  onConvertToCapstone: (puzzleId: number) => void;
  onConvertToRunaround: (puzzleId: number) => void;
  isOrganiser: boolean;
  isPending: boolean;
}

const PuzzleCard = memo(function PuzzleCard({
  puzzle,
  dragId,
  columnGroupCode,
  columnName,
  metas,
  onRetag,
  onOpenPicker,
  onPendingAction,
  onDeletePuzzle,
  onMakeMinimeta,
  onRemoveMinimeta,
  onConvertToMeta,
  onConvertToCapstone,
  onConvertToRunaround,
  isOrganiser,
  isPending,
}: CardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: dragId,
    disabled: isPending,
    data: { puzzleId: puzzle.id, sourceGroupCode: columnGroupCode },
  });
  // Mount the Floating-UI Menu only when the user actually clicks the kebab button.
  const [menuOpen, setMenuOpen] = useState(false);

  // A feeder that belongs to more than one meta is at risk: a plain drag is a
  // "move", which detaches it from all of its other metas. Flag it on the card.
  const metaCount = puzzle.rounds.filter((r) => r.group_code).length;
  const multiMeta = !puzzle.is_meta && metaCount > 1;
  // Arrange is space-constrained (narrow columns), so the puzzle's kind shows
  // as a coloured left-edge line instead of the MetaBadge used elsewhere.
  const kind = getKind(puzzle);

  // Only compute candidate lists and remove eligibility while the menu is open.
  const menuData = menuOpen ? pickerCandidates(puzzle, metas) : null;
  const here = menuOpen ? puzzle.rounds.find((r) => r.group_code === columnGroupCode) : null;
  const canRemove = menuOpen && columnGroupCode != null && here != null && !here.is_target_of;

  const kebabButton = (
    /* stopPropagation so opening the menu doesn't start a drag */
    <ActionIcon
      variant="subtle"
      color="gray"
      size="sm"
      onPointerDown={(e) => e.stopPropagation()}
      onClick={!menuOpen ? () => setMenuOpen(true) : undefined}
      aria-label="Puzzle actions"
    >
      <IconDotsVertical size={15} />
    </ActionIcon>
  );

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
        borderLeft: kind ? `4px solid ${kindColorVar(kind.color)}` : undefined,
      }}
      {...listeners}
      {...attributes}
    >
      <Group gap={6} wrap="nowrap" justify="space-between">
        <Group gap={4} wrap="nowrap" style={{ minWidth: 0 }}>
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
        {!menuOpen ? (
          kebabButton
        ) : (
          <Menu opened={menuOpen} onChange={setMenuOpen} position="bottom-end" withinPortal shadow="md" width={220}>
            <Menu.Target>{kebabButton}</Menu.Target>
            <Menu.Dropdown onPointerDown={(e) => e.stopPropagation()}>
              {menuData!.addable.length === 0 ? (
                <Menu.Item disabled>Already in all groups</Menu.Item>
              ) : (
                <Menu.Item
                  onClick={() => {
                    setMenuOpen(false);
                    onOpenPicker(puzzle.id, "add");
                  }}
                >
                  Add to group…
                </Menu.Item>
              )}
              {menuData!.moveable.length > 0 && (
                <Menu.Item
                  onClick={() => {
                    setMenuOpen(false);
                    onOpenPicker(puzzle.id, "move");
                  }}
                >
                  Move to group…
                </Menu.Item>
              )}
              {canRemove && (
                <>
                  <Menu.Divider />
                  <Menu.Item
                    color="red"
                    onClick={() => {
                      setMenuOpen(false);
                      onRetag(puzzle.id, columnGroupCode!, "remove");
                    }}
                  >
                    Remove from {columnName}
                  </Menu.Item>
                </>
              )}
              {puzzle.is_minimeta && !puzzle.solved && (
                <>
                  <Menu.Divider />
                  <Menu.Item
                    color="red"
                    onClick={() => {
                      setMenuOpen(false);
                      onRemoveMinimeta(puzzle);
                    }}
                  >
                    Remove minimeta status…
                  </Menu.Item>
                </>
              )}
              {!puzzle.is_minimeta && (
                <>
                  <Menu.Divider />
                  {!puzzle.is_meta && !puzzle.solved && (
                    <Menu.Item
                      color="grape"
                      onClick={() => {
                        setMenuOpen(false);
                        onPendingAction({
                          title: "Make minimeta",
                          message: `Mark "${puzzle.name}" as a minimeta?`,
                          onConfirm: () => onMakeMinimeta(puzzle.id),
                        });
                      }}
                    >
                      Make minimeta
                    </Menu.Item>
                  )}
                  {!puzzle.is_meta && (
                    <Menu.Item
                      color="grape"
                      onClick={() => {
                        setMenuOpen(false);
                        onPendingAction({
                          title: "Convert to meta",
                          message: `Convert "${puzzle.name}" to a meta? This creates a new meta round around it.`,
                          onConfirm: () => onConvertToMeta(puzzle.id),
                        });
                      }}
                    >
                      Convert to meta
                    </Menu.Item>
                  )}
                  <Menu.Item
                    color="grape"
                    onClick={() => {
                      setMenuOpen(false);
                      onPendingAction({
                        title: "Convert to capstone",
                        message: puzzle.is_meta
                          ? `Convert "${puzzle.name}" to a capstone? Its feeders' feeds tags flip to informs.`
                          : `Convert "${puzzle.name}" to a capstone? This creates a capstone round around it; other puzzles can then inform it.`,
                        onConfirm: () => onConvertToCapstone(puzzle.id),
                      });
                    }}
                  >
                    Convert to capstone
                  </Menu.Item>
                  <Menu.Item
                    color="grape"
                    onClick={() => {
                      setMenuOpen(false);
                      onPendingAction({
                        title: "Convert to runaround",
                        message: puzzle.is_meta
                          ? `Convert "${puzzle.name}" to a runaround? Membership tags are kept.`
                          : `Convert "${puzzle.name}" to a runaround? This creates a runaround round around it.`,
                        onConfirm: () => onConvertToRunaround(puzzle.id),
                      });
                    }}
                  >
                    Convert to runaround
                  </Menu.Item>
                </>
              )}
              {isOrganiser && !puzzle.is_meta && (
                <>
                  <Menu.Divider />
                  <Menu.Item
                    color="red"
                    onClick={() => {
                      setMenuOpen(false);
                      onDeletePuzzle(puzzle);
                    }}
                  >
                    Delete puzzle…
                  </Menu.Item>
                </>
              )}
            </Menu.Dropdown>
          </Menu>
        )}
      </Group>
      {puzzle.solved && puzzle.solution ? (
        <Text size="xs" ff="monospace" c="green" mt={2}>
          {puzzle.solution}
        </Text>
      ) : (
        puzzle.status && (
          <Badge size="xs" variant="light" color={statusColor(puzzle.status)} mt={4}>
            {formatStatus(puzzle.status)}
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
});

const Column = memo(function Column({
  group,
  metas,
  pendingIds,
  isOrganiser,
  onRetag,
  onOpenPicker,
  onPendingAction,
  onDeletePuzzle,
  onMakeMinimeta,
  onRemoveMinimeta,
  onConvertToMeta,
  onConvertToCapstone,
  onConvertToRunaround,
  onRenameGroup,
  onChangeType,
  onConvertGroupToFeeder,
  onExtractCategory,
  onFoldCategory,
  onDeleteGroup,
}: {
  group: BoardGroup;
  metas: MetaOption[];
  pendingIds: Set<number>;
  isOrganiser: boolean;
  onRetag: (puzzleId: number, groupCode: string, mode: RetagMode, edge?: EdgeType) => void;
  onOpenPicker: (puzzleId: number, mode: "add" | "move") => void;
  onPendingAction: (action: PendingCardAction) => void;
  onDeletePuzzle: (puzzle: Puzzle) => void;
  onMakeMinimeta: (puzzleId: number) => void;
  onRemoveMinimeta: (puzzle: Puzzle) => void;
  onConvertToMeta: (puzzleId: number) => void;
  onConvertToCapstone: (puzzleId: number) => void;
  onConvertToRunaround: (puzzleId: number) => void;
  onRenameGroup: (tagGroupId: number, name: string) => void;
  onChangeType: (group: BoardGroup, type: string) => void;
  onConvertGroupToFeeder: (group: BoardGroup) => void;
  onExtractCategory: (group: BoardGroup) => void;
  onFoldCategory: (group: BoardGroup) => void;
  onDeleteGroup: (group: BoardGroup) => void;
}) {
  // The Unassigned column (group_code === null) is a drop target too: dropping a
  // puzzle there clears it from all metas. Its groupCode in the drop data is null.
  const isUnassigned = group.group_code == null;
  const [editing, setEditing] = useState(false);
  const [temp, setTemp] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const commitRename = () => {
    const newName = temp.trim();
    if (newName && newName !== group.name) onRenameGroup(group.tag_group_id, newName);
    setEditing(false);
  };
  const { setNodeRef, isOver } = useDroppable({
    id: `meta:${group.group_code}`,
    data: { groupCode: group.group_code },
  });
  // A plain group / Unassigned Feeders has no target, so no kind line — only a
  // group with a target (meta/capstone/metameta/runaround) carries one, in the
  // target puzzle's own kind colour.
  const targetPuzzle = groupTarget(group);
  const kind = targetPuzzle ? getKind(targetPuzzle) : null;

  const sortedPuzzles = useMemo(
    () => sortPuzzles(group.puzzles, group.group_code),
    [group.puzzles, group.group_code],
  );

  const groupMenuButton = (
    <ActionIcon
      variant="subtle"
      color="gray"
      size="sm"
      aria-label="Group actions"
      onClick={!menuOpen ? () => setMenuOpen(true) : undefined}
    >
      <IconDotsVertical size={15} />
    </ActionIcon>
  );

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
        borderLeft: kind ? `4px solid ${kindColorVar(kind.color)}` : undefined,
        backgroundColor: group.target_solved ? "var(--mantine-color-green-light)" : undefined,
      }}
    >
      <Group justify="space-between" mb="xs">
        <Group gap={6}>
          {editing ? (
            // No close-on-blur: clicking away used to drop the edit silently.
            // Enter or the tick saves; Escape or the cross abandons.
            <Group gap={2} wrap="nowrap">
              <TextInput
                size="xs"
                w={150}
                value={temp}
                onChange={(e) => setTemp(e.currentTarget.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename();
                  if (e.key === "Escape") setEditing(false);
                }}
                autoFocus
              />
              <ActionIcon variant="light" color="green" size="sm" aria-label="Save group name" onClick={commitRename}>
                <IconCheck size={14} />
              </ActionIcon>
              <ActionIcon variant="subtle" color="gray" size="sm" aria-label="Cancel rename" onClick={() => setEditing(false)}>
                <IconX size={14} />
              </ActionIcon>
            </Group>
          ) : (
            <Text
              fw={600}
              size="sm"
              lineClamp={1}
              style={isUnassigned ? undefined : { cursor: "pointer" }}
              onClick={
                isUnassigned
                  ? undefined
                  : () => {
                      setEditing(true);
                      setTemp(group.name);
                    }
              }
            >
              {group.name}
            </Text>
          )}
        </Group>
        <Group gap={4} wrap="nowrap">
          <Text size="xs" c="dimmed">
            {group.num_solved_feeders}/{group.num_feeders}
          </Text>
          {!isUnassigned && (
            !menuOpen ? (
              groupMenuButton
            ) : (
              <Menu opened={menuOpen} onChange={setMenuOpen} position="bottom-end" withinPortal shadow="md" width={230}>
                <Menu.Target>{groupMenuButton}</Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>Change type</Menu.Label>
                  {["Metapuzzle", "Capstone", "Runaround", "Group"]
                    .filter((t) => t !== group.group_type)
                    .map((t) => (
                      <Menu.Item
                        key={t}
                        onClick={() => {
                          setMenuOpen(false);
                          onChangeType(group, t);
                        }}
                      >
                        {t}
                      </Menu.Item>
                    ))}
                  {(group.target_puzzle_id ?? 0) > 0 && (
                    <>
                      <Menu.Divider />
                      <Menu.Item
                        color="orange"
                        onClick={() => {
                          setMenuOpen(false);
                          onConvertGroupToFeeder(group);
                        }}
                      >
                        Convert to feeder (keep puzzle)…
                      </Menu.Item>
                      <Menu.Divider />
                      <Menu.Label>Discord category</Menu.Label>
                      {group.own_category ? (
                        <Menu.Item
                          onClick={() => {
                            setMenuOpen(false);
                            onFoldCategory(group);
                          }}
                        >
                          Fold into another category…
                        </Menu.Item>
                      ) : (
                        <Menu.Item
                          onClick={() => {
                            setMenuOpen(false);
                            onExtractCategory(group);
                          }}
                        >
                          Extract to own category…
                        </Menu.Item>
                      )}
                    </>
                  )}
                  <Menu.Divider />
                  <Menu.Item
                    color="red"
                    onClick={() => {
                      setMenuOpen(false);
                      onDeleteGroup(group);
                    }}
                  >
                    Delete group…
                  </Menu.Item>
                </Menu.Dropdown>
              </Menu>
            )
          )}
        </Group>
      </Group>
      <Stack gap={6}>
        {sortedPuzzles.map((p) => (
          <PuzzleCard
            key={`${group.tag_group_id}-${p.id}`}
            puzzle={p}
            dragId={`${group.tag_group_id}:${p.id}`}
            columnGroupCode={group.group_code}
            columnName={group.name}
            metas={metas}
            onRetag={onRetag}
            onOpenPicker={onOpenPicker}
            onPendingAction={onPendingAction}
            onDeletePuzzle={onDeletePuzzle}
            onMakeMinimeta={onMakeMinimeta}
            onRemoveMinimeta={onRemoveMinimeta}
            onConvertToMeta={onConvertToMeta}
            onConvertToCapstone={onConvertToCapstone}
            onConvertToRunaround={onConvertToRunaround}
            isOrganiser={isOrganiser}
            isPending={pendingIds.has(p.id)}
          />
        ))}
      </Stack>
    </Paper>
  );
});

/**
 * Sticky mini-scrollbar mirroring the board's horizontal ScrollArea.
 *
 * Isolated in its own component and driven via ResizeObserver + direct DOM
 * scroll listeners so measuring overflow on mount and scrolling horizontally
 * NEVER trigger a re-render of DragBoard or its columns/cards.
 */
function BoardHorizontalScrollbar({
  viewportRef,
  board,
}: {
  viewportRef: RefObject<HTMLDivElement | null>;
  board: BoardGroup[];
}) {
  const [maxScroll, setMaxScroll] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;

    const measure = () => {
      const max = Math.max(0, el.scrollWidth - el.clientWidth);
      setMaxScroll((prev) => (prev === max ? prev : max));
      if (inputRef.current) inputRef.current.value = String(el.scrollLeft);
    };

    const onScroll = () => {
      if (inputRef.current) inputRef.current.value = String(el.scrollLeft);
    };

    const raf = requestAnimationFrame(measure);
    el.addEventListener("scroll", onScroll, { passive: true });
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(measure);
      ro.observe(el);
      if (el.firstElementChild) ro.observe(el.firstElementChild);
    }

    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener("scroll", onScroll);
      ro?.disconnect();
    };
  }, [viewportRef, board]);

  if (maxScroll <= 0) return null;

  return (
    <Box
      style={{ position: "sticky", bottom: 0, zIndex: 10 }}
      bg="var(--mantine-color-body)"
      py={4}
      px={2}
    >
      <input
        ref={inputRef}
        type="range"
        aria-label="Scroll groups horizontally"
        min={0}
        max={maxScroll}
        defaultValue={viewportRef.current?.scrollLeft ?? 0}
        onChange={(e) => {
          const el = viewportRef.current;
          if (el) el.scrollLeft = Number(e.target.value);
        }}
        style={{ width: "100%" }}
      />
    </Box>
  );
}

export const DragBoard = memo(function DragBoard({ detail, uid }: { detail: HuntDetail; uid: string }) {
  const renameGroup = useRenameGroup(uid);
  const retag = useRetagPuzzle(uid);
  const convert = useConvertToFeeder(uid);
  const deletePuzzleMut = useDeletePuzzle(uid);
  const makeMinimeta = useMakeMinimeta(uid);
  const removeMinimeta = useRemoveMinimeta(uid);
  const convertToMeta = useConvertToMeta(uid);
  const convertToCapstone = useConvertToCapstone(uid);
  const convertToRunaround = useConvertToRunaround(uid);
  const changeType = useChangeGroupType(uid);
  const deleteRoundMut = useDeleteRound(uid);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  const [active, setActive] = useState<Puzzle | null>(null);
  // Single lifted Add/Move picker shared across all cards on the board.
  const [pickerTarget, setPickerTarget] = useState<{ puzzleId: number; mode: "add" | "move" } | null>(null);
  // Single lifted ConfirmModal for card menu actions (minimeta / convert-to-*).
  const [cardConfirm, setCardConfirm] = useState<PendingCardAction | null>(null);
  // The metapuzzle awaiting a convert-to-feeder confirm (null = modal closed).
  const [convertTarget, setConvertTarget] = useState<Puzzle | null>(null);
  // Preview the orphan impact only while the confirm modal is open for a target.
  const convertPreview = useConvertToFeederPreview(convertTarget?.id ?? null, convertTarget != null);
  // Puzzle awaiting a delete confirm (null = modal closed).
  const [deleteTarget, setDeleteTarget] = useState<Puzzle | null>(null);
  // Minimeta awaiting a remove-status confirm (null = modal closed).
  const [removeMinimetaTarget, setRemoveMinimetaTarget] = useState<Puzzle | null>(null);
  // Group awaiting a delete confirm (null = modal closed).
  const [deleteGroupTarget, setDeleteGroupTarget] = useState<BoardGroup | null>(null);
  // Arrange-menu Discord category actions (null = dialog closed).
  const [extractTarget, setExtractTarget] = useState<BoardGroup | null>(null);
  const [foldTarget, setFoldTarget] = useState<BoardGroup | null>(null);
  const [deleteAllPuzzles, setDeleteAllPuzzles] = useState(false);
  // Puzzles a full delete actually removes (excludes shared feeders and foreign
  // targets, which are kept & relocated) — so the warning doesn't over-promise.
  const deleteGroupCount = useMemo(
    () => (deleteGroupTarget ? fullDeletePuzzleCount(deleteGroupTarget) : 0),
    [deleteGroupTarget],
  );
  // Group type change awaiting a confirm (null = modal closed).
  const [changeTypeConfirm, setChangeTypeConfirm] = useState<{ group: BoardGroup; type: string; message: string } | null>(null);
  const isOrganiser = detail.hunt.can_manage;
  // Track every in-flight retag ourselves: React Query's single mutation only
  // exposes the latest call's variables, so concurrent retags would otherwise
  // grey out just one card.
  const [pendingIds, setPendingIds] = useState<Set<number>>(() => new Set());
  // Deferred drag action awaiting confirmation via ConfirmModal.
  const [dragConfirm, setDragConfirm] = useState<{ title: string; message: string; puzzleId: number; groupCode: string; mode: RetagMode } | null>(null);

  const boardViewportRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!deleteGroupTarget) setDeleteAllPuzzles(false);
  }, [deleteGroupTarget]);

  const byId = useMemo(() => puzzlesById(detail.board), [detail.board]);

  const metas: MetaOption[] = useMemo(
    () =>
      detail.board
        .filter((g) => g.group_code != null)
        .map((g) => ({ code: g.group_code as string, name: g.name })),
    [detail.board],
  );

  // Derive the open picker's candidate list from the live puzzle object; if a
  // refresh empties the list while the picker is open, close it so a later
  // refresh that restores candidates doesn't spuriously re-open it.
  const pickerPuzzle = pickerTarget ? byId.get(pickerTarget.puzzleId) ?? null : null;
  const pickerState = useMemo(() => {
    if (!pickerTarget || !pickerPuzzle) return null;
    const c = pickerCandidates(pickerPuzzle, metas);
    const items = pickerTarget.mode === "move" ? c.moveable : c.addable;
    return { ...c, items, mode: pickerTarget.mode, puzzleId: pickerPuzzle.id };
  }, [pickerTarget, pickerPuzzle, metas]);

  useEffect(() => {
    if (pickerTarget && (!pickerState || pickerState.items.length === 0)) {
      setPickerTarget(null);
    }
  }, [pickerTarget, pickerState]);

  const { mutateAsync: retagAsync } = retag;
  const runRetag = useCallback(
    (puzzleId: number, groupCode: string, mode: RetagMode, edge?: EdgeType) => {
      setPendingIds((s) => new Set(s).add(puzzleId));
      // mutateAsync (not mutate + inline onSuccess/onError/onSettled): the inline
      // callback form is routed through a single #mutateOptions field shared by
      // every call on this one useMutation() instance, so firing several retags
      // back-to-back (e.g. dragging a few puzzles between metas quickly) makes
      // each new call overwrite the previous one's callbacks and detach its
      // observer — an earlier call's onSettled then never fires, leaving that
      // puzzle's card stuck greyed-out even though its request already
      // succeeded. mutateAsync's returned promise is tied to that call's own
      // mutation instance, so it resolves/rejects independently of any later
      // concurrent call.
      retagAsync({ puzzleId, metaCode: groupCode, mode, edge })
        .then((data) => {
          showSheetWarning(data);
        })
        .catch((err) => {
          notifyError(err);
        })
        .finally(() => {
          setPendingIds((s) => {
            const next = new Set(s);
            next.delete(puzzleId);
            return next;
          });
        });
    },
    [retagAsync],
  );

  const onOpenPicker = useCallback((puzzleId: number, mode: "add" | "move") => {
    setPickerTarget({ puzzleId, mode });
  }, []);

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

  function runRemoveMinimeta() {
    if (!removeMinimetaTarget) return;
    const name = removeMinimetaTarget.name;
    removeMinimeta.mutate(removeMinimetaTarget.id, {
      onSuccess: (data) => {
        showSheetWarning(data);
        notifications.show({ message: `"${name}" is no longer a minimeta.`, color: "green" });
        setRemoveMinimetaTarget(null);
      },
      onError: notifyError,
    });
  }

  const { mutate: makeMinimetaMutate } = makeMinimeta;
  const onMakeMinimeta = useCallback(
    (puzzleId: number) => {
      makeMinimetaMutate(puzzleId, { onError: notifyError });
    },
    [makeMinimetaMutate],
  );

  const { mutate: convertToMetaMutate } = convertToMeta;
  const onConvertToMeta = useCallback(
    (puzzleId: number) => {
      convertToMetaMutate(puzzleId, {
        onSuccess: (data) => {
          showSheetWarning(data);
          notifications.show({ message: "Converted to a metapuzzle.", color: "green" });
        },
        onError: notifyError,
      });
    },
    [convertToMetaMutate],
  );

  const { mutate: convertToCapstoneMutate } = convertToCapstone;
  const onConvertToCapstone = useCallback(
    (puzzleId: number) => {
      convertToCapstoneMutate(puzzleId, {
        onSuccess: (data) => {
          showSheetWarning(data);
          notifications.show({ message: "Converted to a capstone.", color: "green" });
        },
        onError: notifyError,
      });
    },
    [convertToCapstoneMutate],
  );

  const { mutate: convertToRunaroundMutate } = convertToRunaround;
  const onConvertToRunaround = useCallback(
    (puzzleId: number) => {
      convertToRunaroundMutate(puzzleId, {
        onSuccess: (data) => {
          showSheetWarning(data);
          notifications.show({ message: "Converted to a runaround.", color: "green" });
        },
        onError: notifyError,
      });
    },
    [convertToRunaroundMutate],
  );

  const { mutate: renameGroupMutate } = renameGroup;
  const onRenameGroup = useCallback(
    (tagGroupId: number, name: string) => {
      renameGroupMutate(
        { tagGroupId, name },
        {
          onSuccess: () =>
            notifications.show({ message: "Group renamed.", color: "green" }),
          onError: notifyError,
        },
      );
    },
    [renameGroupMutate],
  );

  const { mutate: changeTypeMutate } = changeType;
  const runChangeType = useCallback(
    (group: BoardGroup, type: string) => {
      changeTypeMutate(
        { tagGroupId: group.tag_group_id, type },
        {
          onSuccess: (data) => {
            showSheetWarning(data);
            notifications.show({ message: `"${group.name}" is now a ${type}.`, color: "green" });
          },
          onError: notifyError,
        },
      );
    },
    [changeTypeMutate],
  );

  const onChangeType = useCallback(
    (group: BoardGroup, type: string) => {
      const isTargeted = (group.target_puzzle_id ?? 0) > 0;
      if (isTargeted && type === "Group") {
        setChangeTypeConfirm({
          group, type,
          message: `Convert "${group.name}" to a group? This removes its metapuzzle, deletes its Google sheet, and turns its channel into the round's general channel. Feeders remain but become pooled (untagged).`,
        });
      } else if (!isTargeted && type !== "Group") {
        setChangeTypeConfirm({
          group, type,
          message: `Convert "${group.name}" to a ${type}? This creates a metapuzzle (a new Google sheet and channel) for the group and replaces its chat channel.`,
        });
      } else {
        runChangeType(group, type);
      }
    },
    [runChangeType],
  );

  const onConvertGroupToFeeder = useCallback((group: BoardGroup) => {
    // The group's OWN target — never the first `is_meta` member: is_meta means
    // "target of ANY group", so in a metameta that picks a foreign group's
    // target, and the modal then names one puzzle while converting another.
    const target = groupTarget(group);
    if (target) setConvertTarget(target); // reuses the existing convert-to-feeder modal + preview
  }, []);

  const onDeleteGroup = useCallback((group: BoardGroup) => {
    setDeleteGroupTarget(group);
  }, []);

  function runDeleteRound() {
    if (!deleteGroupTarget) return;
    const name = deleteGroupTarget.name;
    deleteRoundMut.mutate(
      { roundId: deleteGroupTarget.tag_group_id, keepPuzzles: !deleteAllPuzzles },
      {
        onSuccess: (data) => {
          if (data.failed_channels?.length) {
            notifications.show({ message: `Group "${name}" deleted; some channels couldn't be removed: ${data.failed_channels.join(", ")}`, color: "yellow" });
          } else {
            notifications.show({ message: `Group "${name}" deleted.`, color: "green" });
          }
          setDeleteGroupTarget(null);
        },
        onError: notifyError,
      },
    );
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
    requestMove(puzzleId, groupCode);
  }

  // Shared "move" path for both a drag-drop and the card menu's "Move to
  // group…" picker: the server clears all of this puzzle's meta tags and
  // re-adds only the target, silently detaching it from any other metas — warn
  // first when that would actually lose memberships.
  const requestMove = useCallback(
    (puzzleId: number, groupCode: string) => {
      const p = byId.get(puzzleId);
      if (!p) return;
      if (!p.is_meta && p.rounds.filter((r) => r.group_code).length > 1) {
        const lost = p.rounds.filter((r) => r.group_code && r.group_code !== groupCode);
        const names = lost.map((r) => r.name).join(", ");
        setDragConfirm({
          title: "Move puzzle",
          message:
            `"${p.name}" is also in: ${names}. ` +
            `Moving it to a group removes it from those other groups. ` +
            `To keep it in multiple groups, use the ⋮ menu's "Add to group" instead. ` +
            `Move it anyway?`,
          puzzleId,
          groupCode,
          mode: "move",
        });
        return;
      }
      runRetag(puzzleId, groupCode, "move");
    },
    [byId, runRetag],
  );

  return (
    <Box>
      <Text size="sm" c="dimmed" mb="xs">
        Drag a puzzle onto a group to reassign it (moves its Discord channel too if it is a meta), or onto Unassigned Feeders to
        remove it from all groups. Use the ⋮ menu on a card to add it to an additional group, move it without dragging, or remove it from just one.
      </Text>
      <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
        <ScrollArea
          type="auto"
          offsetScrollbars
          viewportRef={boardViewportRef}
        >
          <Group align="flex-start" wrap="nowrap" gap="sm" pb="sm">
            {detail.board.map((group) => (
              <Column
                key={group.tag_group_id}
                group={group}
                metas={metas}
                pendingIds={pendingIds}
                isOrganiser={isOrganiser}
                onRetag={runRetag}
                onOpenPicker={onOpenPicker}
                onPendingAction={setCardConfirm}
                onDeletePuzzle={setDeleteTarget}
                onMakeMinimeta={onMakeMinimeta}
                onRemoveMinimeta={setRemoveMinimetaTarget}
                onConvertToMeta={onConvertToMeta}
                onConvertToCapstone={onConvertToCapstone}
                onConvertToRunaround={onConvertToRunaround}
                onRenameGroup={onRenameGroup}
                onChangeType={onChangeType}
                onConvertGroupToFeeder={onConvertGroupToFeeder}
                onExtractCategory={setExtractTarget}
                onFoldCategory={setFoldTarget}
                onDeleteGroup={onDeleteGroup}
              />
            ))}
          </Group>
        </ScrollArea>
        <BoardHorizontalScrollbar viewportRef={boardViewportRef} board={detail.board} />
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

      {pickerState && pickerState.items.length > 0 && (
        <AddToGroupPicker
          mode={pickerState.mode}
          opened
          onClose={() => setPickerTarget(null)}
          puzzleId={pickerState.puzzleId}
          items={pickerState.items}
          ownTargetCode={pickerState.mode === "add" ? pickerState.ownTargetCode : null}
          selfFeeds={pickerState.mode === "add" ? pickerState.selfFeeds : false}
          selfInforms={pickerState.mode === "add" ? pickerState.selfInforms : false}
          onRetag={
            pickerState.mode === "move"
              ? (id, groupCode) => requestMove(id, groupCode)
              : runRetag
          }
        />
      )}

      {cardConfirm && (
        <ConfirmModal
          opened
          onClose={() => setCardConfirm(null)}
          onConfirm={() => {
            cardConfirm.onConfirm();
            setCardConfirm(null);
          }}
          title={cardConfirm.title}
          message={cardConfirm.message}
          confirmLabel="Confirm"
          confirmColor="grape"
        />
      )}

      {convertTarget && (
        <TypedConfirmModal
          opened
          onClose={() => setConvertTarget(null)}
          onConfirm={runConvertToFeeder}
          title={`Convert "${convertTarget.name}" to a feeder`}
          confirmPhrase={convertTarget.name}
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
      )}

      {deleteTarget && (
        <TypedConfirmModal
          opened
          onClose={() => setDeleteTarget(null)}
          onConfirm={runDeletePuzzle}
          title={`Delete "${deleteTarget.name}"`}
          confirmPhrase={deleteTarget.name}
          confirmLabel="Delete puzzle"
          loading={deletePuzzleMut.isPending}
        >
          <Text size="sm" c="red">
            ⚠️ This permanently deletes the puzzle&rsquo;s Discord channel and Google sheet tab.
            This cannot be undone.
          </Text>
        </TypedConfirmModal>
      )}

      {removeMinimetaTarget && (
        <TypedConfirmModal
          opened
          onClose={() => setRemoveMinimetaTarget(null)}
          onConfirm={runRemoveMinimeta}
          title={`Remove minimeta "${removeMinimetaTarget.name}"`}
          confirmPhrase={removeMinimetaTarget.name}
          confirmLabel="Remove minimeta"
          loading={removeMinimeta.isPending}
        >
          <Text size="sm" c="red">
            ⚠️ This deletes every mini-puzzle under &ldquo;{removeMinimetaTarget.name}&rdquo; —
            each one&rsquo;s Discord thread and Google sheet tab — and reverts this puzzle to an
            ordinary puzzle. This cannot be undone.
          </Text>
        </TypedConfirmModal>
      )}

      {dragConfirm && (
        <ConfirmModal
          opened
          onClose={() => setDragConfirm(null)}
          onConfirm={() => {
            runRetag(dragConfirm.puzzleId, dragConfirm.groupCode, dragConfirm.mode);
            setDragConfirm(null);
          }}
          title={dragConfirm.title}
          message={dragConfirm.message}
          confirmLabel="Move"
          confirmColor="blue"
        />
      )}

      {changeTypeConfirm && (
        <ConfirmModal
          opened
          onClose={() => setChangeTypeConfirm(null)}
          onConfirm={() => {
            runChangeType(changeTypeConfirm.group, changeTypeConfirm.type);
            setChangeTypeConfirm(null);
          }}
          title={`Change type to ${changeTypeConfirm.type}`}
          message={changeTypeConfirm.message}
          confirmLabel="Change type"
          confirmColor="grape"
        />
      )}

      {deleteGroupTarget && (
        <DeleteRoundModal
          opened
          onClose={() => setDeleteGroupTarget(null)}
          onConfirm={runDeleteRound}
          name={deleteGroupTarget.name}
          noun="group"
          puzzleCount={deleteGroupCount}
          loading={deleteRoundMut.isPending}
          deleteAllPuzzles={deleteAllPuzzles}
          onDeleteAllPuzzlesChange={setDeleteAllPuzzles}
        />
      )}

      {extractTarget && (
        <ExtractCategoryModal group={extractTarget} uid={uid} onClose={() => setExtractTarget(null)} />
      )}
      {foldTarget && (
        <FoldCategoryModal
          group={foldTarget}
          groups={detail.board}
          uid={uid}
          onClose={() => setFoldTarget(null)}
        />
      )}
    </Box>
  );
});
