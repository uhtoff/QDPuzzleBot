import { useCallback, useEffect, useMemo, useState } from "react";
import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  horizontalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  ActionIcon,
  Anchor,
  Badge,
  Box,
  FileButton,
  Group,
  Image,
  Text,
  UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconTrash, IconVectorTriangle } from "@tabler/icons-react";
import type { EvidenceItem, PuzzleImage } from "../types";
import {
  useAttachPuzzleImage,
  useDeleteEvidence,
  useEvidence,
  useDetachPuzzleImage,
  usePuzzleImages,
  useReorderPuzzleImages,
  useUploadMedia,
} from "../api";
import { imageFromClipboard, isTextEntryTarget, notifyError } from "../util";
import { ARCHIVED_NOTICE, resolveDeepLink, stripItems } from "../lib/stripItems";
import { ConfirmModal } from "./ConfirmModal";
import { MarkupModal } from "./MarkupModal";

interface Props {
  puzzleId: number;
  puzzleName: string;
  uid: string;
  editable: boolean;
  /** Deep link: open this media's editor once the strip has loaded. */
  openMediaId?: number | null;
  /** Called once the deep link has been acted on (opened, or reported gone). */
  onOpened?: () => void;
}

/** An evidence thumbnail: a source-evidence image pinned to this puzzle. Not
 *  sortable and deliberately outside the SortableContext. */
function EvidenceThumb({
  item,
  editable,
  onOpen,
  onRemove,
}: {
  item: EvidenceItem;
  editable: boolean;
  onOpen: (item: EvidenceItem) => void;
  onRemove: (item: EvidenceItem) => void;
}) {
  const thumb = (
    <Image src={item.media_url ?? undefined} alt={item.note} h={64} w={64} fit="cover" radius="sm" />
  );
  return (
    <Box style={{ position: "relative" }} title={item.note || undefined}>
      {editable ? (
        <UnstyledButton onClick={() => onOpen(item)} title={item.note || "Mark up"}>
          {thumb}
        </UnstyledButton>
      ) : (
        <Anchor href={item.media_url ?? undefined} target="_blank" rel="noreferrer">
          {thumb}
        </Anchor>
      )}
      <Badge
        size="xs"
        variant="filled"
        color="teal"
        style={{ position: "absolute", bottom: 2, left: 2, padding: "0 4px" }}
      >
        Evidence
      </Badge>
      {editable && (
        <ActionIcon
          size="xs"
          variant="filled"
          color="red"
          radius="xl"
          style={{ position: "absolute", top: -4, right: -4 }}
          onClick={() => onRemove(item)}
          aria-label="Delete evidence"
        >
          <IconTrash size={10} />
        </ActionIcon>
      )}
    </Box>
  );
}

/** One draggable/sortable thumbnail. dnd-kit's sortable strategy needs each
 *  item to actually call useSortable — a plain <img> inside SortableContext
 *  will never pick up a drag. */
function SortableThumb({
  image,
  editable,
  onOpen,
  onRemove,
}: {
  image: PuzzleImage;
  editable: boolean;
  onOpen: (image: PuzzleImage) => void;
  onRemove: (image: PuzzleImage) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: image.id,
    disabled: !editable,
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };

  const thumb = (
    <Image src={image.media_url} alt="" h={64} w={64} fit="cover" radius="sm" />
  );

  return (
    <Box
      ref={setNodeRef}
      style={{ ...style, position: "relative", touchAction: "none" }}
      {...(editable ? { ...attributes, ...listeners } : {})}
    >
      {editable ? (
        <UnstyledButton onClick={() => onOpen(image)} title="Mark up" style={{ cursor: isDragging ? "grabbing" : "grab" }}>
          {thumb}
        </UnstyledButton>
      ) : (
        // Not editable: markup writes and the routes 403 on an archived hunt,
        // so there is nothing to open here — link straight to the plain image.
        <Anchor href={image.media_url} target="_blank" rel="noreferrer">
          {thumb}
        </Anchor>
      )}
      {image.source_region_id != null && (
        <Badge
          size="xs"
          variant="filled"
          color="grape"
          style={{ position: "absolute", top: 2, left: 2, padding: "0 4px" }}
          title="From a canvas region"
        >
          <IconVectorTriangle size={10} />
        </Badge>
      )}
      {editable && (
        <ActionIcon
          size="xs"
          variant="filled"
          color="red"
          radius="xl"
          style={{ position: "absolute", top: -4, right: -4 }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onRemove(image); }}
          aria-label="Remove image"
        >
          <IconTrash size={10} />
        </ActionIcon>
      )}
    </Box>
  );
}

/** Horizontal strip of a puzzle's images: fetched on its own (not folded into
 *  the board payload, which every client gets pushed over the WS for every
 *  puzzle). Click opens MarkupModal; drag reorders; FileButton adds one. */
export function PuzzleImages({ puzzleId, puzzleName, uid, editable, openMediaId = null, onOpened }: Props) {
  const { data: images = [], isLoading, isError: imagesError } = usePuzzleImages(puzzleId);
  const uploadMedia = useUploadMedia(uid);
  const attach = useAttachPuzzleImage(puzzleId);
  const reorder = useReorderPuzzleImages(puzzleId);
  const detach = useDetachPuzzleImage(puzzleId);

  const { data: evidence = [], isLoading: evidenceLoading, isError: evidenceError } = useEvidence(uid);
  const deleteEvidence = useDeleteEvidence(uid);

  // Media id being edited; own images and evidence both open the same editor.
  const [markupMediaId, setMarkupMediaId] = useState<number | null>(null);
  const [evidenceTarget, setEvidenceTarget] = useState<EvidenceItem | null>(null);
  const [removeTarget, setRemoveTarget] = useState<PuzzleImage | null>(null);

  const evidenceItems = useMemo(
    () =>
      stripItems(images, evidence, puzzleId).flatMap((i) => (i.kind === "evidence" ? [i.item] : [])),
    [images, evidence, puzzleId],
  );

  // Deep link: act once both lists have loaded, then tell the parent it is done.
  useEffect(() => {
    if (openMediaId == null || isLoading || evidenceLoading) return;
    // A failed query would make a real image look gone; skip rather than lie.
    if (imagesError || evidenceError) {
      onOpened?.();
      return;
    }
    const r = resolveDeepLink(
      { puzzleId, mediaId: openMediaId },
      [{ id: puzzleId }],
      stripItems(images, evidence, puzzleId),
    );
    if (r.notice) notifications.show({ message: r.notice, color: "yellow" });
    else if (r.mediaId != null) {
      if (editable) setMarkupMediaId(r.mediaId);
      else notifications.show({ message: ARCHIVED_NOTICE, color: "yellow" });
    }
    onOpened?.();
  }, [openMediaId, isLoading, evidenceLoading, imagesError, evidenceError, images, evidence, puzzleId, editable, onOpened]);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  // mutateAsync, not the mutation object: react-query hands back a new object
  // on every state change, so depending on those rebuilds this callback (and
  // re-renders the memoised thumbnails) on each upload transition.
  const { mutateAsync: uploadMediaAsync } = uploadMedia;
  const { mutateAsync: attachAsync } = attach;
  const onAddImage = useCallback(async (file: File | null) => {
    if (!file) return;
    try {
      const media = await uploadMediaAsync(file);
      await attachAsync(media.id);
    } catch (err) {
      notifyError(err);
    }
  }, [uploadMediaAsync, attachAsync]);

  /** Paste a screenshot anywhere in the open drawer to add it.
   *
   *  A document listener rather than one on the strip: a paste fires at the
   *  focused element, and focus is almost never inside a thumbnail strip. The
   *  drawer traps focus while it is open and this component only exists while
   *  it is open, so the listener is scoped in practice to exactly that.
   *
   *  Pastes into a text field are left alone — the drawer holds the rename box
   *  and the note field, and hijacking those would be worse than not having
   *  this at all.
   */
  useEffect(() => {
    if (!editable) return;
    const onPaste = (e: ClipboardEvent) => {
      if (e.defaultPrevented) return;
      if (isTextEntryTarget(e.target)) return;
      const file = imageFromClipboard(e.clipboardData);
      if (!file) return;
      e.preventDefault();
      void onAddImage(file);
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [editable, onAddImage]);

  function onDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const oldIndex = images.findIndex((i) => i.id === active.id);
    const newIndex = images.findIndex((i) => i.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;
    const reordered = [...images];
    const [moved] = reordered.splice(oldIndex, 1);
    reordered.splice(newIndex, 0, moved);
    reorder.mutate(reordered.map((i) => i.id), { onError: notifyError });
  }

  if (isLoading) return <Text size="xs" c="dimmed">Loading images…</Text>;

  return (
    <Box>
      {images.length === 0 && evidenceItems.length === 0 && !editable ? null : (
        <Group gap="xs" wrap="wrap" align="flex-start">
          {images.length > 0 && (
            <DndContext sensors={sensors} onDragEnd={onDragEnd}>
              <SortableContext
                items={images.map((i) => i.id)}
                strategy={horizontalListSortingStrategy}
              >
                <Group gap="xs" wrap="wrap">
                  {images.map((image) => (
                    <SortableThumb
                      key={image.id}
                      image={image}
                      editable={editable}
                      onOpen={(img) => setMarkupMediaId(img.media_id)}
                      onRemove={setRemoveTarget}
                    />
                  ))}
                </Group>
              </SortableContext>
            </DndContext>
          )}
          {evidenceItems.map((item) => (
            <EvidenceThumb
              key={`ev-${item.id}`}
              item={item}
              editable={editable}
              onOpen={(e) => setMarkupMediaId(e.media_id)}
              onRemove={setEvidenceTarget}
            />
          ))}
          {editable && (
            <FileButton onChange={onAddImage} accept="image/png,image/jpeg,image/gif,image/webp">
              {(props) => (
                <ActionIcon
                  {...props}
                  size={64}
                  variant="light"
                  radius="sm"
                  aria-label="Add image"
                  loading={uploadMedia.isPending || attach.isPending}
                >
                  {!(uploadMedia.isPending || attach.isPending) && <IconPlus size={20} />}
                </ActionIcon>
              )}
            </FileButton>
          )}
        </Group>
      )}

      <MarkupModal
        mediaId={markupMediaId}
        alt={puzzleName}
        onClose={() => setMarkupMediaId(null)}
      />

      <ConfirmModal
        opened={evidenceTarget != null}
        onClose={() => setEvidenceTarget(null)}
        onConfirm={() => {
          if (evidenceTarget) deleteEvidence.mutate(evidenceTarget.id, { onError: notifyError });
          setEvidenceTarget(null);
        }}
        title="Delete evidence"
        message={`Delete this evidence? It will also disappear from ${evidenceTarget?.target_puzzle_name ?? "the target puzzle"}'s roll-up.`}
        confirmLabel="Delete"
        confirmColor="red"
      />

      <ConfirmModal
        opened={removeTarget != null}
        onClose={() => setRemoveTarget(null)}
        onConfirm={() => {
          if (removeTarget) detach.mutate(removeTarget.id, { onError: notifyError });
          setRemoveTarget(null);
        }}
        title="Remove image"
        message={
          removeTarget?.source_region_id != null
            ? "Remove this image? It came from a canvas region, so it will come back the next time that region's link changes."
            : "Remove this image from the puzzle?"
        }
        confirmLabel="Remove"
        confirmColor="red"
      />
    </Box>
  );
}
