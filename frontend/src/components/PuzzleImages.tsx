import { useCallback, useEffect, useState } from "react";
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
import { IconPlus, IconTrash, IconVectorTriangle } from "@tabler/icons-react";
import type { PuzzleImage } from "../types";
import {
  useAttachPuzzleImage,
  useDetachPuzzleImage,
  usePuzzleImages,
  useReorderPuzzleImages,
  useUploadMedia,
} from "../api";
import { imageFromClipboard, isTextEntryTarget, notifyError } from "../util";
import { ConfirmModal } from "./ConfirmModal";
import { MarkupModal } from "./MarkupModal";

interface Props {
  puzzleId: number;
  puzzleName: string;
  uid: string;
  editable: boolean;
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
export function PuzzleImages({ puzzleId, puzzleName, uid, editable }: Props) {
  const { data: images = [], isLoading } = usePuzzleImages(puzzleId);
  const uploadMedia = useUploadMedia(uid);
  const attach = useAttachPuzzleImage(puzzleId);
  const reorder = useReorderPuzzleImages(puzzleId);
  const detach = useDetachPuzzleImage(puzzleId);

  const [markupImage, setMarkupImage] = useState<PuzzleImage | null>(null);
  const [removeTarget, setRemoveTarget] = useState<PuzzleImage | null>(null);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const onAddImage = useCallback(async (file: File | null) => {
    if (!file) return;
    try {
      const media = await uploadMedia.mutateAsync(file);
      await attach.mutateAsync(media.id);
    } catch (err) {
      notifyError(err);
    }
  }, [uploadMedia, attach]);

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
      {images.length === 0 && !editable ? null : (
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
                      onOpen={setMarkupImage}
                      onRemove={setRemoveTarget}
                    />
                  ))}
                </Group>
              </SortableContext>
            </DndContext>
          )}
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
        mediaId={markupImage?.media_id ?? null}
        alt={puzzleName}
        onClose={() => setMarkupImage(null)}
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
