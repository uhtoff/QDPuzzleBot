import { Badge, Button, Group, Modal, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconBrandGoogle, IconLinkOff, IconRefresh } from "@tabler/icons-react";
import { googleLinkUrl, useUnlinkGoogle } from "../api";
import type { GoogleAccountLink } from "../types";
import { notifyError } from "../util";

interface Props {
  opened: boolean;
  onClose: () => void;
  account: GoogleAccountLink | null;
}

export function GoogleAccountContent({
  account,
  onUnlink,
  unlinking,
  linkHref,
}: {
  account: GoogleAccountLink | null;
  onUnlink: () => void;
  unlinking: boolean;
  linkHref: string;
}) {
  const linkedDate = account?.linked_at
    ? new Date(account.linked_at).toLocaleDateString()
    : null;

  if (account) {
    return (
      <Stack gap="md">
        <Group justify="space-between" wrap="nowrap">
          <Stack gap={2} style={{ minWidth: 0 }}>
            <Text size="sm" fw={600} truncate="end">
              {account.email}
            </Text>
            {linkedDate && (
              <Text size="xs" c="dimmed">
                Linked {linkedDate}
              </Text>
            )}
          </Stack>
          <Badge color="green" variant="light">
            Linked
          </Badge>
        </Group>
        <Text size="sm" c="dimmed">
          Active hunt folders and spreadsheets are shared silently with this Google account so your
          name appears on Google Sheets cursors instead of &ldquo;Anonymous Animal&rdquo;.
        </Text>
        <Text size="xs" c="dimmed">
          Unlinking removes your saved Google account and revokes your individual Drive permissions
          on active hunts. You can still view and edit sheets without a linked Google account.
        </Text>
        <Group justify="flex-end" gap="xs">
          <Button
            component="a"
            href={linkHref}
            variant="light"
            leftSection={<IconRefresh size={16} />}
          >
            Switch account
          </Button>
          <Button
            color="red"
            variant="light"
            leftSection={<IconLinkOff size={16} />}
            loading={unlinking}
            onClick={onUnlink}
          >
            Unlink
          </Button>
        </Group>
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      <Text size="sm">
        Optionally link your Google account so your name appears on Google Sheets cursors instead of
        &ldquo;Anonymous Animal&rdquo;.
      </Text>
      <Text size="sm" c="dimmed">
        Puzzbot only asks Google to verify your email address (no access to your Google Drive) and
        silently shares active hunt folders/spreadsheets with that email. A Google account is not
        required to participate.
      </Text>
      <Group justify="flex-end">
        <Button
          component="a"
          href={linkHref}
          leftSection={<IconBrandGoogle size={16} />}
        >
          Link with Google
        </Button>
      </Group>
    </Stack>
  );
}

export function GoogleAccountModal({ opened, onClose, account }: Props) {
  const unlink = useUnlinkGoogle();
  const linkHref = typeof window !== "undefined" ? googleLinkUrl() : "/api/auth/google/link";

  function handleUnlink() {
    unlink.mutate(undefined, {
      onSuccess: () => {
        notifications.show({
          color: "green",
          title: "Google account unlinked",
          message: "Your Google account has been unlinked and active-hunt permissions are being removed.",
        });
        onClose();
      },
      onError: notifyError,
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Google account" centered>
      <GoogleAccountContent
        account={account}
        onUnlink={handleUnlink}
        unlinking={unlink.isPending}
        linkHref={linkHref}
      />
    </Modal>
  );
}
