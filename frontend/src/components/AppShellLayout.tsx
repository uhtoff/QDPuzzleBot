import { ReactNode, useEffect } from "react";
import {
  Alert,
  Anchor,
  AppShell,
  Avatar,
  Badge,
  Burger,
  Group,
  Menu,
  Text,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import {
  IconBrandGoogle,
  IconCheck,
  IconEye,
  IconLogout,
  IconSettings,
  IconUsersGroup,
} from "@tabler/icons-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { logout, useAdminGuilds } from "../api";
import type { User } from "../types";
import { useViewAs, type ViewAs } from "../context/ViewAsContext";
import { GoogleAccountModal } from "./GoogleAccountModal";

function avatarUrl(user: User) {
  return user.avatar_url ?? undefined;
}

export function AppShellLayout({ user, children }: { user: User; children: ReactNode }) {
  const [opened, { toggle }] = useDisclosure();
  const [googleModalOpen, { open: openGoogleModal, close: closeGoogleModal }] = useDisclosure();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const displayName = user.global_name || user.username || "Solver";

  useEffect(() => {
    const status = searchParams.get("google_link");
    if (!status) return;
    if (status === "linked") {
      queryClient.invalidateQueries({ queryKey: ["me"] });
      notifications.show({
        color: "green",
        title: "Google account linked",
        message: "Active hunt sheets are being shared with your Google account for named cursors.",
      });
    } else if (status === "cancelled") {
      notifications.show({
        color: "gray",
        title: "Google linking cancelled",
        message: "No changes were made to your account.",
      });
    } else if (status === "error") {
      notifications.show({
        color: "red",
        title: "Couldn't link Google account",
        message: "Please try again — make sure your Google account has a verified email.",
      });
    } else if (status === "auth_required") {
      notifications.show({
        color: "orange",
        title: "Sign in required",
        message: "Please sign in with Discord before linking your Google account.",
      });
    }
    const next = new URLSearchParams(searchParams);
    next.delete("google_link");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, queryClient]);

  const { viewAs, setViewAs } = useViewAs();
  const { data: adminGuilds } = useAdminGuilds();
  // The menu entry needs a guild to link to; the settings page itself offers a
  // picker when there is more than one.
  const firstAdminGuild = (adminGuilds ?? [])[0] ?? null;
  const isAdminSomewhere = firstAdminGuild !== null;
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
            <Anchor href="/guide/index.html" target="_blank" rel="noopener" size="sm" c="dimmed" visibleFrom="sm">
              User guide
            </Anchor>
            <Anchor href="/privacy" target="_blank" rel="noopener" size="sm" c="dimmed" visibleFrom="sm">
              Privacy
            </Anchor>
          </Group>
          <Menu shadow="md" width={220} position="bottom-end">
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
              {firstAdminGuild && viewAs === "admin" && (
                <>
                  <Menu.Item
                    leftSection={<IconSettings size={16} />}
                    onClick={() => navigate(`/guilds/${firstAdminGuild.id}/settings`)}
                  >
                    Server settings
                  </Menu.Item>
                  <Menu.Item
                    leftSection={<IconUsersGroup size={16} />}
                    onClick={() => navigate(`/guilds/${firstAdminGuild.id}/teams`)}
                  >
                    Team servers
                  </Menu.Item>
                  <Menu.Divider />
                </>
              )}
              {user.google_linking_enabled && (
                <>
                  <Menu.Item
                    leftSection={<IconBrandGoogle size={16} />}
                    rightSection={
                      user.google_account ? (
                        <Badge size="xs" color="green" variant="light">
                          Linked
                        </Badge>
                      ) : undefined
                    }
                    onClick={openGoogleModal}
                  >
                    {user.google_account ? "Google account" : "Link Google account"}
                  </Menu.Item>
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
      {user.google_linking_enabled && (
        <GoogleAccountModal
          opened={googleModalOpen}
          onClose={closeGoogleModal}
          account={user.google_account}
        />
      )}
    </AppShell>
  );
}
