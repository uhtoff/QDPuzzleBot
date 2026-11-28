import { useState } from "react";
import { ActionIcon, Box, Group, Text, Textarea, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck, IconPencil, IconX } from "@tabler/icons-react";
import { useMediaInfo, useUpdateCaption } from "../api";
import { DiscordMarkdown } from "./DiscordMarkdown";

/** The mark-up view's title: "Mark up" and the picture's caption, which anyone
 *  who can mark the picture up can edit here. A caption belongs to the picture,
 *  so it shows the same wherever the picture appears (drawer, Evidence, the
 *  puzzle's pinned info in Discord). Keys match the other multi-line editors:
 *  Enter breaks the line, Ctrl/⌘+Enter saves, Escape cancels. */
export function CaptionHeader({ mediaId, editable = true }: { mediaId: number; editable?: boolean }) {
  const info = useMediaInfo(mediaId);
  const update = useUpdateCaption(mediaId);
  const [draft, setDraft] = useState<string | null>(null);
  const caption = info.data?.caption ?? null;

  const save = () => {
    if (draft === null) return;
    update.mutate(draft, {
      onSuccess: () => setDraft(null),
      onError: (e) => notifications.show({ message: (e as Error).message, color: "red" }),
    });
  };

  if (draft !== null) {
    return (
      <Group gap="xs" wrap="nowrap" align="flex-start">
        <Text fw={600} mt={6}>Mark up</Text>
        <Textarea
          autoFocus
          autosize
          minRows={1}
          maxRows={4}
          w={420}
          maxLength={1000}
          value={draft}
          placeholder="Caption (markdown), then Ctrl/⌘+Enter"
          aria-label="Caption"
          onChange={(e) => setDraft(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              // Cancel the edit, not the whole mark-up view.
              e.stopPropagation();
              setDraft(null);
            }
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
          }}
        />
        <ActionIcon variant="light" color="green" mt={4} aria-label="Save caption"
                    loading={update.isPending} onClick={save}>
          <IconCheck size={16} />
        </ActionIcon>
        <ActionIcon variant="subtle" color="gray" mt={4} aria-label="Cancel caption"
                    onClick={() => setDraft(null)}>
          <IconX size={16} />
        </ActionIcon>
      </Group>
    );
  }

  return (
    <Group gap="sm" wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
      <Text fw={600}>Mark up</Text>
      {caption ? (
        <Box style={{ minWidth: 0, maxHeight: "4.5em", overflowY: "auto" }}>
          <DiscordMarkdown text={caption} />
        </Box>
      ) : (
        editable && <Text size="sm" c="dimmed">No caption</Text>
      )}
      {editable && (
        <Tooltip label={caption ? "Edit caption" : "Add a caption"}>
          <ActionIcon variant="subtle" color="gray" size="sm" aria-label="Edit caption"
                      onClick={() => setDraft(caption ?? "")}>
            <IconPencil size={14} />
          </ActionIcon>
        </Tooltip>
      )}
    </Group>
  );
}
