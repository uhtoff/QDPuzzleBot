import { memo, useCallback, useMemo, useState, type ReactNode } from "react";
import {
  ActionIcon,
  Anchor,
  Box,
  Button,
  FileButton,
  FileInput,
  Group,
  Image,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  UnstyledButton,
} from "@mantine/core";
import { IconArrowDown, IconArrowsSort, IconArrowUp, IconTrash } from "@tabler/icons-react";
import type { EvidenceItem, HuntDetail } from "../types";
import {
  useAddEvidence,
  useDeleteEvidence,
  useEditClueNote,
  useEvidence,
  useOcrEvidenceImage,
  useReplaceClueImage,
  useUpdateEvidence,
  useUploadMedia,
} from "../api";
import { imageFromClipboard, notifyError } from "../util";
import { ConfirmModal } from "./ConfirmModal";
import { MarkupModal } from "./MarkupModal";

interface Props {
  uid: string;
  detail: HuntDetail;
}

type SortKey = "source_puzzle_name" | "note" | "target_puzzle_name" | "created_at";
type SortDirection = "asc" | "desc";

/** The one thing a row's Source column shows: a puzzle, a hunt sheet, a canvas
 *  region, or nothing. The three sources are mutually exclusive server-side. */
function sourceLabel(item: EvidenceItem): string {
  return item.source_puzzle_name ?? item.source_sheet_name ?? item.source_region_name ?? "—";
}

export function EvidencePanel({ uid, detail }: Props) {
  const { data: items = [], isLoading, error } = useEvidence(uid);
  const updateEvidence = useUpdateEvidence(uid);
  const uploadMedia = useUploadMedia(uid);
  const addEvidence = useAddEvidence(uid);
  const deleteEvidence = useDeleteEvidence(uid);
  const editClueNote = useEditClueNote(uid);
  const ocrClueImage = useOcrEvidenceImage(uid);
  const replaceClueImage = useReplaceClueImage(uid);
  const [clueOcrConfirmOpen, setClueOcrConfirmOpen] = useState(false);
  const [pendingClueOcrText, setPendingClueOcrText] = useState<string | null>(null);
  const [markupItem, setMarkupItem] = useState<EvidenceItem | null>(null);
  const [filterSource, setFilterSource] = useState<string | null>(null);
  const [filterTarget, setFilterTarget] = useState<string | null>(null);
  const [filterText, setFilterText] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection } | null>(null);

  function toggleSort(key: SortKey) {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, direction: "asc" };
      if (prev.direction === "asc") return { key, direction: "desc" };
      return null;
    });
  }

  // "p:<puzzle id>" | "s:<hunt sheet id>" | null. Namespaced because a source is
  // now one of two kinds and a bare id could mean either.
  const [clueSource, setClueSource] = useState<string | null>(null);
  const [clueNote, setClueNote] = useState("");
  const [clueFile, setClueFile] = useState<File | null>(null);

  /** Accept a screenshot pasted into the clue box.
   *
   *  Mantine's FileInput is a file picker with no clipboard handling, so
   *  Ctrl+V did nothing here even though pasting a screenshot is the natural
   *  flow — and the one Discord's !clue already supports. This reads the image
   *  out of the paste and feeds it to the SAME upload path the picker uses, so
   *  there is one code path and one set of validation.
   *
   *  Only swallows the event when an image was actually found: a plain text
   *  paste must still land in the note field.
   */
  function onCluePaste(e: React.ClipboardEvent) {
    const file = imageFromClipboard(e.clipboardData);
    if (!file) return;
    e.preventDefault();
    setClueFile(file);
  }

  /** OCR the attached/pasted image and fill the clue note with the result.
   *
   *  Line breaks are preserved. They used to be collapsed to spaces because
   *  the note field was a single-line TextInput that submitted on Enter; it is
   *  an autosizing Textarea now, and OCR gets the line structure of a grid or
   *  a verse right, which is exactly the part worth keeping.
   */
  async function extractClueText() {
    if (!clueFile) return;
    try {
      const { text } = await ocrClueImage.mutateAsync(clueFile);
      const extracted = text.trim();
      if (!extracted) {
        notifyError(new Error("No text found in that image."));
        return;
      }
      if (clueNote.trim()) {
        setPendingClueOcrText(extracted);
        setClueOcrConfirmOpen(true);
      } else {
        setClueNote(extracted);
      }
    } catch (err) {
      notifyError(err);
    }
  }

  const editable = !detail.hunt.archived;

  /** Upload the new picture, then point the existing row at it. Two steps
   *  because the media route is what sniffs and stores the bytes; the PATCH
   *  only ever takes an id. */
  //  Depend on the mutateAsync functions, not the mutation objects: react-query
  //  returns a NEW mutation object on every state change (including each
  //  upload's own pending/success transitions), so depending on those rebuilds
  //  this callback constantly and defeats the memo on the rows below it.
  //  mutateAsync itself is referentially stable.
  const { mutateAsync: uploadMediaAsync } = uploadMedia;
  const { mutateAsync: replaceClueImageAsync } = replaceClueImage;
  const onReplaceImage = useCallback(async (itemId: number, file: File) => {
    try {
      const media = await uploadMediaAsync(file);
      await replaceClueImageAsync({ itemId, mediaId: media.id });
    } catch (err) {
      notifyError(err);
    }
  }, [uploadMediaAsync, replaceClueImageAsync]);

  async function submitClue() {
    const note = clueNote.trim();
    // Either alone is enough; neither is nothing at all.
    if (!note && !clueFile) return;
    try {
      const media = clueFile ? await uploadMedia.mutateAsync(clueFile) : null;
      await addEvidence.mutateAsync({
        puzzleId: clueSource?.startsWith("p:") ? Number(clueSource.slice(2)) : null,
        sheetId: clueSource?.startsWith("s:") ? Number(clueSource.slice(2)) : null,
        note,
        mediaId: media ? media.id : null,
      });
      setClueNote("");
      setClueFile(null);
      // The source deliberately stays selected: logging several clues from the
      // same puzzle or sheet in a row is the common case during a hunt, and
      // re-picking it every time is the kind of friction that stops people
      // logging things at all.
    } catch (err) {
      notifyError(err);
    }
  }

  // Target options stay puzzles only, with bare-id values: a hunt sheet can be
  // where a datum came FROM, but never a puzzle it is routed TO.
  const allPuzzles = useMemo(() => {
    const seen = new Map<number, string>();
    detail.board.forEach((g) => g.puzzles.forEach((p) => seen.set(p.id, p.name)));
    return Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name }));
  }, [detail]);

  // Everything an evidence item can be sourced from. Built off `detail`, not
  // `items`: the picker must offer every puzzle and sheet, not only the ones
  // that already have evidence. The Map de-duplicates a puzzle that appears in
  // more than one group.
  const sourceChoices = useMemo(() => {
    const puzzles = new Map<number, string>();
    detail.board.forEach((g) => g.puzzles.forEach((p) => puzzles.set(p.id, p.name)));
    return [
      ...Array.from(puzzles.entries()).map(([id, name]) => ({
        value: `p:${id}`, label: name,
      })),
      // A sheet and a puzzle can share a name, and the picker is the only place
      // the difference is visible before the item is logged.
      ...detail.hunt_sheets.map((s) => ({
        value: `s:${s.id}`, label: `${s.name} (hunt sheet)`,
      })),
    ];
  }, [detail]);

  const sourceOptions = useMemo(() => {
    const puzzles = new Map<number, string>();
    const sheets = new Map<number, string>();
    const regions = new Map<number, string>();
    let hasSourceless = false;
    items.forEach((i) => {
      if (i.source_puzzle_id != null) puzzles.set(i.source_puzzle_id, i.source_puzzle_name ?? "");
      else if (i.source_sheet_id != null) sheets.set(i.source_sheet_id, i.source_sheet_name ?? "");
      // Before regions were resolved at all they landed in the else below and
      // were offered under "No source", which is exactly wrong: an
      // informs-region clue has a very specific source.
      else if (i.source_region_id != null) regions.set(i.source_region_id, i.source_region_name ?? "");
      else hasSourceless = true;
    });
    return [
      { value: "", label: "All sources" },
      // The filter is how someone would go looking for hunt-level evidence, so
      // give it an explicit label rather than an empty one.
      ...(hasSourceless ? [{ value: "none", label: "No source" }] : []),
      ...Array.from(puzzles.entries()).map(([id, name]) => ({ value: `p:${id}`, label: name })),
      ...Array.from(sheets.entries()).map(([id, name]) => ({
        value: `s:${id}`, label: `${name} (hunt sheet)`,
      })),
      ...Array.from(regions.entries()).map(([id, name]) => ({
        value: `r:${id}`, label: `${name} (canvas)`,
      })),
    ];
  }, [items]);

  const targetOptions = useMemo(() => {
    const seen = new Map<number, string>();
    let hasUnassigned = false;
    items.forEach((i) => {
      if (i.target_puzzle_id == null) hasUnassigned = true;
      else seen.set(i.target_puzzle_id, i.target_puzzle_name ?? "");
    });
    return [
      { value: "", label: "All targets" },
      ...(hasUnassigned ? [{ value: "unassigned", label: "Unassigned" }] : []),
      ...Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name })),
    ];
  }, [items]);

  const filtered = useMemo(() => {
    const text = filterText.trim().toLowerCase();
    return items
      .filter((i) => {
        if (!filterSource) return true;
        if (filterSource === "none") {
          return i.source_puzzle_id == null && i.source_sheet_id == null
            && i.source_region_id == null;
        }
        if (filterSource.startsWith("p:")) return String(i.source_puzzle_id) === filterSource.slice(2);
        if (filterSource.startsWith("r:")) return String(i.source_region_id) === filterSource.slice(2);
        return String(i.source_sheet_id) === filterSource.slice(2);
      })
      .filter((i) => {
        if (!filterTarget) return true;
        if (filterTarget === "unassigned") return i.target_puzzle_id == null;
        return String(i.target_puzzle_id) === filterTarget;
      })
      .filter((i) => !text || i.note.toLowerCase().includes(text));
  }, [items, filterSource, filterTarget, filterText]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const { key, direction } = sort;
    const dir = direction === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      // The Source column can hold a puzzle name OR a hunt sheet name, so the
      // sort must compare the same string the cell shows.
      const av = key === "source_puzzle_name" ? sourceLabel(a) : (a[key] ?? "");
      const bv = key === "source_puzzle_name" ? sourceLabel(b) : (b[key] ?? "");
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  }, [filtered, sort]);

  if (isLoading) return <Text size="sm" c="dimmed">Loading evidence…</Text>;
  if (error) return <Text size="sm" c="red">Failed to load evidence.</Text>;

  return (
    <Stack gap="sm">
      <Group gap="sm">
        <Select
          size="xs"
          placeholder="Filter by source puzzle"
          data={sourceOptions}
          value={filterSource ?? ""}
          onChange={(v) => setFilterSource(v || null)}
          searchable
          clearable
          w={240}
        />
        <Select
          size="xs"
          placeholder="Filter by target puzzle"
          data={targetOptions}
          value={filterTarget ?? ""}
          onChange={(v) => setFilterTarget(v || null)}
          searchable
          clearable
          w={240}
        />
        <TextInput
          size="xs"
          placeholder="Search note text…"
          value={filterText}
          onChange={(e) => setFilterText(e.currentTarget.value)}
          w={200}
        />
        <Text size="xs" c="dimmed">{filtered.length} item{filtered.length !== 1 ? "s" : ""}</Text>
      </Group>

      {editable && (
        <Group gap="sm">
          <Select
            size="xs"
            placeholder="Source …"
            data={sourceChoices}
            value={clueSource ?? ""}
            onChange={(v) => setClueSource(v || null)}
            searchable
            clearable
            w={240}
          />
          <Textarea
            size="xs"
            placeholder="Log a clue… (Ctrl/⌘+Enter to log)"
            value={clueNote}
            onChange={(e) => setClueNote(e.currentTarget.value)}
            // Enter inserts a line break; Ctrl/Cmd+Enter logs. Enter cannot do
            // both, and a clue transcribed off a grid or a verse is worth more
            // with its line structure intact than with a one-keystroke submit.
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                submitClue();
              }
            }}
            onPaste={onCluePaste}
            autosize
            minRows={1}
            maxRows={8}
            style={{ flex: 1 }}
          />
          <FileInput
            size="xs"
            placeholder="Image (or paste)"
            accept="image/png,image/jpeg,image/gif,image/webp"
            value={clueFile}
            onChange={setClueFile}
            clearable
            w={180}
          />
          <Button
            size="xs"
            variant="light"
            onClick={extractClueText}
            loading={ocrClueImage.isPending}
            disabled={!clueFile}
          >
            Extract text
          </Button>
          <Button
            size="xs"
            onClick={submitClue}
            loading={uploadMedia.isPending || addEvidence.isPending}
          >
            Log clue
          </Button>
        </Group>
      )}

      {filtered.length === 0 ? (
        <Box p="md">
          <Text size="sm" c="dimmed">No clues logged yet. Use <code>!clue</code> or <code>!clues</code> in any puzzle channel.</Text>
        </Box>
      ) : (
        <Table striped highlightOnHover withTableBorder withColumnBorders fz="sm">
          <Table.Thead>
            <Table.Tr>
              <SortableTh sortKey="source_puzzle_name" sort={sort} onSort={toggleSort}>Source</SortableTh>
              <Table.Th>Image</Table.Th>
              <SortableTh sortKey="note" sort={sort} onSort={toggleSort}>Note</SortableTh>
              <SortableTh sortKey="target_puzzle_name" sort={sort} onSort={toggleSort}>Target Puzzle</SortableTh>
              <SortableTh sortKey="created_at" sort={sort} onSort={toggleSort}>Logged At</SortableTh>
              {editable && <Table.Th w={40} />}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {sorted.map((item) => (
              <EvidenceRow
                key={item.id}
                item={item}
                puzzleOptions={allPuzzles}
                editable={editable}
                updateEvidenceMutate={updateEvidence.mutate}
                editClueNoteMutate={editClueNote.mutate}
                deleteEvidenceMutate={deleteEvidence.mutate}
                onReplaceImage={onReplaceImage}
                onMarkup={setMarkupItem}
              />
            ))}
          </Table.Tbody>
        </Table>
      )}
      <ConfirmModal
        opened={clueOcrConfirmOpen}
        onClose={() => setClueOcrConfirmOpen(false)}
        onConfirm={() => {
          if (pendingClueOcrText !== null) setClueNote(pendingClueOcrText);
          setClueOcrConfirmOpen(false);
        }}
        title="Replace existing note?"
        message="This will replace the current note text with the extracted text."
        confirmLabel="Replace"
      />
      <MarkupModal
        mediaId={markupItem?.media_id ?? null}
        alt={markupItem?.note ?? "Evidence image"}
        onClose={() => setMarkupItem(null)}
      />
    </Stack>
  );
}

function SortableTh({
  sortKey,
  sort,
  onSort,
  children,
}: {
  sortKey: SortKey;
  sort: { key: SortKey; direction: SortDirection } | null;
  onSort: (key: SortKey) => void;
  children: ReactNode;
}) {
  const active = sort?.key === sortKey;
  const Icon = active ? (sort!.direction === "asc" ? IconArrowUp : IconArrowDown) : IconArrowsSort;
  return (
    <Table.Th
      onClick={() => onSort(sortKey)}
      style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
    >
      <Group gap={4} wrap="nowrap">
        <Text size="sm" fw={active ? 700 : undefined}>{children}</Text>
        <Icon size={12} opacity={active ? 1 : 0.4} />
      </Group>
    </Table.Th>
  );
}

const EvidenceRow = memo(function EvidenceRow({
  item,
  puzzleOptions,
  editable,
  updateEvidenceMutate,
  editClueNoteMutate,
  deleteEvidenceMutate,
  onReplaceImage,
  onMarkup,
}: {
  item: EvidenceItem;
  puzzleOptions: { value: string; label: string }[];
  editable: boolean;
  updateEvidenceMutate: ReturnType<typeof useUpdateEvidence>["mutate"];
  editClueNoteMutate: ReturnType<typeof useEditClueNote>["mutate"];
  deleteEvidenceMutate: ReturnType<typeof useDeleteEvidence>["mutate"];
  onReplaceImage: (itemId: number, file: File) => void;
  onMarkup: (item: EvidenceItem) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  // created_at is a UTC-offset ISO string (#33); parse it so the display is in
  // the viewer's local time, like every other dashboard timestamp.
  const loggedAt = item.created_at ? new Date(item.created_at).toLocaleString() : "";

  const itemId = item.id;
  const itemNote = item.note;

  // updateEvidenceMutate/editClueNoteMutate/deleteEvidenceMutate must be the
  // bare .mutate function (react-query keeps that reference-stable), not the
  // useMutation() result object — that object is a fresh literal every
  // render, which would defeat the memo() above and re-render every row on
  // every keystroke anywhere in EvidencePanel.
  const onSetTarget = useCallback(
    (targetId: number | null) =>
      updateEvidenceMutate({ itemId, targetPuzzleId: targetId }, { onError: notifyError }),
    [updateEvidenceMutate, itemId]
  );
  const onEditNote = useCallback(
    (note: string) => editClueNoteMutate({ itemId, note }, { onError: notifyError }),
    [editClueNoteMutate, itemId]
  );
  const onDelete = useCallback(
    () => deleteEvidenceMutate(itemId, { onError: notifyError }),
    [deleteEvidenceMutate, itemId]
  );

  function startEdit() {
    setDraft(itemNote);
    setEditing(true);
  }

  function commitEdit() {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== itemNote) {
      onEditNote(trimmed);
    }
    setEditing(false);
  }

  function cancelEdit() {
    setEditing(false);
  }

  return (
    <Table.Tr>
      <Table.Td>{sourceLabel(item)}</Table.Td>
      <Table.Td>
        {/* Focus this cell (click the blank space beside the thumbnail, or Tab
            to it) and Ctrl+V to swap the picture. A paste fires only at the
            focused element, and a table of rows has no other way to say WHICH
            row a pasted screenshot belongs to — so the cell has to be the
            target rather than the panel. */}
        <Stack
          gap={4}
          align="flex-start"
          tabIndex={editable ? 0 : undefined}
          title={editable ? "Paste an image here to replace it" : undefined}
          onPaste={editable ? (e) => {
            const file = imageFromClipboard(e.clipboardData);
            if (!file) return;
            e.preventDefault();
            onReplaceImage(item.id, file);
          } : undefined}
          style={{ outlineOffset: 2 }}
        >
          {item.media_url ? (
            // The thumbnail IS the affordance: clicking it opens the markup
            // view, which shows the image far larger than the browser's own
            // image view did and can annotate it. No separate button.
            //
            // The archived fallback is the native link, not a disabled
            // thumbnail: marking up WRITES, and the markup routes refuse an
            // archived hunt with a 403, so on an archived hunt the click must
            // still do the one useful thing left — show the picture. (A
            // read-only markup view would need a mode MarkupCanvas does not
            // have yet.)
            editable ? (
              <UnstyledButton onClick={() => onMarkup(item)} title="Mark up">
                <Image
                  src={item.media_url}
                  alt={item.note || "evidence image"}
                  h={40}
                  w="auto"
                  fit="contain"
                />
              </UnstyledButton>
            ) : (
              <Anchor href={item.media_url} target="_blank" rel="noreferrer">
                <Image
                  src={item.media_url}
                  alt={item.note || "evidence image"}
                  h={40}
                  w="auto"
                  fit="contain"
                />
              </Anchor>
            )
          ) : null}
          {editable && (
            // Swapping the picture keeps the note, the target and the row's
            // place in the table — previously the only way to attach a clearer
            // version of a clue was to delete the item and log it again.
            <FileButton
              onChange={(f) => { if (f) onReplaceImage(item.id, f); }}
              accept="image/png,image/jpeg,image/gif,image/webp"
            >
              {(props) => (
                <Button {...props} size="compact-xs" variant="subtle"
                        title="Pick a file, or focus this cell and paste">
                  {item.media_url ? "Replace" : "Add image"}
                </Button>
              )}
            </FileButton>
          )}
        </Stack>
      </Table.Td>
      <Table.Td>
        {editable && editing ? (
          <Textarea
            size="xs"
            value={draft}
            onChange={(e) => setDraft(e.currentTarget.value)}
            // Same bargain as the clue entry box above: Enter is a line break,
            // Ctrl/Cmd+Enter commits. A single-line input here would flatten a
            // multi-line note the moment anyone opened it for editing.
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                commitEdit();
              }
              if (e.key === "Escape") cancelEdit();
            }}
            onBlur={commitEdit}
            autosize
            minRows={1}
            maxRows={8}
            autoFocus
          />
        ) : (
          <Text
            size="sm"
            // Line breaks in a note are content: HTML would collapse them.
            style={editable
              ? { cursor: "pointer", whiteSpace: "pre-wrap" }
              : { whiteSpace: "pre-wrap" }}
            onClick={editable ? startEdit : undefined}
            title={editable ? "Click to edit" : undefined}
          >
            {item.note}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Select
          size="xs"
          placeholder="Assign target…"
          data={puzzleOptions}
          value={item.target_puzzle_id ? String(item.target_puzzle_id) : null}
          onChange={(v) => onSetTarget(v ? Number(v) : null)}
          searchable
          clearable
          w={200}
          disabled={!editable}
        />
      </Table.Td>
      <Table.Td>{loggedAt}</Table.Td>
      {editable && (
        <Table.Td>
          <ActionIcon
            variant="subtle"
            color="red"
            size="sm"
            onClick={() => setConfirmOpen(true)}
            aria-label="Delete clue"
          >
            <IconTrash size={14} />
          </ActionIcon>
          <ConfirmModal
            opened={confirmOpen}
            onClose={() => setConfirmOpen(false)}
            onConfirm={() => { setConfirmOpen(false); onDelete(); }}
            title="Delete clue"
            message="Delete this clue? This cannot be undone."
            confirmLabel="Delete"
            confirmColor="red"
          />
        </Table.Td>
      )}
    </Table.Tr>
  );
});
