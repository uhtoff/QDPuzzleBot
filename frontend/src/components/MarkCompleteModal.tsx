import { Button, Group, Modal, Stack, Text } from "@mantine/core";

/** Confirms marking a team complete: it opens the spoiler-filled finishers channel to them. */
export function MarkCompleteModal({
  opened,
  team,
  loading = false,
  onClose,
  onConfirm,
}: {
  opened: boolean;
  team: string;
  loading?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal opened={opened} onClose={onClose} title="Mark complete" centered>
      <Stack>
        <Text size="sm">
          Mark <b>{team}</b> complete? They&apos;ll see the finishers channel, which has spoilers.
        </Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={onConfirm} loading={loading}>
            Mark complete
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
