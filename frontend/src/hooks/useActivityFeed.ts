import { useEffect, useRef, useState } from "react";
import type { ActivityEvent } from "../types";

export interface LiveActivity {
  at: string;
  author: string | null;
}

/**
 * Subscribe to the live activity WebSocket and keep a map of
 * channel_id -> most recent activity. Auto-reconnects on drop.
 */
export function useActivityFeed(): Record<string, LiveActivity> {
  const [live, setLive] = useState<Record<string, LiveActivity>>({});
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${window.location.host}/api/ws/activity`);
      wsRef.current = ws;

      ws.onmessage = (msg) => {
        try {
          const event: ActivityEvent = JSON.parse(msg.data);
          if (event.type === "activity" && event.channel_id && event.at) {
            setLive((prev) => ({
              ...prev,
              [event.channel_id!]: { at: event.at!, author: event.author ?? null },
            }));
          }
        } catch {
          /* ignore malformed frames */
        }
      };

      ws.onclose = () => {
        if (!closed) reconnectTimer = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      closed = true;
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, []);

  return live;
}
