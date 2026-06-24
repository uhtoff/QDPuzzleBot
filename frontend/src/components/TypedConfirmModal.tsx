import { useEffect, useState, type ReactNode } from "react";
import { Button, Code, Modal, Stack, TextInput } from "@mantine/core";

/**
 * Shared gate for destructive actions: the user must type an exact phrase
 * (usually the thing's name) before the confirm button enables. Reused across
 * the destructive web-parity surface (convert-to-feeder, delete puzzle/round,
 * reconcile orphan deletion). The body (`children`) carries the impact summary —
 * orphan counts, puzzle/channel counts, the orphan list — so each caller spells
 * out exactly what will be destroyed.
 */
export function TypedConfirmModal({
  opened,
  onClose,
  onConfirm,
  title,
  confirmPhrase,
  confirmLabel = "Delete",
  loading = false,
  children,
}: {
  opened: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  /** Exact text the user must type to enable the confirm button. */
  confirmPhrase: string;
  confirmLabel?: string;
  loading?: boolean;
  children?: ReactNode;
}) {
  const [typed, setTyped] = useState("");
  // Clear the field whenever the modal closes so a reopen starts blank.
  useEffect(() => {
    if (!opened) setTyped("");
  }, [opened]);

  const matches = typed.trim() === confirmPhrase.trim();

  return (
    <Modal opened={opened} onClose={onClose} title={title} centered>
      <Stack>
        {children}
        <TextInput
          label={
            <>
              Type <Code>{confirmPhrase}</Code> to confirm
            </>
          }
          value={typed}
          onChange={(e) => setTyped(e.currentTarget.value)}
          data-autofocus
          onKeyDown={(e) => {
            if (e.key === "Enter" && matches && !loading) onConfirm();
          }}
        />
        <Button color="red" disabled={!matches} loading={loading} onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </Stack>
    </Modal>
  );
}
