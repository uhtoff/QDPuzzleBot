import { useEffect, useState } from "react";
import { Alert, Button, Group, Modal, Stack, Text, Textarea } from "@mantine/core";
import { MAX_POST_NOTE, postErrorMessage } from "../lib/postToDiscord";

type Props = {
  opened: boolean;
  /** "the puzzle's channel" / "the hunt channel" (see postDestination). */
  destination: string;
  onClose: () => void;
  /** Renders, uploads and notifies; throws on failure. Resolves once posted. */
  onPost: (content: string) => Promise<void>;
  /** Text the message starts with when the dialog opens empty (a picture's caption). */
  initialContent?: string;
};

/** Asks for the optional message, then runs onPost. Failures stay in the
 *  dialog (with the draft intact) so the user can retry or cancel. */
export function PostToDiscordModal({ opened, destination, onClose, onPost, initialContent }: Props) {
  const [content, setContent] = useState("");
  // Start from the caption, but never overwrite what someone already typed.
  useEffect(() => {
    if (opened && initialContent) setContent((cur) => cur || initialContent.slice(0, MAX_POST_NOTE));
  }, [opened, initialContent]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    if (busy) return;
    setError(null);
    onClose();
  };

  const post = async () => {
    setBusy(true);
    setError(null);
    try {
      await onPost(content);
      setContent("");
      onClose();
    } catch (e) {
      setError(postErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={opened} onClose={close} title="Post to Discord" centered>
      <Stack gap="sm">
        <Text size="sm">Posting to {destination}</Text>
        <Textarea
          label="Message (optional)"
          autosize minRows={2} maxRows={8}
          maxLength={MAX_POST_NOTE}
          value={content}
          onChange={(e) => setContent(e.currentTarget.value)}
          disabled={busy}
        />
        {error && <Alert color="red">{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="default" onClick={close} disabled={busy}>Cancel</Button>
          <Button onClick={() => void post()} loading={busy}>Post</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
