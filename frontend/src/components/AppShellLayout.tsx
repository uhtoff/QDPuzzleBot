import { ReactNode } from "react";
import {
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
import { IconLogout } from "@tabler/icons-react";
import { Link, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { logout } from "../api";
import type { User } from "../types";

function avatarUrl(user: User) {
  return user.avatar_url ?? undefined;
}

export function AppShellLayout({ user, children }: { user: User; children: ReactNode }) {
  const [opened, { toggle }] = useDisclosure();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const displayName = user.global_name || user.username || "Solver";

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
              <Menu.Item leftSection={<IconLogout size={16} />} onClick={onLogout}>
                Log out
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      </AppShell.Header>
      <AppShell.Main>{children}</AppShell.Main>
    </AppShell>
  );
}
