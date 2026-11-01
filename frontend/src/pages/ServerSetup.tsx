import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Alert,
  Code,
  Anchor,
  Button,
  Center,
  Collapse,
  Divider,
  Group,
  Loader,
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
import { discordSpans } from "../lib/discordText";
import { buildPayload, canSubmit, emptySetupForm, type SetupForm } from "../lib/setupForm";
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
  }
  return form;
}

function DiscordLine({ line }: { line: string }) {
  return (
    <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
      {discordSpans(line).map((span, i) =>
        span.kind === "bold" ? (
          <strong key={i}>{span.text}</strong>
        ) : span.kind === "code" ? (
          <Code key={i}>{span.text}</Code>
        ) : (
          <span key={i}>{span.text}</span>
        ),
      )}
    </Text>
  );
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
      <TextInput
        label="Hunt name"
        value={form.huntName}
        onChange={(e) => set("huntName", e.currentTarget.value)}
        required
      />
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
            type="password"
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
