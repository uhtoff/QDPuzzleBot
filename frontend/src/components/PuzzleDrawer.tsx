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
import { IconCircleCheck, IconPlus, IconSend, IconTrash } from "@tabler/icons-react";
import type { HuntDetail, Puzzle } from "../types";
import {
  useAddNote,
  useAddSheet,
  useEraseNote,
  useRemoveSheet,
  useSetPuzzleLink,
  useUpdatePuzzle,
} from "../api";
import { PriorityCell, SolveModal, StatusCell, TypeCell } from "./PuzzleEditCells";

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
  const add = useAddNote(uid);
  const erase = useEraseNote(uid);
  const update = useUpdatePuzzle(uid);
  const setLink = useSetPuzzleLink(uid);
  const addSheet = useAddSheet(uid);
  const removeSheet = useRemoveSheet(uid);

  function submitNote() {
    const note = text.trim();
    if (!note || !puzzle) return;
    add.mutate({ puzzleId: puzzle.id, note }, { onSuccess: () => setText("") });
  }

  function submitSheet() {
    if (!puzzle) return;
    addSheet.mutate(
      { puzzleId: puzzle.id, name: sheetName.trim() },
      { onSuccess: () => setSheetName("") }
    );
  }

  const sheetBase = puzzle?.archived && detail.hunt.archive_sheet_base_url
    ? detail.hunt.archive_sheet_base_url
    : detail.hunt.sheet_base_url;

  return (
    <Drawer
      opened={puzzle !== null}
      onClose={onClose}
      position="right"
      size="md"
      title={puzzle ? puzzle.name : "Puzzle"}
    >
      {puzzle && (
        <Stack>
          {editable ? (
            <Stack gap="xs">
              <MetaRow label="Status">
                <StatusCell
                  value={puzzle.status}
                  onChange={(status) => update.mutate({ puzzleId: puzzle.id, patch: { status } })}
                />
              </MetaRow>
              <MetaRow label="Priority">
                <PriorityCell
                  value={puzzle.priority}
                  onChange={(priority) => update.mutate({ puzzleId: puzzle.id, patch: { priority } })}
                />
              </MetaRow>
              <MetaRow label="Type">
                <TypeCell
                  value={puzzle.puzzle_type}
                  onSave={(puzzle_type) => update.mutate({ puzzleId: puzzle.id, patch: { puzzle_type } })}
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
                          { onSuccess: () => closeLink() }
                        )}
                        data-autofocus
                      />
                      <Button
                        size="xs"
                        onClick={() => {
                          setLink.mutate(
                            { puzzleId: puzzle.id, url: linkUrl.trim() },
                            { onSuccess: () => closeLink() }
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
              {puzzle.status && <Badge variant="light">{puzzle.status}</Badge>}
              {puzzle.priority && <Badge variant="dot">{puzzle.priority}</Badge>}
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
                      onClick={() => removeSheet.mutate({ puzzleId: puzzle.id, sheetId: s.id })}
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
                    onClick={() => erase.mutate({ puzzleId: puzzle.id, index: i + 1 })}
                    loading={erase.isPending}
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

          <SolveModal puzzle={solveOpen ? puzzle : null} uid={uid} onClose={() => setSolveOpen(false)} />
        </Stack>
      )}
    </Drawer>
  );
}
