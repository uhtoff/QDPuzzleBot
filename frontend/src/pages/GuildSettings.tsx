import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Autocomplete,
  Button,
  Card,
  Center,
  Checkbox,
  Group,
  Loader,
  MultiSelect,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconShieldCheck, IconShieldOff } from "@tabler/icons-react";

import { useAdminGuilds, useGuildSettings, useUpdateGuildSettings } from "../api";
import type { GuildSettingsPatch, GuildSettingsResponse } from "../types";

/** Where *what* goes with this category setting (the server trims it; blank =
 *  top level, a new name is made on first use). */
function categoryHint(value: string, categories: string[], what: string, where: string): string {
  const wanted = value.trim();
  if (!wanted) return `${what} go at the top level.`;
  if (categories.some((c) => c.toLowerCase() === wanted.toLowerCase())) {
    return `${what} go at the ${where} of this category.`;
  }
  return `This server has no "${wanted}" category; it will be made when first needed.`;
}

/** The authorisation tier a role list gates. Kept as the API's own key names so
 *  the patch body is built without a lookup table. */
type TierKey = "admin_role_ids" | "organiser_role_ids";

function TierWarning({ pinned, tier }: { pinned: boolean; tier: "admin" | "organiser" }) {
  if (pinned) {
    return (
      <Group gap={6} c="green">
        <IconShieldCheck size={16} />
        <Text size="sm">Pinned to these roles by id.</Text>
      </Group>
    );
  }
  // Only the admin tier is a problem when unpinned. Leaving the organiser tier
  // unpinned is a legitimate choice — it keeps organiser actions open to the
  // whole team, which is what a single trusted team usually wants — so it gets a
  // plain note, not a warning.
  if (tier === "organiser") {
    return (
      <Group gap={6} c="dimmed" mt="xs">
        <IconShieldOff size={16} />
        <Text size="sm">
          Not pinned — matched by role name (Organiser, Organisers, Organizer,
          Organizers). With no such role, organiser actions are open to everyone here.
          That is fine for a single trusted team; pin roles to restrict it.
        </Text>
      </Group>
    );
  }
  return (
    <Alert color="orange" icon={<IconShieldOff size={16} />} mt="xs">
      <Text size="sm">
        Not pinned — this tier is still matched by role <b>name</b> (Moderator, mod,
        admin). Anyone here with Discord&apos;s <b>Manage Roles</b> permission can create a
        role with one of those names, assign it to themselves, and hold full admin —
        including deleting a hunt. Pick the roles that should hold it and save.
      </Text>
    </Alert>
  );
}

function SettingsForm({ data, guildId }: { data: GuildSettingsResponse; guildId: number }) {
  const update = useUpdateGuildSettings(guildId);

  const [adminRoles, setAdminRoles] = useState<string[]>(data.settings.admin_role_ids);
  const [organiserRoles, setOrganiserRoles] = useState<string[]>(
    data.settings.organiser_role_ids,
  );
  const [botChannel, setBotChannel] = useState(data.settings.discord_bot_channel ?? "");
  const [driveParent, setDriveParent] = useState(data.settings.drive_parent_id ?? "");
  const [voice, setVoice] = useState(data.settings.discord_use_voice_channels);
  const [templateLink, setTemplateLink] = useState(data.settings.template_link ?? "");
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [mtCategory, setMtCategory] = useState(data.settings.multi_team_category);
  const [archiveCategory, setArchiveCategory] = useState(data.settings.archive_category);

  // Re-seed when the query refetches or the guild changes under us.
  useEffect(() => {
    setAdminRoles(data.settings.admin_role_ids);
    setOrganiserRoles(data.settings.organiser_role_ids);
    setBotChannel(data.settings.discord_bot_channel ?? "");
    setDriveParent(data.settings.drive_parent_id ?? "");
    setVoice(data.settings.discord_use_voice_channels);
    setTemplateLink(data.settings.template_link ?? "");
    setMtCategory(data.settings.multi_team_category);
    setArchiveCategory(data.settings.archive_category);
  }, [data]);

  const roleOptions = useMemo(
    () => data.roles.map((r) => ({ value: r.id, label: r.name })),
    [data.roles],
  );

  // The server refuses a non-empty admin list you don't hold — it would lock you
  // out of the only page that could undo it. Say so before the round trip.
  const yours = new Set(data.your_role_ids);
  const wouldLockOut =
    adminRoles.length > 0 && !adminRoles.some((id) => yours.has(id));

  function onSave() {
    const patch: GuildSettingsPatch = {
      admin_role_ids: adminRoles,
      organiser_role_ids: organiserRoles,
      discord_bot_channel: botChannel,
      drive_parent_id: driveParent,
      discord_use_voice_channels: voice,
      template_link: templateLink,
      multi_team_category: mtCategory,
      archive_category: archiveCategory,
    };
    setTemplateError(null);
    update.mutate(patch, {
      onSuccess: () =>
        notifications.show({ color: "green", message: "Server settings saved." }),
      onError: (err: Error) => {
        // The server names the field only in the 400 body; friendlyError
        // surfaces its code, and the only bad_value a non-empty link can hit
        // here is the template link (role lists are built from pickers).
        if (err.message === "bad_value") {
          setTemplateError(
            "Not a recognised template link. Use https://discord.new/<code>, discord.new/<code> or https://discord.com/template/<code>.",
          );
        } else {
          notifications.show({ color: "red", message: err.message });
        }
      },
    });
  }

  const tierField = (
    key: TierKey,
    label: string,
    value: string[],
    onChange: (v: string[]) => void,
    tier: "admin" | "organiser",
  ) => (
    <Card withBorder padding="md" key={key}>
      <Stack gap="xs">
        <Title order={5}>{label}</Title>
        <MultiSelect
          data={roleOptions}
          value={value}
          onChange={onChange}
          placeholder="Pick the roles that hold this tier"
          searchable
          clearable
        />
        <TierWarning pinned={data.tiers[tier].pinned} tier={tier} />
      </Stack>
    </Card>
  );

  return (
    <Stack gap="md">
      {tierField("admin_role_ids", "Admin tier", adminRoles, setAdminRoles, "admin")}
      {tierField(
        "organiser_role_ids",
        "Organiser tier",
        organiserRoles,
        setOrganiserRoles,
        "organiser",
      )}

      {wouldLockOut && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          You hold none of the roles you have picked for the admin tier. Saving this would
          lock you out of this page, so it will be refused — include one of your own roles,
          or clear the list to go back to name matching.
        </Alert>
      )}

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={5}>Bot behaviour</Title>
          <TextInput
            label="Bot command channel"
            description="If set, most bot commands must be run in this channel. Leave empty to allow any channel."
            value={botChannel}
            onChange={(e) => setBotChannel(e.currentTarget.value)}
          />
          <TextInput
            label="Google Drive parent folder id"
            description="The folder new hunt spreadsheets are created under."
            value={driveParent}
            onChange={(e) => setDriveParent(e.currentTarget.value)}
          />
          <Checkbox
            label="Create a voice channel alongside each puzzle channel"
            checked={voice}
            onChange={(e) => setVoice(e.currentTarget.checked)}
          />
          <Autocomplete
            label="Archive category"
            description={categoryHint(
              archiveCategory,
              data.categories,
              "Archived hunts (and, on a hub, team and multi-team archives)",
              "top",
            )}
            placeholder="None (top level)"
            data={data.categories}
            value={archiveCategory}
            onChange={setArchiveCategory}
            maxLength={100}
          />
        </Stack>
      </Card>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Title order={5}>Team servers</Title>
          <TextInput
            label="Team-server template link"
            description="The Discord server template new team servers are made from. Accepted: https://discord.new/<code>, discord.new/<code>, https://discord.com/template/<code>. Leave empty to clear."
            value={templateLink}
            onChange={(e) => setTemplateLink(e.currentTarget.value)}
            error={templateError}
          />
          <Autocomplete
            label="Multi-team hunt channels category"
            description={categoryHint(
              mtCategory,
              data.categories,
              "A new multi-team hunt's no-spoiler and finishers channels",
              "bottom",
            )}
            placeholder="None (top level)"
            data={data.categories}
            value={mtCategory}
            onChange={setMtCategory}
            maxLength={100}
          />
          <Group>
            <Button component={Link} to={`/guilds/${guildId}/teams`} variant="light">
              Team servers
            </Button>
          </Group>
        </Stack>
      </Card>

      <Group justify="flex-end">
        <Button onClick={onSave} loading={update.isPending} disabled={wouldLockOut}>
          Save
        </Button>
      </Group>
    </Stack>
  );
}

export default function GuildSettings() {
  const { guildId } = useParams();
  const navigate = useNavigate();
  const { data: adminGuilds } = useAdminGuilds();
  const id = guildId ? Number(guildId) : null;
  const { data, isLoading, error } = useGuildSettings(id);

  const guildOptions = (adminGuilds ?? []).map((g) => ({
    value: String(g.id),
    label: g.guild_name,
  }));

  if (isLoading) {
    return (
      <Center h={200}>
        <Loader />
      </Center>
    );
  }
  if (error || !data || id === null) {
    return <Text c="red">Failed to load server settings.</Text>;
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <Title order={3}>Server settings — {data.guild.guild_name}</Title>
        {guildOptions.length > 1 && (
          <Select
            label="Server"
            data={guildOptions}
            value={String(id)}
            onChange={(v) => v && navigate(`/guilds/${v}/settings`)}
            allowDeselect={false}
            w={220}
          />
        )}
      </Group>
      <SettingsForm data={data} guildId={id} />
    </Stack>
  );
}
