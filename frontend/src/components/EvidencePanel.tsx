import { useMemo, useState } from "react";
import {
  ActionIcon,
  Box,
  Button,
  Group,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import type { EvidenceItem, HuntDetail } from "../types";
import { useDeleteEvidence, useEditClueNote, useEvidence, useLogClue, useUpdateEvidence } from "../api";
import { ConfirmModal } from "./ConfirmModal";

interface Props {
  uid: string;
  detail: HuntDetail;
}

export function EvidencePanel({ uid, detail }: Props) {
  const { data: items = [], isLoading, error } = useEvidence(uid);
  const updateEvidence = useUpdateEvidence(uid);
  const logClue = useLogClue(uid);
  const deleteEvidence = useDeleteEvidence(uid);
  const editClueNote = useEditClueNote(uid);
  const [filterSource, setFilterSource] = useState<string | null>(null);

  const [cluePuzzleId, setCluePuzzleId] = useState<string | null>(null);
  const [clueNote, setClueNote] = useState("");

  const editable = !detail.hunt.archived;

  function submitClue() {
    if (!cluePuzzleId || !clueNote.trim()) return;
    logClue.mutate(
      { puzzleId: Number(cluePuzzleId), note: clueNote.trim() },
      { onSuccess: () => { setCluePuzzleId(null); setClueNote(""); } }
    );
  }

  const allPuzzles = useMemo(() => {
    const seen = new Map<number, string>();
    detail.board.forEach((g) => g.puzzles.forEach((p) => seen.set(p.id, p.name)));
    return Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name }));
  }, [detail]);

  const sourceOptions = useMemo(() => {
    const seen = new Map<number, string>();
    items.forEach((i) => seen.set(i.source_puzzle_id, i.source_puzzle_name));
    return [{ value: "", label: "All sources" }, ...Array.from(seen.entries()).map(([id, name]) => ({ value: String(id), label: name }))];
  }, [items]);

  const filtered = filterSource
    ? items.filter((i) => String(i.source_puzzle_id) === filterSource)
    : items;

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
          clearable
          w={240}
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
          <Button size="xs" onClick={submitClue} loading={logClue.isPending}>Log clue</Button>
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
              <Table.Th>Source Puzzle</Table.Th>
              <Table.Th>Note</Table.Th>
              <Table.Th>Target Puzzle</Table.Th>
              <Table.Th>Logged At</Table.Th>
              {editable && <Table.Th w={40} />}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.map((item) => (
              <EvidenceRow
                key={item.id}
                item={item}
                puzzleOptions={allPuzzles}
                editable={editable}
                onSetTarget={(targetId) =>
                  updateEvidence.mutate({ itemId: item.id, targetPuzzleId: targetId })
                }
                onEditNote={(note) =>
                  editClueNote.mutate({ itemId: item.id, note })
                }
                onDelete={() =>
                  deleteEvidence.mutate(item.id)
                }
              />
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

function EvidenceRow({
  item,
  puzzleOptions,
  editable,
  onSetTarget,
  onEditNote,
  onDelete,
}: {
  item: EvidenceItem;
  puzzleOptions: { value: string; label: string }[];
  editable: boolean;
  onSetTarget: (targetId: number | null) => void;
  onEditNote: (note: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const loggedAt = item.created_at ? item.created_at.slice(0, 16).replace("T", " ") : "";

  function startEdit() {
    setDraft(item.note);
    setEditing(true);
  }

  function commitEdit() {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== item.note) {
      onEditNote(trimmed);
    }
    setEditing(false);
  }

  function cancelEdit() {
    setEditing(false);
  }

  return (
    <Table.Tr>
      <Table.Td>{item.source_puzzle_name}</Table.Td>
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
}
