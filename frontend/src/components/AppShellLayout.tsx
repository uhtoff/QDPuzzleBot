import { ReactNode } from "react";
import {
  Alert,
  AppShell,
  Avatar,
  Burger,
  Group,
  Menu,
  Text,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconCheck, IconEye, IconLogout } from "@tabler/icons-react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { logout, useAdminGuilds } from "../api";
import type { User } from "../types";
import { useViewAs, type ViewAs } from "../context/ViewAsContext";

function avatarUrl(user: User) {
  return user.avatar_url ?? undefined;
}

export function AppShellLayout({ user, children }: { user: User; children: ReactNode }) {
  const [opened, { toggle }] = useDisclosure();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const displayName = user.global_name || user.username || "Solver";

  const { viewAs, setViewAs } = useViewAs();
  const { data: adminGuilds } = useAdminGuilds();
  const isAdminSomewhere = (adminGuilds ?? []).length > 0;
  const VIEW_LABELS: Record<ViewAs, string> = {
    admin: "Admin (you)",
    organiser: "Organiser",
    regular: "Regular user",
  };

  async function onLogout() {
    await logout();
    queryClient.clear();
    navigate("/");
  }

  return (
    <AppShell header={{ height: 56 }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <UnstyledButton component={Link} to="/">
              <Title order={4}>🧩 Puzzle Dashboard</Title>
            </UnstyledButton>
          </Group>
          <Menu shadow="md" width={180} position="bottom-end">
            <Menu.Target>
              <UnstyledButton>
                <Group gap="xs">
                  <Avatar src={avatarUrl(user)} radius="xl" size={32}>
                    {displayName.slice(0, 2).toUpperCase()}
                  </Avatar>
                  <Text size="sm" visibleFrom="sm">
                    {displayName}
                  </Text>
                </Group>
              </UnstyledButton>
            </Menu.Target>
            <Menu.Dropdown>
              {isAdminSomewhere && (
                <>
                  <Menu.Label>View as</Menu.Label>
                  {(["admin", "organiser", "regular"] as ViewAs[]).map((level) => (
                    <Menu.Item
                      key={level}
                      leftSection={
                        viewAs === level ? <IconCheck size={16} /> : <span style={{ width: 16 }} />
                      }
                      onClick={() => setViewAs(level)}
                    >
                      {VIEW_LABELS[level]}
                    </Menu.Item>
                  ))}
                  <Menu.Divider />
                </>
              )}
              <Menu.Item leftSection={<IconLogout size={16} />} onClick={onLogout}>
                Log out
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      </AppShell.Header>
      <AppShell.Main>
        {viewAs !== "admin" && (
          <Alert color="orange" mb="md" icon={<IconEye size={16} />} withCloseButton={false}>
            <Group justify="space-between" wrap="nowrap">
              <Text size="sm">Viewing as: {VIEW_LABELS[viewAs]}</Text>
              <UnstyledButton onClick={() => setViewAs("admin")}>
                <Text size="sm" fw={600} c="orange">
                  Restore
                </Text>
              </UnstyledButton>
            </Group>
          </Alert>
        )}
        {children}
      </AppShell.Main>
    </AppShell>
  );
}
