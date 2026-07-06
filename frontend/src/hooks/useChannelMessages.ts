import { useEffect, useState } from "react";
import type { ChatMessage } from "../types";
import { fetchChannelMessages } from "../api";
import { subscribeActivity } from "./activityStream";

export function useChannelMessages(channelId: string | null, live: boolean) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);

  // Initial transcript load whenever the channel changes.
  useEffect(() => {
    if (!channelId) return;
    let cancelled = false;
    setLoading(true);
    fetchChannelMessages(channelId)
      .then((m) => { if (!cancelled) setMessages(m); })
      .catch(() => { if (!cancelled) setMessages([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [channelId]);

  // Live tail off the shared activity stream, only for live (non-archived) channels.
  useEffect(() => {
    if (!channelId || !live) return;
    return subscribeActivity((ev) => {
      if (ev.channel_id !== channelId) return;
      if (ev.type === "message" && ev.message) {
        setMessages((prev) =>
          prev.some((m) => m.id === ev.message!.id) ? prev : [...prev, ev.message!]);
      } else if (ev.type === "message_edit" && ev.message) {
        // Edit payloads are partial ({id, content, maybe embeds}); merge so
        // author/attachments/timestamps survive.
        setMessages((prev) =>
          prev.map((m) => (m.id === ev.message!.id ? { ...m, ...ev.message! } : m)));
      } else if (ev.type === "message_delete" && ev.message_id) {
        setMessages((prev) => prev.filter((m) => m.id !== ev.message_id));
      }
    });
  }, [channelId, live]);

  return { messages, loading };
}
