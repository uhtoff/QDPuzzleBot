import { notifications } from "@mantine/notifications";
import { Badge, Button, Group, Stack, Text, TextInput, Title } from "@mantine/core";
import { useState } from "react";
import { ConfirmModal } from "./ConfirmModal";
import { useSetHuntRole } from "../api";

/**
 * Admin panel to gate an OPEN hunt behind a Discord team role (web + Discord
 * visibility). Already-gated hunts just show their current role. Mirrors the
 * create-flow confirm: if the named role already exists the backend returns
 * needs_confirm, and we re-submit with confirmExisting after the admin agrees.
 */
export function GateHuntPanel({
  uid,
  isGated,
  roleName,
}: {
  uid: string;
  isGated: boolean;
  roleName: string | null;
}) {
  const [role, setRole] = useState("");
  const [confirm, setConfirm] = useState<{ message: string } | null>(null);
  const mut = useSetHuntRole(uid);

  if (isGated) {
    return (
      <Stack gap="sm">
        <Title order={5}>Team role</Title>
        <Group>
          <Badge color="green" size="sm">Gated</Badge>
          <Text size="sm">
            {roleName ? `Gated to @${roleName}.` : "This hunt is gated to a team role."}
          </Text>
        </Group>
      </Stack>
    );
  }

  const run = (confirmExisting: boolean) => {
    mut.mutate(
      { roleName: role.trim(), confirmExisting },
      {
        onSuccess: (data) => {
          if (data.needs_confirm === "existing_role") {
            setConfirm({
              message: data.message ?? "That role already exists — reuse it for this hunt?",
            });
            return;
          }
          notifications.show({ message: `Hunt gated to "${role.trim()}".`, color: "green" });
          if (data.failed_categories && data.failed_categories.length > 0) {
            notifications.show({
              message: `Some categories weren't updated: ${data.failed_categories.join(", ")}`,
              color: "yellow",
            });
          }
          setRole("");
        },
        onError: (e) => {
          notifications.show({ message: (e as Error).message, color: "red" });
        },
      }
    );
  };

  return (
    <Stack gap="sm">
      <Title order={5}>Team role</Title>
      <Text size="sm" c="dimmed">
        This hunt is open (visible to everyone in the server). Gate it behind a Discord
        team role to restrict the dashboard and channels to that team.
      </Text>
      <Group align="flex-end" gap="sm">
        <TextInput
          label="Team role name"
          placeholder="Hunt Team"
          value={role}
          onChange={(e) => setRole(e.currentTarget.value)}
          style={{ flex: 1 }}
        />
        <Button onClick={() => run(false)} loading={mut.isPending} disabled={!role.trim()}>
          Gate hunt
        </Button>
      </Group>
      <ConfirmModal
        opened={confirm !== null}
        onClose={() => setConfirm(null)}
        onConfirm={() => {
          run(true);
          setConfirm(null);
        }}
        title="Role already exists"
        message={confirm?.message ?? ""}
        confirmLabel="Reuse role"
        confirmColor="orange"
      />
    </Stack>
  );
}
