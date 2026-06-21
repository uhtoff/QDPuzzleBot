import { useState } from "react";
import {
  ActionIcon,
  Button,
  Divider,
  Drawer,
  Group,
  ScrollArea,
  Stack,
  Text,
  Textarea,
} from "@mantine/core";
import { IconSend, IconTrash } from "@tabler/icons-react";
import type { Puzzle } from "../types";
import { useAddNote, useEraseNote } from "../api";

interface Props {
  puzzle: Puzzle | null;
  huntUid: string;
  onClose: () => void;
}

export function NotesDrawer({ puzzle, huntUid, onClose }: Props) {
  const [text, setText] = useState("");
  const add = useAddNote(huntUid);
  const erase = useEraseNote(huntUid);

  function submit() {
    const note = text.trim();
    if (!note || !puzzle) return;
    add.mutate({ puzzleId: puzzle.id, note }, { onSuccess: () => setText("") });
  }

  return (
    <Drawer
      opened={puzzle !== null}
      onClose={onClose}
      position="right"
      size="md"
      title={puzzle ? `Notes — ${puzzle.name}` : "Notes"}
    >
      {puzzle && (
        <Stack>
          <ScrollArea.Autosize mah={420}>
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

          <Divider />

          <Textarea
            placeholder="Add a note…"
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
            autosize
            minRows={2}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
            }}
          />
          {add.isError && (
            <Text c="red" size="xs">
              {(add.error as Error).message}
            </Text>
          )}
          <Button
            leftSection={<IconSend size={16} />}
            onClick={submit}
            loading={add.isPending}
            disabled={!text.trim()}
          >
            Add note
          </Button>
        </Stack>
      )}
    </Drawer>
  );
}
