import { useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Button,
  Code,
  Divider,
  Group,
  Loader,
  Anchor,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { IconAlertTriangle, IconCheck } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";
import { useReconcile, type ReconcileResult } from "../api";
import { notifyError } from "../util";
import { TypedConfirmModal } from "./TypedConfirmModal";

export function ReconcilePanel({ uid }: { uid: string }) {
  const reconcileMut = useReconcile(uid);
  const [report, setReport] = useState<ReconcileResult | null>(null);
  const [orphanConfirmOpen, setOrphanConfirmOpen] = useState(false);

  async function runReport() {
    reconcileMut.mutate({ mode: "report" }, {
      onSuccess: (data) => setReport(data),
      onError: notifyError,
    });
  }

  async function runApply() {
    reconcileMut.mutate({ mode: "apply" }, {
      onSuccess: (data) => {
        setReport(data);
        notifications.show({
          message: `Reconcile applied ${data.actions.length} fix(es).`,
          color: "green",
        });
      },
      onError: notifyError,
    });
  }

  function runDeleteOrphans() {
    if (!report) return;
    const confirmed = [
      ...report.orphan_channels.map((c) => c.name),
      ...report.orphan_tabs.map((t) => t.title),
    ];
    reconcileMut.mutate({ mode: "apply-orphans", confirmedOrphans: confirmed }, {
      onSuccess: (data) => {
        setReport(data);
        notifications.show({
          message: `Deleted ${data.deleted} orphan(s).`,
          color: "green",
        });
        setOrphanConfirmOpen(false);
      },
      onError: notifyError,
    });
  }

  const orphanNames = report
    ? [
        ...report.orphan_channels.map((c) => c.name),
        ...report.orphan_tabs.map((t) => t.title),
      ]
    : [];

  return (
    <Stack gap="md">
      <Title order={4}>Reconcile Discord ↔ Database</Title>
      <Text size="sm" c="dimmed">
        Scans every puzzle in this hunt and creates any missing Discord channels or Google Sheet
        tabs. The &ldquo;Apply&rdquo; mode fixes gaps; &ldquo;Report&rdquo; shows a dry-run.
        Orphan deletion requires a separate typed-confirm.
      </Text>

      <Group>
        <Button variant="light" onClick={runReport} loading={reconcileMut.isPending}>
          Report (dry run)
        </Button>
        <Button variant="filled" color="blue" onClick={runApply} loading={reconcileMut.isPending}>
          Apply fixes
        </Button>
      </Group>

      {report && (
        <Box>
          <Divider mb="sm" />

          {report.failures.length > 0 && (
            <Alert icon={<IconAlertTriangle size={16} />} color="red" mb="sm" title="Failures">
              {report.failures.map((f, i) => (
                <Text key={i} size="xs">
                  <b>{f.target}</b>: {f.error}
                </Text>
              ))}
            </Alert>
          )}

          {report.actions.length === 0 && orphanNames.length === 0 &&
            report.stray_files.length === 0 && report.failures.length === 0 ? (
            <Group gap={6}>
              <IconCheck size={16} color="green" />
              <Text size="sm" c="green">Everything looks good — no fixes needed.</Text>
            </Group>
          ) : (
            <>
              {report.actions.length > 0 && (
                <>
                  <Text size="sm" fw={600} mb={4}>
                    Actions ({report.actions.length})
                  </Text>
                  <Stack gap={2} mb="sm">
                    {report.actions.map((a, i) => (
                      <Group key={i} gap={6}>
                        <Badge size="xs" color="blue" variant="light">{a.kind}</Badge>
                        <Text size="xs">{a.target}</Text>
                        {a.detail && <Text size="xs" c="dimmed">— {a.detail}</Text>}
                      </Group>
                    ))}
                  </Stack>
                </>
              )}

              {report.stray_files.length > 0 && (
                <>
                  <Text size="sm" fw={600} mb={4} c="orange">
                    Stray files ({report.stray_files.length}) — in the hunt&rsquo;s Drive
                    folders but matching no puzzle; never deleted, tidy them by hand
                  </Text>
                  <Stack gap={2} mb="sm">
                    {report.stray_files.map((f) => (
                      <Group key={f.id} gap={6}>
                        <Badge size="xs" color="gray" variant="light">file</Badge>
                        {f.url ? (
                          <Anchor href={f.url} target="_blank" rel="noopener noreferrer" size="xs">
                            {f.name}
                          </Anchor>
                        ) : (
                          <Code>{f.name}</Code>
                        )}
                      </Group>
                    ))}
                  </Stack>
                </>
              )}

              {orphanNames.length > 0 && (
                <>
                  <Text size="sm" fw={600} mb={4} c="red">
                    Orphans ({orphanNames.length}) — not deleted yet
                  </Text>
                  <Stack gap={2} mb="sm">
                    {report.orphan_channels.map((c, i) => (
                      <Group key={`ch-${i}`} gap={6}>
                        <Badge size="xs" color="red" variant="light">channel</Badge>
                        <Code>{c.name}</Code>
                      </Group>
                    ))}
                    {report.orphan_tabs.map((t, i) => (
                      <Group key={`tab-${i}`} gap={6}>
                        <Badge size="xs" color="orange" variant="light">sheet tab</Badge>
                        <Code>{t.title}</Code>
                      </Group>
                    ))}
                  </Stack>
                  <Button
                    color="red"
                    variant="light"
                    size="xs"
                    onClick={() => setOrphanConfirmOpen(true)}
                    leftSection={<IconAlertTriangle size={14} />}
                  >
                    Delete {orphanNames.length} orphan{orphanNames.length === 1 ? "" : "s"}…
                  </Button>
                </>
              )}
            </>
          )}

          {report.deleted > 0 && (
            <Text size="sm" c="green" mt="xs">
              ✅ Deleted {report.deleted} orphan{report.deleted === 1 ? "" : "s"}.
            </Text>
          )}
        </Box>
      )}

      {reconcileMut.isPending && !report && (
        <Group gap={6}>
          <Loader size="xs" />
          <Text size="sm" c="dimmed">Running reconcile…</Text>
        </Group>
      )}

      <TypedConfirmModal
        opened={orphanConfirmOpen}
        onClose={() => setOrphanConfirmOpen(false)}
        onConfirm={runDeleteOrphans}
        title="Delete orphan channels and sheet tabs"
        confirmPhrase="delete orphans"
        confirmLabel="Delete orphans"
        loading={reconcileMut.isPending}
      >
        <Text size="sm" c="red">
          ⚠️ This permanently deletes {orphanNames.length} orphan Discord channel
          {report?.orphan_channels.length !== 1 ? "s" : ""} and/or sheet tab
          {report?.orphan_tabs.length !== 1 ? "s" : ""} that have no matching puzzle in the
          database. Type <b>delete orphans</b> to confirm.
        </Text>
      </TypedConfirmModal>
    </Stack>
  );
}
