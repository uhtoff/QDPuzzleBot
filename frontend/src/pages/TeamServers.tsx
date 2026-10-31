import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Accordion,
  Anchor,
  Badge,
  Center,
  Group,
  Loader,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";

import { useAdminGuilds, useTeamHunt, useTeamHunts } from "../api";
import { historyLine } from "../lib/accessHistory";
import type { TeamHuntSummary } from "../types";

const when = (iso: string) => new Date(iso).toLocaleString();

function HuntDetail({ guildId, hunt }: { guildId: number; hunt: TeamHuntSummary }) {
  const { data, isLoading, error } = useTeamHunt(guildId, hunt.uid);
  if (isLoading) {
    return (
      <Center h={80}>
        <Loader size="sm" />
      </Center>
    );
  }
  if (error || !data) return <Text c="red">Failed to load this hunt&apos;s access.</Text>;

  return (
    <Stack gap="md">
      {!hunt.archived && (
        <Stack gap="xs">
          <Title order={5}>On the server now</Title>
          {!data.bot_on_server || data.on_server === null ? (
            <Text c="dimmed">I&apos;m not on that server any more.</Text>
          ) : (
            <>
              {data.team_role_missing && (
                <Text c="orange">This hunt&apos;s team role no longer exists.</Text>
              )}
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Name</Table.Th>
                    <Table.Th>Team role</Table.Th>
                    <Table.Th>Mod</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {data.on_server.map((m) => (
                    <Table.Tr key={m.user_id}>
                      <Table.Td>{m.name}</Table.Td>
                      <Table.Td>{m.team_role ? "✓" : ""}</Table.Td>
                      <Table.Td>{m.mod ? "✓" : ""}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </>
          )}
        </Stack>
      )}

      <Stack gap="xs">
        <Title order={5}>
          {hunt.archived ? "Invited, never arrived" : "Invited, not arrived"}
        </Title>
        {data.invited_waiting.length === 0 ? (
          <Text c="dimmed">Nobody.</Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Invited at</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.invited_waiting.map((w) => (
                <Table.Tr key={w.user_id}>
                  <Table.Td>{w.name}</Table.Td>
                  <Table.Td>{when(w.invited_at)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>

      <Stack gap="xs">
        <Title order={5}>History</Title>
        {data.history.length === 0 ? (
          <Text c="dimmed">Nothing yet.</Text>
        ) : (
          <Table>
            <Table.Tbody>
              {data.history.map((h, i) => (
                <Table.Tr key={`${h.at}-${i}`}>
                  <Table.Td>{when(h.at)}</Table.Td>
                  <Table.Td>
                    {historyLine(h)}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Stack>
  );
}

export default function TeamServers() {
  const { guildId } = useParams();
  const navigate = useNavigate();
  const { data: adminGuilds } = useAdminGuilds();
  const id = guildId ? Number(guildId) : null;
  const { data, isLoading, error } = useTeamHunts(id);
  const [open, setOpen] = useState<string | null>(null);

  const guildOptions = (adminGuilds ?? []).map((g) => ({
    value: String(g.id),
    label: g.guild_name,
  }));

  const hubName = (adminGuilds ?? []).find((g) => g.id === id)?.guild_name;

  if (isLoading) {
    return (
      <Center h={200}>
        <Loader />
      </Center>
    );
  }
  if (error || !data || id === null) {
    return <Text c="red">Failed to load team servers.</Text>;
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <Title order={3}>
          Team servers{hubName ? ` — ${hubName}` : ""}
        </Title>
        {guildOptions.length > 1 && (
          <Select
            label="Server"
            data={guildOptions}
            value={String(id)}
            onChange={(v) => v && navigate(`/guilds/${v}/teams`)}
            allowDeselect={false}
            w={220}
          />
        )}
      </Group>
      <Anchor component={Link} to={`/guilds/${id}/settings`} size="sm">
        Back to Server settings
      </Anchor>

      {data.hunts.length === 0 ? (
        <Text c="dimmed">No team servers use this hub yet.</Text>
      ) : (
        <Accordion variant="separated" value={open} onChange={setOpen}>
          {data.hunts.map((h) => (
            <Accordion.Item key={h.uid} value={h.uid}>
              <Accordion.Control>
                <Group gap="sm">
                  <Text fw={500}>{h.name}</Text>
                  {h.team && <Text c="dimmed">{h.team}</Text>}
                  <Badge color={h.archived ? "gray" : "green"}>
                    {h.archived ? "Archived" : "Running"}
                  </Badge>
                  {!h.archived && h.on_server_count !== null && (
                    <Text size="sm">{h.on_server_count} on the server</Text>
                  )}
                  <Text size="sm">{h.invited_waiting_count} invited, waiting</Text>
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                {open === h.uid && <HuntDetail guildId={id} hunt={h} />}
              </Accordion.Panel>
            </Accordion.Item>
          ))}
        </Accordion>
      )}
    </Stack>
  );
}
