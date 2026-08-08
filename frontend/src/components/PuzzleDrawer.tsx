import { useState } from "react";
import { useDisclosure } from "@mantine/hooks";
import {
  ActionIcon,
  Anchor,
  Badge,
  Button,
  Divider,
  Drawer,
  Group,
  Popover,
  ScrollArea,
  Stack,
  Text,
  Textarea,
  TextInput,
} from "@mantine/core";
import { IconCircleCheck, IconEdit, IconPlus, IconSend, IconTrash } from "@tabler/icons-react";
import type { HuntDetail, Puzzle } from "../types";
import { notifications } from "@mantine/notifications";
import {
  useAddNote,
  useAddSheet,
  useDeletePuzzle,
  useEraseNote,
  useRemoveSheet,
  useRenamePuzzle,
  useSetPuzzleLink,
  useUpdatePuzzle,
} from "../api";
import { PriorityCell, SolveModal, StatusCell, TypeCell } from "./PuzzleEditCells";
import { ConfirmModal } from "./ConfirmModal";
import { PuzzleImages } from "./PuzzleImages";
import { TypedConfirmModal } from "./TypedConfirmModal";
import { formatPriority, formatStatus, notifyError, showSheetWarning } from "../util";

interface Props {
  puzzle: Puzzle | null;
  detail: HuntDetail;
  onClose: () => void;
}

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Group justify="space-between" wrap="nowrap">
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      {children}
    </Group>
  );
}

export function PuzzleDrawer({ puzzle, detail, onClose }: Props) {
  const uid = detail.hunt.uid;
  const editable = !detail.hunt.archived;
  const [text, setText] = useState("");
  const [sheetName, setSheetName] = useState("");
  const [linkUrl, setLinkUrl] = useState("");
  const [solveOpen, setSolveOpen] = useState(false);
  const [linkOpened, { open: openLink, close: closeLink }] = useDisclosure(false);
  const [renameEditing, setRenameEditing] = useState(false);
  const [renameTemp, setRenameTemp] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [eraseIndex, setEraseIndex] = useState<number | null>(null);
  const canManage = detail.hunt.can_manage;

  const add = useAddNote(uid);
  const deletePuzzleMut = useDeletePuzzle(uid);
  const erase = useEraseNote(uid);
  const update = useUpdatePuzzle(uid);
  const setLink = useSetPuzzleLink(uid);
  const addSheet = useAddSheet(uid);
  const removeSheet = useRemoveSheet(uid);
  const rename = useRenamePuzzle(uid);

  function submitNote() {
    const note = text.trim();
    if (!note || !puzzle) return;
    add.mutate({ puzzleId: puzzle.id, note }, { onSuccess: () => setText(""), onError: notifyError });
  }

  function submitSheet() {
    if (!puzzle) return;
    addSheet.mutate(
      { puzzleId: puzzle.id, name: sheetName.trim() },
      { onSuccess: () => setSheetName(""), onError: notifyError }
    );
  }

  function runDeletePuzzle() {
    if (!puzzle) return;
    const name = puzzle.name;
    deletePuzzleMut.mutate(puzzle.id, {
      onSuccess: (data) => {
        showSheetWarning(data);
        notifications.show({ message: `"${name}" permanently deleted.`, color: "green" });
        setDeleteOpen(false);
        onClose();
      },
      onError: notifyError,
    });
  }

  const sheetBase = puzzle?.on_solved_sheet && detail.hunt.solved_sheet_base_url
    ? detail.hunt.solved_sheet_base_url
    : detail.hunt.sheet_base_url;

  return (
    <Drawer
      opened={puzzle !== null}
      onClose={onClose}
      position="right"
      size="md"
      title={
        puzzle && editable ? (
          <Group gap={4} justify="center" wrap="nowrap">
            {renameEditing ? (
              <TextInput
                size="sm"
                value={renameTemp}
                onChange={(e) => setRenameTemp(e.currentTarget.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    const newName = renameTemp.trim();
                    if (newName && puzzle.id !== undefined) {
                      rename.mutate({ puzzleId: puzzle.id, name: newName }, {
                        onSuccess: (data) => { showSheetWarning(data); setRenameEditing(false); },
                        onError: () => setRenameEditing(false),
                      });
                    } else {
                      setRenameEditing(false);
                    }
                  }
                  if (e.key === "Escape") setRenameEditing(false);
                }}
                autoFocus
              />
            ) : (
              <>
                <Text size="sm" fw={600} style={{ cursor: "pointer" }}
                  onClick={() => { setRenameEditing(true); setRenameTemp(puzzle.name); }}
                >
                  {puzzle.name}
                </Text>
                <ActionIcon variant="subtle" size="sm" color="gray"
                  onClick={() => { setRenameEditing(true); setRenameTemp(puzzle.name); }}
                >
                  <IconEdit size={14} />
                </ActionIcon>
              </>
            )}
          </Group>
        ) : (puzzle ? puzzle.name : "Puzzle")
      }
    >
      {puzzle && (
        <Stack>
          {editable ? (
            <Stack gap="xs">
              <MetaRow label="Status">
                <StatusCell
                  value={puzzle.status}
                  solved={puzzle.solved}
                  onChange={(status) =>
                    update.mutate({ puzzleId: puzzle.id, patch: { status } }, { onError: notifyError })
                  }
                />
              </MetaRow>
              <MetaRow label="Priority">
                <PriorityCell
                  value={puzzle.priority}
                  onChange={(priority) =>
                    update.mutate({ puzzleId: puzzle.id, patch: { priority } }, { onError: notifyError })
                  }
                />
              </MetaRow>
              <MetaRow label="Type">
                <TypeCell
                  value={puzzle.puzzle_type}
                  onSave={(puzzle_type) =>
                    update.mutate({ puzzleId: puzzle.id, patch: { puzzle_type } }, { onError: notifyError })
                  }
                />
              </MetaRow>
              <MetaRow label="Link">
                <Popover opened={linkOpened} onClose={closeLink} position="bottom-start" withArrow shadow="md">
                  <Popover.Target>
                    <Text
                      size="sm"
                      c={!puzzle.url ? "dimmed" : undefined}
                      style={{ cursor: "pointer" }}
                      onClick={() => {
                        setLinkUrl(puzzle.url || "");
                        openLink();
                      }}
                    >
                      {puzzle.url || "set link"}
                    </Text>
                  </Popover.Target>
                  <Popover.Dropdown>
                    <Group gap="xs">
                      <TextInput
                        size="xs"
                        placeholder="https://…"
                        value={linkUrl}
                        onChange={(e) => setLinkUrl(e.currentTarget.value)}
                        onKeyDown={(e) => e.key === "Enter" && setLink.mutate(
                          { puzzleId: puzzle.id, url: linkUrl.trim() },
                          { onSuccess: () => closeLink(), onError: notifyError }
                        )}
                        data-autofocus
                      />
                      <Button
                        size="xs"
                        onClick={() => {
                          setLink.mutate(
                            { puzzleId: puzzle.id, url: linkUrl.trim() },
                            { onSuccess: () => closeLink(), onError: notifyError }
                          );
                        }}
                        loading={setLink.isPending}
                      >
                        Save
                      </Button>
                    </Group>
                  </Popover.Dropdown>
                </Popover>
              </MetaRow>
            </Stack>
          ) : (
            <Group gap="xs">
              {puzzle.status && <Badge variant="light">{formatStatus(puzzle.status)}</Badge>}
              {puzzle.priority && <Badge variant="dot">{formatPriority(puzzle.priority)}</Badge>}
              {puzzle.puzzle_type && <Text size="sm">{puzzle.puzzle_type}</Text>}
            </Group>
          )}

          {puzzle.solved && puzzle.solution ? (
            <Badge color="green" variant="light" size="lg">
              Solved: {puzzle.solution}
            </Badge>
          ) : (
            editable && (
              <Button
                color="green"
                variant="light"
                leftSection={<IconCircleCheck size={16} />}
                onClick={() => setSolveOpen(true)}
              >
                Mark solved
              </Button>
            )
          )}

          <Divider label="Images" labelPosition="left" />
          <PuzzleImages puzzleId={puzzle.id} puzzleName={puzzle.name} uid={uid} editable={editable} />

          <Divider label="Sheets" labelPosition="left" />
          <Stack gap="xs">
            {puzzle.sheets.length === 0 && (
              <Text c="dimmed" size="sm">
                No additional sheets.
              </Text>
            )}
            {puzzle.sheets.map((s) => {
              const url = sheetBase && s.page_id ? `${sheetBase}${s.page_id}` : null;
              return (
                <Group key={s.id} justify="space-between" wrap="nowrap">
                  {url ? (
                    <Anchor href={url} target="_blank" size="sm" lineClamp={1}>
                      {s.name}
                    </Anchor>
                  ) : (
                    <Text size="sm" lineClamp={1}>
                      {s.name}
                    </Text>
                  )}
                  {editable && (
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      onClick={() =>
                        removeSheet.mutate({ puzzleId: puzzle.id, sheetId: s.id }, { onError: notifyError })
                      }
                      loading={removeSheet.isPending}
                      aria-label="Remove sheet"
                    >
                      <IconTrash size={14} />
                    </ActionIcon>
                  )}
                </Group>
              );
            })}
            {editable && !puzzle.solved && (
              <Group gap="xs" wrap="nowrap">
                <TextInput
                  placeholder="New sheet name (optional)"
                  value={sheetName}
                  onChange={(e) => setSheetName(e.currentTarget.value)}
                  onKeyDown={(e) => e.key === "Enter" && submitSheet()}
                  style={{ flex: 1 }}
                />
                <Button
                  variant="light"
                  leftSection={<IconPlus size={14} />}
                  onClick={submitSheet}
                  loading={addSheet.isPending}
                >
                  Add
                </Button>
              </Group>
            )}
          </Stack>

          <Divider label="Notes" labelPosition="left" />
          <ScrollArea.Autosize mah={300}>
            <Stack gap="xs">
              {puzzle.notes.length === 0 && (
                <Text c="dimmed" size="sm">
                  No notes yet.
                </Text>
              )}
              {puzzle.notes.map((note, i) => (
                <Group key={`${i}-${note}`} justify="space-between" wrap="nowrap" align="flex-start">
                  <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
                    {note}
                  </Text>
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    onClick={() => setEraseIndex(i + 1)}
                    aria-label="Delete note"
                  >
                    <IconTrash size={14} />
                  </ActionIcon>
                </Group>
              ))}
            </Stack>
          </ScrollArea.Autosize>

          <Textarea
            placeholder="Add a note…"
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
            autosize
            minRows={2}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitNote();
            }}
          />
          {add.isError && (
            <Text c="red" size="xs">
              {(add.error as Error).message}
            </Text>
          )}
          <Button leftSection={<IconSend size={16} />} onClick={submitNote} loading={add.isPending} disabled={!text.trim()}>
            Add note
          </Button>

          {/* Target puzzles (meta/capstone/runaround) can't be deleted directly —
              it would orphan the owning tag group. The server rejects it too. */}
          {editable && canManage && !puzzle.rounds.some((r) => r.is_target_of) && (
            <>
              <Divider />
              <Button
                color="red"
                variant="light"
                leftSection={<IconTrash size={16} />}
                onClick={() => setDeleteOpen(true)}
              >
                Delete puzzle…
              </Button>
            </>
          )}

          <ConfirmModal
            opened={eraseIndex !== null}
            onClose={() => setEraseIndex(null)}
            onConfirm={() => {
              if (eraseIndex !== null) {
                erase.mutate({ puzzleId: puzzle.id, index: eraseIndex }, { onError: notifyError });
              }
              setEraseIndex(null);
            }}
            title="Delete note"
            message="Delete this note?"
            confirmLabel="Delete"
            confirmColor="red"
          />

          <SolveModal puzzle={solveOpen ? puzzle : null} uid={uid} onClose={() => setSolveOpen(false)} />

          <TypedConfirmModal
            opened={deleteOpen}
            onClose={() => setDeleteOpen(false)}
            onConfirm={runDeletePuzzle}
            title={`Delete "${puzzle.name}"`}
            confirmPhrase={puzzle.name}
            confirmLabel="Delete puzzle"
            loading={deletePuzzleMut.isPending}
          >
            <Text size="sm" c="red">
              ⚠️ This permanently deletes the puzzle&rsquo;s Discord channel and Google sheet tab.
              This cannot be undone.
            </Text>
          </TypedConfirmModal>
        </Stack>
      )}
    </Drawer>
  );
}
