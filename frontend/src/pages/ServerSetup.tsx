import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Alert,
  Anchor,
  Autocomplete,
  Button,
  Center,
  Collapse,
  Divider,
  Group,
  Loader,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { IconChevronDown, IconChevronUp } from "@tabler/icons-react";

import type { UseMutationResult } from "@tanstack/react-query";

import { useRunSetup, useSetupStatus } from "../api";
import { DiscordLine } from "../components/DiscordLine";
import {
  buildPayload,
  canSubmit,
  emptySetupForm,
  multiTeamMatch,
  pickRunningHunt,
  type SetupForm,
} from "../lib/setupForm";
import type { SetupResult, SetupStatus } from "../types";
import { UTC_OFFSET_OPTIONS, browserOffsetMinutes } from "../util";

function initialForm(status: SetupStatus): SetupForm {
  const form = emptySetupForm(browserOffsetMinutes());
  const ex = status.existing;
  // A partly finished setup is prefilled so a retry can't hit "already tied to another hub role".
  if (ex) {
    form.hubId = ex.hub_id;
    form.roleId = ex.roster_role_id;
    form.huntName = ex.hunt_name;
    form.huntUrl = ex.hunt_url;
    form.multiTeam = ex.multi_team !== null;
  }
  return form;
}

// The lines already carry the outcome (":white_check_mark: Setup complete…" or
// ":x: Setup stopped…" plus what to do), so nothing here repeats them.
function SetupResultView({ result }: { result: SetupResult }) {
  const lines = result.lines.map((line, i) => <DiscordLine key={i} line={line} />);
  return (
    <Stack gap="xs">
      {result.error ? (
        <Alert color="red" title="Setup stopped">
          <Stack gap="xs">{lines}</Stack>
        </Alert>
      ) : (
        <>
          {lines}
          {result.invites && <DiscordLine line={result.invites} />}
          {result.hunt_uid && (
            <Anchor component={Link} to={`/hunts/${result.hunt_uid}`}>
              Open the hunt board
            </Anchor>
          )}
        </>
      )}
    </Stack>
  );
}

function SetupFormView({
  status,
  mut,
}: {
  status: SetupStatus;
  mut: UseMutationResult<SetupResult, Error, unknown>;
}) {
  const [form, setForm] = useState<SetupForm>(() => initialForm(status));
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const set = <K extends keyof SetupForm>(key: K, value: SetupForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const hub = status.hubs.find((h) => h.id === form.hubId);
  const problems = status.template_problems;
  const running = hub?.multi_team_hunts ?? [];
  const match = multiTeamMatch(form.huntName, running);
  const teams = (n: number) => `${n} ${n === 1 ? "team" : "teams"}`;
  // Where a new multi-team hunt's no-spoiler and finishers channels go on the hub.
  const categories = hub?.categories ?? [];
  const wantedCategory = form.hubCategory.trim();
  const categoryHint = !wantedCategory
    ? "The no-spoiler and finishers channels go at the top level of the hub."
    : categories.some((c) => c.toLowerCase() === wantedCategory.toLowerCase())
      ? "The no-spoiler and finishers channels go at the bottom of this category."
      : `The hub has no "${wantedCategory}" category; it will be made.`;

  return (
    <Stack gap="md" maw={560}>
      <Stack gap={4}>
        <Title order={5}>Server check</Title>
        {problems.length === 0 ? (
          <Text c="green" size="sm">
            ✓ The server matches the template.
          </Text>
        ) : (
          problems.map((p, i) => (
            <Text key={i} c="red" size="sm">
              ✗ {p}
            </Text>
          ))
        )}
      </Stack>

      <Select
        label="Hub"
        placeholder={status.hubs.length ? "Pick the hub server" : "No hub servers available to you"}
        data={status.hubs.map((h) => ({ value: h.id, label: h.name }))}
        value={form.hubId}
        onChange={(v) => setForm((f) => ({ ...f, hubId: v, roleId: null }))}
        allowDeselect={false}
        required
      />
      <Select
        label="Hub role"
        description="The role on the hub that lists this team's people."
        placeholder="Pick the team's role"
        data={(hub?.roles ?? []).map((r) => ({ value: r.id, label: r.name }))}
        value={form.roleId}
        onChange={(v) => set("roleId", v)}
        disabled={!hub}
        allowDeselect={false}
        searchable
        required
      />
      <Stack gap={4}>
        <Text size="sm" fw={500}>
          Are we fielding more than one team?
        </Text>
        <SegmentedControl
          w={160}
          data={[
            { value: "no", label: "No" },
            { value: "yes", label: "Yes" },
          ]}
          value={form.multiTeam ? "yes" : "no"}
          onChange={(v) => set("multiTeam", v === "yes")}
        />
      </Stack>
      {form.multiTeam && running.length > 0 && (
        <Stack gap={4}>
          <Text size="sm" c="dimmed">
            Running on this hub:
          </Text>
          <Group gap="xs">
            {running.map((m) => (
              <Button
                key={m.id}
                size="xs"
                variant={match?.id === m.id ? "filled" : "light"}
                onClick={() => setForm((f) => pickRunningHunt(f, m))}
              >
                {m.name} ({teams(m.team_count)})
              </Button>
            ))}
          </Group>
        </Stack>
      )}
      <TextInput
        label="Hunt name"
        value={form.huntName}
        onChange={(e) => set("huntName", e.currentTarget.value)}
        required
      />
      {form.multiTeam && match && (
        <Text size="sm">
          This team joins <strong>{match.name}</strong> ({teams(match.team_count)}).
        </Text>
      )}
      {form.multiTeam && !match && form.huntName.trim() && (
        <Text size="sm">
          This starts a new multi-team hunt called <strong>{form.huntName.trim()}</strong>.
        </Text>
      )}
      {form.multiTeam && !match && (
        <Autocomplete
          label="Hub category"
          description={categoryHint}
          placeholder="No category"
          data={categories}
          value={form.hubCategory}
          onChange={(v) => set("hubCategory", v)}
        />
      )}
      <TextInput
        label="Hunt website"
        value={form.huntUrl}
        onChange={(e) => set("huntUrl", e.currentTarget.value)}
        required
      />

      <Divider />
      <Group onClick={() => setAdvancedOpen((o) => !o)} style={{ cursor: "pointer" }}>
        {advancedOpen ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
        <Text size="sm" fw={600}>
          Advanced
        </Text>
      </Group>
      <Collapse in={advancedOpen}>
        <Stack gap="sm" pb="sm">
          <Group align="flex-end" gap="sm" wrap="nowrap">
            <TextInput
              type="datetime-local"
              label="Start time"
              value={form.startLocal}
              onChange={(e) => set("startLocal", e.currentTarget.value)}
              style={{ flex: 1 }}
            />
            <Select
              label="Timezone"
              data={UTC_OFFSET_OPTIONS}
              value={String(form.startOffsetMinutes)}
              onChange={(v) => v != null && set("startOffsetMinutes", Number(v))}
              allowDeselect={false}
              w={140}
            />
          </Group>
          <TextInput
            label="Shared username"
            value={form.username}
            onChange={(e) => set("username", e.currentTarget.value)}
          />
          <TextInput
            autoComplete="off"
            label="Shared password"
            value={form.password}
            onChange={(e) => set("password", e.currentTarget.value)}
          />
          <Switch
            label="One spreadsheet per puzzle"
            description="A Drive folder holds a spreadsheet for each puzzle."
            checked={form.filesMode}
            onChange={(e) => set("filesMode", e.currentTarget.checked)}
          />
          {!form.filesMode && (
            <Switch
              label="Create a solved-puzzle sheet"
              checked={form.createSolvedSheet}
              onChange={(e) => set("createSolvedSheet", e.currentTarget.checked)}
            />
          )}
        </Stack>
      </Collapse>
      <Divider />

      <Switch
        label="Send invites when setup finishes"
        description="Leave off to look the server over first; run !send_invites later"
        checked={form.sendInvites}
        onChange={(e) => set("sendInvites", e.currentTarget.checked)}
      />

      {mut.error && <Alert color="red">{(mut.error as Error).message}</Alert>}
      <Group>
        <Button
          disabled={!canSubmit(form, problems)}
          loading={mut.isPending}
          onClick={() => mut.mutate(buildPayload(form))}
        >
          Set up
        </Button>
      </Group>
    </Stack>
  );
}

export default function ServerSetup() {
  const { guildId } = useParams();
  const id = guildId ? Number(guildId) : null;
  const { data, isLoading, error } = useSetupStatus(id);
  const mut = useRunSetup(id ?? 0);

  if (isLoading) {
    return (
      <Center h={200}>
        <Loader />
      </Center>
    );
  }
  if (error || !data || id === null) {
    return <Text c="red">Failed to load the setup page. Only this server&apos;s owner or admins can use it.</Text>;
  }

  return (
    <Stack gap="md">
      <Title order={3}>Set up this server</Title>
      {/* The result outlives the refetch that flips already_set_up, so it is
          rendered from here, ahead of that branch. */}
      {mut.data && <SetupResultView result={mut.data} />}
      {data.already_set_up ? (
        !mut.data && (
          <Stack gap="xs">
            <Text>Already set up on {new Date(data.already_set_up).toLocaleString()}.</Text>
            {data.hunt_uid && (
              <Anchor component={Link} to={`/hunts/${data.hunt_uid}`}>
                Open the hunt board
              </Anchor>
            )}
          </Stack>
        )
      ) : (
        <SetupFormView status={data} mut={mut} />
      )}
    </Stack>
  );
}
