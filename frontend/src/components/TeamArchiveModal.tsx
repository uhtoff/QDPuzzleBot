import { Alert, Button, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";

import { useArchiveToHub } from "../api";

interface Props {
  uid: string;
  huntName: string;
  opened: boolean;
  onClose: () => void;
}

/** Archive a team hunt onto its hub: it moves the history, then the bot leaves
 *  the team server. The archive runs in the background, so success is just
 *  "started" plus where to watch. */
export function TeamArchiveModal({ uid, huntName, opened, onClose }: Props) {
  const [typed, setTyped] = useState("");
  const mut = useArchiveToHub(uid);
  const started = mut.data?.started ? mut.data : null;

  const close = () => {
    setTyped("");
    mut.reset();
    onClose();
  };

  return (
    <Modal opened={opened} onClose={close} title="Archive team hunt" centered>
      {started ? (
        <Stack gap="md">
          <Text size="sm">
            Archiving started — progress and the result will appear in{" "}
            {started.progress_channel
              ? `#${started.progress_channel} on the hub`
              : "the hub's archive channel"}
            .
          </Text>
          <Group justify="flex-end">
            <Button onClick={close}>Close</Button>
          </Group>
        </Stack>
      ) : (
        <Stack gap="md">
          <Text size="sm" c="red">
            This moves the hunt&apos;s history to the hub and the bot leaves this team server.
            It cannot be undone.
          </Text>
          <TextInput
            label={`Type the hunt name (${huntName}) to confirm`}
            value={typed}
            onChange={(e) => setTyped(e.currentTarget.value)}
            data-autofocus
          />
          {mut.error && <Alert color="red">{(mut.error as Error).message}</Alert>}
          <Group justify="flex-end">
            <Button variant="default" onClick={close}>
              Cancel
            </Button>
            <Button
              color="red"
              disabled={typed !== huntName}
              loading={mut.isPending}
              onClick={() => mut.mutate(typed)}
            >
              Archive to hub
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
