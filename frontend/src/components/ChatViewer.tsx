import { Modal, Stack, Group, Avatar, Text, Anchor, Image, Loader } from "@mantine/core";
import { IconBrandDiscord } from "@tabler/icons-react";
import { useChannelMessages } from "../hooks/useChannelMessages";

interface Props {
  opened: boolean;
  onClose: () => void;
  channelId: string | null;
  live: boolean;                 // true while the hunt is active; false when archived
  title: string;
  discordDeepUrl: string | null; // discord://-/channels/... (opens the app)
  discordUrl: string | null;     // https fallback
}

const KIND_COMMAND = 3;

// Discord embed accent (0xRRGGBB int) → CSS hex; falls back to a neutral grey.
function embedColor(color?: number): string {
  if (typeof color !== "number") return "var(--mantine-color-gray-5)";
  return `#${(color & 0xffffff).toString(16).padStart(6, "0")}`;
}

export function ChatViewer({ opened, onClose, channelId, live, title, discordDeepUrl, discordUrl }: Props) {
  const { messages, loading } = useChannelMessages(opened ? channelId : null, live);

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg">
      {live && (discordDeepUrl || discordUrl) && (
        <Group gap={6} mb="sm">
          <IconBrandDiscord size={16} />
          <Anchor href={discordDeepUrl ?? discordUrl!} size="sm">Open in Discord</Anchor>
          {discordDeepUrl && discordUrl && (
            <Anchor href={discordUrl} target="_blank" size="xs" c="dimmed">(browser)</Anchor>
          )}
        </Group>
      )}
      {loading && <Loader size="sm" />}
      {!loading && messages.length === 0 && (
        <Text c="dimmed" size="sm">No captured messages for this channel.</Text>
      )}
      <Stack gap="xs">
        {messages.map((m) => (
          <Group key={m.id} align="flex-start" gap="xs" wrap="nowrap">
            <Avatar src={m.author_avatar} size={24} radius="xl" />
            <div style={{ minWidth: 0 }}>
              <Group gap={6}>
                <Text size="sm" fw={600}>{m.author_name}</Text>
                <Text size="xs" c="dimmed">{new Date(m.created_at).toLocaleString()}</Text>
                {m.edited_at && <Text size="xs" c="dimmed">(edited)</Text>}
              </Group>
              {m.content && (
                <Text size="sm" c={m.kind === KIND_COMMAND ? "dimmed" : undefined}
                      style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {m.content}
                </Text>
              )}
              {m.embeds.map((e, i) => (
                <div key={i} style={{
                  borderLeft: `3px solid ${embedColor(e.color)}`,
                  background: "var(--mantine-color-default-hover)",
                  borderRadius: 3, padding: "6px 10px", marginTop: 4, maxWidth: 460,
                }}>
                  {e.title && (
                    e.url
                      ? <Anchor href={e.url} target="_blank" size="sm" fw={600}>{e.title}</Anchor>
                      : <Text size="sm" fw={600}>{e.title}</Text>
                  )}
                  {e.description && (
                    <Text size="sm" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {e.description}
                    </Text>
                  )}
                  {e.fields?.map((f, fi) => (
                    <div key={fi} style={{ marginTop: 4 }}>
                      {f.name && <Text size="xs" fw={600}>{f.name}</Text>}
                      <Text size="xs" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                        {f.value}
                      </Text>
                    </div>
                  ))}
                  {e.footer && <Text size="xs" c="dimmed" mt={4}>{e.footer}</Text>}
                </div>
              ))}
              {m.attachments.map((a) =>
                a.url ? (
                  a.content_type?.startsWith("image/") ? (
                    <Image key={a.id} src={a.url} alt={a.filename} maw={320} radius="sm" mt={4} />
                  ) : (
                    <Anchor key={a.id} href={a.url} size="sm" mt={4}>{a.filename}</Anchor>
                  )
                ) : (
                  <Text key={a.id} size="xs" c="dimmed">{a.filename} (unavailable)</Text>
                ),
              )}
            </div>
          </Group>
        ))}
      </Stack>
    </Modal>
  );
}
