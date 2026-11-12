import { notifications } from "@mantine/notifications";
import { DiscordLine } from "./DiscordLine";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Divider,
  Group,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  IconAlertTriangle,
  IconArchive,
  IconFileSpreadsheet,
  IconHeadphones,
  IconTrash,
} from "@tabler/icons-react";
import { MarkCompleteModal } from "./MarkCompleteModal";
import { ManageItem, ManageSection } from "./ManageSection";
import { ConfirmModal } from "./ConfirmModal";
import { TypedConfirmModal } from "./TypedConfirmModal";
import { TeamArchiveModal } from "./TeamArchiveModal";
import { useArchiveHunt, useCompleteHunt, useCreateSolvedSheet, useDeleteHunt, useSetSheetMode, useUpdateHuntSettings, useVoiceCleanup, type HuntSettingsPatch } from "../api";
import { UTC_OFFSET_OPTIONS, browserOffsetMinutes, localToTs, tsToLocal } from "../util";

interface AdminSettingsPanelProps {
  uid: string;
  huntName: string;
  url: string;
  start_timestamp: number | null;
  username: string;
  password: string;
  url_sep: string;
  puzzle_prefix: string;
  archived: boolean;
  isTeamHunt: boolean;
  multiTeam: { name: string; completed: boolean; archived: boolean; team: string | null } | null;
  hasSolvedSheet: boolean;
  sheetMode: "tabs" | "files";
  puzzleCount: number;
  voicePerPuzzle: boolean;
  headerStyle: "full" | "compact";
  /** Organiser-level controls placed inside this panel's sections. */
  canvasControl?: ReactNode;
  maintenance?: ReactNode;
  teamRole?: ReactNode;
}

interface FormValues {
  url: string;
  start_local: string;  // datetime-local input format, wall-clock time in start_offset_minutes
  start_offset_minutes: number;  // UTC offset (minutes east of UTC) start_local is expressed in
  username: string;
  password: string;
  url_sep: string;
  puzzle_prefix: string;
  voice_per_puzzle: boolean;
  header_style: "full" | "compact";
}

export const HEADER_STYLE_OPTIONS = [
  { value: "compact", label: "Compact header" },
  { value: "full", label: "Full header" },
] as const;

// The "Saved: …" notice names fields as the form labels them, not the API keys.
const SETTING_LABELS: Record<string, string> = {
  url: "website URL",
  start_timestamp: "start time",
  username: "shared username",
  password: "shared password",
  url_sep: "URL separator",
  puzzle_prefix: "puzzle prefix",
  voice_per_puzzle: "voice channel for every new puzzle",
  header_style: "header style",
};

export function settingLabel(key: string): string {
  return SETTING_LABELS[key] ?? key.replace(/_/g, " ");
}

export function AdminSettingsPanel({
  uid,
  huntName,
  url,
  start_timestamp,
  username,
  password,
  url_sep,
  puzzle_prefix,
  archived,
  isTeamHunt,
  multiTeam,
  hasSolvedSheet,
  sheetMode,
  puzzleCount,
  voicePerPuzzle,
  headerStyle,
  canvasControl,
  maintenance,
  teamRole,
}: AdminSettingsPanelProps) {
  const navigate = useNavigate();
  const [archiveConfirmOpen, setArchiveConfirmOpen] = useState(false);
  const [completeConfirmOpen, setCompleteConfirmOpen] = useState(false);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [voiceCleanupOpen, setVoiceCleanupOpen] = useState(false);

  const settingsMut = useUpdateHuntSettings(uid);
  const archiveMut = useArchiveHunt(uid);
  const sheetMut = useCreateSolvedSheet(uid);
  const modeMut = useSetSheetMode(uid);
  const deleteMut = useDeleteHunt(uid);
  const completeMut = useCompleteHunt(uid);
  const voiceCleanupMut = useVoiceCleanup(uid);

  const [values, setValues] = useState<FormValues>(() => {
    const offset = browserOffsetMinutes();
    return {
      url,
      start_local: tsToLocal(start_timestamp, offset),
      start_offset_minutes: offset,
      username,
      password,
      url_sep,
      puzzle_prefix,
      voice_per_puzzle: voicePerPuzzle,
      header_style: headerStyle,
    };
  });

  // Re-sync the form from the server only when we switch to a different hunt —
  // not on every background refetch of the SAME hunt (the dashboard polls every
  // 30s), which would otherwise wipe out-of-sync in-progress edits every time it
  // fired. See #7/#8 in docs/reviews/2026-07-02-staging-full-review.md.
  const lastUid = useRef(uid);
  useEffect(() => {
    if (lastUid.current === uid) return;
    lastUid.current = uid;
    const offset = browserOffsetMinutes();
    setValues({
      url,
      start_local: tsToLocal(start_timestamp, offset),
      start_offset_minutes: offset,
      username,
      password,
      url_sep,
      puzzle_prefix,
      voice_per_puzzle: voicePerPuzzle,
      header_style: headerStyle,
    });
  }, [uid, url, start_timestamp, username, password, url_sep, puzzle_prefix, voicePerPuzzle, headerStyle]);

  const handleSave = useCallback(() => {
    const patch: HuntSettingsPatch = {};

    if (values.url !== url) {
      patch.url = values.url;
    }

    if (values.start_local) {
      const ts = localToTs(values.start_local, values.start_offset_minutes);
      if (ts !== null && ts !== start_timestamp) {
        patch.start_timestamp = ts;
      }
    } else if (start_timestamp != null && start_timestamp > 0) {
      patch.start_timestamp = 0;
    }

    if (values.username !== username) {
      patch.username = values.username;
    }
    if (values.password !== password) {
      patch.password = values.password;
    }
    if (values.url_sep !== url_sep) {
      patch.url_sep = values.url_sep;
    }
    if (values.puzzle_prefix !== puzzle_prefix) {
      patch.puzzle_prefix = values.puzzle_prefix;
    }
    if (values.voice_per_puzzle !== voicePerPuzzle) {
      patch.voice_per_puzzle = values.voice_per_puzzle;
    }
    if (values.header_style !== headerStyle) {
      patch.header_style = values.header_style;
    }

    const changedKeys = Object.keys(patch);
    if (changedKeys.length === 0) {
      notifications.show({ message: "No changes to save.", color: "gray" });
      return;
    }

    settingsMut.mutate(patch, {
      onSuccess: () => {
        notifications.show({
          message: `Saved: ${changedKeys.map(settingLabel).join(", ")}.`,
          color: "green",
        });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [values, url, start_timestamp, username, password, url_sep, puzzle_prefix, voicePerPuzzle, headerStyle, settingsMut]);

  const handleToggleComplete = (complete: boolean) => {
    completeMut.mutate(complete, {
      onSuccess: (r) => {
        setCompleteConfirmOpen(false);
        notifications.show({ message: <DiscordLine line={r.message} />, color: "green" });
      },
      onError: (e) => notifications.show({ message: (e as Error).message, color: "red" }),
    });
  };

  const handleToggleArchive = useCallback(() => {
    archiveMut.mutate(!archived, {
      onSuccess: () => {
        notifications.show({
          message: archived ? "Hunt unarchived." : "Hunt archived.",
          color: "green",
        });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [archived, archiveMut]);

  const handleCreateSolvedSheet = useCallback(() => {
    sheetMut.mutate(undefined, {
      onSuccess: () => {
        notifications.show({
          message: "Solved puzzles sheet created.",
          color: "green",
        });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [sheetMut]);

  const handleToggleSheetMode = useCallback(() => {
    const next = sheetMode === "files" ? "tabs" : "files";
    modeMut.mutate(next, {
      onSuccess: () => {
        notifications.show({ message: `Sheet mode is now "${next}".`, color: "green" });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [sheetMode, modeMut]);

  const handleVoiceCleanup = useCallback(() => {
    setVoiceCleanupOpen(false);
    voiceCleanupMut.mutate(undefined, {
      onSuccess: (r) => {
        notifications.show({
          message: `Removed ${r.removed.length} puzzle voice channel${r.removed.length === 1 ? "" : "s"}.`,
          color: "green",
        });
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [voiceCleanupMut]);

  const handleDelete = useCallback(() => {
    deleteMut.mutate(undefined, {
      onSuccess: () => {
        notifications.show({ message: `Hunt "${huntName}" deleted.`, color: "green" });
        navigate("/");
      },
      onError: (e) => {
        notifications.show({ message: (e as Error).message, color: "red" });
      },
    });
  }, [deleteMut, huntName, navigate]);

  // Fields saved by "Save settings" sit together above the button; everything
  // under "Change straight away" acts as soon as it is clicked.
  const saveButton = (
    <Group gap="sm">
      <Button onClick={handleSave} loading={settingsMut.isPending}>
        Save settings
      </Button>
    </Group>
  );

  return (
    <Stack gap="md">
      {settingsMut.isError && (
        <Alert icon={<IconAlertTriangle size={16} />} color="red" title="Error">
          {(settingsMut.error as Error).message}
        </Alert>
      )}

      <ManageSection title="Hunt details">
        <TextInput
          label="Website URL"
          placeholder="https://example.com/"
          value={values.url}
          onChange={(e) => setValues((v) => ({ ...v, url: e.target.value }))}
        />

        <Group align="flex-end" gap="sm" wrap="nowrap">
          <TextInput
            type="datetime-local"
            label="Start time"
            description="Leaving this blank clears the start time."
            value={values.start_local}
            onChange={(e) => setValues((v) => ({ ...v, start_local: e.target.value }))}
            style={{ flex: 1 }}
          />
          <Select
            label="Timezone"
            data={UTC_OFFSET_OPTIONS}
            value={String(values.start_offset_minutes)}
            onChange={(val) =>
              val != null &&
              setValues((v) => ({ ...v, start_offset_minutes: Number(val) }))
            }
            allowDeselect={false}
            w={130}
          />
        </Group>

        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <TextInput
            label="Shared username"
            placeholder="team-name"
            value={values.username}
            onChange={(e) => setValues((v) => ({ ...v, username: e.target.value }))}
          />
          <TextInput
            autoComplete="off"
            label="Shared password"
            value={values.password}
            onChange={(e) => setValues((v) => ({ ...v, password: e.target.value }))}
          />
        </SimpleGrid>

        {saveButton}
      </ManageSection>

      <ManageSection title="Advanced" collapsible>
        <Select
          label="Header style"
          description="Applies to puzzles created from now on; existing puzzles keep their header."
          data={[...HEADER_STYLE_OPTIONS]}
          value={values.header_style}
          allowDeselect={false}
          onChange={(val) => {
            if (val === "full" || val === "compact") {
              setValues((v) => ({ ...v, header_style: val }));
            }
          }}
        />

        <Checkbox
          label="Voice channel for every new puzzle"
          checked={values.voice_per_puzzle}
          onChange={(e) => {
            const checked = e.currentTarget.checked;
            setValues((v) => ({ ...v, voice_per_puzzle: checked }));
          }}
        />

        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <TextInput
            label="URL separator"
            description="Used to build puzzle URLs (e.g. puzzle-1, puzzles_2)."
            placeholder="-"
            value={values.url_sep}
            onChange={(e) => setValues((v) => ({ ...v, url_sep: e.target.value }))}
          />
          <TextInput
            label="Puzzle prefix"
            description="Prefix embedded in puzzle URLs."
            placeholder="puzzle"
            value={values.puzzle_prefix}
            onChange={(e) => setValues((v) => ({ ...v, puzzle_prefix: e.target.value }))}
          />
        </SimpleGrid>

        {saveButton}

        <Divider label="Change straight away" labelPosition="left" />

        <ManageItem
          title="Sheets"
          description={
            puzzleCount === 0
              ? "Can be changed until the first puzzle is added."
              : "Fixed once the hunt has puzzles."
          }
        >
          <Group>
            <Badge color={sheetMode === "files" ? "blue" : "gray"} size="sm">
              {sheetMode === "files" ? "One spreadsheet per puzzle" : "Tabs in one spreadsheet"}
            </Badge>
            {puzzleCount === 0 && (
              <Button
                size="xs"
                variant="light"
                onClick={handleToggleSheetMode}
                loading={modeMut.isPending}
              >
                {sheetMode === "files" ? "Use tabs instead" : "Use one spreadsheet per puzzle"}
              </Button>
            )}
          </Group>

          {sheetMode === "tabs" && !hasSolvedSheet && (
            <Group>
              <Button
                size="xs"
                variant="light"
                leftSection={<IconFileSpreadsheet size={14} />}
                onClick={handleCreateSolvedSheet}
                loading={sheetMut.isPending}
              >
                Move solved feeders to their own sheet
              </Button>
            </Group>
          )}

          {sheetMode === "tabs" && hasSolvedSheet && (
            <Text size="sm" c="green">✓ Solved puzzles sheet already created.</Text>
          )}
        </ManageItem>

        {canvasControl}
      </ManageSection>

      <ManageSection title="Maintenance">
        {maintenance}
        <ManageItem
          title="Puzzle voice channels"
          description="Removes every puzzle's voice channel and turns them off for new puzzles."
        >
          <Group>
            <Button
              variant="light"
              size="xs"
              color="red"
              leftSection={<IconHeadphones size={14} />}
              onClick={() => setVoiceCleanupOpen(true)}
              loading={voiceCleanupMut.isPending}
            >
              Remove puzzle voice channels
            </Button>
          </Group>
        </ManageItem>
      </ManageSection>
      <ConfirmModal
        opened={voiceCleanupOpen}
        onClose={() => setVoiceCleanupOpen(false)}
        onConfirm={handleVoiceCleanup}
        title="Remove puzzle voice channels"
        message="Removes every puzzle's voice channel in this hunt and turns off voice channels for new puzzles. People still in one get a 15-second warning and are moved to the hunt's voice channel."
        confirmLabel="Remove voice channels"
        confirmColor="red"
      />

      <ManageSection title="Hunt status">
        <Group>
          <Badge color={archived ? "gray" : "green"} size="sm">
            {archived ? "Archived" : "Active"}
          </Badge>
          <Button
            variant="light"
            size="xs"
            leftSection={<IconArchive size={14} />}
            onClick={() => setArchiveConfirmOpen(true)}
            loading={archiveMut.isPending}
          >
            {archived ? "Unarchive" : "Archive"} hunt
          </Button>
        </Group>

        {multiTeam && (
          <Switch
            label="Finished the hunt"
            description={`Part of the multi-team hunt ${multiTeam.name}.`}
            checked={multiTeam.completed}
            disabled={archived || multiTeam.archived || completeMut.isPending}
            onChange={(e) => {
              // Marking complete opens spoilers to the team, so it needs a confirm;
              // undoing it does not.
              if (e.currentTarget.checked) setCompleteConfirmOpen(true);
              else handleToggleComplete(false);
            }}
          />
        )}
        {multiTeam && (
          <MarkCompleteModal
            opened={completeConfirmOpen}
            team={multiTeam.team ?? huntName}
            loading={completeMut.isPending}
            onClose={() => setCompleteConfirmOpen(false)}
            onConfirm={() => handleToggleComplete(true)}
          />
        )}

        {teamRole}
      </ManageSection>

      {isTeamHunt && !archived && (
        <TeamArchiveModal
          uid={uid}
          huntName={huntName}
          opened={archiveConfirmOpen}
          onClose={() => setArchiveConfirmOpen(false)}
        />
      )}
      <TypedConfirmModal
        opened={archiveConfirmOpen && !(isTeamHunt && !archived)}
        onClose={() => setArchiveConfirmOpen(false)}
        onConfirm={handleToggleArchive}
        title={archived ? "Unarchive hunt" : "Archive hunt"}
        confirmPhrase={archived ? "unarchive" : "archive"}
        confirmLabel={archived ? "Unarchive" : "Archive"}
        loading={archiveMut.isPending}
      >
        <Text size="sm" c={archived ? "yellow" : "red"}>
          {archived
            ? "Are you sure you want to unarchive this hunt? It will become active and editable again."
            : "⚠️ This will archive the hunt, making it read-only for all users."}
        </Text>
      </TypedConfirmModal>

      <ManageSection title="Danger zone" danger>
        <ManageItem title="Delete hunt" description="Deletes every puzzle, channel and spreadsheet. Cannot be undone.">
          <Group>
            <Button
              color="red"
              variant="light"
              size="xs"
              leftSection={<IconTrash size={14} />}
              onClick={() => setDeleteConfirmOpen(true)}
              loading={deleteMut.isPending}
            >
              Delete hunt
            </Button>
          </Group>
        </ManageItem>
      </ManageSection>

      <TypedConfirmModal
        opened={deleteConfirmOpen}
        onClose={() => setDeleteConfirmOpen(false)}
        onConfirm={handleDelete}
        title="Delete hunt permanently"
        confirmPhrase={huntName}
        confirmLabel="Delete permanently"
        loading={deleteMut.isPending}
      >
        <Stack gap="xs">
          <Text size="sm" c="red" fw={600}>
            This is irreversible. The following will be permanently deleted:
          </Text>
          <Text size="sm">• {puzzleCount} puzzle(s), all rounds, all Discord channels and categories</Text>
          <Text size="sm">• Google spreadsheets — permanently deleted from Google Drive, not recoverable</Text>
        </Stack>
      </TypedConfirmModal>
    </Stack>
  );
}
