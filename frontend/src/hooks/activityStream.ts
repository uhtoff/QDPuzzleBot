import { reconnectDelay } from "../lib/reconnect";
import type { ActivityEvent } from "../types";

/**
 * Single shared connection to /api/ws/activity for the whole app. Consumers
 * (the board's live-activity map, the chat viewer's message tail) subscribe to
 * the same socket instead of each opening their own. The socket opens on the
 * first subscriber, reconnects while any remain, and closes when the last leaves.
 *
 * Reconnection backs off and gives up on a fatal close code -- see
 * ../lib/reconnect. A 1008 means the server refused the handshake (no session,
 * no longer a member, cross-site origin); retrying cannot fix any of those.
 */
type Handler = (ev: ActivityEvent) => void;

const handlers = new Set<Handler>();
let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let failedAttempts = 0;

function connect() {
  reconnectTimer = null;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${window.location.host}/api/ws/activity`);
  ws.onopen = () => { failedAttempts = 0; };
  ws.onmessage = (msg) => {
    let ev: ActivityEvent;
    try { ev = JSON.parse(msg.data); } catch { return; }
    for (const h of [...handlers]) h(ev);
  };
  ws.onclose = (event) => {
    ws = null;
    if (handlers.size === 0 || reconnectTimer) return;
    const delay = reconnectDelay(event.code, failedAttempts);
    if (delay === null) return;
    failedAttempts += 1;
    reconnectTimer = setTimeout(connect, delay);
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
      failedAttempts = 0;
      ws?.close();
      ws = null;
    }
  };
}
