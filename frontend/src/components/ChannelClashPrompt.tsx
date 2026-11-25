import { useState, type ReactNode } from "react";
import { Button, Group, Modal, Stack, Text } from "@mantine/core";
import { ApiError } from "../api";

export interface ChannelClash {
  channel: string;
  suggested: string;
}

/** The clash a create/rename was refused for (409 channel_name_clash), or null. */
export function channelClashOf(err: unknown): ChannelClash | null {
  if (!(err instanceof ApiError) || err.slug !== "channel_name_clash") return null;
  const { channel, suggested } = err.detail;
  return typeof channel === "string" && typeof suggested === "string"
    ? { channel, suggested }
    : null;
}

/**
 * Accept/Cancel for a puzzle whose Discord channel name is already taken in its
 * category. `handle(err, accept)` returns false for any other error (the caller
 * reports it as usual); for a clash it opens the prompt, and Accept calls
 * `accept` — the caller's retry with acceptChannelSuffix. Render `modal`.
 */
export function useChannelClashPrompt(): {
  handle: (err: unknown, accept: () => void) => boolean;
  modal: ReactNode;
} {
  const [pending, setPending] = useState<{ clash: ChannelClash; accept: () => void } | null>(null);
  const handle = (err: unknown, accept: () => void) => {
    const clash = channelClashOf(err);
    if (!clash) return false;
    setPending({ clash, accept });
    return true;
  };
  const close = () => setPending(null);
  const modal = (
    <Modal opened={pending !== null} onClose={close} title="Channel name already in use" centered>
      {pending && (
        <Stack>
          <Text size="sm">
            This would lead to two channels named <b>#{pending.clash.channel}</b>. The
            puzzle&apos;s channel will be named <b>#{pending.clash.suggested}</b> to avoid
            confusion.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={close}>Cancel</Button>
            <Button
              onClick={() => {
                const accept = pending.accept;
                close();
                accept();
              }}
            >
              Accept
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
  return { handle, modal };
}
