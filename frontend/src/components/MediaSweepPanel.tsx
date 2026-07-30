import { useState } from "react";
import { Button, Group, Loader, Stack, Text, Title } from "@mantine/core";
import { IconAlertTriangle, IconCheck } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { useMediaStats, useMediaSweep, type MediaGcResult } from "../api";
import { notifyError } from "../util";
import { TypedConfirmModal } from "./TypedConfirmModal";

/** Bytes as a short human string. Mirrors media_gc.format_size on the backend so
 *  the panel and the bang commands read the same. */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  let size = bytes / 1024;
  for (const unit of ["KB", "MB"]) {
    if (size < 1024) return `${size.toFixed(1)} ${unit}`;
    size /= 1024;
  }
  return `${size.toFixed(1)} GB`;
}

export function MediaSweepPanel({ uid }: { uid: string }) {
  const statsMut = useMediaStats(uid);
  const sweepMut = useMediaSweep(uid);
  const [report, setReport] = useState<MediaGcResult | null>(null);
  const [swept, setSwept] = useState<MediaGcResult | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  function runReport() {
    setSwept(null);
    statsMut.mutate(undefined, { onSuccess: setReport, onError: notifyError });
  }

  function runSweep() {
    sweepMut.mutate(undefined, {
      onSuccess: (data) => {
        setSwept(data);
        // The report is now spent — anything it listed is gone.
        setReport({ count: 0, bytes: 0 });
        setConfirmOpen(false);
        notifications.show({
          message: `Swept ${data.count} image(s), freeing at least ${formatSize(data.bytes)}.`,
          color: "green",
        });
      },
      onError: notifyError,
    });
  }

  const busy = statsMut.isPending || sweepMut.isPending;

  return (
    <Stack gap="md">
      <Title order={4}>Reclaim image storage</Title>
      <Text size="sm" c="dimmed">
        Finds images nothing points at any more — pictures detached from a puzzle, clue
        images that were replaced, and the leftovers of a deleted canvas — and deletes them
        with their files. Anything still shown on a puzzle, canvas or evidence item is never
        touched, and nor is anything uploaded in the last 24 hours.
      </Text>

      <Group>
        <Button variant="light" onClick={runReport} loading={statsMut.isPending}>
          Report
        </Button>
        {report && report.count > 0 && (
          <Button
            color="red"
            variant="light"
            onClick={() => setConfirmOpen(true)}
            leftSection={<IconAlertTriangle size={14} />}
          >
            Sweep {report.count} image{report.count === 1 ? "" : "s"}…
          </Button>
        )}
      </Group>

      {report && report.count === 0 && !swept && (
        <Group gap={6}>
          <IconCheck size={16} color="green" />
          <Text size="sm" c="green">No orphaned images — nothing to sweep.</Text>
        </Group>
      )}

      {report && report.count > 0 && (
        <Text size="sm">
          {report.count} orphaned image{report.count === 1 ? "" : "s"}, holding at least{" "}
          <b>{formatSize(report.bytes)}</b>.
        </Text>
      )}

      {swept && (
        <Text size="sm" c="green">
          ✅ Swept {swept.count} image{swept.count === 1 ? "" : "s"}, freeing at least{" "}
          {formatSize(swept.bytes)}.
        </Text>
      )}

      {busy && !report && (
        <Group gap={6}>
          <Loader size="xs" />
          <Text size="sm" c="dimmed">Checking…</Text>
        </Group>
      )}

      <TypedConfirmModal
        opened={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={runSweep}
        title="Delete orphaned images"
        confirmPhrase="sweep images"
        confirmLabel="Sweep images"
        loading={sweepMut.isPending}
      >
        <Text size="sm" c="red">
          ⚠️ This permanently deletes {report?.count ?? 0} image
          {report?.count === 1 ? "" : "s"} and their files, including pictures you detached
          from a puzzle or replaced on a clue. Type <b>sweep images</b> to confirm.
        </Text>
      </TypedConfirmModal>
    </Stack>
  );
}
