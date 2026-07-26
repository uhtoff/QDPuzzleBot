import { memo, useCallback, useMemo, useState, type ReactNode } from "react";
import {
  ActionIcon,
  Anchor,
  Box,
  Button,
  FileInput,
  Group,
  Image,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { IconArrowDown, IconArrowsSort, IconArrowUp, IconTrash } from "@tabler/icons-react";
import type { EvidenceItem, HuntDetail } from "../types";
import {
  useAddEvidence,
  useDeleteEvidence,
  useEditClueNote,
  useEvidence,
  useUpdateEvidence,
  useUploadMedia,
} from "../api";
import { notifyError } from "../util";
import { ConfirmModal } from "./ConfirmModal";

interface Props {
  uid: string;
  detail: HuntDetail;
}

type SortKey = "source_puzzle_name" | "note" | "target_puzzle_name" | "created_at";
type SortDirection = "asc" | "desc";

export function EvidencePanel({ uid, detail }: Props) {
  const { data: items = [], isLoading, error } = useEvidence(uid);
  const updateEvidence = useUpdateEvidence(uid);
  const uploadMedia = useUploadMedia(uid);
  const addEvidence = useAddEvidence(uid);
  const deleteEvidence = useDeleteEvidence(uid);
  const editClueNote = useEditClueNote(uid);
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

  const [cluePuzzleId, setCluePuzzleId] = useState<string | null>(null);
  const [clueNote, setClueNote] = useState("");
  const [clueFile, setClueFile] = useState<File | null>(null);

  const editable = !detail.hunt.archived;

  async function submitClue() {
    const note = clueNote.trim();
    // Either alone is enough; neither is nothing at all.
    if (!note && !clueFile) return;
    try {
      const media = clueFile ? await uploadMedia.mutateAsync(clueFile) : null;
      await addEvidence.mutateAsync({
        puzzleId: cluePuzzleId ? Number(cluePuzzleId) : null,
        note,
        mediaId: media ? media.id : null,
      });
      setClueNote("");
      setClueFile(null);
      setCluePuzzleId(null);
    } catch (err) {
      notifyError(err);
    }
  }

  const allPuzzles = useMemo(() => {
    const seen = new Map<number, string>();
    detail.board.forEach((g) => g.puzzles.forEach((p) => seen.set(p.id, p.name)));
    return Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name }));
  }, [detail]);

  const sourceOptions = useMemo(() => {
    const seen = new Map<number, string>();
    let hasSourceless = false;
    items.forEach((i) => {
      if (i.source_puzzle_id == null) hasSourceless = true;
      else seen.set(i.source_puzzle_id, i.source_puzzle_name ?? "");
    });
    return [
      { value: "", label: "All sources" },
      // The filter is how someone would go looking for hunt-level evidence, so
      // give it an explicit label rather than an empty one.
      ...(hasSourceless ? [{ value: "none", label: "No source" }] : []),
      ...Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name })),
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
        if (filterSource === "none") return i.source_puzzle_id == null;
        return String(i.source_puzzle_id) === filterSource;
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
      const av = a[key] ?? "";
      const bv = b[key] ?? "";
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
            placeholder="Source puzzle …"
            data={allPuzzles}
            value={cluePuzzleId ?? ""}
            onChange={(v) => setCluePuzzleId(v || null)}
            searchable
            clearable
            w={240}
          />
          <TextInput
            size="xs"
            placeholder="Log a clue…"
            value={clueNote}
            onChange={(e) => setClueNote(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && submitClue()}
            style={{ flex: 1 }}
          />
          <FileInput
            size="xs"
            placeholder="Image (optional)"
            accept="image/png,image/jpeg,image/gif,image/webp"
            value={clueFile}
            onChange={setClueFile}
            clearable
            w={180}
          />
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
              <SortableTh sortKey="source_puzzle_name" sort={sort} onSort={toggleSort}>Source Puzzle</SortableTh>
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
              />
            ))}
          </Table.Tbody>
        </Table>
      )}
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
}: {
  item: EvidenceItem;
  puzzleOptions: { value: string; label: string }[];
  editable: boolean;
  updateEvidenceMutate: ReturnType<typeof useUpdateEvidence>["mutate"];
  editClueNoteMutate: ReturnType<typeof useEditClueNote>["mutate"];
  deleteEvidenceMutate: ReturnType<typeof useDeleteEvidence>["mutate"];
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
      <Table.Td>{item.source_puzzle_name ?? "—"}</Table.Td>
      <Table.Td>
        {item.media_url ? (
          <Anchor href={item.media_url} target="_blank" rel="noreferrer">
            <Image
              src={item.media_url}
              alt={item.note || "evidence image"}
              h={40}
              w="auto"
              fit="contain"
            />
          </Anchor>
        ) : null}
      </Table.Td>
      <Table.Td>
        {editable && editing ? (
          <TextInput
            size="xs"
            value={draft}
            onChange={(e) => setDraft(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitEdit();
              if (e.key === "Escape") cancelEdit();
            }}
            onBlur={commitEdit}
            autoFocus
          />
        ) : (
          <Text
            size="sm"
            style={editable ? { cursor: "pointer" } : undefined}
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
