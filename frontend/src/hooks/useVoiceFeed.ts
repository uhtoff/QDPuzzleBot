import { useEffect, useState } from "react";
import { subscribeActivity } from "./activityStream";
import { applyVoiceEvent, emptyVoice, type LiveVoice } from "../lib/voice";

/** Live voice head-counts off the shared activity stream. */
export function useVoiceFeed(): LiveVoice {
  const [live, setLive] = useState<LiveVoice>(emptyVoice);
  useEffect(() => subscribeActivity((event) => setLive((prev) => applyVoiceEvent(prev, event))), []);
  return live;
}
