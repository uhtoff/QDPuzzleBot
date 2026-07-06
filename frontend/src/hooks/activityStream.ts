import type { ActivityEvent } from "../types";

/**
 * Single shared connection to /api/ws/activity for the whole app. Consumers
 * (the board's live-activity map, the chat viewer's message tail) subscribe to
 * the same socket instead of each opening their own. The socket opens on the
 * first subscriber, reconnects while any remain, and closes when the last leaves.
 */
type Handler = (ev: ActivityEvent) => void;

const handlers = new Set<Handler>();
let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

function connect() {
  reconnectTimer = null;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${window.location.host}/api/ws/activity`);
  ws.onmessage = (msg) => {
    let ev: ActivityEvent;
    try { ev = JSON.parse(msg.data); } catch { return; }
    for (const h of [...handlers]) h(ev);
  };
  ws.onclose = () => {
    ws = null;
    if (handlers.size > 0 && !reconnectTimer) reconnectTimer = setTimeout(connect, 3000);
  };
  ws.onerror = () => ws?.close();
}

export function subscribeActivity(handler: Handler): () => void {
  handlers.add(handler);
  if (!ws && !reconnectTimer) connect();
  return () => {
    handlers.delete(handler);
    if (handlers.size === 0) {
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      ws?.close();
      ws = null;
    }
  };
}
