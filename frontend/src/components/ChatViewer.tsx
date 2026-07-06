import { Modal, Stack, Group, Avatar, Text, Anchor, Image, Loader } from "@mantine/core";
import { IconBrandDiscord, IconArrowBackUp } from "@tabler/icons-react";
import { useChannelMessages } from "../hooks/useChannelMessages";
import type { ChatMessage } from "../types";
import { emojify } from "../emoji";

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

// One-line preview of a replied-to message for the quote row.
function replySnippet(m: ChatMessage): string {
  const text = m.content?.trim() || m.embeds?.[0]?.title || m.embeds?.[0]?.description;
  if (text) return emojify(text.length > 80 ? text.slice(0, 80) + "…" : text);
  if (m.attachments.length) return m.attachments[0].filename;
  return "message";
}

export function ChatViewer({ opened, onClose, channelId, live, title, discordDeepUrl, discordUrl }: Props) {
  const { messages, loading } = useChannelMessages(opened ? channelId : null, live);
  const byId = new Map(messages.map((m) => [m.id, m]));

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg">
      {live && (discordUrl || discordDeepUrl) && (
        <Group gap={6} mb="sm">
          <IconBrandDiscord size={16} />
          {/* Default to the https link: it opens in the browser everywhere and the
              desktop app intercepts it when installed. The discord:// deep link only
              works where the app has registered the protocol handler, so it is a
              demoted secondary rather than the primary (silently-failing) action. */}
          <Anchor href={discordUrl ?? discordDeepUrl!} target="_blank" rel="noopener noreferrer" size="sm">
            Open in Discord
          </Anchor>
          {discordDeepUrl && discordUrl && (
            <Anchor href={discordDeepUrl} size="xs" c="dimmed">(app)</Anchor>
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
              {m.reference_id && (
                <Group gap={4} wrap="nowrap" c="dimmed" style={{ overflow: "hidden" }}>
                  <IconArrowBackUp size={12} style={{ flexShrink: 0 }} />
                  {byId.has(m.reference_id) ? (
                    <Text size="xs" truncate>
                      <Text span size="xs" fw={600}>{byId.get(m.reference_id)!.author_name}</Text>
                      {" "}{replySnippet(byId.get(m.reference_id)!)}
                    </Text>
                  ) : (
                    <Text size="xs" fs="italic">replying to an earlier message</Text>
                  )}
                </Group>
              )}
              <Group gap={6}>
                <Text size="sm" fw={600}>{m.author_name}</Text>
                <Text size="xs" c="dimmed">{new Date(m.created_at).toLocaleString()}</Text>
                {m.edited_at && <Text size="xs" c="dimmed">(edited)</Text>}
              </Group>
              {m.content && (
                <Text size="sm" c={m.kind === KIND_COMMAND ? "dimmed" : undefined}
                      style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {emojify(m.content)}
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
                      ? <Anchor href={e.url} target="_blank" size="sm" fw={600}>{emojify(e.title)}</Anchor>
                      : <Text size="sm" fw={600}>{emojify(e.title)}</Text>
                  )}
                  {e.description && (
                    <Text size="sm" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {emojify(e.description)}
                    </Text>
                  )}
                  {e.fields?.map((f, fi) => (
                    <div key={fi} style={{ marginTop: 4 }}>
                      {f.name && <Text size="xs" fw={600}>{emojify(f.name)}</Text>}
                      <Text size="xs" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                        {emojify(f.value)}
                      </Text>
                    </div>
                  ))}
                  {e.footer && <Text size="xs" c="dimmed" mt={4}>{emojify(e.footer)}</Text>}
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
