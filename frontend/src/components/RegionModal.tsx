import { useEffect, useState } from "react";
import {
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
  useOcrCanvasRegion,
  useUpdateRegion,
} from "../api";
import { useOcrReplaceConfirm } from "../hooks/useOcrReplaceConfirm";
import { notifyError } from "../util";
import type { CanvasRegion, HuntDetail, RegionRole, RegionShape } from "../types";

// Sentinel dropdown value for "no round" — matches CreateControls' pattern,
// sent to the API as no round_id at all (an unattached, hunt-level puzzle).
const UNASSIGNED = "__unassigned__";

type Props = {
  uid: string;
  canvasId: number;
  detail: HuntDetail;
  /** An existing region to edit, or a freshly drawn shape to save. Exactly one. */
  region: CanvasRegion | null;
  pendingShape: RegionShape | null;
  onClose: () => void;
};

/** What the region's puzzle link should end up as when the modal is saved.
 *  "none" is explicit rather than implied by an empty Select: from "create"
 *  mode there was previously no way back to unlinked at all. */
type LinkMode = "none" | "link" | "create";

const ROLE_HELP: Record<RegionRole, string> = {
  locates: "This part of the picture IS that puzzle.",
  informs: "This part of the picture is a CLUE FOR that puzzle — it also logs a piece of evidence against it.",
};

export function RegionModal({ uid, canvasId, detail, region, pendingShape, onClose }: Props) {
  const open = !!region || !!pendingShape;
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [puzzleId, setPuzzleId] = useState<string | null>(null);
  // Informs by default: it keeps a crop of the region as evidence on the
  // puzzle, where locates stores geometry and nothing else. Matches the
  // server-side default in create_puzzle_from_region.
  const [role, setRole] = useState<RegionRole>("informs");
  const [mode, setMode] = useState<LinkMode>("link");
  const [newName, setNewName] = useState("");
  const [roundId, setRoundId] = useState<string | null>(UNASSIGNED);
  // Field validation only — API failures go through notifyError like the rest
  // of the app.
  const [nameError, setNameError] = useState<string | null>(null);

  const createRegion = useCreateRegion(uid);
  const updateRegion = useUpdateRegion(uid);
  const deleteRegion = useDeleteRegion(uid);
  const createPuzzle = useCreatePuzzleFromRegion(uid);
  const ocrRegion = useOcrCanvasRegion();
  const ocrConfirm = useOcrReplaceConfirm(
    setNote,
    "This will replace the current Notes text with the extracted text.",
  );

  // Reset from whichever region (or blank shape) the modal was opened with.
  useEffect(() => {
    if (!open) return;
    setLabel(region?.label ?? "");
    setNote(region?.note ?? "");
    setPuzzleId(region?.puzzle_id ? String(region.puzzle_id) : null);
    setRole(region?.role ?? "informs");
    // Reflect an existing region's actual state, so an unlinked region does not
    // open showing "Existing puzzle". A freshly drawn shape still opens on
    // "link" — drawing a box in order to link it is the common case, and
    // costing that flow an extra click to gain the escape hatch is a bad trade.
    setMode(region && !region.puzzle_id ? "none" : "link");
    setNewName("");
    setRoundId(UNASSIGNED);
    setNameError(null);
  }, [open, region]);

  const puzzleOptions = detail.board
    .flatMap((g) => g.puzzles)
    .map((p) => ({ value: String(p.id), label: p.name }))
    // A puzzle in several groups appears once.
    .filter((o, i, all) => all.findIndex((x) => x.value === o.value) === i)
    .sort((a, b) => a.label.localeCompare(b.label));

  const roundOptions = [
    { value: UNASSIGNED, label: "Unassigned (hunt-level puzzle)" },
    ...detail.board
      .filter((g) => g.tag_group_id !== 0)
      .map((g) => ({ value: String(g.tag_group_id), label: g.name })),
  ];

  const busy = createRegion.isPending || updateRegion.isPending ||
    deleteRegion.isPending || createPuzzle.isPending;

  // Whichever shape is currently on screen: a freshly drawn one, or an
  // existing region's. Exactly one of region/pendingShape is ever set (see
  // the Props comment above), so this is never both.
  const currentShape: RegionShape | null = pendingShape ?? region?.shape ?? null;

  const extractText = async () => {
    if (!currentShape) return;
    try {
      const { text } = await ocrRegion.mutateAsync({ canvasId, shape: currentShape });
      if (!text) {
        notifyError("No text found in that region.");
        return;
      }
      ocrConfirm.offerText(note, text);
    } catch (e) {
      notifyError(e);
    }
  };

  const save = async () => {
    // puzzle_id and role travel together: the API rejects a half-set link, so
    // clearing the puzzle must clear the role rather than send a stale one.
    // "none" wins over whatever the Select still holds — flipping to "No
    // puzzle" and saving is how an existing link gets cleared, and the Select
    // deliberately keeps its value so flipping back restores the choice.
    const linked = mode === "none" || !puzzleId ? null : Number(puzzleId);
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
      notifyError(e);
    }
  };

  const createAndLink = async () => {
    if (!newName.trim()) {
      setNameError("A new puzzle needs a name.");
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
        roundId: roundId && roundId !== UNASSIGNED ? Number(roundId) : null, role,
      });
      onClose();
    } catch (e) {
      notifyError(e);
    }
  };

  const remove = async () => {
    if (!region) return;
    try {
      await deleteRegion.mutateAsync({ canvasId, regionId: region.id });
      onClose();
    } catch (e) {
      notifyError(e);
    }
  };

  return (
    <Modal opened={open} onClose={onClose} title={region ? "Region" : "New region"} size="lg">
      <Stack gap="sm">
        <TextInput label="Label" placeholder="Hex maze, top left"
                   description="Shown on the picture, and as the source name in rendered tables."
                   value={label} onChange={(e) => setLabel(e.currentTarget.value)} />
        <Textarea
          label={
            <Group justify="space-between" wrap="nowrap" gap="xs">
              <span>Notes</span>
              <Button size="xs" variant="subtle" onClick={extractText}
                      loading={ocrRegion.isPending} disabled={!currentShape}>
                Extract text
              </Button>
            </Group>
          }
          placeholder="Transcribe what the region says…"
          description="Free text. Transcription is the usual use."
          autosize minRows={3} maxRows={10}
          value={note} onChange={(e) => setNote(e.currentTarget.value)} />

        <Divider label="Link to a puzzle" labelPosition="left" />

        <SegmentedControl
          value={mode}
          onChange={(v) => setMode(v as LinkMode)}
          data={[
            { label: "No puzzle", value: "none" },
            { label: "Existing puzzle", value: "link" },
            { label: "Create a new puzzle", value: "create" },
          ]}
        />

        {mode === "none" ? (
          <Text size="xs" c="dimmed">
            {region?.puzzle_id
              ? "Saving will unlink this region from its puzzle. The region, its label and its notes stay."
              : "The region is kept on the picture with no puzzle attached. Link it later by reopening it."}
          </Text>
        ) : mode === "link" ? (
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
            <TextInput label="New puzzle name" value={newName} error={nameError}
                       onChange={(e) => { setNewName(e.currentTarget.value); setNameError(null); }} />
            <Select label="Round" placeholder="Pick a round" data={roundOptions}
                    value={roundId} onChange={setRoundId} searchable />
            <Text size="xs" c="dimmed">
              Creates the Discord channel and the sheet tab, exactly as creating a
              puzzle from the board does. To make a metapuzzle, use the board's own
              create control — that builds a category, which is a different thing.
            </Text>
          </Stack>
        )}

        {((mode === "link" && puzzleId) || mode === "create") && (
          <Stack gap={4}>
            <Text size="sm" fw={500}>Role</Text>
            <SegmentedControl
              value={role}
              onChange={(v) => setRole(v as RegionRole)}
              // Default first.
              data={[
                { label: "Informs", value: "informs" },
                { label: "Locates", value: "locates" },
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
      {ocrConfirm.confirmModal}
    </Modal>
  );
}
