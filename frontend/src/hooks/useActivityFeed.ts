import { useEffect, useRef, useState } from "react";
import { subscribeActivity } from "./activityStream";

export interface LiveActivity {
  at: string;
  author: string | null;
}

/**
 * Keep a map of channel_id -> most recent activity off the shared activity
 * stream, and invoke `onBoard` on board-change events.
 */
export function useActivityFeed(onBoard?: (uid: string) => void): Record<string, LiveActivity> {
  const [live, setLive] = useState<Record<string, LiveActivity>>({});
  const cb = useRef(onBoard);
  cb.current = onBoard;

  useEffect(() => subscribeActivity((event) => {
    if (event.type === "activity" && event.channel_id && event.at) {
      setLive((prev) => ({
        ...prev,
        [event.channel_id!]: { at: event.at!, author: event.author ?? null },
      }));
    }
    if (event.type === "board" && event.uid) {
      cb.current?.(event.uid);
    }
  }), []);

  return live;
}
