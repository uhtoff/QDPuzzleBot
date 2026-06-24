import { Button, Modal, Stack, Text } from "@mantine/core";

/**
 * Simple confirmation modal for reversible actions — no text input required.
 * Use TypedConfirmModal for destructive/irreversible actions that need friction.
 */
export function ConfirmModal({
  opened,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = "Confirm",
  confirmColor = "blue",
}: {
  opened: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  confirmLabel?: string;
  confirmColor?: string;
}) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} centered>
      <Stack>
        <Text size="sm">{message}</Text>
        <Button color={confirmColor} onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </Stack>
    </Modal>
  );
}
