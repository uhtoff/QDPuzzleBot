import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Divider,
  Group,
  Modal,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea,
} from "@mantine/core";
import {
  useCreatePuzzleFromRegion,
  useCreateRegion,
  useDeleteRegion,
  useUpdateRegion,
} from "../api";
import type { CanvasRegion, HuntDetail, RegionRole, RegionShape } from "../types";

type Props = {
  uid: string;
  canvasId: number;
  detail: HuntDetail;
  /** An existing region to edit, or a freshly drawn shape to save. Exactly one. */
  region: CanvasRegion | null;
  pendingShape: RegionShape | null;
  onClose: () => void;
};

const ROLE_HELP: Record<RegionRole, string> = {
  locates: "This part of the picture IS that puzzle.",
  informs: "This part of the picture is a CLUE FOR that puzzle — it also logs a piece of evidence against it.",
};

export function RegionModal({ uid, canvasId, detail, region, pendingShape, onClose }: Props) {
  const open = !!region || !!pendingShape;
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [puzzleId, setPuzzleId] = useState<string | null>(null);
  const [role, setRole] = useState<RegionRole>("locates");
  const [mode, setMode] = useState<"link" | "create">("link");
  const [newName, setNewName] = useState("");
  const [roundId, setRoundId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const createRegion = useCreateRegion(uid);
  const updateRegion = useUpdateRegion(uid);
  const deleteRegion = useDeleteRegion(uid);
  const createPuzzle = useCreatePuzzleFromRegion(uid);

  // Reset from whichever region (or blank shape) the modal was opened with.
  useEffect(() => {
    if (!open) return;
    setLabel(region?.label ?? "");
    setNote(region?.note ?? "");
    setPuzzleId(region?.puzzle_id ? String(region.puzzle_id) : null);
    setRole(region?.role ?? "locates");
    setMode("link");
    setNewName("");
    setRoundId(null);
    setErr(null);
  }, [open, region]);

  const puzzleOptions = detail.board
    .flatMap((g) => g.puzzles)
    .map((p) => ({ value: String(p.id), label: p.name }))
    // A puzzle in several groups appears once.
    .filter((o, i, all) => all.findIndex((x) => x.value === o.value) === i)
    .sort((a, b) => a.label.localeCompare(b.label));

  const roundOptions = detail.board
    .filter((g) => g.tag_group_id !== 0)
    .map((g) => ({ value: String(g.tag_group_id), label: g.name }));

  const busy = createRegion.isPending || updateRegion.isPending ||
    deleteRegion.isPending || createPuzzle.isPending;

  const save = async () => {
    setErr(null);
    // puzzle_id and role travel together: the API rejects a half-set link, so
    // clearing the puzzle must clear the role rather than send a stale one.
    const linked = puzzleId ? Number(puzzleId) : null;
    const body = {
      label,
      note: note || null,
      puzzle_id: linked,
      role: linked ? role : null,
    };
    try {
      if (region) {
        await updateRegion.mutateAsync({ canvasId, regionId: region.id, ...body });
      } else if (pendingShape) {
        await createRegion.mutateAsync({ canvasId, shape: pendingShape, ...body });
      }
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save that region.");
    }
  };

  const createAndLink = async () => {
    setErr(null);
    if (!newName.trim() || !roundId) {
      setErr("A new puzzle needs a name and a round.");
      return;
    }
    try {
      // Creating a puzzle needs a region to hang it on, so a freshly drawn shape
      // is saved first and then linked. That keeps one create path rather than a
      // second "create region and puzzle together" endpoint.
      let target = region;
      if (!target && pendingShape) {
        target = await createRegion.mutateAsync({
          canvasId, shape: pendingShape, label, note: note || null,
        });
      }
      if (!target) return;
      await createPuzzle.mutateAsync({
        canvasId, regionId: target.id, name: newName.trim(),
        roundId: Number(roundId), role,
      });
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not create that puzzle.");
    }
  };

  const remove = async () => {
    if (!region) return;
    setErr(null);
    try {
      await deleteRegion.mutateAsync({ canvasId, regionId: region.id });
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not delete that region.");
    }
  };

  return (
    <Modal opened={open} onClose={onClose} title={region ? "Region" : "New region"} size="lg">
      <Stack gap="sm">
        {err && <Alert color="red">{err}</Alert>}

        <TextInput label="Label" placeholder="Hex maze, top left"
                   description="Shown on the picture, and as the source name in rendered tables."
                   value={label} onChange={(e) => setLabel(e.currentTarget.value)} />
        <Textarea label="Notes" placeholder="Transcribe what the region says…"
                  description="Free text. Transcription is the usual use."
                  autosize minRows={3} maxRows={10}
                  value={note} onChange={(e) => setNote(e.currentTarget.value)} />

        <Divider label="Link to a puzzle" labelPosition="left" />

        <SegmentedControl
          value={mode}
          onChange={(v) => setMode(v as "link" | "create")}
          data={[
            { label: "Existing puzzle", value: "link" },
            { label: "Create a new puzzle", value: "create" },
          ]}
        />

        {mode === "link" ? (
          <Select
            label="Puzzle"
            placeholder="Not linked"
            data={puzzleOptions}
            value={puzzleId}
            onChange={setPuzzleId}
            searchable
            clearable
          />
        ) : (
          <Stack gap="sm">
            <TextInput label="New puzzle name" value={newName}
                       onChange={(e) => setNewName(e.currentTarget.value)} />
            <Select label="Round" placeholder="Pick a round" data={roundOptions}
                    value={roundId} onChange={setRoundId} searchable />
            <Text size="xs" c="dimmed">
              Creates the Discord channel and the sheet tab, exactly as creating a
              puzzle from the board does. To make a metapuzzle, use the board's own
              create control — that builds a category, which is a different thing.
            </Text>
          </Stack>
        )}

        {(puzzleId || mode === "create") && (
          <Stack gap={4}>
            <Text size="sm" fw={500}>Role</Text>
            <SegmentedControl
              value={role}
              onChange={(v) => setRole(v as RegionRole)}
              data={[
                { label: "Locates", value: "locates" },
                { label: "Informs", value: "informs" },
              ]}
            />
            <Text size="xs" c="dimmed">{ROLE_HELP[role]}</Text>
          </Stack>
        )}

        <Group justify="space-between" mt="sm">
          <div>
            {region && (
              <Button variant="light" color="red" onClick={remove} loading={busy}>
                Delete region
              </Button>
            )}
          </div>
          <Group gap="xs">
            <Button variant="default" onClick={onClose}>Cancel</Button>
            {mode === "create" ? (
              <Button onClick={createAndLink} loading={busy}>Create and link</Button>
            ) : (
              <Button onClick={save} loading={busy}>Save</Button>
            )}
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}
