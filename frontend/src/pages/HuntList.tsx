import {
  Accordion,
  Badge,
  Button,
  Card,
  Center,
  Checkbox,
  Collapse,
  Divider,
  Group,
  Loader,
  Modal,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { notifications } from "@mantine/notifications";
import { IconChevronDown, IconChevronUp, IconPlus } from "@tabler/icons-react";
import { useAdminGuilds, useCreateHunt, useHunts, type CreateHuntPayload } from "../api";
import type { HuntSummary } from "../types";

function HuntCard({ hunt }: { hunt: HuntSummary }) {
  return (
    <Card
      component={Link}
      to={`/hunts/${hunt.uid}`}
      shadow="sm"
      padding="lg"
      radius="md"
      withBorder
    >
      <Group justify="space-between" wrap="nowrap">
        <Text fw={600} truncate>
          {hunt.name}
        </Text>
        {hunt.archived && <Badge color="gray" variant="light">Archived</Badge>}
      </Group>
      {hunt.guild_name && (
        <Text c="dimmed" size="sm" mt={4}>
          {hunt.guild_name}
        </Text>
      )}
    </Card>
  );
}

function NewHuntModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { data: guilds } = useAdminGuilds();
  const createMut = useCreateHunt();

  const [guildId, setGuildId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [startLocal, setStartLocal] = useState("");
  const [roleName, setRoleName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [createArchiveSheet, setCreateArchiveSheet] = useState(false);
  const [forumLayout, setForumLayout] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const guildOptions = (guilds ?? []).map((g) => ({
    value: String(g.id),
    label: g.guild_name,
  }));

  const handleSubmit = () => {
    if (!guildId || !name.trim() || !url.trim() || !startLocal) return;

    const payload: CreateHuntPayload = {
      guild_id: Number(guildId),
      name: name.trim(),
      url: url.trim(),
      start_timestamp: Math.round(new Date(startLocal).getTime() / 1000),
    };
    if (roleName.trim()) payload.role_name = roleName.trim();
    if (username.trim()) payload.username = username.trim();
    if (password.trim()) payload.password = password.trim();
    if (createArchiveSheet) payload.create_archive_sheet = true;
    if (forumLayout) payload.layout_mode = "forum";

    createMut.mutate(payload, {
      onSuccess: (data) => {
        notifications.show({ message: `Hunt "${name}" created.`, color: "green" });
        onClose();
        navigate(`/hunts/${data.uid}`);
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  };

  const canSubmit = !!guildId && !!name.trim() && !!url.trim() && !!startLocal;

  return (
    <Modal opened={opened} onClose={onClose} title="New hunt" size="md">
      <Stack gap="sm">
        <Select
          label="Discord server"
          placeholder="Select server"
          data={guildOptions}
          value={guildId}
          onChange={setGuildId}
          required
        />
        <TextInput
          label="Hunt name"
          placeholder="My Hunt 2025"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          required
        />
        <TextInput
          label="Website URL"
          placeholder="https://hunt.example.com/"
          value={url}
          onChange={(e) => setUrl(e.currentTarget.value)}
          required
        />
        <TextInput
          type="datetime-local"
          label="Start time"
          value={startLocal}
          onChange={(e) => setStartLocal(e.currentTarget.value)}
          required
        />

        <Divider />

        <Group onClick={() => setAdvancedOpen((o) => !o)} style={{ cursor: "pointer" }}>
          {advancedOpen ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
          <Text size="sm" fw={600}>Advanced</Text>
        </Group>
        <Collapse in={advancedOpen}>
          <Stack gap="sm" pb="sm">
            <TextInput
              label="Team role name"
              description="Discord role to assign to hunt members."
              placeholder="Hunt Team"
              value={roleName}
              onChange={(e) => setRoleName(e.currentTarget.value)}
            />
            <TextInput
              label="Shared username"
              value={username}
              onChange={(e) => setUsername(e.currentTarget.value)}
            />
            <TextInput
              type="password"
              label="Shared password"
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
            />
            <Switch
              label="Forum-backed layout"
              description="Use a forum channel per round (each puzzle is a post) instead of a category per round. Fixed at creation."
              checked={forumLayout}
              onChange={(e) => setForumLayout(e.currentTarget.checked)}
            />
          </Stack>
        </Collapse>

        <Checkbox
          label="Create archive sheet"
          checked={createArchiveSheet}
          onChange={(e) => setCreateArchiveSheet(e.currentTarget.checked)}
        />

        <Button onClick={handleSubmit} loading={createMut.isPending} disabled={!canSubmit}>
          Create hunt
        </Button>
      </Stack>
    </Modal>
  );
}

export default function HuntList() {
  const { data, isLoading, error } = useHunts();
  const { data: adminGuilds } = useAdminGuilds();
  const [newHuntOpen, setNewHuntOpen] = useState(false);

  const isAdmin = (adminGuilds ?? []).length > 0;

  if (isLoading) {
    return (
      <Center h={200}>
        <Loader />
      </Center>
    );
  }
  if (error || !data) {
    return <Text c="red">Failed to load hunts.</Text>;
  }

  return (
    <Stack gap="xl">
      <Group justify="space-between">
        <Title order={3}>Active hunts</Title>
        {isAdmin && (
          <Button
            leftSection={<IconPlus size={16} />}
            size="sm"
            onClick={() => setNewHuntOpen(true)}
          >
            New hunt
          </Button>
        )}
      </Group>

      {data.active.length === 0 ? (
        <Text c="dimmed">No active hunts for your team right now.</Text>
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
          {data.active.map((h) => (
            <HuntCard key={h.uid} hunt={h} />
          ))}
        </SimpleGrid>
      )}

      {data.archived.length > 0 && (
        <Accordion variant="separated">
          <Accordion.Item value="old">
            <Accordion.Control>
              <Group gap="xs">
                <Text fw={600}>Old hunts</Text>
                <Badge variant="light" color="gray">
                  {data.archived.length}
                </Badge>
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
                {data.archived.map((h) => (
                  <HuntCard key={h.uid} hunt={h} />
                ))}
              </SimpleGrid>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}

      <NewHuntModal opened={newHuntOpen} onClose={() => setNewHuntOpen(false)} />
    </Stack>
  );
}
