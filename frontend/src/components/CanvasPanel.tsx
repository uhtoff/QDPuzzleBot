import { useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Center,
  FileButton,
  Group,
  Loader,
  Select,
  Stack,
  Text,
  TextInput,
} from "@mantine/core";
import { IconTrash, IconUpload } from "@tabler/icons-react";
import { useCanvases, useCreateCanvas, useDeleteCanvas, useUpdateCanvas, useUploadMedia } from "../api";
import { CanvasView } from "./CanvasView";
import { RegionModal } from "./RegionModal";
import { ConfirmModal } from "./ConfirmModal";
import type { CanvasRegion, HuntDetail, Puzzle, RegionShape } from "../types";

type Props = { uid: string; detail: HuntDetail };

export function CanvasPanel({ uid, detail }: Props) {
  const { data, isLoading, error } = useCanvases(uid);
  const upload = useUploadMedia(uid);
  const createCanvas = useCreateCanvas(uid);
  const updateCanvas = useUpdateCanvas(uid);
  const deleteCanvas = useDeleteCanvas(uid);

  const [selected, setSelected] = useState<string | null>(null);
  const [openRegion, setOpenRegion] = useState<CanvasRegion | null>(null);
  const [pendingShape, setPendingShape] = useState<RegionShape | null>(null);
  const [renaming, setRenaming] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const resetFile = useRef<() => void>(null);

  const canvases = data?.canvases ?? [];
  const canvas = canvases.find((c) => String(c.id) === selected) ?? canvases[0];

  const puzzlesById = useMemo(() => {
    const m = new Map<number, Puzzle>();
    detail.board.forEach((g) => g.puzzles.forEach((p) => m.set(p.id, p)));
    return m;
  }, [detail.board]);

  const canManage = detail.hunt.can_manage;
  const editable = !detail.hunt.archived;

  const onUpload = async (file: File | null) => {
    if (!file) return;
    setErr(null);
    try {
      const media = await upload.mutateAsync(file);
      const created = await createCanvas.mutateAsync({
        mediaId: media.id,
        name: file.name.replace(/\.[^.]+$/, ""),
      });
      setSelected(String(created.id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not upload that image.");
    } finally {
      resetFile.current?.();
    }
  };

  if (isLoading) {
    return <Center h={160}><Loader /></Center>;
  }
  if (error) {
    return <Text c="red">Could not load this hunt's canvases.</Text>;
  }

  return (
    <Stack gap="sm">
      {err && <Alert color="red" onClose={() => setErr(null)} withCloseButton>{err}</Alert>}

      <Group justify="space-between" align="flex-end">
        <Group gap="sm" align="flex-end">
          {canvases.length > 1 && (
            <Select
              label="Canvas"
              data={canvases.map((c) => ({ value: String(c.id), label: c.name || `Canvas ${c.id}` }))}
              value={canvas ? String(canvas.id) : null}
              onChange={setSelected}
              w={240}
            />
          )}
          {canvas && canManage && editable && (
            <TextInput
              label="Rename"
              placeholder={canvas.name}
              value={renaming}
              onChange={(e) => setRenaming(e.currentTarget.value)}
              onBlur={() => {
                if (renaming.trim() && renaming.trim() !== canvas.name) {
                  updateCanvas.mutate({ canvasId: canvas.id, name: renaming.trim() });
                }
                setRenaming("");
              }}
              w={200}
            />
          )}
        </Group>
        <Group gap="xs">
          {/* Uploading is organiser-level, matching the API: it puts a shared
              image in front of the whole team. Drawing on one is not. */}
          {canManage && editable && (
            <FileButton onChange={onUpload} accept="image/png,image/jpeg,image/gif,image/webp"
                        resetRef={resetFile}>
              {(props) => (
                <Button {...props} size="xs" variant="light"
                        loading={upload.isPending || createCanvas.isPending}
                        leftSection={<IconUpload size={14} />}>
                  Upload an image
                </Button>
              )}
            </FileButton>
          )}
          {canvas && canManage && editable && (
            <Button size="xs" variant="light" color="red"
                    leftSection={<IconTrash size={14} />}
                    onClick={() => setConfirmDelete(true)}>
              Delete canvas
            </Button>
          )}
        </Group>
      </Group>

      {!canvas ? (
        <Alert color="gray">
          No canvases yet.{" "}
          {canManage && editable
            ? "Upload the hunt's big image and start drawing boxes around the puzzles on it."
            : "An organiser can upload the hunt's big image here."}
        </Alert>
      ) : (
        <CanvasView
          canvas={canvas}
          puzzlesById={puzzlesById}
          editable={editable}
          onOpenRegion={setOpenRegion}
          onDraw={setPendingShape}
        />
      )}

      {canvas && (
        <RegionModal
          uid={uid}
          canvasId={canvas.id}
          detail={detail}
          region={openRegion}
          pendingShape={pendingShape}
          onClose={() => { setOpenRegion(null); setPendingShape(null); }}
        />
      )}

      {canvas && (
        <ConfirmModal
          opened={confirmDelete}
          onClose={() => setConfirmDelete(false)}
          title="Delete this canvas?"
          confirmLabel="Delete canvas"
          confirmColor="red"
          message={
            `Deletes "${canvas.name || `Canvas ${canvas.id}`}", its ` +
            `${canvas.regions.length} region${canvas.regions.length === 1 ? "" : "s"}, ` +
            "and any evidence logged from them. The puzzles themselves are not deleted."
          }
          onConfirm={async () => {
            await deleteCanvas.mutateAsync(canvas.id);
            setConfirmDelete(false);
            setSelected(null);
          }}
        />
      )}
    </Stack>
  );
}
