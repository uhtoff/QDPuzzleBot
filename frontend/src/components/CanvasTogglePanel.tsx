import { Stack, Switch, Text, Title } from "@mantine/core";
import { useSetCanvasEnabled } from "../api";
import { notifyError } from "../util";

/** Organiser switch for the hunt's Canvas view (same as `!canvas on` / `!canvas off`). */
export function CanvasTogglePanel({ uid, enabled }: { uid: string; enabled: boolean }) {
  const setEnabled = useSetCanvasEnabled(uid);
  return (
    <Stack gap="sm">
      <Title order={5}>Canvas view</Title>
      <Text size="sm" c="dimmed">
        Shows the Canvas tab, for marking up one of the hunt's big images. Turning it off hides the tab
        but keeps any canvases. Discord: <code>!canvas on</code> / <code>!canvas off</code>.
      </Text>
      <Switch
        label="Show the Canvas tab"
        checked={enabled}
        disabled={setEnabled.isPending}
        onChange={(e) => setEnabled.mutate(e.currentTarget.checked, { onError: notifyError })}
      />
    </Stack>
  );
}
