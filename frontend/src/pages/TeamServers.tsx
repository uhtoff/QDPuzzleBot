import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Center,
  Group,
  Loader,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import {
  useAdminGuilds,
  useArchiveMultiTeam,
  useCompleteTeamHunt,
  useTeamHunt,
  useTeamHunts,
} from "../api";
import { TypedConfirmModal } from "../components/TypedConfirmModal";
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

const teamsLabel = (n: number) => `${n} ${n === 1 ? "team" : "teams"}`;

function HuntItem({
  guildId,
  hunt,
  open,
}: {
  guildId: number;
  hunt: TeamHuntSummary;
  open: string | null;
}) {
  const completeMut = useCompleteTeamHunt(guildId);
  const mt = hunt.multi_team;
  // Controls only on a running team of a multi-team hunt that is still live.
  const showComplete = mt !== null && !mt.archived && !hunt.archived;

  const toggle = () => {
    if (!mt) return;
    completeMut.mutate(
      { uid: hunt.uid, complete: !mt.completed },
      {
        onSuccess: (r) => notifications.show({ message: r.message, color: "green" }),
        onError: (e) => notifications.show({ message: (e as Error).message, color: "red" }),
      },
    );
  };

  return (
    <Accordion.Item value={hunt.uid}>
      <Center>
        <Accordion.Control>
          <Group gap="sm">
            <Text fw={500}>{hunt.name}</Text>
            {hunt.team && <Text c="dimmed">{hunt.team}</Text>}
            <Badge color={hunt.archived ? "gray" : "green"}>
              {hunt.archived ? "Archived" : "Running"}
            </Badge>
            {mt?.completed && <Badge color="blue">Finished</Badge>}
            {!hunt.archived && hunt.on_server_count !== null && (
              <Text size="sm">{hunt.on_server_count} on the server</Text>
            )}
            <Text size="sm">{hunt.invited_waiting_count} invited, waiting</Text>
          </Group>
        </Accordion.Control>
        {showComplete && (
          <Button
            size="xs"
            variant="light"
            mx="sm"
            onClick={toggle}
            loading={completeMut.isPending}
          >
            {mt.completed ? "Mark not complete" : "Mark complete"}
          </Button>
        )}
      </Center>
      <Accordion.Panel>
        {open === hunt.uid && <HuntDetail guildId={guildId} hunt={hunt} />}
      </Accordion.Panel>
    </Accordion.Item>
  );
}

function MultiTeamGroup({
  guildId,
  group,
  open,
  setOpen,
}: {
  guildId: number;
  group: { id: number; name: string; archived: boolean; hunts: TeamHuntSummary[] };
  open: string | null;
  setOpen: (v: string | null) => void;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const archiveMut = useArchiveMultiTeam(guildId);
  // Teams already archived don't count towards the heading's "N teams".
  const running = group.hunts.filter((h) => !h.archived).length;
  const heading = group.archived
    ? group.name
    : `${group.name} (${teamsLabel(running)})`;

  return (
    <Stack gap="xs">
      <Group justify="space-between">
        <Title order={4}>{heading}</Title>
        {!group.archived && (
          <Button
            size="xs"
            color="red"
            variant="light"
            onClick={() => setConfirmOpen(true)}
            loading={archiveMut.isPending}
          >
            Archive now
          </Button>
        )}
      </Group>
      {archiveMut.isSuccess && (
        <Alert color="blue">
          Archiving… the result will be posted in the hub&apos;s admin channel.
        </Alert>
      )}
      <Accordion variant="separated" value={open} onChange={setOpen}>
        {group.hunts.map((h) => (
          <HuntItem key={h.uid} guildId={guildId} hunt={h} open={open} />
        ))}
      </Accordion>
      <TypedConfirmModal
        opened={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() =>
          archiveMut.mutate(
            { id: group.id, confirmName: group.name },
            {
              onSuccess: () => {
                setConfirmOpen(false);
                notifications.show({
                  message: "Archiving… the result will be posted in the hub's admin channel.",
                  color: "blue",
                });
              },
              onError: (e) =>
                notifications.show({ message: (e as Error).message, color: "red" }),
            },
          )
        }
        title="Archive multi-team hunt"
        confirmPhrase={group.name}
        confirmLabel="Archive now"
        loading={archiveMut.isPending}
      >
        <Text size="sm" c="red">
          This archives every team&apos;s server for {group.name} and the hub&apos;s
          channels for it. It cannot be undone.
        </Text>
      </TypedConfirmModal>
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

  // Team hunts of one multi-team hunt sit together under its name.
  const byMulti = new Map<
    number,
    { id: number; name: string; archived: boolean; hunts: TeamHuntSummary[] }
  >();
  const ungrouped: TeamHuntSummary[] = [];
  for (const h of data.hunts) {
    const mt = h.multi_team;
    if (!mt) {
      ungrouped.push(h);
      continue;
    }
    const g = byMulti.get(mt.id) ?? { id: mt.id, name: mt.name, archived: mt.archived, hunts: [] };
    g.hunts.push(h);
    byMulti.set(mt.id, g);
  }
  const groups = [...byMulti.values()];

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
        <>
          {groups.map((g) => (
            <MultiTeamGroup key={g.id} guildId={id} group={g} open={open} setOpen={setOpen} />
          ))}
          {ungrouped.length > 0 && (
            <Accordion variant="separated" value={open} onChange={setOpen}>
              {ungrouped.map((h) => (
                <HuntItem key={h.uid} guildId={id} hunt={h} open={open} />
              ))}
            </Accordion>
          )}
        </>
      )}
    </Stack>
  );
}
